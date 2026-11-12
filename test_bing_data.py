"""Bing data in the sheets: the Reports tabs, the dashboard columns, the monthly export of the AI report, and what happens
when Bing fails. Search answers, sites, pages and numbers are all invented; Sheets is an in-memory fake."""

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from unittest import mock

import aio_import
import bing
import bing_data
import content
import gsc_pulse
import sites
from test_content_run import make_site
from test_history import FakeSheets, Req, Tab, patch_time

TODAY = date(2026, 11, 12)
KEY = "k3y-SECRET-9876543210"
HOME = "https://example.test"
A, B, GONE = f"{HOME}/article/a", f"{HOME}/article/b", f"{HOME}/article/gone"
EPOCH = date(1970, 1, 1)


def ms(d: date) -> str:
    return f"/Date({(d - EPOCH).days * 86400000}-0700)/"


def daily(first: date, last: date, fn) -> list:
    out, d = [], first
    while d <= last:
        c, n = fn(d)
        out.append({"__type": "RankAndTrafficStats:#x", "Date": ms(d), "Clicks": c, "Impressions": n})
        d += timedelta(days=1)
    return out


def traffic_fn(d):
    return 1 + d.day % 3 + (2 if d.month == 10 else 0), 20 + d.day * 2


def qrow(day, key, clicks, impr, pos):
    return {"Query": key, "Date": ms(day), "Clicks": clicks, "Impressions": impr,
            "AvgClickPosition": 3, "AvgImpressionPosition": pos}


SEP7, SEP14, SEP21 = date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21)
OCT5, OCT12, OCT19, OCT26, NOV2 = date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26), date(2026, 11, 2)

QUERIES = [qrow(SEP7, "alpha", 2, 20, 4.0), qrow(SEP14, "alpha", 3, 30, 4.0),
           qrow(OCT5, "alpha", 4, 40, 6.0), qrow(OCT12, "alpha", 5, 50, 6.0), qrow(OCT26, "alpha", 1, 10, 6.0),
           qrow(SEP7, "beta", 5, 50, 5.0), qrow(SEP14, "beta", 4, 50, 5.0), qrow(OCT5, "beta", 4, 60, 5.0),
           qrow(SEP21, "gamma", 0, 30, 9.0), qrow(OCT19, "delta", 1, 25, 8.0), qrow(OCT19, "tiny", 0, 3, 8.0)]
NEWER = [qrow(NOV2, "alpha", 1, 10, 6.0)]                    # a bucket after the end of October has arrived
PAGE_ROWS = [qrow(SEP7, A, 2, 20, 4.0), qrow(OCT5, A, 6, 60, 4.0), qrow(date(2026, 6, 1), A, 90, 900, 4.0),
             qrow(SEP14, B, 6, 60, 5.0), qrow(OCT12, B, 1, 10, 5.0), qrow(SEP21, GONE, 3, 30, 7.0)]
CRAWL = [{"Date": ms(date(2026, 11, d)), "CrawledPages": 10 + d, "CrawlErrors": 1, "InIndex": 500, "InLinks": 900,
          "Code2xx": 9, "Code301": 2, "Code302": 0, "Code4xx": 1, "Code5xx": 0, "AllOtherCodes": 0, "BlockedByRobotsTxt": 0,
          "ContainsMalware": 0, "DnsFailures": 0, "ConnectionTimeout": 0} for d in (1, 2, 3)]
ISSUES = [{"Url": f"{HOME}/broken", "HttpCode": 404, "Issues": 4, "InLinks": 2}]


def answers(**over) -> dict:
    base = {"GetUserSites": [{"Url": "https://www.example.test/", "IsVerified": True},
                             {"Url": "https://other.test/", "IsVerified": True}],
            "GetRankAndTrafficStats": daily(date(2026, 5, 3), date(2026, 11, 6), traffic_fn),
            "GetQueryStats": QUERIES + NEWER, "GetPageStats": PAGE_ROWS,
            "GetCrawlStats": CRAWL, "GetCrawlIssues": ISSUES}
    base.update(over)
    return base


