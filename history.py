"""
PulseO: permanent history
============================
Search Console only keeps about 16 months of data. This module appends every
finished day to a separate Google Sheet, once, and never overwrites it.

How it works
------------
* Five append-only tabs (H Daily, H Queries, H Pages, H Device, H Country).
* A "State" tab holds, per history tab, the last date stored and the row count.
  It is the single source of truth for "what is already stored"; the data tabs
  are never scanned to find it.
* Each run finds the last final date GSC has, then for every tab fetches the
  missing days in chunks of one calendar month and appends them. State is moved
  forward only after a chunk's rows are written, so a crash can never skip days.
* A time budget stops new chunks after ~10 minutes (the backfill simply
  continues on the next run) and a size guard stops appending before the
  spreadsheet reaches the 10,000,000 cell limit.

The module is independent of the v1 sync: it receives an authorised Sheets
service and a page-fetching callable, and returns a small result object.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from googleapiclient.errors import HttpError


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

# (tab name, GSC dimensions). The first dimension is always the date.
HISTORY_TABS = [
    ("H Daily",   ["date"]),
    ("H Queries", ["date", "query"]),
    ("H Pages",   ["date", "page"]),
    ("H Device",  ["date", "device"]),
    ("H Country", ["date", "country"]),
]

STATE_TAB    = "State"
STATE_HEADER = ["Tab", "Last date stored", "Rows total", "Updated at"]
STATE_ROWS   = 20          # grid rows reserved for the State tab

METRIC_HEADERS = ["Clicks", "Impressions", "CTR (%)", "Avg. Position"]

HISTORY_ROW_LIMIT   = 25000      # rows per GSC request
APPEND_BATCH        = 10000      # rows per Sheets append call
BACKFILL_DAYS       = 486        # start of an empty tab: today minus this many days
FINAL_LOOKBACK_DAYS = 10         # window used to find the last final date
TIME_BUDGET_S       = 600        # stop starting new chunks after this runtime
CELL_LIMIT          = 10_000_000 # Google Sheets hard limit per spreadsheet
CELL_WARN           = 9_000_000  # never append past this many cells
RETRIES             = 5          # transient API errors (5xx) are retried
CALLS_PER_MINUTE    = 45         # own limit, below Sheets' 60 reads / 60 writes per minute
QUOTA_WAITS         = 3          # times a 429 is waited out before giving up
QUOTA_WAIT_S        = 65

FULL_WARNING = ("History sheet almost full: create a new history sheet "
                "and update HISTORY_SHEET_ID")


class HistoryError(Exception):
    pass


@dataclass
class HistoryResult:
    status: str = "off"
    warning: str = ""
    oldest: str = ""
    newest: str = ""
    rows_total: int = 0
    appended: int = 0       # rows added during this run
    paused: bool = False
    full: bool = False
    per_tab: dict = field(default_factory=dict)   # tab -> (last date, rows total)

    def meta_rows(self) -> list:
        """Rows for the main sheet's Meta tab."""
        rows = [["History status", self.status]]
        if self.warning:
            rows.append(["History warning", self.warning])
        rows += [
            ["History oldest date", self.oldest],
            ["History newest date", self.newest],
            ["History rows",        self.rows_total],
        ]
        return rows


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

_sleep = time.sleep             # replaced in tests
_now   = time.monotonic         # replaced in tests
_calls = {"read": deque(), "write": deque()}      # recent call times, per kind


def _throttle(kind: str):
    """Keeps this run under the Sheets API per-minute quotas (60 reads and 60 writes
    per minute per user). Waits when the last minute is already used up."""
    recent = _calls[kind]
    now = _now()
    while recent and now - recent[0] >= 60:
        recent.popleft()
    if len(recent) >= CALLS_PER_MINUTE:
        _sleep(60 - (now - recent[0]) + 0.5)
        now = _now()
        while recent and now - recent[0] >= 60:
            recent.popleft()
    recent.append(now)


def _exec(request, kind: str):
    """Executes a Google API request: throttled, transient errors (5xx) retried,
    and a quota error (429) waited out, since the quota resets every minute."""
    for attempt in range(QUOTA_WAITS + 1):
        _throttle(kind)
        try:
            return request.execute(num_retries=RETRIES)
        except HttpError as e:
            if e.resp.status != 429 or attempt == QUOTA_WAITS:
                raise
            print(f"  History: Sheets API quota reached, waiting {QUOTA_WAIT_S}s")
            _sleep(QUOTA_WAIT_S)


def _read(request):
    return _exec(request, "read")


def _write(request):
    return _exec(request, "write")


