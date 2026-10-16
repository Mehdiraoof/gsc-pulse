"""
Tests for the report engine (reports.py), the alarms (alarms.py) and the AIO import
(aio_import.py), against an in-memory Search Console and an in-memory Sheets.
"""

import functools
import re
import unittest
from datetime import date, timedelta
from unittest import mock

import aio_import
import alarms
import gsc_pulse
import history
import periods
import reports
import sites
from test_history import FakeSheets, Req, patch_time

RID   = "reports-sheet-id"
TODAY = date(2026, 10, 16)
FINAL = date(2026, 10, 14)
START = TODAY - timedelta(days=history.BACKFILL_DAYS)       # 2025-06-17

PAGES = {
    "en_a": "https://www.alpha.test/en/article/a",
    "en_b": "https://www.alpha.test/en/guide/b",
    "fa_a": "https://www.alpha.test/fa/article/c",
    "shop": "https://shop.alpha.test/p/1",
    "home": "https://www.alpha.test/",
}
TYPES = [{"name": "English", "regex": "/en/"}, {"name": "Article", "regex": "/article/"}]


def build_records(start=START, end=date(2026, 11, 30)):
    """Deterministic traffic: five pages with two queries each, a seasonal query that
    exists only in July and August, and #fragment variants of one page."""
    records, d = [], start
    while d <= end:
        for i, url in enumerate(PAGES.values()):
            for q in (0, 1):
                clicks = (i + 1) * (q + 1) * (1 + (d.month + i + d.year) % 3)
                records.append({"date": d, "query": f"{list(PAGES)[i]} q{q}", "page": url,
                                "clicks": clicks, "impressions": clicks * 20 + 5,
                                "position": 3.0 + i * 0.5 + q * 0.25})
        if d.month in (7, 8):
            records.append({"date": d, "query": "seasonal", "page": PAGES["en_a"],
                            "clicks": 12, "impressions": 300, "position": 2.0})
        records.append({"date": d, "query": "en_a q0", "page": PAGES["en_a"] + "#frag",
                        "clicks": 1, "impressions": 10, "position": 4.0})
        d += timedelta(days=1)
    for r in records:                       # the v1 tabs also ask for these dimensions
        r.setdefault("device", "DESKTOP")
        r.setdefault("country", "usa")
    return records


class FakeSA:
    """Just enough of the Search Analytics API: date range, dimensions, page filters,
    impression-weighted position, ordering and pagination."""

    def __init__(self, records):
        self.records, self.bodies = records, []

    def searchanalytics(self):
        return self

    def query(self, siteUrl, body):
        self.bodies.append(body)
        return Req(lambda: self._answer(body))

    def _passes(self, r, groups):
        for g in groups:
            for f in g["filters"]:
                hit = re.search(f["expression"], r[f["dimension"]]) is not None
                if (f["operator"] == "includingRegex") != hit:
                    return False
        return True

    def _answer(self, body):
        lo, hi = date.fromisoformat(body["startDate"]), date.fromisoformat(body["endDate"])
        groups = body.get("dimensionFilterGroups") or []
        agg = {}
        for r in self.records:
            if lo <= r["date"] <= hi and self._passes(r, groups):
                key = tuple(r["date"].isoformat() if d == "date" else r[d] for d in body["dimensions"])
                a = agg.setdefault(key, [0, 0, 0.0])
                a[0] += r["clicks"]
                a[1] += r["impressions"]
                a[2] += r["position"] * r["impressions"]
        rows = [{"keys": list(k), "clicks": v[0], "impressions": v[1],
                 "ctr": v[0] / v[1], "position": v[2] / v[1]} for k, v in agg.items()]
        rows.sort(key=lambda r: (-r["clicks"], r["keys"]))
        page = rows[body["startRow"]: body["startRow"] + body["rowLimit"]]
        return {"rows": page} if page else {}


def naive(records, first, last, predicate=lambda r: True):
    """Reference numbers computed directly from the records."""
    rows = [r for r in records if first <= r["date"] <= last and predicate(r)]
    clicks = sum(r["clicks"] for r in rows)
    impr = sum(r["impressions"] for r in rows)
    pos = sum(r["position"] * r["impressions"] for r in rows) / impr if impr else None
    return clicks, impr, pos


