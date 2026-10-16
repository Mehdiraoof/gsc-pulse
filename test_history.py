"""
Tests for the history module and its wiring into gsc_pulse.main().
Google Search Console and Google Sheets are replaced by small in-memory fakes,
so no credentials or network are needed:

    python -m unittest -v
"""

import contextlib
import functools
import io
import os
import re
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

import httplib2
from googleapiclient.errors import HttpError

import gsc_pulse
import history

HID   = "hist-sheet-id"
MAIN  = "main-sheet-id"
TODAY = date(2026, 10, 16)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

def http_error(status, reason="error"):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = reason
    return HttpError(resp, b"{}")


class Req:
    """Mimics a googleapiclient request: nothing happens until execute()."""
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeGsc:
    """Search Console with `per_day` rows per day for every dimension combo
    (1 row per day for the plain date dimension), data in [first, final]."""

    def __init__(self, first, final, per_day=3):
        self.first, self.final, self.per_day = first, final, per_day
        self.bodies = []

    def searchanalytics(self):
        return self

    def query(self, siteUrl, body):
        self.bodies.append(body)
        return Req(lambda: self._answer(body))

    def _answer(self, body):
        dims = body["dimensions"]
        lo = max(date.fromisoformat(body["startDate"]), self.first)
        hi = min(date.fromisoformat(body["endDate"]), self.final)
        rows, d = [], hi
        while d >= lo:                     # newest first, like "sorted by clicks"
            n = 1 if dims == ["date"] else self.per_day
            for j in range(n):
                keys = [d.isoformat()] + ([f"{dims[1]}-{j}"] if len(dims) > 1 else [])
                rows.append({"keys": keys, "clicks": j + 1, "impressions": 10 * (j + 1),
                             "ctr": 0.1234, "position": 3.26})
            d -= timedelta(days=1)
        page = rows[body["startRow"]: body["startRow"] + body["rowLimit"]]
        return {"rows": page} if page else {}


class Tab:
    def __init__(self, sheet_id, rows=1000, cols=26):
        self.sheet_id, self.row_count, self.col_count = sheet_id, rows, cols
        self.data = []          # list of rows, trailing empty rows trimmed
        self.frozen, self.bold = 0, False


def _col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def parse_range(rng):
    tab, _, cells = rng.rpartition("!") if "!" in rng else (rng, "", "")
    tab = tab.strip("'")
    r1 = r2 = None
    c1, c2 = 1, None
    if cells:
        m = re.fullmatch(r"([A-Z]+)(\d*)(?::([A-Z]+)(\d*))?", cells)
        c1 = _col(m.group(1))
        r1 = int(m.group(2)) if m.group(2) else None
        if m.group(3):
            c2 = _col(m.group(3))
            r2 = int(m.group(4)) if m.group(4) else None
        else:
            c2, r2 = c1, r1
    return tab, c1, r1, c2, r2