def _iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _parse(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def tab_header(dimensions: list) -> list:
    return [d.capitalize() for d in dimensions] + METRIC_HEADERS


def format_row(row: dict) -> list:
    """One GSC API row as a sheet row. Same formats as the v1 tabs."""
    return list(row.get("keys", [])) + [
        int(row.get("clicks", 0)),
        int(row.get("impressions", 0)),
        round(row.get("ctr", 0) * 100, 2),
        round(row.get("position", 0), 1),
    ]


def month_chunks(start: date, end: date):
    """Yields (first day, last day) pairs, one per calendar month."""
    cur = start
    while cur <= end:
        first_of_next = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(first_of_next - timedelta(days=1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def last_final_date(fetch_pages, today: date):
    """Latest date for which Search Console has final data, or None."""
    start = _iso(today - timedelta(days=FINAL_LOOKBACK_DAYS))
    latest = None
    for page in fetch_pages(start, _iso(today), ["date"], HISTORY_ROW_LIMIT):
        for row in page:
            d = row["keys"][0]
            if latest is None or d > latest:
                latest = d
    return _parse(latest) if latest else None


# --------------------------------------------------------------------------- #
# Sheets access
# --------------------------------------------------------------------------- #

def grid_info(sheets, spreadsheet_id: str):
    """Returns ({tab title: sheetId}, total grid cells of the spreadsheet)."""
    resp = _read(sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))",
    ))
    titles, total = {}, 0
    for s in resp.get("sheets", []):
        p = s["properties"]
        gp = p.get("gridProperties", {})
        titles[p["title"]] = p.get("sheetId")
        total += gp.get("rowCount", 0) * gp.get("columnCount", 0)
    return titles, total


def create_tab(sheets, spreadsheet_id: str, title: str, header: list, rows: int = 2):
    """Creates a tab with a bold, frozen header row. Grid is kept tiny on purpose:
    every grid cell counts toward the 10M cell limit."""
    resp = _write(sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {
            "title": title,
            "gridProperties": {"rowCount": rows, "columnCount": len(header),
                               "frozenRowCount": 1},
        }}}]},
    ))
    sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    _write(sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!A1",
        valueInputOption="RAW", body={"values": [header]},
    ))
    _write(sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }}]},
    ))