def month_span(label):
    first = periods.parse_month(label)
    return first, periods.month_end(first)


def site_with(**kw):
    base = dict(name="site-t", site_url="sc-domain:alpha.test", sheet_id="main-id",
                reports_sheet_id=RID)
    base.update(kw)
    return sites.Site(**base)


class ReportsTestCase(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.records = build_records()
        self.sa = FakeSA(self.records)
        self.sheets = FakeSheets()
        self.sheets.add_book(RID)
        self.logs = []

    def fetch(self, dimensions, first, last, groups=None):
        rows = []
        for page in gsc_pulse.iter_gsc_pages(self.sa, "site", first.isoformat(), last.isoformat(),
                                             dimensions, 25000, filter_groups=groups):
            rows.extend(page)
        return rows

    def run_reports(self, site=None, final=FINAL, today=TODAY):
        return reports.run_reports(self.sheets, RID, site or site_with(), self.fetch,
                                   final, today, self.logs.append)

    def tab(self, name):
        return self.sheets.books[RID][name].data

    def rows(self, name):
        return self.tab(name)[1:]

    def monthly(self, label, segment="All"):
        for r in self.rows(reports.MONTHLY):
            if r[0] == label and r[1] == segment:
                return dict(zip(reports.MONTHLY_HEADER, r + [""] * (17 - len(r))))
        self.fail(f"no R Monthly row for {label} / {segment}")

    def weekly(self, label, segment="All"):
        for r in self.rows(reports.WEEKLY):
            if r[0] == label and r[1] == segment:
                return dict(zip(reports.WEEKLY_HEADER, r + [""] * (17 - len(r))))
        self.fail(f"no R Weekly row for {label} / {segment}")


class MonthlyTests(ReportsTestCase):

    def test_tabs_exist_with_the_agreed_columns(self):
        self.run_reports()
        for name, header in ((reports.MONTHLY, reports.MONTHLY_HEADER),
                             (reports.WEEKLY, reports.WEEKLY_HEADER),
                             (reports.KEYWORDS, reports.KEYWORDS_HEADER),
                             (reports.PAGES, reports.PAGES_HEADER),
                             (reports.AIO_PAGES, reports.AIO_PAGES_HEADER),
                             (reports.ALARMS, ["Period", "Severity", "Area", "Message"]),
                             (reports.NOTES, ["Topic", "Explanation"]),
                             (aio_import.RAW_TAB, ["Period", "Kind", "Date or Page", "AIO Impressions"])):
            self.assertEqual(self.tab(name)[0], header, name)
        self.assertEqual(reports.MONTHLY_HEADER[:7], ["Period", "Segment", "Clicks", "Impressions",
                                                       "CTR (%)", "Avg. Position", "AIO Impressions"])
        self.assertEqual(reports.MONTHLY_HEADER[7:],
                         ["Clicks MoM", "Clicks MoM %", "Impr MoM", "Impr MoM %", "AIO MoM",
                          "AIO MoM %", "Clicks YoY", "Clicks YoY %", "Impr YoY", "Impr YoY %"])
        self.assertEqual(reports.WEEKLY_HEADER[7], "Clicks WoW")

    def test_all_months_match_the_raw_numbers(self):
        self.run_reports()
        labels = [r[0] for r in self.rows(reports.MONTHLY)]
        self.assertEqual(labels[0], "2025-07")
        self.assertEqual(labels[-1], "2026-09")
        self.assertEqual(len(labels), 15)
        for label in labels:
            clicks, impr, pos = naive(self.records, *month_span(label))
            row = self.monthly(label)
            self.assertEqual((row["Clicks"], row["Impressions"]), (clicks, impr), label)
            self.assertEqual(row["CTR (%)"], round(clicks / impr * 100, 2))
            self.assertEqual(row["Avg. Position"], round(pos, 1))

    def test_only_complete_months_are_reported(self):
        self.run_reports()
        self.assertNotIn("2026-10", {r[0] for r in self.rows(reports.MONTHLY)})

    def test_month_on_month_and_year_on_year(self):
        self.run_reports()
        cur = naive(self.records, *month_span("2026-09"))
        prev = naive(self.records, *month_span("2026-08"))
        year = naive(self.records, *month_span("2025-09"))
        row = self.monthly("2026-09")
        self.assertEqual(row["Clicks MoM"], cur[0] - prev[0])
        self.assertEqual(row["Clicks MoM %"], round((cur[0] - prev[0]) / prev[0] * 100, 1))
        self.assertEqual(row["Impr MoM"], cur[1] - prev[1])
        self.assertEqual(row["Clicks YoY"], cur[0] - year[0])
        self.assertEqual(row["Clicks YoY %"], round((cur[0] - year[0]) / year[0] * 100, 1))
        self.assertEqual(row["Impr YoY"], cur[1] - year[1])

    def test_missing_prior_periods_leave_the_changes_empty(self):
        self.run_reports()
        first = self.monthly("2025-07")
        # June 2025 is only partly available and July 2024 is gone: no comparison possible
        for col in ("Clicks MoM", "Clicks MoM %", "Impr MoM", "Clicks YoY", "Clicks YoY %", "Impr YoY"):
            self.assertEqual(first[col], "", col)
        dec = self.monthly("2025-12")
        self.assertEqual(dec["Clicks YoY"], "")                 # Dec 2024: older than 16 months
        self.assertNotEqual(dec["Clicks MoM"], "")

    def test_percent_is_empty_when_the_base_is_zero(self):
        self.assertEqual(reports._diff(5, 0), (5, ""))
        self.assertEqual(reports._diff(0, 0), (0, ""))
        self.assertEqual(reports._diff(5, None), ("", ""))
        self.assertEqual(reports._diff(None, 5), ("", ""))
        self.assertEqual(reports._diff(75, 100), (-25, -25.0))

    def test_aio_columns_are_empty_without_an_import(self):
        self.run_reports()
        row = self.monthly("2026-09")
        self.assertEqual((row["AIO Impressions"], row["AIO MoM"], row["AIO MoM %"]), ("", "", ""))

    def test_young_site_starts_at_its_first_whole_month(self):
        self.records[:] = build_records(start=date(2026, 3, 12))
        self.sa.records = self.records
        self.run_reports()
        labels = [r[0] for r in self.rows(reports.MONTHLY)]
        self.assertEqual(labels[0], "2026-04")
        self.assertEqual(self.monthly("2026-04")["Clicks MoM"], "")     # March was partial

    def test_no_data_at_all(self):
        self.sa.records = []
        result = self.run_reports()
        self.assertEqual(self.rows(reports.MONTHLY), [])
        self.assertEqual(result.note, "no data yet")


class SegmentTests(ReportsTestCase):

    def test_only_all_without_page_types(self):
        self.run_reports(site_with())
        self.assertEqual({r[1] for r in self.rows(reports.MONTHLY)}, {"All"})
        self.assertTrue(all("dimensionFilterGroups" not in b for b in self.sa.bodies))
        # no Other segment, no "new URL pattern" alarm for a site without page types
        self.assertFalse(any(r[2] == "New URL pattern" for r in self.rows(reports.ALARMS)))
        self.assertEqual({r[3] for r in self.rows(reports.PAGES)} - {""}, set())

    def test_segments_use_page_filters_and_may_overlap(self):
        self.run_reports(site_with(page_types=TYPES))
        self.assertEqual({r[1] for r in self.rows(reports.MONTHLY)},
                         {"All", "English", "Article", "Other"})
        label = "2026-09"
        first, last = month_span(label)
        inc = lambda rx: (lambda r: re.search(rx, r["page"]) is not None)                 # noqa: E731
        none = lambda r: not re.search("/en/", r["page"]) and not re.search("/article/", r["page"])  # noqa: E731
        for seg, pred in (("English", inc("/en/")), ("Article", inc("/article/")), ("Other", none)):
            self.assertEqual(self.monthly(label, seg)["Clicks"], naive(self.records, first, last, pred)[0], seg)
        # overlap: an English article counts in both, so the segments add up to more than All
        parts = sum(self.monthly(label, s)["Clicks"] for s in ("English", "Article", "Other"))
        self.assertGreater(parts, self.monthly(label)["Clicks"])

    def test_filter_groups_sent_to_search_console(self):
        self.run_reports(site_with(page_types=TYPES))
        groups = [b["dimensionFilterGroups"] for b in self.sa.bodies if "dimensionFilterGroups" in b]
        including = [g[0]["filters"][0]["expression"] for g in groups
                     if g[0]["filters"][0]["operator"] == "includingRegex"]
        self.assertEqual(sorted(set(including)), ["/article/", "/en/"])
        other = [g for g in groups if g[0]["filters"][0]["operator"] == "excludingRegex"]
        self.assertTrue(other)
        for g in other:                                    # one group, one excludingRegex per rule, ANDed
            self.assertEqual(len(g), 1)
            self.assertEqual(g[0]["groupType"], "and")
            self.assertEqual([f["expression"] for f in g[0]["filters"]], ["/en/", "/article/"])
            self.assertTrue(all(f["dimension"] == "page" and f["operator"] == "excludingRegex"
                                for f in g[0]["filters"]))

    def test_all_totals_come_from_a_date_only_query(self):
        self.run_reports(site_with(page_types=TYPES))
        all_queries = [b for b in self.sa.bodies
                       if b["dimensions"] == ["date"] and "dimensionFilterGroups" not in b]
        self.assertTrue(all_queries)

    def test_weekly_is_all_only_unless_asked(self):
        self.run_reports(site_with(page_types=TYPES))
        self.assertEqual({r[1] for r in self.rows(reports.WEEKLY)}, {"All"})

    def test_weekly_segments_flag(self):
        self.run_reports(site_with(page_types=TYPES, weekly_segments=True))
        self.assertEqual({r[1] for r in self.rows(reports.WEEKLY)}, {"All", "English", "Article", "Other"})

    def test_new_url_pattern_alarm_for_unmatched_pages(self):
        extra = [{"date": d, "query": f"s{n} q", "page": f"https://www.alpha.test/news/item{n}",
                  "clicks": 1, "impressions": 10, "position": 5.0}
                 for n in range(6) for d in (date(2026, 9, 5), date(2026, 9, 6))]
        self.records.extend(extra)
        self.run_reports(site_with(page_types=TYPES))
        messages = [r[3] for r in self.rows(reports.ALARMS) if r[2] == "New URL pattern"]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("6 pages", messages[0])
        self.assertIn("/news/", messages[0])
        self.assertEqual([r[0] for r in self.rows(reports.ALARMS) if r[2] == "New URL pattern"], ["2026-09"])


class WeeklyTests(ReportsTestCase):

    def test_weeks_are_monday_to_sunday_and_complete(self):
        self.run_reports()
        labels = [r[0] for r in self.rows(reports.WEEKLY)]
        self.assertEqual(labels[0], "2025-W26")                          # first Monday 2025-06-23
        self.assertEqual(labels[-1], "2026-W41")                         # ends Sunday 2026-10-11
        self.assertEqual(len(labels), len(set(labels)))
        week = self.weekly("2026-W38")
        monday = date(2026, 9, 14)
        clicks, impr, _ = naive(self.records, monday, monday + timedelta(days=6))
        self.assertEqual((week["Clicks"], week["Impressions"]), (clicks, impr))

    def test_week_on_week_and_year_on_year(self):
        self.run_reports()
        cur = naive(self.records, date(2026, 9, 14), date(2026, 9, 20))
        prev = naive(self.records, date(2026, 9, 7), date(2026, 9, 13))
        year = naive(self.records, date(2025, 9, 15), date(2025, 9, 21))
        week = self.weekly("2026-W38")
        self.assertEqual(week["Clicks WoW"], cur[0] - prev[0])
        self.assertEqual(week["Clicks YoY"], cur[0] - year[0])
        self.assertEqual(week["Clicks YoY %"], round((cur[0] - year[0]) / year[0] * 100, 1))
        self.assertEqual(self.weekly("2025-W30")["Clicks YoY"], "")      # no data a year earlier


class AppendOnlyTests(ReportsTestCase):

    def test_second_run_writes_nothing(self):
        self.run_reports(site_with(page_types=TYPES))
        snapshot = {n: [list(r) for r in t.data] for n, t in self.sheets.books[RID].items()
                    if n != reports.NOTES}
        appends = self.sheets.append_calls
        result = self.run_reports(site_with(page_types=TYPES))
        self.assertEqual(self.sheets.append_calls, appends)
        self.assertEqual((result.months, result.weeks, result.keyword_periods, result.page_periods,
                          result.alarms), (0, 0, 0, 0, 0))
        for name, data in snapshot.items():
            self.assertEqual(self.sheets.books[RID][name].data, data, name)

    def test_closed_periods_are_never_rewritten(self):
        self.run_reports()
        before = [list(r) for r in self.rows(reports.MONTHLY)]
        for r in self.records:                                    # the past changes afterwards
            if r["date"] < date(2026, 9, 1):
                r["clicks"] += 7
        self.run_reports()
        self.assertEqual(self.rows(reports.MONTHLY), before)

    def test_next_month_is_appended_once_and_older_rows_stay(self):
        self.run_reports()
        before = [list(r) for r in self.rows(reports.MONTHLY)]
        self.run_reports(final=date(2026, 10, 31), today=date(2026, 11, 5))
        rows = self.rows(reports.MONTHLY)
        self.assertEqual(rows[:len(before)], before)
        self.assertEqual([r[0] for r in rows[len(before):]], ["2026-10"])
        # and the keywords / pages of the new month were added next to the old ones
        self.assertIn("2026-10", {r[0] for r in self.rows(reports.KEYWORDS)})
        self.assertIn("2026-10", {r[0] for r in self.rows(reports.PAGES)})
        self.run_reports(final=date(2026, 10, 31), today=date(2026, 11, 5))
        self.assertEqual(len(self.rows(reports.MONTHLY)), len(before) + 1)

    def test_a_new_page_type_is_backfilled_without_touching_old_rows(self):
        self.run_reports()
        before = [list(r) for r in self.rows(reports.MONTHLY)]
        self.run_reports(site_with(page_types=TYPES))
        rows = self.rows(reports.MONTHLY)
        self.assertEqual(rows[:len(before)], before)
        self.assertEqual({r[1] for r in rows[len(before):]}, {"English", "Article", "Other"})
        self.assertEqual(len(rows) - len(before), 3 * 15)


class KeywordTests(unittest.TestCase):
    TH = {"lost_new_min_impressions": 10, "movers_top_n": 100}

    def rows(self, cur, prev, **th):
        return reports.keyword_rows("2026-09", cur, prev, {**self.TH, **th})

    @staticmethod
    def of(rows, kind):
        return [r for r in rows if r[1] == kind]

    def test_lost_needs_enough_impressions_and_absence_or_zero(self):
        prev = {"a": (5, 10, 3.0), "b": (5, 9, 3.0), "c": (5, 50, 2.0), "d": (1, 30, 2.0)}
        cur = {"c": (0, 0, 0.0), "d": (1, 20, 2.5)}
        rows, lost = self.rows(cur, prev)
        self.assertEqual([r[2] for r in self.of(rows, "Lost")], ["c", "a"])      # b: 9 < 10; d still there
        self.assertEqual(lost, [("c", 5), ("a", 5)])
        a = self.of(rows, "Lost")[1]
        self.assertEqual(a[3:], [0, 5, -5, 0, 10, -10, "", 3.0, ""])

    def test_new_needs_enough_impressions_and_no_previous_data(self):
        cur = {"a": (3, 10, 4.0), "b": (3, 9, 4.0), "c": (4, 40, 1.5), "d": (1, 15, 2.0)}
        prev = {"c": (0, 0, 0.0), "d": (1, 15, 2.0)}
        rows, _ = self.rows(cur, prev)
        self.assertEqual([r[2] for r in self.of(rows, "New")], ["c", "a"])       # by impressions
        self.assertEqual(self.of(rows, "New")[1][3:], [3, 0, 3, 10, 0, 10, 4.0, "", ""])

    def test_threshold_is_configurable(self):
        cur, prev = {"b": (3, 9, 4.0)}, {}
        self.assertEqual(self.of(self.rows(cur, prev)[0], "New"), [])
        self.assertEqual(len(self.of(self.rows(cur, prev, lost_new_min_impressions=9)[0], "New")), 1)

    def test_growing_and_declining_order_and_ties(self):
        prev = {"w": (10, 100, 3.0), "x": (10, 100, 3.0), "y": (10, 100, 3.0), "z": (50, 500, 3.0),
                "u": (20, 200, 3.0), "v": (20, 200, 3.0)}
        cur = {"w": (15, 130, 2.0), "x": (15, 150, 2.0), "y": (13, 100, 3.0), "z": (30, 400, 4.0),
               "u": (10, 150, 3.0), "v": (10, 100, 3.0)}
        rows, _ = self.rows(cur, prev)
        # +5/+30 and +5/+50 tie on clicks: more impressions first; then +3
        self.assertEqual([r[2] for r in self.of(rows, "Growing")], ["x", "w", "y"])
        # -20 (z), then -10 with the bigger impression loss first (v: -100, u: -50)
        self.assertEqual([r[2] for r in self.of(rows, "Declining")], ["z", "v", "u"])
        z = self.of(rows, "Declining")[0]
        self.assertEqual(z[3:], [30, 50, -20, 400, 500, -100, 4.0, 3.0, 1.0])

    def test_movers_are_cut_at_top_n_per_list(self):
        prev = {f"k{i}": (1, 10, 3.0) for i in range(10)}
        cur = {f"k{i}": (1 + i, 10, 3.0) for i in range(10)}
        rows, _ = self.rows(cur, prev, movers_top_n=3)
        self.assertEqual([r[2] for r in self.of(rows, "Growing")], ["k9", "k8", "k7"])

    def test_ties_break_alphabetically_so_output_is_stable(self):
        prev = {"b": (1, 10, 3.0), "a": (1, 10, 3.0)}
        cur = {"b": (2, 10, 3.0), "a": (2, 10, 3.0)}
        rows, _ = self.rows(cur, prev)
        self.assertEqual([r[2] for r in self.of(rows, "Growing")], ["a", "b"])


class PageTests(unittest.TestCase):
    TH = {"movers_top_n": 100}
    SEGMENTS = reports.build_segments(TYPES)

    def test_yoy_types_only_when_last_year_is_known(self):
        cur = {"https://x.test/en/a": (10, 100, 3.0)}
        prev = {"https://x.test/en/a": (5, 100, 3.0)}
        rows = reports.page_rows("2026-09", cur, prev, None, self.SEGMENTS, self.TH)
        self.assertEqual({r[1] for r in rows}, {"Growing"})
        yoy = {"https://x.test/en/a": (20, 100, 3.0)}
        rows = reports.page_rows("2026-09", cur, prev, yoy, self.SEGMENTS, self.TH)
        self.assertEqual({r[1] for r in rows}, {"Growing", "YoY Declining"})

    def test_page_types_column_lists_every_matching_segment(self):
        rows = reports.page_rows("2026-09", {"https://x.test/en/article/a": (9, 90, 3.0),
                                             "https://x.test/other": (8, 80, 3.0)},
                                 {}, None, self.SEGMENTS, self.TH)
        by_page = {r[2]: r[3] for r in rows}
        self.assertEqual(by_page["https://x.test/en/article/a"], "English, Article")
        self.assertEqual(by_page["https://x.test/other"], "Other")

    def test_no_page_types_means_empty_column(self):
        rows = reports.page_rows("2026-09", {"https://x.test/a": (9, 90, 3.0)}, {}, None,
                                 reports.build_segments([]), self.TH)
        self.assertEqual(rows[0][3], "")

    def test_fragment_variants_are_added_up(self):
        merged = reports.by_page([
            {"keys": ["https://x.test/a"], "clicks": 4, "impressions": 100, "position": 2.0},
            {"keys": ["https://x.test/a#i"], "clicks": 1, "impressions": 100, "position": 4.0}])
        self.assertEqual(merged, {"https://x.test/a": (5, 200, 3.0)})


class KeywordPageEngineTests(ReportsTestCase):

    def test_lost_and_new_seasonal_keyword(self):
        self.run_reports()
        sep = [r for r in self.rows(reports.KEYWORDS) if r[0] == "2026-09"]
        self.assertIn("seasonal", [r[2] for r in sep if r[1] == "Lost"])
        jul = [r for r in self.rows(reports.KEYWORDS) if r[0] == "2026-07"]
        self.assertIn("seasonal", [r[2] for r in jul if r[1] == "New"])
        lost = [r for r in sep if r[1] == "Lost" and r[2] == "seasonal"][0]
        self.assertEqual(lost[3:6], [0, 12 * 31, -12 * 31])                    # August has 31 days

    def test_first_month_has_no_keyword_or_page_rows(self):
        self.run_reports()
        self.assertNotIn("2025-07", {r[0] for r in self.rows(reports.KEYWORDS)})
        self.assertNotIn("2025-07", {r[0] for r in self.rows(reports.PAGES)})
        self.assertIn("2025-08", {r[0] for r in self.rows(reports.KEYWORDS)})

    def test_year_on_year_page_rows_exist_only_where_last_year_does(self):
        self.run_reports()
        types = lambda p: {r[1] for r in self.rows(reports.PAGES) if r[0] == p}      # noqa: E731
        self.assertTrue({"YoY Growing", "YoY Declining"} & types("2026-09"))
        self.assertFalse({"YoY Growing", "YoY Declining"} & types("2025-12"))
        self.assertTrue({"Growing", "Declining"} & types("2025-12"))

    def test_pages_have_no_fragments_and_include_their_clicks(self):
        self.run_reports()
        pages = [r for r in self.rows(reports.PAGES) if r[0] == "2026-09"]
        self.assertFalse(any("#" in r[2] for r in pages))
        first, last = month_span("2026-09")
        a = naive(self.records, first, last, lambda r: r["page"].split("#")[0] == PAGES["en_a"])[0]
        prev_first, prev_last = month_span("2026-08")
        b = naive(self.records, prev_first, prev_last, lambda r: r["page"].split("#")[0] == PAGES["en_a"])[0]
        row = [r for r in pages if r[2] == PAGES["en_a"] and r[1] in ("Growing", "Declining")][0]
        self.assertEqual((row[4], row[5]), (a, b))

    def test_rows_are_capped_per_list(self):
        site = site_with(thresholds={"movers_top_n": 2})
        self.run_reports(site)
        for period in ("2026-09", "2026-05"):
            for kind in ("Growing", "Declining"):
                self.assertLessEqual(len([r for r in self.rows(reports.KEYWORDS)
                                          if r[0] == period and r[1] == kind]), 2)


class AlarmTests(unittest.TestCase):
    TH = {k: v for k, v in sites.THRESHOLD_DEFAULTS.items()}

    def test_segment_change_thresholds_and_severity(self):
        f = lambda c, p: alarms.segment_change("2026-09", "2026-08", "All", c, p, self.TH)   # noqa: E731
        self.assertEqual(f(119, 100), [])                         # +19%
        self.assertEqual(f(100, 100), [])
        medium = f(120, 100)
        self.assertEqual(medium[0][:3], ["2026-09", "medium", "Segment clicks"])
        self.assertIn("rose by 20.0%", medium[0][3])
        self.assertEqual(f(60, 100)[0][1], "high")                 # -40%
        self.assertIn("fell by 40.0%", f(60, 100)[0][3])
        self.assertEqual(f(70, 100)[0][1], "medium")
        self.assertEqual(f(10, 0), [])                            # no base
        self.assertEqual(f(60, 29), [])                           # below alarm_min_clicks (30)
        self.assertEqual(f(2, 1), [])

    def test_lost_keyword_needs_ten_clicks(self):
        out = alarms.lost_keywords("2026-09", "2026-08", [("big", 10), ("small", 9)], self.TH)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][2], "Lost keyword")
        self.assertIn("big", out[0][3])
        self.assertIn("10 clicks in 2026-08", out[0][3])

    def test_page_loss_needs_50_percent_and_20_clicks(self):
        pages = [("https://x.test/a", 10, 20), ("https://x.test/b", 11, 20), ("https://x.test/c", 0, 19),
                 ("https://x.test/d", 0, 40)]
        out = alarms.page_losses("2026-09", "2026-08", pages, self.TH)
        self.assertEqual([r[3].split()[2] for r in out], ["https://x.test/a", "https://x.test/d"])
        self.assertIn("lost 50.0% of its clicks", out[0][3])

    def test_aio_drop(self):
        f = lambda a, p: alarms.aio_drop("2026-09", "2026-08", a, p, self.TH)    # noqa: E731
        self.assertEqual(f(71, 100), [])
        self.assertEqual(f(70, 100)[0][2], "AI Overviews")
        self.assertEqual(f(None, 100), [])
        self.assertEqual(f(10, None), [])
        self.assertEqual(f(0, 0), [])

    def test_new_url_pattern_needs_a_new_group_of_five(self):
        urls = lambda folder, n: [f"https://www.x.test/{folder}/p{i}" for i in range(n)]    # noqa: E731
        out = alarms.new_url_patterns("2026-09", urls("news", 5) + urls("misc", 4),
                                      urls("news", 2), self.TH)
        self.assertEqual(len(out), 1)
        self.assertIn("first folder", out[0][3])
        self.assertIn("5 pages", out[0][3])
        # the group was already big last month: not new any more
        self.assertEqual(alarms.new_url_patterns("2026-09", urls("news", 6), urls("news", 5), self.TH), [])

    def test_new_subdomain_but_not_the_main_host(self):
        main = [f"https://www.x.test/p{i}" for i in range(30)]
        shop = [f"https://shop.x.test/p{i}" for i in range(5)]
        out = alarms.new_url_patterns("2026-09", main + shop, main, self.TH)
        self.assertEqual(len(out), 1)
        self.assertIn("subdomain", out[0][3])
        self.assertIn("shop.x.test", out[0][3])

    def test_thresholds_come_from_the_site(self):
        site = sites.Site(name="s", thresholds={"alarm_clicks_change_pct": 10})
        th = {k: site.threshold(k) for k in sites.THRESHOLD_DEFAULTS}
        self.assertEqual(len(alarms.segment_change("p", "q", "All", 112, 100, th)), 1)


