"""
PulseO: the daily technical run (phase 1: health checks and alerts)
===================================================================
For every site that has the technical module on and runs with the owner's own access:

  1. the Technical sheet of the site is found (or made, next to the site's other sheets) and its tabs are made sure of:
     Overview (today's status), Health Log (every check result), Robots Versions (every version of robots.txt),
     and the hidden _state (what is open, the last robots.txt, the last sitemap sizes, the last key page statuses);
  2. the checks run once for every site; what failed is asked again after the configured wait (the same wait for all
     sites, once), and only a problem that fails twice is a problem;
  3. against the open problems of each site it is decided what to say; ONE email covers all sites and is sent only when
     there is something new, worse or fixed. A manual run sends nothing unless it was asked to; a problem that was not
     told stays untold, so the next scheduled run tells it.

Nothing is written to a site. The Reports sheets, the decks and the dashboards are not touched. The log holds counts only.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import content
import history
import provision
import site_check
import tabs
import technical
from technical import TechState

OVERVIEW, HEALTH, ROBOTS, STATE = "Overview", "Health Log", "Robots Versions", "_state"
BLOCKED = "Checker Blocked"
BLOCKED_HEADER = ["Address", "Note"]
OVERVIEW_HEADER = ["Check", "Status", "Detail", "Open since", "Clicks (28 days)", "Last checked (UTC)"]
HEALTH_HEADER = ["Date", "Time (UTC)", "Check", "Result", "Detail", "Pages", "Clicks (28 days)"]
ROBOTS_HEADER = ["Date", "Time (UTC)", "robots.txt"]
STATE_HEADER = ["kind", "key", "value", "updated"]
PAGES_SHOWN = 5


@dataclass
class Deps:
    """What touches the world, handed in so a test can replace it."""
    fetch: object = technical.http_fetch
    get: object = site_check.default_get
    sleep: object = None
    now: object = lambda: datetime.now(timezone.utc)
    final: object = None            # (run) -> the last day with final Search Console data, or None
    page_rows: object = None        # (run, first_day, last_day) -> Search Console rows of ["page"]
    sitemaps_api: object = None     # (run) -> [{"path", "errors", ...}] of the sitemaps submitted in Search Console
    mail: object = None             # (subject, body) -> None; raises when it cannot send. None: no email in this run
    daily: bool = False             # the small daily check: only the home page and robots.txt


# --------------------------------------------------------------------------- #
# The sheet
# --------------------------------------------------------------------------- #

def _add_tab(sheets, file_id: str, title: str, header: list, hidden: bool = False):
    resp = history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [{"addSheet": {
        "properties": dict({"title": title, "gridProperties": {"rowCount": 2, "columnCount": len(header), "frozenRowCount": 1}},
                           **({"hidden": True} if hidden else {}))}}]}))
    sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    history._write(sheets.spreadsheets().values().update(spreadsheetId=file_id, range=f"'{title}'!A1",
                                                         valueInputOption="RAW", body={"values": [header]}))
    _bold(sheets, file_id, sid)


def _bold(sheets, file_id: str, sheet_id: int):
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [{"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
        "fields": "userEnteredFormat.textFormat.bold"}}]}))


def ensure_tabs(sheets, file_id: str):
    """Overview, Health Log, Robots Versions and the hidden _state exist (the empty first tab of a new file becomes Overview)."""
    titles, _ = history.grid_info(sheets, file_id)
    if OVERVIEW not in titles:
        first = next(iter(titles), None)
        if first is not None and len(titles) == 1 and titles[first] is not None:
            history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [{
                "updateSheetProperties": {"properties": {"sheetId": titles[first], "title": OVERVIEW,
                                                         "gridProperties": {"frozenRowCount": 1}},
                                          "fields": "title,gridProperties.frozenRowCount"}}]}))
            _bold(sheets, file_id, titles[first])
        else:
            _add_tab(sheets, file_id, OVERVIEW, OVERVIEW_HEADER)
        titles[OVERVIEW] = None
    for name, header, hidden in ((HEALTH, HEALTH_HEADER, False), (ROBOTS, ROBOTS_HEADER, False), (BLOCKED, BLOCKED_HEADER, False), (STATE, STATE_HEADER, True)):
        if name not in titles:
            _add_tab(sheets, file_id, name, header, hidden)


def _stamp(now: datetime) -> tuple:
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M")


def pages_text(pages: list) -> str:
    shown = " · ".join(technical.readable(p) for p in pages[:PAGES_SHOWN])
    return shown + (f" · +{len(pages) - PAGES_SHOWN} more" if len(pages) > PAGES_SHOWN else "")


def health_rows(log: list, now: datetime) -> list:
    day, clock = _stamp(now)
    return [[day, clock, technical.name_of(l.key), l.result, l.detail, pages_text(l.pages), l.clicks or ""] for l in log]


ORDER = ("home", "robots", "robots_block", "robots_changed", "sitemap", "sitemap_drop", "key_status", "key_blocked", "key_noindex",
         "key_canonical", "server_errors", "sitemaps_api")


def entry_of(r, now: datetime) -> dict:
    """What the Overview shows of one result, small enough to be kept in the state between runs."""
    day, clock = _stamp(now)
    if r.skipped:
        status = "Not checked"
    elif r.note == "changed":
        status = "Changed"
    elif r.note == "blocked":
        status = "Blocked for checker only"
    elif r.note == "not confirmed":
        status = "OK (one failed check, not confirmed)"
    else:
        status = "OK" if r.ok else "PROBLEM"
    return {"status": status, "detail": r.detail, "clicks": r.clicks or "", "stamp": f"{day} {clock}"}


def overview_rows(entries: dict, opened: dict, now: datetime) -> list:
    """The Overview: the state of the site, then every check as it was last made (the small daily check only renews its own
    two checks), then the open problems."""
    day, clock = _stamp(now)
    open_count = len(opened)
    rows = [["Site health", "OK" if not open_count else f"{open_count} open problem{'s' if open_count != 1 else ''}",
             "Every check passed" if not open_count else "See the open problems below", "", "", f"{day} {clock}"],
            ["Last audit", "none yet", "No technical audit has been written yet", "", "", ""], [""]]
    family = lambda key: key.split(":", 1)[0]
    for key in sorted(entries, key=lambda k: ORDER.index(family(k)) if family(k) in ORDER else len(ORDER)):
        e = entries[key]
        name = technical.name_of(key)
        if key.startswith(("sitemap:", "sitemap_drop:")):
            name += f": {technical.readable(key.split(':', 1)[1])}"
        rows.append([name, e.get("status", ""), e.get("detail", ""), opened.get(key, {}).get("since", ""),
                     e.get("clicks", ""), e.get("stamp", "")])
    if opened:
        rows += [[""], ["Open problems", "", "", "", "", ""]]
        for key, rec in opened.items():
            e = entries.get(key, {})
            rows.append([rec.get("title", key), "open", e.get("detail", ""), rec.get("since", ""), e.get("clicks", ""), ""])
    return rows


# --------------------------------------------------------------------------- #
# One site
# --------------------------------------------------------------------------- #

@dataclass
class Work:
    """One site in this run."""
    run: object
    label: str
    file_id: str
    state: TechState
    ctx: technical.Context
    first: technical.Round
    second: technical.Round = None
    final: list = field(default_factory=list)
    log: list = field(default_factory=list)
    decision: technical.Decision = None
    now_status: dict = field(default_factory=dict)
    created: bool = False


def key_page_set(site, origin: str, rows: list) -> tuple:
    """([key page addresses], {page key: clicks in the last 28 days}): the top pages by clicks, then the configured ones."""
    clicks, order = {}, []
    for row in rows:
        url = row["keys"][0]
        k = content.url_key(url)
        clicks[k] = clicks.get(k, 0) + int(row.get("clicks", 0))
    # a page address with a query string (filters, store views, tracking) is not a page to watch; its clicks still count
    ranked = sorted({content.url_key(r["keys"][0]): r["keys"][0] for r in rows if "?" not in r["keys"][0]}.items(),
                    key=lambda kv: -clicks[kv[0]])
    urls, seen = [], set()
    for k, url in ranked[:technical.KEY_PAGES]:
        urls.append(url)
        seen.add(k)
    for extra in site.technical.key_pages_extra:
        url = extra if "://" in extra else origin + ("" if extra.startswith("/") else "/") + extra
        if content.url_key(url) not in seen:
            seen.add(content.url_key(url))
            urls.append(url)
    return urls, clicks


def prepare(run, deps: Deps, now: datetime, log) -> Work:
    """The sheet, the state, the key pages and the first round of checks of one site."""
    site = run.site
    label = site.deck.site_label or site.name
    file_id, created = provision.technical_file(run.drive, run.sheets, site, label, log)
    ensure_tabs(run.sheets, file_id)
    state = TechState(tabs.read_tab(run.sheets, file_id, STATE))
    origin = site_check.origin_of(site.site_url)
    final_day = deps.final(run) if deps.final else None
    rows = []
    if final_day and deps.page_rows:
        try:
            rows = deps.page_rows(run, final_day)
        except Exception as e:                        # noqa: BLE001  without the clicks the checks still run, on the configured pages
            log(f"technical: {run.name}: the clicks could not be read ({type(e).__name__})")
    urls, clicks = key_page_set(site, origin, rows)
    blocked = {content.url_key(r[0]) for r in tabs.read_rows(run.sheets, file_id, BLOCKED, "B") if r and str(r[0]).strip()}
    if deps.daily:
        urls = []                                   # the small daily check does not look at the key pages
    try:
        prev_counts = json.loads(state.get("sitemaps", "counts", "{}"))
    except ValueError:
        prev_counts = {}
    ctx = technical.Context(
        site=site, origin=origin, key_urls=urls, clicks=clicks, fetch=deps.fetch, get=deps.get,
        sitemaps_api=(lambda: deps.sitemaps_api(run)) if deps.sitemaps_api else None,
        prev_robots=state.get("robots", "text"), prev_counts=prev_counts,
        extra_sitemaps=list(site.content.extra_sitemaps), daily=deps.daily, blocked=blocked)
    first = technical.check_all(ctx)
    return Work(run, label, file_id, state, ctx, first, created=created)


def confirm_work(w: Work, deps: Deps, today: date):
    """The second round (only what failed), the final results and what is worth saying."""
    failing = technical.failing_of(w.first)
    if failing:
        w.second = technical.check_all(w.ctx, only=failing)
    w.final, w.log = technical.confirm(w.first, w.second, w.ctx)
    now_status = dict(w.first.key_status)
    now_status.update(w.second.key_status if w.second else {})
    try:
        prev = json.loads(w.state.get("keystat", "snapshot", "{}"))
    except ValueError:
        prev = {}
    large = technical.large_change(prev, now_status, w.ctx.cfg.large_change_share)
    w.decision = technical.decide(w.final, w.state.open(), today, large, w.ctx.clicks_of)
    w.now_status = now_status


def write_work(w: Work, deps: Deps, now: datetime, mailed: bool):
    """The sheet and the state, after the email was tried: a problem is marked as told only if the email went out."""
    run, sheets, today = w.run, w.run.sheets, now.date()
    state, d = w.state, w.decision
    prev_robots = state.get("robots", "text")
    text = w.first.robots_text if w.first.robots_text is not None else (w.second.robots_text if w.second else None)
    if text is not None:
        if prev_robots is None or technical._normal_robots(prev_robots) != technical._normal_robots(text):
            day, clock = _stamp(now)
            tabs.append(sheets, w.file_id, ROBOTS, [[day, clock, text[:technical.ROBOTS_TEXT_MAX]]])
        state.set("robots", "text", text[:technical.ROBOTS_TEXT_MAX], today)
    counts = json.loads(state.get("sitemaps", "counts", "{}"))
    merged = dict(w.first.sitemap_counts)
    merged.update(w.second.sitemap_counts if w.second else {})
    dropped = {r.key.split(":", 1)[1] for r in w.final if r.key.startswith("sitemap_drop:") and not r.ok}
    for root, total in merged.items():
        if root not in dropped:                       # a drop is measured against the last good size
            counts[root] = total
    state.set("sitemaps", "counts", json.dumps(counts), today)
    if w.now_status and not (d.large and not mailed):   # a large change that was not told is seen again next time
        state.set("keystat", "snapshot", json.dumps(w.now_status, ensure_ascii=False), today)
    opened = technical.mark_told(d.opened, d.told, today) if mailed else d.opened
    state.save_open(opened, today)
    tabs.append(sheets, w.file_id, HEALTH, health_rows(w.log, now))
    entries = {k: json.loads(v) for k, v in state.of("last").items()} if deps.daily else {}      # the daily check keeps the rest
    for r in w.final:
        entries[r.key] = entry_of(r, now)
    state.clear("last")
    for key, e in entries.items():
        state.set("last", key, json.dumps(e, ensure_ascii=False), today)
    tabs.rewrite(sheets, w.file_id, OVERVIEW, OVERVIEW_HEADER, overview_rows(entries, opened, now))
    tabs.rewrite(sheets, w.file_id, STATE, STATE_HEADER, state.table())


def summary(w: Work) -> str:
    """Counts only: this goes into a log that anyone can read."""
    d = w.decision
    results = [r for r in w.final if not r.skipped and r.note != "changed"]
    failed = sum(1 for l in w.log if l.result.startswith("failed"))
    confirmed = sum(1 for l in w.log if l.result == "confirmed problem")
    return (f"{len(results)} checks, {len(w.ctx.key_urls)} key pages, {failed} failed the first check, {confirmed} confirmed, "
            f"{len(d.opened)} open, {len(d.alerts) + (1 if d.large else 0)} to tell, {len(d.fixed)} fixed")


def run_technical(runs: list, deps: Deps, log=print, on_error=None) -> list:
    """The whole daily run for these sites. Returns the lines for the run summary. A site that fails is left out
    (reported through on_error) and never stops the others."""
    now = deps.now()
    today = now.date()
    works, lines = [], []
    for run in runs:
        site = run.site
        if not (site.technical.enabled and site.technical.checks.get("health", True)):
            continue
        try:
            works.append(prepare(run, deps, now, log))
        except Exception as e:                        # noqa: BLE001  one site never stops the others
            (on_error or (lambda r, x: log(f"technical: {r.name}: failed ({type(x).__name__})")))(run, e)
            lines.append(f"{run.name}: failed")
    failing = [w for w in works if technical.failing_of(w.first)]
    if failing:
        minutes = max(w.ctx.cfg.confirm_minutes for w in failing)
        log(f"technical: {sum(len(technical.failing_of(w.first)) for w in failing)} checks failed in {len(failing)} sites; "
            f"the second check comes in {minutes} minutes")
        if minutes and deps.sleep:
            deps.sleep(minutes * 60)
    good = []
    for w in works:
        try:
            confirm_work(w, deps, today)
            good.append(w)
        except Exception as e:                        # noqa: BLE001
            (on_error or (lambda r, x: log(f"technical: {r.name}: failed ({type(x).__name__})")))(w.run, e)
            lines.append(f"{w.run.name}: failed")
    mail = technical.build_email([(w.label, w.decision) for w in good])
    mailed = False
    if mail and deps.mail:
        try:
            deps.mail(*mail)
            mailed = True
            log("technical: one email was sent")
        except Exception as e:                        # noqa: BLE001  not told: the next run tells it
            log(f"technical: the email could not be sent ({type(e).__name__}); it stays untold")
    elif mail:
        log("technical: something is worth telling, no email in this run; it stays untold until a scheduled run")
    for w in good:
        try:
            write_work(w, deps, now, mailed)
            lines.append(f"{w.run.name}: {summary(w)}")
        except Exception as e:                        # noqa: BLE001
            (on_error or (lambda r, x: log(f"technical: {r.name}: failed ({type(x).__name__})")))(w.run, e)
            lines.append(f"{w.run.name}: failed")
    return lines