class FakeSheets:
    """Several in-memory spreadsheets with the few Sheets API calls we use."""

    def __init__(self):
        self.books = {}
        self.next_id = 1
        self.append_sizes = []
        self.append_calls = 0
        self.fail_append_call = None        # raise on the Nth append call
        self.fail_state_update_once = False # raise on first State update that has a date

    def add_book(self, book_id, tabs=None):
        book = {"Sheet1": Tab(0)}
        for title, (rows, cols) in (tabs or {}).items():
            book[title] = Tab(self.next_id, rows, cols)
            self.next_id += 1
        self.books[book_id] = book
        return book

    def cells(self, book_id):
        return sum(t.row_count * t.col_count for t in self.books[book_id].values())

    def _tab(self, book_id, title):
        try:
            return self.books[book_id][title]
        except KeyError:
            raise http_error(400, f"Unable to parse range: {title}")

    # ---- API surface ------------------------------------------------------
    def spreadsheets(self):
        return self

    def get(self, spreadsheetId, fields=None):
        def run():
            book = self.books.setdefault(spreadsheetId, {"Sheet1": Tab(0)})
            return {"sheets": [{"properties": {
                "sheetId": t.sheet_id, "title": title,
                "gridProperties": {"rowCount": t.row_count, "columnCount": t.col_count}}}
                for title, t in book.items()]}
        return Req(run)

    def batchUpdate(self, spreadsheetId, body):
        def run():
            book = self.books.setdefault(spreadsheetId, {"Sheet1": Tab(0)})
            replies = []
            for req in body["requests"]:
                if "addSheet" in req:
                    p = req["addSheet"]["properties"]
                    gp = p.get("gridProperties", {})
                    if p["title"] in book:
                        raise http_error(400, "already exists")
                    t = Tab(self.next_id, gp.get("rowCount", 1000), gp.get("columnCount", 26))
                    t.frozen = gp.get("frozenRowCount", 0)
                    self.next_id += 1
                    book[p["title"]] = t
                    replies.append({"addSheet": {"properties": {
                        "sheetId": t.sheet_id, "title": p["title"]}}})
                elif "repeatCell" in req:
                    sid = req["repeatCell"]["range"]["sheetId"]
                    for t in book.values():
                        if t.sheet_id == sid:
                            t.bold = True
                    replies.append({})
                else:
                    replies.append({})
            return {"replies": replies}
        return Req(run)

    def values(self):
        return FakeValues(self)


range_ = range      # FakeValues methods take a parameter called `range`, like the real API


class FakeValues:
    """values() resource bound to a FakeSheets."""

    def __init__(self, fs):
        self.fs = fs

    def get(self, spreadsheetId, range):
        def run():
            tab, c1, r1, c2, r2 = parse_range(range)
            t = self.fs._tab(spreadsheetId, tab)
            r1 = r1 or 1
            if r1 > t.row_count:
                raise http_error(400, "Range exceeds grid limits")
            last = r2 if r2 else len(t.data)
            out = []
            for r in range_(r1, last + 1):
                row = t.data[r - 1] if r - 1 < len(t.data) else []
                cells = [str(v) for v in row[c1 - 1: c2]] if c2 else [str(v) for v in row[c1 - 1:]]
                while cells and cells[-1] == "":
                    cells.pop()
                out.append(cells)
            while out and not out[-1]:
                out.pop()
            return {"values": out} if out else {}
        return Req(run)

    def update(self, spreadsheetId, range, valueInputOption, body):
        def run():
            tab, c1, r1, c2, r2 = parse_range(range)
            t = self.fs._tab(spreadsheetId, tab)
            r1 = r1 or 1
            if (tab == history.STATE_TAB and self.fs.fail_state_update_once
                    and r1 >= 2 and body["values"][0][1] != ""):
                self.fs.fail_state_update_once = False
                raise RuntimeError("boom (state update)")
            for i, row in enumerate(body["values"]):
                r = r1 + i
                if r > t.row_count:
                    raise http_error(400, "Range exceeds grid limits")
                while len(t.data) < r:
                    t.data.append([])
                t.data[r - 1] = list(row)
            return {}
        return Req(run)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        def run():
            assert valueInputOption == "RAW" and insertDataOption == "INSERT_ROWS"
            tab = parse_range(range)[0]
            t = self.fs._tab(spreadsheetId, tab)
            self.fs.append_calls += 1
            if self.fs.fail_append_call == self.fs.append_calls:
                raise RuntimeError("boom (append)")
            self.fs.append_sizes.append(len(body["values"]))
            t.data.extend(list(r) for r in body["values"])
            t.row_count += len(body["values"])        # INSERT_ROWS grows the grid
            return {}
        return Req(run)

    def clear(self, spreadsheetId, range, body=None):
        def run():
            tab, c1, r1, c2, r2 = parse_range(range)
            t = self.fs._tab(spreadsheetId, tab)
            if r1 is None:
                t.data = []
            else:
                t.data = t.data[: r1 - 1]
                while t.data and not t.data[-1]:
                    t.data.pop()
            return {}
        return Req(run)




# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class Clock:
    def __init__(self, step=1.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def fetcher(gsc):
    return functools.partial(gsc_pulse.iter_gsc_pages, gsc, "site", num_retries=5)


def run_history(sheets, gsc, today=TODAY, **kw):
    logs = []
    res = history.run_history(sheets, HID, fetcher(gsc), today=today,
                              log=logs.append, **kw)
    return res, logs


def state_of(sheets):
    rows = sheets.books[HID][history.STATE_TAB].data[1:]
    return {r[0]: (r[1], int(r[2])) for r in rows}


def tab_rows(sheets, tab):
    return sheets.books[HID][tab].data[1:]


def expected_days(gsc, today=TODAY):
    lo = max(gsc.first, today - timedelta(days=history.BACKFILL_DAYS))
    return (gsc.final - lo).days + 1


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

class HistoryTests(unittest.TestCase):

    def setUp(self):
        self.sheets = FakeSheets()
        self.sheets.add_book(HID)

    def assert_clean_tab(self, tab, expected_rows):
        rows = tab_rows(self.sheets, tab)
        self.assertEqual(len(rows), expected_rows, tab)
        self.assertEqual(len({tuple(r) for r in rows}), len(rows), f"{tab}: duplicate rows")
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates), f"{tab}: not in date order")
        self.assertEqual(state_of(self.sheets)[tab][1], expected_rows, f"{tab}: State rows")

    # ---- empty history: full backfill -------------------------------------
    def test_backfill_from_empty(self):
        gsc = FakeGsc(first=date(2026, 8, 15), final=date(2026, 10, 13))
        with mock.patch.object(history, "APPEND_BATCH", 40):
            res, logs = run_history(self.sheets, gsc)

        days = expected_days(gsc)
        self.assertEqual(res.status, "up to date (2026-10-13)")
        self.assertFalse(res.paused or res.full)
        self.assertEqual(res.oldest, "2026-08-15")
        self.assertEqual(res.newest, "2026-10-13")
        self.assertEqual(res.rows_total, days * (1 + 4 * 3))

        for tab, dims in history.HISTORY_TABS:
            n = days * (1 if dims == ["date"] else 3)
            self.assert_clean_tab(tab, n)
            self.assertEqual(state_of(self.sheets)[tab][0], "2026-10-13")
            t = self.sheets.books[HID][tab]
            self.assertEqual(t.data[0], history.tab_header(dims))
            self.assertEqual((t.frozen, t.bold), (1, True))

        # formats match v1: CTR as percent with 2 decimals, position with 1
        self.assertEqual(tab_rows(self.sheets, "H Daily")[0], ["2026-08-15", 1, 10, 12.34, 3.3])
        self.assertEqual(tab_rows(self.sheets, "H Queries")[0][:2], ["2026-08-15", "query-0"])
        self.assertTrue(self.sheets.append_sizes and max(self.sheets.append_sizes) <= 40)

        # backfill window starts 486 days back; final date comes from the API, not today-3
        starts = {b["startDate"] for b in gsc.bodies if b["dimensions"] != ["date"]}
        self.assertEqual(min(starts), (TODAY - timedelta(days=486)).isoformat())
        self.assertTrue(all("dataState" not in b for b in gsc.bodies))
        self.assertTrue(all(b["rowLimit"] == 25000 for b in gsc.bodies))
        self.assertEqual(state_of(self.sheets).keys(), {t for t, _ in history.HISTORY_TABS})

        # State header
        self.assertEqual(self.sheets.books[HID]["State"].data[0], history.STATE_HEADER)

    # ---- incremental ------------------------------------------------------
    def test_incremental_run_adds_only_new_days(self):
        gsc = FakeGsc(first=date(2026, 8, 15), final=date(2026, 10, 13))
        run_history(self.sheets, gsc)
        before = {t: len(tab_rows(self.sheets, t)) for t, _ in history.HISTORY_TABS}

        gsc.final = date(2026, 10, 16)
        gsc.bodies.clear()
        res, _ = run_history(self.sheets, gsc, today=date(2026, 10, 19))

        for tab, dims in history.HISTORY_TABS:
            added = 3 * (1 if dims == ["date"] else 3)
            self.assert_clean_tab(tab, before[tab] + added)
            self.assertEqual(state_of(self.sheets)[tab][0], "2026-10-16")
        self.assertEqual(res.newest, "2026-10-16")
        self.assertEqual(res.status, "up to date (2026-10-16)")
        starts = {b["startDate"] for b in gsc.bodies if b["dimensions"] != ["date"]}
        self.assertEqual(starts, {"2026-10-14"})      # State + 1 day, nothing refetched

    # ---- nothing new ------------------------------------------------------
    def test_nothing_new_makes_no_writes(self):
        gsc = FakeGsc(first=date(2026, 8, 15), final=date(2026, 10, 13))
        run_history(self.sheets, gsc)
        appends = self.sheets.append_calls
        snapshot = {t: [list(r) for r in self.sheets.books[HID][t].data]
                    for t in self.sheets.books[HID]}
        gsc.bodies.clear()

        res, logs = run_history(self.sheets, gsc)

        self.assertEqual(self.sheets.append_calls, appends)
        self.assertEqual(snapshot, {t: self.sheets.books[HID][t].data
                                    for t in self.sheets.books[HID]})
        self.assertTrue(all(b["dimensions"] == ["date"] for b in gsc.bodies))  # only the probe
        self.assertEqual(res.status, "up to date (2026-10-13)")
        self.assertTrue(any("up to date" in l for l in logs))

    def test_no_final_data_yet(self):
        gsc = FakeGsc(first=date(2030, 1, 1), final=date(2030, 1, 2))   # nothing in range
        res, logs = run_history(self.sheets, gsc)
        self.assertEqual(res.status, "waiting for final data")
        self.assertEqual(self.sheets.append_calls, 0)
        self.assertNotIn(history.STATE_TAB, self.sheets.books[HID])

    # ---- time budget: pause, then resume ----------------------------------
    def test_time_budget_pause_and_resume(self):
        gsc = FakeGsc(first=date(2025, 6, 1), final=date(2026, 10, 13))

        reference = FakeSheets()
        reference.add_book(HID)
        history.run_history(reference, HID, fetcher(gsc), today=TODAY, log=lambda m: None)

        res, logs = run_history(self.sheets, gsc, clock=Clock(1.0), started_at=0.0, budget=12)
        self.assertTrue(res.paused)
        self.assertIn("backfilling", res.status)
        self.assertTrue(any(l.startswith("History: paused at 2026-05-31") and
                            l.endswith("will continue next run") for l in logs), logs)
        part = state_of(self.sheets)
        self.assertEqual(part["H Daily"][0], "2026-05-31")
        self.assertNotIn("H Queries", part)            # never started

        # next run: plenty of time, continues from State without gaps or duplicates
        res, logs = run_history(self.sheets, gsc, clock=Clock(0.0), started_at=0.0)
        self.assertFalse(res.paused)
        self.assertEqual(res.status, "up to date (2026-10-13)")
        for tab, _ in history.HISTORY_TABS:
            self.assertEqual(self.sheets.books[HID][tab].data,
                             reference.books[HID][tab].data, tab)
        self.assertEqual(state_of(self.sheets), state_of(reference))

    # ---- size guard -------------------------------------------------------
    def test_size_guard_blocks_append_when_sheet_is_full(self):
        self.sheets.add_book(HID, {"Filler": (90000, 100)})      # 9,000,000 cells
        gsc = FakeGsc(first=date(2026, 8, 15), final=date(2026, 10, 13))

        res, logs = run_history(self.sheets, gsc)

        self.assertTrue(res.full)
        self.assertEqual(res.warning, history.FULL_WARNING)
        self.assertTrue(res.status.startswith("full"))
        self.assertEqual(self.sheets.append_calls, 0)
        self.assertTrue(any(history.FULL_WARNING in l for l in logs))
        self.assertTrue(all(last == "" for last, _ in state_of(self.sheets).values()))
        self.assertIn(["History warning", history.FULL_WARNING], res.meta_rows())

    def test_size_guard_stops_before_crossing_the_limit(self):
        # room for ~900 cells: the first months fit, then the guard stops mid-backfill
        self.sheets.add_book(HID, {"Filler": (8973, 1000)})
        gsc = FakeGsc(first=date(2025, 6, 1), final=date(2026, 10, 13))

        res, logs = run_history(self.sheets, gsc)

        self.assertTrue(res.full)
        self.assertLessEqual(self.sheets.cells(HID), history.CELL_WARN)
        last, rows = state_of(self.sheets)["H Daily"]
        self.assertTrue("" < last < "2026-10-13", last)   # some months stored, not all
        self.assertEqual(len(tab_rows(self.sheets, "H Daily")), rows)
        self.assertNotIn("H Queries", state_of(self.sheets))
        self.assertTrue(any(history.FULL_WARNING in l for l in logs))

    # ---- crash safety -----------------------------------------------------
    def test_crash_between_append_and_state_does_not_duplicate(self):
        gsc = FakeGsc(first=date(2025, 6, 1), final=date(2026, 10, 13))
        reference = FakeSheets()
        reference.add_book(HID)
        history.run_history(reference, HID, fetcher(gsc), today=TODAY, log=lambda m: None)

        self.sheets.fail_state_update_once = True
        with self.assertRaises(RuntimeError):
            run_history(self.sheets, gsc)
        self.assertGreater(len(tab_rows(self.sheets, "H Daily")), 0)   # rows are in, State is not

        res, logs = run_history(self.sheets, gsc)
        self.assertTrue(any("Removed them" in l for l in logs), logs)
        for tab, _ in history.HISTORY_TABS:
            self.assertEqual(tab_rows(self.sheets, tab), reference.books[HID][tab].data[1:], tab)
        self.assertEqual(state_of(self.sheets), state_of(reference))

    def test_partial_append_is_logged_and_repaired(self):
        gsc = FakeGsc(first=date(2025, 6, 1), final=date(2026, 10, 13))
        reference = FakeSheets()
        reference.add_book(HID)
        history.run_history(reference, HID, fetcher(gsc), today=TODAY, log=lambda m: None)

        with mock.patch.object(history, "APPEND_BATCH", 5):
            self.sheets.fail_append_call = 2             # 1st batch lands, 2nd fails
            logs = []
            with self.assertRaises(RuntimeError):
                history.run_history(self.sheets, HID, fetcher(gsc), today=TODAY, log=logs.append)
            self.assertTrue(any("only 5 of" in l for l in logs), logs)

            self.sheets.fail_append_call = None
            run_history(self.sheets, gsc)

        for tab, _ in history.HISTORY_TABS:
            self.assertEqual(tab_rows(self.sheets, tab), reference.books[HID][tab].data[1:], tab)

    def test_tab_with_data_but_no_state_row_is_refused(self):
        gsc = FakeGsc(first=date(2026, 8, 15), final=date(2026, 10, 13))
        run_history(self.sheets, gsc)
        self.sheets.books[HID][history.STATE_TAB].data = [history.STATE_HEADER]   # State lost
        with self.assertRaises(history.HistoryError):
            run_history(self.sheets, gsc)

    # ---- Meta rows --------------------------------------------------------
    def test_meta_rows(self):
        gsc = FakeGsc(first=date(2026, 8, 15), final=date(2026, 10, 13))
        res, _ = run_history(self.sheets, gsc)
        rows = dict(res.meta_rows())
        self.assertEqual(rows["History status"], "up to date (2026-10-13)")
        self.assertEqual(rows["History oldest date"], "2026-08-15")
        self.assertEqual(rows["History newest date"], "2026-10-13")
        self.assertEqual(rows["History rows"], res.rows_total)

    def test_month_chunks(self):
        chunks = list(history.month_chunks(date(2026, 1, 20), date(2026, 3, 5)))
        self.assertEqual(chunks, [(date(2026, 1, 20), date(2026, 1, 31)),
                                  (date(2026, 2, 1), date(2026, 2, 28)),
                                  (date(2026, 3, 1), date(2026, 3, 5))])
        self.assertEqual(list(history.month_chunks(date(2026, 3, 6), date(2026, 3, 5))), [])