class FakeAccess(bing.Access):
    """Answers the methods from a dict; a BingError instance in the dict is raised. Records the calls."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def get(self, method, **params):
        self.calls.append(method)
        reply = self.replies[method]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FilterSheets(FakeSheets):
    """Gives back the basic filter of a tab as the real service does, keeps the one that was set, and inserts and moves
    columns the way Sheets does (cells move with their column; a filter that spans the place grows)."""

    def __init__(self):
        super().__init__()
        self.filters = {}                       # sheet id -> filter

    def _tab_of(self, book, sid):
        return next(t for t in self.books[book].values() if t.sheet_id == sid)

    def get(self, spreadsheetId, fields=None):
        inner = super().get(spreadsheetId, fields)

        def run():
            resp = inner.execute()
            for s in resp["sheets"]:
                if s["properties"]["sheetId"] in self.filters:
                    s["basicFilter"] = self.filters[s["properties"]["sheetId"]]
            return resp
        return Req(run)

    def batchUpdate(self, spreadsheetId, body):
        inner = super().batchUpdate(spreadsheetId, body)

        def run():
            out = inner.execute()
            for r in body["requests"]:
                if "setBasicFilter" in r:
                    f = r["setBasicFilter"]["filter"]
                    self.filters[f["range"]["sheetId"]] = f
                elif "insertDimension" in r and r["insertDimension"]["range"]["dimension"] == "COLUMNS":
                    rg = r["insertDimension"]["range"]
                    t, a, n = self._tab_of(spreadsheetId, rg["sheetId"]), rg["startIndex"], rg["endIndex"] - rg["startIndex"]
                    for row in t.data:
                        if len(row) > a:
                            row[a:a] = [""] * n
                    t.col_count += n
                    flt = self.filters.get(rg["sheetId"])
                    if flt and a < flt["range"]["endColumnIndex"]:
                        flt["range"]["endColumnIndex"] += n
                elif "moveDimension" in r and r["moveDimension"]["source"]["dimension"] == "COLUMNS":
                    src = r["moveDimension"]["source"]
                    t, a, b, dest = self._tab_of(spreadsheetId, src["sheetId"]), src["startIndex"], src["endIndex"], r["moveDimension"]["destinationIndex"]
                    for row in t.data:
                        row.extend([""] * (max(b, dest) - len(row)))
                        moved, rest = row[a:b], row[:a] + row[b:]
                        at = dest - (b - a) if dest > a else dest
                        row[:] = rest[:at] + moved + rest[at:]
            return out
        return Req(run)


class Base(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.fs.add_book("REP")
        self.site = make_site(sheet_id="DASH")
        self.logs = []

    def run_bing(self, replies=None, today=TODAY, site=None, drive=None):
        return bing_data.run_bing(self.fs, site or self.site, FakeAccess(replies or answers()), today, self.logs.append,
                                  drive=drive, sleep=lambda s: None)

    def tab(self, name, book="REP"):
        return self.fs.books[book][name].data

    def rows(self, name):
        return self.tab(name)[1:]

    def put_tab(self, title, rows, book="REP"):
        t = Tab(self.fs.next_id)
        self.fs.next_id += 1
        self.fs.books[book][title] = t
        self.fs.write_cells(t, 1, 1, rows)

    def snapshot(self, book="REP", skip=()):
        return {name: [list(r) for r in t.data] for name, t in self.fs.books[book].items() if name not in skip}


def naive(first: date, last: date) -> tuple:
    clicks = impr = 0
    d = first
    while d <= last:
        c, n = traffic_fn(d)
        clicks, impr, d = clicks + c, impr + n, d + timedelta(days=1)
    return clicks, impr


class PeriodTests(Base):

    def test_monthly_rows_are_complete_months_with_their_change(self):
        self.run_bing()
        rows = {r[0]: r for r in self.rows(bing_data.MONTHLY)}
        self.assertEqual(sorted(rows), ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])    # November is not complete
        c, n = naive(date(2026, 10, 1), date(2026, 10, 31))
        pc, pn = naive(date(2026, 9, 1), date(2026, 9, 30))
        r = rows["2026-10"]
        self.assertEqual(r[1:3], [c, n])
        self.assertEqual(r[3], round(c / n * 100, 2))
        self.assertEqual(r[5], c - pc)
        self.assertEqual(r[6], round((c - pc) / pc * 100, 1))
        self.assertEqual(r[7], n - pn)
        self.assertEqual(rows["2026-06"][5:], ["", "", "", ""])               # no month before it in the data

    def test_weekly_rows_run_monday_to_sunday(self):
        self.run_bing()
        rows = {r[0]: r for r in self.rows(bing_data.WEEKLY)}
        self.assertIn("2026-W44", rows)                                       # 26 Oct - 1 Nov
        self.assertNotIn("2026-W45", rows)                                    # 2 - 8 Nov is not complete
        self.assertEqual(rows["2026-W44"][1:3], list(naive(date(2026, 10, 26), date(2026, 11, 1))))

    def test_position_is_that_of_the_top_queries_weighted_by_impressions(self):
        self.run_bing()
        rows = {r[0]: r for r in self.rows(bing_data.MONTHLY)}
        self.assertEqual(rows["2026-10"][4], 6.0)          # (6*100 + 5*60 + 8*28) / 188 = 5.98
        self.assertEqual(rows["2026-06"][4], "")           # no query data that month: empty, not guessed

    def test_a_closed_period_is_written_once(self):
        self.run_bing()
        before = self.snapshot()
        self.run_bing()
        self.assertEqual(self.snapshot(), before)
        self.run_bing(answers(GetRankAndTrafficStats=daily(date(2026, 5, 3), date(2026, 11, 6), lambda d: (99, 999))))
        self.assertEqual(self.snapshot(), before)           # a different answer does not rewrite a closed month

    def test_no_traffic_gives_no_period_rows(self):
        result = self.run_bing(answers(GetRankAndTrafficStats=[]))
        self.assertEqual(self.rows(bing_data.MONTHLY), [])
        self.assertIsNone(result.last_month)

    def test_the_report_of_the_run_names_the_last_month(self):
        result = self.run_bing()
        c, n = naive(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(result.last_month, ("2026-10", c, n))
        self.assertEqual((result.queries, result.pages), (5, 3))


class KeywordPageTests(Base):

    def test_keywords_of_a_month_against_the_month_before(self):
        self.run_bing()
        rows = [r for r in self.rows(bing_data.KEYWORDS) if r[0] == "2026-10"]
        by = {(r[1], r[2]): r for r in rows}
        self.assertEqual(self.tab(bing_data.KEYWORDS)[0][0], bing_data.PERIOD_NOTE)
        self.assertEqual(by[("Growing", "alpha")][3:9], [10, 5, 5, 100, 50, 50])
        self.assertEqual(by[("Declining", "beta")][3:6], [4, 9, -5])
        self.assertIn(("Lost", "gamma"), by)
        self.assertIn(("New", "delta"), by)
        self.assertNotIn(("New", "tiny"), by)              # fewer impressions than the floor

    def test_pages_with_their_path_and_page_type(self):
        self.run_bing()
        by = {(r[1], r[2]): r for r in self.rows(bing_data.PAGES) if r[0] == "2026-10"}
        self.assertEqual(by[("Growing", A)][4:7], [6, 2, 4])
        self.assertEqual(by[("Declining", B)][4:7], [1, 6, -5])
        self.assertEqual(by[("Growing", A)][-1], "/article/a")

    def test_a_month_waits_until_its_last_week_has_arrived(self):
        self.run_bing(answers(GetQueryStats=QUERIES, GetPageStats=PAGE_ROWS))        # newest bucket 26 Oct, 12 days after
        self.assertEqual([r for r in self.rows(bing_data.KEYWORDS) if r[0] == "2026-10"], [])
        self.assertEqual([r for r in self.rows(bing_data.PAGES) if r[0] == "2026-10"], [])
        self.run_bing(answers(GetQueryStats=QUERIES, GetPageStats=PAGE_ROWS), today=TODAY + timedelta(days=3))
        self.assertTrue([r for r in self.rows(bing_data.KEYWORDS) if r[0] == "2026-10"])      # 14 days: final
        self.assertTrue([r for r in self.rows(bing_data.PAGES) if r[0] == "2026-10"])

    def test_keywords_and_pages_are_written_once_per_month(self):
        self.run_bing()
        before = (list(map(list, self.rows(bing_data.KEYWORDS))), list(map(list, self.rows(bing_data.PAGES))))
        self.run_bing()
        self.assertEqual((self.rows(bing_data.KEYWORDS), self.rows(bing_data.PAGES)), before)


class CrawlTests(Base):

    def test_crawl_days_and_issues_as_bing_gives_them(self):
        result = self.run_bing()
        rows = self.rows(bing_data.CRAWL)
        self.assertEqual([r[:2] for r in rows if r[0] == "Stats"],
                         [["Stats", "2026-11-01"], ["Stats", "2026-11-02"], ["Stats", "2026-11-03"]])
        self.assertEqual(rows[0][2:6], [11, 1, 500, 900])
        issue = [r for r in rows if r[0] == "Issue"]
        self.assertEqual(len(issue), 1)
        self.assertEqual((issue[0][1], issue[0][5], issue[0][16], issue[0][17], issue[0][18]),
                         (f"{HOME}/broken", 2, 404, "4", TODAY.isoformat()))
        self.assertEqual((result.crawl_days, result.crawl_issues), (3, 1))
        self.assertTrue(all(len(r) == len(bing_data.CRAWL_HEADER) for r in rows))

    def test_days_stay_and_the_issue_list_is_replaced(self):
        self.run_bing()
        self.run_bing(answers(GetCrawlStats=[CRAWL[2]] + [dict(CRAWL[0], Date=ms(date(2026, 11, 4)))], GetCrawlIssues=[]))
        rows = self.rows(bing_data.CRAWL)
        self.assertEqual([r[1] for r in rows if r[0] == "Stats"], ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"])
        self.assertEqual([r for r in rows if r[0] == "Issue"], [])

    def test_a_call_that_failed_leaves_what_is_there(self):
        self.run_bing()
        before = self.snapshot()[bing_data.CRAWL]
        result = self.run_bing(answers(GetCrawlStats=bing.BingError("GetCrawlStats: HTTP 500"),
                                       GetCrawlIssues=bing.BingError("GetCrawlIssues: HTTP 500")))
        self.assertEqual(self.tab(bing_data.CRAWL), before)
        self.assertEqual(len(result.problems), 2)


class DashboardTests(Base):

    def setUp(self):
        super().setUp()
        self.fs = FilterSheets()
        self.fs.add_book("REP")
        self.fs.add_book("DASH", {"Articles": (50, 42), "Landing Pages": (50, 42)})
        sid = self.fs.books["DASH"]["Articles"].sheet_id          # the owner has a filter on Articles, with a condition set
        self.fs.filters[sid] = {"range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": 42},
                                "criteria": {"2": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Published"}]}}}}
        header = content.HEADERS
        for tab in ("Articles", "Landing Pages"):
            self.fs.write_cells(self.fs.books["DASH"][tab], 1, 1, [header])
        self.add("Articles", {content.KEY: "owner kw", content.URL: A + "/", content.STATUS: "Published",
                              content.TODO: "Keep", "Notes/Checklist": "my note", "Clicks (90d)": 33})
        self.add("Articles", {content.KEY: "second", content.URL: B, content.STATUS: "Published"})
        self.add("Articles", {content.KEY: "not on bing", content.URL: "http://www.example.test/article/zzz"})
        self.add("Articles", {content.KEY: "a topic with no page"})
        self.add("Articles", {content.KEY: "old", content.URL: GONE, content.STATUS: "Removed"})

    def add(self, tab, cells):
        t = self.fs.books["DASH"][tab]
        t.data.append([cells.get(h, "") for h in content.HEADERS])
        t.row_count = max(t.row_count, len(t.data))

    def dash(self, tab="Articles"):
        data = self.fs.books["DASH"][tab].data
        return data[0], data[1:]

    def value(self, row, name):
        header, rows = self.dash()
        r = rows[row]
        c = header.index(name)
        return r[c] if c < len(r) else ""

    def by_name(self, tab="Articles"):
        header, rows = self.dash(tab)
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)} for r in rows]

    def test_the_columns_are_put_after_the_last_google_performance_column_and_filled_by_address(self):
        result = self.run_bing(drive=object())
        header, rows = self.dash()
        at = content.HEADERS.index("Ranking Queries") + 1
        self.assertEqual(header, content.HEADERS[:at] + [bing_data.COL_CLICKS, bing_data.COL_IMPR] + content.HEADERS[at:])
        self.assertEqual([self.value(i, bing_data.COL_CLICKS) for i in range(5)], [8, 7, 0, "", ""])
        self.assertEqual([self.value(i, bing_data.COL_IMPR) for i in range(5)], [80, 70, 0, "", ""])
        self.assertEqual(result.dashboard, 2)                                # both tabs have the columns ...
        self.assertEqual(self.dash("Landing Pages")[1], [])                  # ... and the empty one stays empty

    def test_the_owners_columns_are_not_touched_though_they_move_to_the_right(self):
        before = self.by_name()
        self.run_bing(drive=object())
        after = self.by_name()
        for old, new in zip(before, after):
            for name in content.HEADERS:
                self.assertEqual(new[name], old[name], name)
        self.assertEqual(after[0]["Clicks (90d)"], 33)                       # Google's number in Google's column stays
        self.assertEqual(after[0]["Secondary Keywords"], "")
        self.assertEqual((after[0][content.TODO], after[0]["Notes/Checklist"]), ("Keep", "my note"))

    def test_the_filter_keeps_its_conditions_and_the_grid_grows(self):
        self.run_bing(drive=object())
        sid = self.fs.books["DASH"]["Articles"].sheet_id
        flt = self.fs.filters[sid]
        self.assertEqual(flt["range"]["endColumnIndex"], 44)
        self.assertEqual(flt["criteria"]["2"]["condition"]["type"], "TEXT_EQ")       # what the owner set is kept
        self.assertEqual(self.fs.books["DASH"]["Articles"].col_count, 44)
        self.assertFalse([r for _, r in self.fs.requests if "setBasicFilter" in r])  # nothing was reset

    def test_columns_an_earlier_version_put_at_the_end_are_moved_into_place(self):
        t = self.fs.books["DASH"]["Articles"]
        t.data[0] += [bing_data.COL_CLICKS, bing_data.COL_IMPR]
        for r in t.data[1:]:
            r += ["old", "old"]
        t.col_count = 44
        self.fs.filters[t.sheet_id]["range"]["endColumnIndex"] = 44
        before = self.by_name()
        self.run_bing(drive=object())
        header, _ = self.dash()
        at = content.HEADERS.index("Ranking Queries") + 1
        self.assertEqual(header, content.HEADERS[:at] + [bing_data.COL_CLICKS, bing_data.COL_IMPR] + content.HEADERS[at:])
        self.assertEqual([self.value(i, bing_data.COL_CLICKS) for i in range(5)], [8, 7, 0, "old", "old"])      # a row with no page, and a settled row, keep what their cells held
        for old, new in zip(before, self.by_name()):
            for name in content.HEADERS:
                self.assertEqual(new[name], old[name], name)
        self.assertEqual(self.fs.books["DASH"]["Articles"].col_count, 44)
        self.assertEqual(self.fs.filters[t.sheet_id]["range"]["endColumnIndex"], 44)

    def test_a_tab_without_the_anchor_column_gets_them_at_the_end(self):
        t = self.fs.books["DASH"]["Articles"]
        t.data[0][content.HEADERS.index("Ranking Queries")] = "Ranking"
        self.run_bing(drive=object())
        header, _ = self.dash()
        self.assertEqual(header[42:], [bing_data.COL_CLICKS, bing_data.COL_IMPR])
        self.assertEqual(self.fs.filters[t.sheet_id]["range"]["endColumnIndex"], 44)
        self.assertEqual([self.value(i, bing_data.COL_CLICKS) for i in range(3)], [8, 7, 0])

    def test_a_second_run_adds_no_column_and_changes_nothing(self):
        self.run_bing(drive=object())
        before = self.snapshot("DASH")
        self.run_bing(drive=object())
        self.assertEqual(self.snapshot("DASH"), before)

    def test_no_page_from_bing_at_all_adds_nothing_to_the_dashboard(self):
        before = self.snapshot("DASH")
        result = self.run_bing(answers(GetPageStats=[]), drive=object())
        self.assertEqual(self.snapshot("DASH"), before)
        self.assertEqual(result.dashboard, 0)

    def test_pages_older_than_90_days_still_give_the_columns_with_zeros(self):
        """A site whose Bing data stopped long ago: Bing did answer, so the columns are there and say 0 for the last 90 days."""
        result = self.run_bing(answers(GetPageStats=[qrow(date(2026, 6, 1), A, 90, 900, 4.0)]), drive=object())
        self.assertEqual(result.dashboard, 2)
        header, _ = self.dash()
        self.assertIn(bing_data.COL_CLICKS, header)
        self.assertEqual([self.value(i, bing_data.COL_CLICKS) for i in range(5)], [0, 0, 0, "", ""])
        self.assertEqual([self.value(i, bing_data.COL_IMPR) for i in range(5)], [0, 0, 0, "", ""])

    def test_a_page_not_in_bing_gets_zero_but_only_when_bing_gave_pages(self):
        self.run_bing(drive=object())
        self.assertEqual(self.value(2, bing_data.COL_CLICKS), 0)

    def test_citations_of_the_newest_month_fill_the_third_column(self):
        self.put_tab("Bing AI 2026-09 Pages", [["Cited page", "Citations"], [A, 1]])
        self.put_tab("Bing AI 2026-10 Pages", [["Cited page", "Citations"], [A + "#section", 12], [B, 3]])
        self.run_bing(drive=object())
        header, _ = self.dash()
        at = content.HEADERS.index("Ranking Queries") + 1
        self.assertEqual(header[at:at + 3], [bing_data.COL_CLICKS, bing_data.COL_IMPR, bing_data.COL_AI])
        self.assertEqual([self.value(i, bing_data.COL_AI) for i in range(5)], [12, 3, 0, "", ""])

    def test_the_citations_column_added_later_goes_next_to_the_other_two(self):
        self.run_bing(drive=object())
        self.put_tab("Bing AI 2026-10 Pages", [["Cited page", "Citations"], [A, 12]])
        self.run_bing(drive=object())
        header, _ = self.dash()
        at = header.index(bing_data.COL_CLICKS)
        self.assertEqual(header[at:at + 3], [bing_data.COL_CLICKS, bing_data.COL_IMPR, bing_data.COL_AI])
        self.assertEqual(self.value(0, bing_data.COL_AI), 12)
        self.assertEqual(self.value(0, "Secondary Keywords"), "")

    def test_a_site_without_the_dashboard_or_own_access_is_left_alone(self):
        before = self.snapshot("DASH")
        self.run_bing()                                  # no drive: a site that runs with the service account
        self.assertEqual(self.snapshot("DASH"), before)


class CitationTests(Base):

    def test_a_copied_export_is_stored_and_its_tabs_are_removed(self):
        self.put_tab("Bing AI 2026-10 Pages", [["Cited pages", "Citations", "Date"], [A, 12, "2026-10-03"], [B + "#x", 3, "2026-10-04"]])
        self.put_tab("Bing AI 2026-10 Queries", [["Grounding query", "Citations"], ["how to  plant", 7], ["garden shoes", 4]])
        result = self.run_bing()
        rows = self.rows(bing_data.CITATIONS)
        self.assertEqual([r[:4] for r in rows],
                         [["2026-10", "Page", A, 12], ["2026-10", "Page", B, 3],
                          ["2026-10", "Query", "how to plant", 7], ["2026-10", "Query", "garden shoes", 4]])
        self.assertEqual(rows[0][6], "/article/a")
        self.assertNotIn("Bing AI 2026-10 Pages", self.fs.books["REP"])
        self.assertNotIn("Bing AI 2026-10 Queries", self.fs.books["REP"])
        self.assertEqual(result.ai_months, 1)

    def test_a_second_month_shows_the_change(self):
        self.put_tab("Bing AI 2026-09 Pages", [["URL", "Citations"], [A, 5]])
        self.run_bing()
        self.put_tab("Bing AI 2026-10 Pages", [["URL", "Citations"], [A, 12], [B, 3]])
        self.run_bing()
        by = {(r[0], r[2]): r for r in self.rows(bing_data.CITATIONS)}
        self.assertEqual(by[("2026-10", A)][3:6], [12, 5, 7])
        self.assertEqual(by[("2026-10", B)][3:6], [3, "", ""])
        self.assertEqual(by[("2026-09", A)][3:6], [5, "", ""])

    def test_the_same_month_again_replaces_it(self):
        self.put_tab("Bing AI 2026-10 Pages", [["URL", "Citations"], [A, 12]])
        self.run_bing()
        self.put_tab("Bing AI 2026-10 Pages", [["URL", "Citations"], [A, 20], [B, 1]])
        self.run_bing()
        self.assertEqual([(r[2], r[3]) for r in self.rows(bing_data.CITATIONS)], [(A, 20), (B, 1)])

    def test_no_export_is_no_problem(self):
        result = self.run_bing()
        self.assertEqual(self.rows(bing_data.CITATIONS), [])
        self.assertEqual((result.ai_months, result.ai_problems), (0, []))

    def test_a_tab_that_cannot_be_read_stays_and_the_others_go_on(self):
        self.put_tab("Bing AI 2026-10 Pages", [["URL", "Citations"], [A, 12]])
        self.put_tab("Bing AI 2026-10 Queries", [["Notes"], ["nothing here"]])
        self.put_tab("Bing AI October Pages", [["URL", "Citations"], [A, 1]])
        result = self.run_bing()
        self.assertIn("Bing AI 2026-10 Queries", self.fs.books["REP"])
        self.assertIn("Bing AI October Pages", self.fs.books["REP"])
        self.assertNotIn("Bing AI 2026-10 Pages", self.fs.books["REP"])
        self.assertEqual(len(result.ai_problems), 2)
        self.assertTrue(any("October" in line for line in self.logs))

    def test_the_import_runs_when_bing_is_down(self):
        self.put_tab("Bing AI 2026-10 Pages", [["URL", "Citations"], [A, 12]])
        down = bing.BingError("GetUserSites: HTTP 503")
        result = self.run_bing(answers(GetUserSites=down))
        self.assertEqual(self.rows(bing_data.CITATIONS)[0][:4], ["2026-10", "Page", A, 12])
        self.assertTrue(result.no_data)

    def test_aio_imports_are_not_touched(self):
        self.put_tab("AIO 2026-10 Pages", [["Page", "Impressions"], [A, 5]])
        self.run_bing()
        self.assertIn("AIO 2026-10 Pages", self.fs.books["REP"])


class ParseTests(unittest.TestCase):

    def parse(self, rows, kind="Pages"):
        return bing_data.parse_citations(rows, kind)

    def test_pages_by_header_or_by_what_the_values_look_like(self):
        self.assertEqual(self.parse([["Cited URL", "Citations"], ["https://e.test/a", "1,200"]])[0], {"https://e.test/a": 1200})
        self.assertEqual(self.parse([["https://e.test/a", 5], ["https://e.test/b#x", 2]])[0],
                         {"https://e.test/a": 5, "https://e.test/b": 2})
        self.assertEqual(self.parse([["Page", "Avg position", "Citations"], ["https://e.test/a", "3.2", 8]])[0],
                         {"https://e.test/a": 8})

    def test_queries_and_repeated_rows_add_up(self):
        got, skipped = self.parse([["Grounding queries", "Citations"], ["a b", 1], ["a  b", 2], ["", 4], ["c", "x"]], "Queries")
        self.assertEqual(got, {"a b": 3})
        self.assertEqual(skipped, 2)

    def test_what_cannot_be_told_is_said(self):
        with self.assertRaisesRegex(aio_import.AioImportError, "citations"):
            self.parse([["URL", "A", "B"], ["https://e.test/a", 1, 2]])
        with self.assertRaisesRegex(aio_import.AioImportError, "empty"):
            self.parse([])
        with self.assertRaisesRegex(aio_import.AioImportError, "cited pages"):
            self.parse([["Name", "Citations"], ["plain", 1]])


class SafetyTests(Base):

    def test_a_bing_error_changes_nothing_that_is_not_bing(self):
        self.fs.write_cells(self.fs.books["REP"]["Sheet1"], 1, 1, [["R Monthly sentinel", 1]])
        before = self.snapshot()
        every = answers(**{m: bing.BingError(f"{m}: HTTP 503") for m in
                           ("GetUserSites",)})
        result = self.run_bing(every)
        self.assertTrue(result.no_data)
        for name, rows in before.items():
            self.assertEqual(self.snapshot()[name], rows)
        self.assertEqual(self.tab(bing_data.WEEKLY), [bing_data.WEEKLY_HEADER])

    def test_one_failed_call_does_not_stop_the_others(self):
        result = self.run_bing(answers(GetQueryStats=bing.BingError("GetQueryStats: HTTP 429")))
        self.assertTrue(self.rows(bing_data.MONTHLY))
        self.assertTrue(self.rows(bing_data.PAGES))
        self.assertEqual(self.rows(bing_data.KEYWORDS), [])
        self.assertEqual(result.problems, ["queries: GetQueryStats: HTTP 429"])

    def test_a_site_not_in_the_account_is_said_and_nothing_is_written(self):
        site = make_site(sheet_id="DASH")
        site.site_url = "sc-domain:not-there.test"
        result = self.run_bing(site=site)
        self.assertTrue(result.no_data)
        self.assertTrue(any("not in the Bing account" in p for p in result.problems))

    def test_the_site_address_in_the_configuration_wins(self):
        site = make_site(sheet_id="DASH")
        site.bing.site_url = "https://other.test/"
        access = FakeAccess(answers())
        bing_data.run_bing(self.fs, site, access, TODAY, self.logs.append, sleep=lambda s: None)
        self.assertEqual(access.calls[:2], ["GetUserSites", "GetRankAndTrafficStats"])

    def test_the_key_is_never_in_a_log_or_a_cell(self):
        replies = answers()

        def opener(url):
            self.assertIn(KEY, url)                                        # it does travel in the address
            method = url.split("/json/")[1].split("?")[0]
            if method == "GetCrawlStats":
                return 500, b"boom " + KEY.encode()
            if method == "GetCrawlIssues":
                return 401, json.dumps({"Message": "InvalidApiKey " + KEY, "ErrorCode": 3}).encode()
            return 200, json.dumps({"d": replies[method]}).encode()

        access = bing.ApiKeyAccess(KEY, opener=opener, sleep=lambda s: None)
        out = io.StringIO()
        with redirect_stdout(out):
            result = bing_data.run_bing(self.fs, self.site, access, TODAY, print, sleep=lambda s: None)
        everything = out.getvalue() + "\n".join(self.logs) + json.dumps(result.problems, default=str) + result.text()
        for book in self.fs.books.values():
            for t in book.values():
                everything += json.dumps(t.data, default=str)
        self.assertNotIn(KEY, everything)
        self.assertNotIn("apikey", everything)
        self.assertEqual(len(result.problems), 2)
        self.assertIn("HTTP 500", " ".join(result.problems))

    def test_the_data_code_does_not_know_how_access_was_obtained(self):
        """A provider added later (OAuth) is one more entry in the registry: the same tabs come out of the same answers."""
        replies = answers()

        class FakeOAuth(bing.Access):
            def __init__(self, env):
                self.token = env["TOKEN"]                    # whatever the provider needs

            def get(self, method, **params):
                return replies[method]

        def via_key():
            fs = FakeSheets()
            fs.add_book("REP")
            access = bing.ApiKeyAccess(KEY, opener=lambda url: (200, json.dumps(
                {"d": replies[url.split("/json/")[1].split("?")[0]]}).encode()))
            bing_data.run_bing(fs, self.site, access, TODAY, lambda m: None, sleep=lambda s: None)
            return {n: t.data for n, t in fs.books["REP"].items() if n.startswith("Bing")}

        want = via_key()
        with mock.patch.dict(bing.PROVIDERS, {"oauth": FakeOAuth}):
            parsed = sites.parse_sites_json(json.dumps([{"name": "site-t", "site_url": "sc-domain:example.test",
                                                         "sheet_id": "S", "bing": {"auth": "oauth"}}]))
            self.assertEqual(parsed[0].bing.auth, "oauth")
            access = bing.access_for("oauth", {"TOKEN": "t"})
            bing_data.run_bing(self.fs, self.site, access, TODAY, lambda m: None, sleep=lambda s: None)
        got = {n: t.data for n, t in self.fs.books["REP"].items() if n.startswith("Bing")}
        self.assertEqual(got, want)
        self.assertTrue(want[bing_data.MONTHLY][1:])


class PhaseTests(Base):
    """gsc_pulse.sync_bing: whatever happens, the site is not failed and nothing raises."""

    def run_phase(self, make_access, sheets=None, site=None, accesses=None):
        run = gsc_pulse.SiteRun(site or self.site)
        run.sheets = sheets or self.fs
        run.drive = None
        out = io.StringIO()
        with redirect_stdout(out):
            gsc_pulse.sync_bing(run, {} if accesses is None else accesses, TODAY, make_access=make_access)
        return run, out.getvalue()

    def test_a_good_run_fills_the_tabs_and_says_the_last_month(self):
        run, out = self.run_phase(lambda auth, env: FakeAccess(answers()))
        self.assertEqual(run.status, "pending")
        self.assertTrue(run.bing_text.startswith("+5 months"))
        self.assertIn("bing: 2026-10:", out)
        self.assertTrue(self.rows(bing_data.MONTHLY))

    def test_no_key_in_the_run_is_off_not_an_error(self):
        env = {k: v for k, v in os.environ.items() if k != "BING_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            run, out = self.run_phase(None)
        self.assertEqual(run.status, "pending")
        self.assertTrue(run.bing_text.startswith("off ("))
        self.assertNotIn(bing_data.WEEKLY, self.fs.books["REP"])

    def test_a_sheets_failure_is_skipped_and_the_site_is_not_failed(self):
        class Broken:
            def spreadsheets(self):
                raise RuntimeError("sheets are down")
        run, out = self.run_phase(lambda auth, env: FakeAccess(answers()), sheets=Broken())
        self.assertEqual((run.status, run.bing_text), ("pending", "skipped"))
        self.assertIn("bing: skipped (", out)

    def test_bing_down_is_reported_and_the_site_is_not_failed(self):
        down = bing.BingError("GetUserSites: HTTP 503")
        run, out = self.run_phase(lambda auth, env: FakeAccess(answers(GetUserSites=down)))
        self.assertEqual(run.status, "pending")
        self.assertTrue(run.bing_text.startswith("no data from Bing yet"))

    def test_a_site_with_bing_switched_off_is_not_touched(self):
        site = make_site(sheet_id="DASH")
        site.bing.enabled = False
        before = self.snapshot()
        run, out = self.run_phase(lambda auth, env: self.fail("asked for access"), site=site)
        self.assertEqual((run.bing_text, out), ("off", ""))
        self.assertEqual(self.snapshot(), before)

    def test_one_access_serves_every_site_of_the_run(self):
        made = []
        accesses = {}
        for _ in range(2):
            self.run_phase(lambda auth, env: made.append(auth) or FakeAccess(answers()), accesses=accesses)
        self.assertEqual(made, ["api_key"])


class ConfigTests(unittest.TestCase):

    def parse(self, bing_block=None, **extra):
        entry = {"name": "site-t", "site_url": "sc-domain:example.test", "sheet_id": "S", **extra}
        if bing_block is not None:
            entry["bing"] = bing_block
        return sites.parse_sites_json(json.dumps([entry]))[0]

    def test_default_is_on_with_the_key(self):
        cfg = self.parse().bing
        self.assertEqual((cfg.enabled, cfg.auth, cfg.site_url), (True, "api_key", ""))

    def test_the_block_is_read(self):
        cfg = self.parse({"enabled": False, "auth": "api_key", "site_url": " https://www.example.test/ "}).bing
        self.assertEqual((cfg.enabled, cfg.site_url), (False, "https://www.example.test/"))

    def test_mistakes_are_refused_with_a_message_that_has_no_value_in_it(self):
        for block, text in (("yes", "must be a JSON object"), ({"enabled": "yes"}, "'enabled'"),
                            ({"auth": "magic"}, "'auth' must be one of"), ({"colour": 1}, "unknown field"),
                            ({"site_url": 5}, "'site_url'")):
            with self.assertRaisesRegex(sites.SitesConfigError, text):
                self.parse(block)

    def test_the_address_is_kept_out_of_public_logs(self):
        site = self.parse({"site_url": "https://www.example.test/"})
        self.assertIn("https://www.example.test/", site.sensitive_values())



from test_reports_main import A as SITE_A, B as SITE_B, ReportsMainTests     # noqa: E402  the whole run, on its in-memory fakes


class MainTests(unittest.TestCase):
    """gsc_pulse.main() with the Bing phase: it comes last of the Google phases, and whatever it does, they are unharmed."""

    setUp = ReportsMainTests.setUp
    run_main = ReportsMainTests.run_main

    def replies(self):
        today = date.today()
        return answers(GetUserSites=[{"Url": "https://www.alpha.test/", "IsVerified": True}],
                       GetRankAndTrafficStats=daily(today - timedelta(days=200), today - timedelta(days=4), traffic_fn))

    def with_bing(self, factory, env=None):
        with mock.patch.object(bing, "PROVIDERS", {"api_key": factory}), \
             mock.patch.object(bing_data, "CALL_PAUSE_S", 0):
            return self.run_main([SITE_A, SITE_B], {"BING_API_KEY": KEY, **(env or {})})

    def google(self):
        return {book: {n: [list(r) for r in t.data] for n, t in tabs.items() if not n.startswith("Bing") and n not in ("Meta", "State")}
                for book, tabs in self.sheets.books.items()}

    def fresh(self):
        ReportsMainTests.setUp(self)

    def test_the_bing_tabs_come_after_history_and_the_google_tabs_are_the_same_as_without_bing(self):
        out, err = self.with_bing(lambda env: FakeAccess(self.replies()))
        self.assertIsNone(err)
        self.assertLess(out.index("site-a · History"), out.index("site-a · Bing"))
        self.assertLess(out.index("site-a · Bing"), out.index("Summary"))
        book = self.sheets.books["A-REPORTS-ID"]
        self.assertGreater(len(book[bing_data.MONTHLY].data), 1)
        self.assertIn("bing: +", out[out.index("Summary"):])
        with_bing = self.google()
        self.fresh()
        out2, err2 = self.run_main([SITE_A, SITE_B], {})                      # the same run without the key
        self.assertIsNone(err2)
        self.assertNotIn("bing:", out2[out2.index("Summary"):])
        self.assertEqual(self.google(), with_bing)

    def test_bing_down_changes_nothing_in_the_google_phases(self):
        down = {m: bing.BingError(f"{m}: HTTP 503") for m in answers()}
        out, err = self.with_bing(lambda env: FakeAccess(dict(self.replies(), **down)))
        self.assertIsNone(err)
        self.assertIn("bing: no data from Bing yet", out)
        self.assertIn("Done", out)
        mine = self.google()
        self.fresh()
        self.run_main([SITE_A, SITE_B], {})
        self.assertEqual(self.google(), mine)

    def test_a_crash_in_the_bing_phase_is_skipped(self):
        with mock.patch.object(bing_data, "run_bing", side_effect=RuntimeError("something broke")):
            out, err = self.with_bing(lambda env: FakeAccess(self.replies()))
        self.assertIsNone(err)
        self.assertIn("bing: skipped (", out)
        self.assertIn("site-a: ok", out)
        self.assertGreater(len(self.sheets.books["A-REPORTS-ID"][reports_name()].data), 1)

    def test_the_key_is_not_in_the_log_even_when_bing_answers_with_it(self):
        replies = self.replies()

        def opener(url):
            method = url.split("/json/")[1].split("?")[0]
            if method == "GetQueryStats":
                return 403, json.dumps({"Message": "no access for " + KEY, "ErrorCode": 5}).encode()
            return 200, json.dumps({"d": replies[method]}).encode()

        out, err = self.with_bing(lambda env: bing.ApiKeyAccess(env["BING_API_KEY"], opener=opener, sleep=lambda s: None))
        self.assertIsNone(err)
        self.assertNotIn(KEY, out)
        self.assertIn("queries could not be read (GetQueryStats: HTTP 403", out)

    def test_a_site_without_a_reports_sheet_is_off(self):
        out, err = self.with_bing(lambda env: FakeAccess(self.replies()))
        self.assertIsNone(err)
        self.assertNotIn("site-b · Bing", out)               # no reports sheet: nothing for Bing to write to


def reports_name() -> str:
    import reports
    return reports.MONTHLY


if __name__ == "__main__":
    unittest.main()