def read_cell(sheets, spreadsheet_id: str, tab: str, cell: str) -> str:
    """Value of one cell ('' when empty, or when it lies outside the tab's grid)."""
    try:
        resp = _read(sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!{cell}:{cell}"))
    except HttpError as e:
        if e.resp.status == 400:   # "range exceeds grid limits": nothing is there
            return ""
        raise
    values = resp.get("values") or [[]]
    return str(values[0][0]) if values[0] else ""


def read_state(sheets, spreadsheet_id: str) -> dict:
    """{tab: {"row": sheet row number, "last": 'YYYY-MM-DD', "rows": int}}"""
    resp = _read(sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{STATE_TAB}'!A2:D"))
    state = {}
    for i, row in enumerate(resp.get("values", []), start=2):
        if row and row[0]:
            state[row[0]] = {
                "row":  i,
                "last": row[1] if len(row) > 1 else "",
                "rows": int(row[2]) if len(row) > 2 and str(row[2]).strip() else 0,
            }
    return state


def next_state_row(state: dict) -> int:
    return max((v["row"] for v in state.values()), default=1) + 1


def write_state(sheets, spreadsheet_id: str, state: dict, tab: str, last: str, rows: int):
    """Creates or updates the State row of one tab."""
    if tab not in state:
        state[tab] = {"row": next_state_row(state), "last": "", "rows": 0}
    row = state[tab]["row"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    _write(sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{STATE_TAB}'!A{row}:D{row}",
        valueInputOption="RAW", body={"values": [[tab, last, rows, now_str]]},
    ))
    state[tab]["last"], state[tab]["rows"] = last, rows


def append_rows(sheets, spreadsheet_id: str, tab: str, rows: list, log):
    """Appends rows in batches. Logs clearly if a chunk is only partly written."""
    for i in range(0, len(rows), APPEND_BATCH):
        batch = rows[i:i + APPEND_BATCH]
        try:
            _write(sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1",
                valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                body={"values": batch},
            ))
        except Exception:
            if i > 0:
                log(f"  ⚠  History: {tab}: only {i} of {len(rows)} rows of this chunk were "
                    f"appended before the error. State was not advanced; the next run "
                    f"removes those rows and fetches the chunk again.")
            raise


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def run_history(sheets, history_id: str, fetch_pages, started_at: float = None,
                today: date = None, clock=time.monotonic,
                budget: float = TIME_BUDGET_S, log=print) -> HistoryResult:
    """
    Brings the history sheet up to date.

    sheets      authorised Google Sheets v4 service
    history_id  ID of the history spreadsheet (already shared with the service account)
    fetch_pages callable(start 'YYYY-MM-DD', end, dimensions, row_limit) -> iterator of
                pages, each a list of GSC API rows
    started_at  clock() value when the whole run began (the time budget counts from it)
    """
    today = today or date.today()
    started_at = clock() if started_at is None else started_at
    result = HistoryResult()

    final = last_final_date(fetch_pages, today)
    if final is None:
        log("History: no final Search Console data available yet, nothing to store")
        result.status = "waiting for final data"
        _fill_summary(sheets, history_id, result, final)
        return result
    log(f"History: syncing up to {_iso(final)}")

    # State tab (created once) and the current size of the spreadsheet
    titles, cells = grid_info(sheets, history_id)
    if STATE_TAB not in titles:
        create_tab(sheets, history_id, STATE_TAB, STATE_HEADER, rows=STATE_ROWS)
        titles[STATE_TAB] = None
    state = read_state(sheets, history_id)

    stop = False
    for tab, dims in HISTORY_TABS:
        if stop:
            break
        header = tab_header(dims)
        ncols = len(header)

        if tab not in titles:
            create_tab(sheets, history_id, tab, header)
            titles[tab] = None
        elif not read_cell(sheets, history_id, tab, "A1"):
            raise HistoryError(f"{tab} exists but has no header row. Delete the tab "
                               f"and the matching State row, then run again.")

        # Where do we continue?
        if tab in state:
            counted = state[tab]["rows"]
            if read_cell(sheets, history_id, tab, f"A{counted + 2}"):
                # A previous run crashed after appending a chunk but before State moved on.
                # Those rows are not counted anywhere, so remove them: the chunk is fetched
                # again below and nothing ends up stored twice.
                _write(sheets.spreadsheets().values().clear(
                    spreadsheetId=history_id,
                    range=f"'{tab}'!A{counted + 2}:{chr(64 + ncols)}", body={}))
                log(f"  ⚠  History: {tab} had rows below the {counted} that State counts "
                    f"(a previous run stopped between appending and updating State). "
                    f"Removed them; that chunk is fetched again, so nothing is stored twice.")
        else:
            if read_cell(sheets, history_id, tab, "A2"):
                raise HistoryError(f"{tab} already has data but no State row. Clear the tab "
                                   f"(keep the header) or add the State row by hand.")
            # Register the tab before the first append so State always has its row
            write_state(sheets, history_id, state, tab, "", 0)

        last_stored = state[tab]["last"]
        if last_stored:
            start = _parse(last_stored) + timedelta(days=1)
        else:
            start = today - timedelta(days=BACKFILL_DAYS)

        if start > final:
            log(f"  ✓  {tab:<12} up to date ({last_stored})")
            continue

        appended = 0
        for chunk_start, chunk_end in month_chunks(start, final):
            if clock() - started_at >= budget:
                paused_at = state[tab]["last"] or _iso(start)
                log(f"History: paused at {paused_at} ({tab}), will continue next run")
                result.paused = True
                stop = True
                break

            _, cells = grid_info(sheets, history_id)
            if cells >= CELL_WARN:
                result.full = stop = True
                break

            rows = []
            for page in fetch_pages(_iso(chunk_start), _iso(chunk_end), dims, HISTORY_ROW_LIMIT):
                rows.extend(page)
            rows.sort(key=lambda r: r["keys"])
            table = [format_row(r) for r in rows]

            if cells + len(table) * ncols > CELL_WARN:
                result.full = stop = True
                break

            if table:
                append_rows(sheets, history_id, tab, table, log)
            total = state[tab]["rows"] + len(table)
            write_state(sheets, history_id, state, tab, _iso(chunk_end), total)
            appended += len(table)
            result.appended += len(table)

        if not stop:
            log(f"  ✓  {tab:<12} +{appended} rows  (through {state[tab]['last']})")
        elif appended:
            log(f"  ✓  {tab:<12} +{appended} rows  (through {state[tab]['last']}, not finished)")

    if result.full:
        result.warning = FULL_WARNING
        log(f"  ⚠  History: {FULL_WARNING}")

    _fill_summary(sheets, history_id, result, final, state)
    return result


def _fill_summary(sheets, history_id, result: HistoryResult, final, state=None):
    """Status line plus oldest / newest date and row count for the Meta tab."""
    if state is None:
        state = read_state(sheets, history_id) if _has_state(sheets, history_id) else {}

    lasts = [state[t]["last"] for t, _ in HISTORY_TABS if t in state and state[t]["last"]]
    result.rows_total = sum(state[t]["rows"] for t, _ in HISTORY_TABS if t in state)
    result.newest = min(lasts) if lasts else ""   # complete through this date, all tabs

    firsts = []
    for t, _ in HISTORY_TABS:
        if t in state and state[t]["rows"] > 0:
            v = read_cell(sheets, history_id, t, "A2")
            if v:
                firsts.append(v)
    result.oldest = min(firsts) if firsts else ""
    result.per_tab = {t: (state[t]["last"], state[t]["rows"])
                      for t, _ in HISTORY_TABS if t in state}

    complete = (final is not None and len(lasts) == len(HISTORY_TABS)
                and all(d >= _iso(final) for d in lasts))
    if result.full:
        result.status = "full: not appending"
    elif result.paused:
        result.status = "backfilling: continues next run"
    elif final is None:
        result.status = "waiting for final data"
    elif complete:
        result.status = f"up to date ({_iso(final)})"
    else:
        result.status = "backfilling: continues next run"


def _has_state(sheets, history_id) -> bool:
    titles, _ = grid_info(sheets, history_id)
    return STATE_TAB in titles