class MainWiringTests(unittest.TestCase):
    """gsc_pulse.main() end to end with fakes: history off, history on, history failing."""

    def run_main(self, env, gsc, sheets):
        def fake_build(name, version, credentials=None):
            return gsc if name == "webmasters" else sheets

        out = io.StringIO()
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("HISTORY_SHEET_ID", "SITE_URL", "SHEET_ID", "DAYS", "GITHUB_ACTIONS")}
        clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(sys, "argv", ["gsc_pulse.py", "--config", "no-such-file.yaml"]), \
             mock.patch.object(gsc_pulse, "build", fake_build), \
             mock.patch.object(gsc_pulse, "get_credentials", lambda f: object()), \
             mock.patch.object(gsc_pulse, "PUBLIC_LOGS", env.get("GITHUB_ACTIONS") == "true"), \
             mock.patch.object(gsc_pulse, "_SENSITIVE", []), \
             contextlib.redirect_stdout(out):
            error = None
            try:
                gsc_pulse.main()
            except Exception as e:      # noqa: BLE001
                error = e
        return out.getvalue(), error

    def setUp(self):
        self.sheets = FakeSheets()
        self.sheets.add_book(MAIN)
        today = date.today()
        self.gsc = FakeGsc(first=today - timedelta(days=60), final=today - timedelta(days=3))
        self.base = {"SITE_URL": "sc-domain:example.test", "SHEET_ID": MAIN}

    def meta(self):
        return dict(self.sheets.books[MAIN]["Meta"].data)

    def test_history_off_when_secret_missing(self):
        out, err = self.run_main(self.base, self.gsc, self.sheets)
        self.assertIsNone(err)
        self.assertIn("History: off (no HISTORY_SHEET_ID)", out)
        self.assertEqual(self.sheets.books.keys(), {MAIN})            # history sheet never touched
        self.assertEqual({r[0] for r in self.sheets.books[MAIN]["Meta"].data},
                         {"Property", "Date range", "Days", "Last updated", "Built with"})
        for tab in ("Queries", "Pages", "Date Trend", "Device", "Country", "Query×Page"):
            self.assertIn(tab, self.sheets.books[MAIN])

    def test_empty_secret_counts_as_missing(self):
        out, err = self.run_main({**self.base, "HISTORY_SHEET_ID": ""}, self.gsc, self.sheets)
        self.assertIsNone(err)
        self.assertIn("History: off (no HISTORY_SHEET_ID)", out)

    def test_history_on_writes_meta_and_hides_ids_in_public_logs(self):
        env = {**self.base, "HISTORY_SHEET_ID": HID, "GITHUB_ACTIONS": "true"}
        out, err = self.run_main(env, self.gsc, self.sheets)
        self.assertIsNone(err)
        self.assertIn("History: on", out)
        for secret in (HID, MAIN, "example.test"):
            self.assertNotIn(secret, out)
        meta = self.meta()
        self.assertTrue(meta["History status"].startswith("up to date"))
        self.assertGreater(int(meta["History rows"]), 0)
        self.assertEqual(meta["History newest date"],
                         (date.today() - timedelta(days=3)).isoformat())
        # v1 tabs are still written
        self.assertGreater(len(self.sheets.books[MAIN]["Queries"].data), 1)

    def test_history_failure_still_writes_v1_tabs_then_fails(self):
        env = {**self.base, "HISTORY_SHEET_ID": "missing-book", "GITHUB_ACTIONS": "true"}
        # the history spreadsheet is not shared with the service account -> 403
        real_get = self.sheets.get

        def get(spreadsheetId, fields=None):
            if spreadsheetId == "missing-book":
                def boom():
                    raise http_error(403, "The caller does not have permission")
                return Req(boom)
            return real_get(spreadsheetId, fields)

        with mock.patch.object(self.sheets, "get", get):
            out, err = self.run_main(env, self.gsc, self.sheets)
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("403", out)
        self.assertNotIn("missing-book", out)
        self.assertEqual(self.meta()["History status"], "error (see run log)")
        self.assertGreater(len(self.sheets.books[MAIN]["Queries"].data), 1)


if __name__ == "__main__":
    unittest.main()