class AlarmEngineTests(ReportsTestCase):

    def test_alarms_are_written_once(self):
        self.run_reports()
        first = [list(r) for r in self.rows(reports.ALARMS)]
        self.assertTrue(first)
        areas = {r[2] for r in first}
        self.assertIn("Segment clicks", areas)
        self.assertIn("Lost keyword", areas)
        self.assertTrue({r[1] for r in first} <= {"high", "medium", "low"})
        self.run_reports()
        self.assertEqual(self.rows(reports.ALARMS), first)

    def test_a_big_drop_raises_a_high_alarm(self):
        for r in self.records:
            if date(2026, 9, 1) <= r["date"] <= date(2026, 9, 30):
                r["clicks"] = 1
        self.run_reports()
        high = [r for r in self.rows(reports.ALARMS)
                if r[0] == "2026-09" and r[2] == "Segment clicks" and r[1] == "high"]
        self.assertTrue(high)
        self.assertIn("fell by", high[0][3])


class NotesTests(ReportsTestCase):

    def test_notes_explain_the_rules_and_list_page_type_names_only(self):
        self.run_reports(site_with(page_types=TYPES))
        text = " ".join(" ".join(map(str, r)) for r in self.rows(reports.NOTES))
        for phrase in ("anonymized", "Complete periods", "AIO", "Closed periods"):
            self.assertIn(phrase, text)
        self.assertIn("English", text)
        self.assertNotIn("/en/", text)                  # names only, never the regexes
        self.assertNotIn("/article/", text)


class ScrubbingTests(ReportsTestCase):

    def test_log_has_counts_only(self):
        self.run_reports(site_with(page_types=TYPES))
        out = "\n".join(self.logs)
        for secret in ("/en/", "/article/", "alpha.test", RID, "English", "Article", "seasonal"):
            self.assertNotIn(secret, out)
        self.assertIn("reports:", out)


if __name__ == "__main__":
    unittest.main()
