"""
Tests for v2.2: the Path column, the new alarm wording and the view tabs (views.py).
Sheets is the in-memory fake of test_history; it has no formula engine, so these tests check
what is built and written (tabs, order, colours, formulas, dropdowns, versions), and a planted
error stands in for a broken formula when the read-back is tested.
"""

import re
import unittest
from datetime import date
from unittest import mock

import alarms
import deck_data as dd
import history
import reports
import sites
import state
import tabs
import views
from test_reports import RID, TYPES, ReportsTestCase, site_with

VIEW_NAMES = ["Dashboard", "Monthly", "Weekly", "Lost Keywords", "New Keywords", "Growing Keywords",
              "Declining Keywords", "Growing Pages", "Declining Pages", "YoY Growing Pages",
              "YoY Declining Pages", "Growing AIO Pages", "Declining AIO Pages", "Events"]
PERSIAN = "https://shop.test/%D9%85%D8%A7%DB%8C%D8%B9-%D8%B6%D8%AF-%D8%A7%D9%84%DA%A9%D8%AA%D8%B1%DB%8C%D8%B3%DB%8C%D8%AA%D9%87/"


# --------------------------------------------------------------------------- #
# Data fix: the readable Path column
# --------------------------------------------------------------------------- #

class PathTests(unittest.TestCase):

    def test_persian_is_decoded_and_the_domain_removed(self):
        self.assertEqual(tabs.path_of(PERSIAN), "/مایع-ضد-الکتریسیته/")

    def test_zero_width_non_joiner_and_the_persian_question_mark(self):
        self.assertEqual(tabs.path_of("https://x.test/%D9%85%DB%8C%E2%80%8C%D8%AE%D9%88%D8%A7%D9%87%D9%85/"),
                         "/می‌خواهم/")
        self.assertEqual(tabs.path_of("https://x.test/%DA%86%D8%B1%D8%A7%D8%9F"), "/چرا؟")

    def test_spaces_and_a_query_string(self):
        self.assertEqual(tabs.path_of("https://x.test/a%20b/c?x=%D8%A7&y=1"), "/a b/c?x=ا&y=1")

    def test_input_that_is_already_decoded_stays_as_it_is(self):
        self.assertEqual(tabs.path_of("https://x.test/مایع/ضد/"), "/مایع/ضد/")
        self.assertEqual(tabs.path_of("https://x.test/plain/page"), "/plain/page")

    def test_a_bare_domain_is_the_root_and_a_subdomain_is_removed_too(self):
        self.assertEqual(tabs.path_of("https://www.x.test"), "/")
        self.assertEqual(tabs.path_of("https://shop.x.test/p/1"), "/p/1")

    def test_a_broken_byte_sequence_does_not_raise(self):
        self.assertIn("�", tabs.path_of("https://x.test/%FF%FE"))


class PathColumnTests(ReportsTestCase):

    def test_new_page_rows_carry_their_path(self):
        self.run_reports()
        rows = self.rows(reports.PAGES)
        self.assertTrue(rows)
        self.assertEqual(self.tab(reports.PAGES)[0][-1], "Path")
        for r in rows:
            self.assertEqual(len(r), 14)
            self.assertEqual(r[13], tabs.path_of(r[2]))
        self.assertIn("/en/article/a", {r[13] for r in rows})

    def test_aio_page_rows_carry_their_path(self):
        rows = reports.aio_page_rows("2026-09", {PERSIAN: 90, "https://x.test/b": 5},
                                     {PERSIAN: 40, "https://x.test/b": 9}, 10)
        self.assertEqual(rows[0], ["2026-09", "Growing", PERSIAN, 90, 40, 50, "/مایع-ضد-الکتریسیته/"])
        self.assertEqual(rows[1][-1], "/b")
        self.assertEqual(len(reports.AIO_PAGES_HEADER), 7)

    def shrink(self, name, header):
        """Puts a tab back into the layout of v2.1 (no Path column)."""
        t = self.sheets.books[RID][name]
        t.data = [row[:len(header) - 1] for row in t.data]
        t.col_count = len(header) - 1

    def test_tabs_without_a_path_column_get_it_once_with_every_row_filled(self):
        self.run_reports()
        before = [list(r) for r in self.tab(reports.PAGES)]
        self.shrink(reports.PAGES, reports.PAGES_HEADER)
        self.sheets.books[RID][reports.PAGES].data[1][2] = PERSIAN
        result = self.run_reports()
        after = self.tab(reports.PAGES)
        self.assertEqual(after[0], reports.PAGES_HEADER)
        self.assertEqual(result.path_backfilled, len(before) - 1)
        self.assertEqual(after[1][13], "/مایع-ضد-الکتریسیته/")
        for old, new in zip(before[2:], after[2:]):
            self.assertEqual(new, old)                                       # nothing else moved
        self.assertEqual(len(after), len(before))
        self.assertEqual(self.sheets.books[RID][reports.PAGES].col_count, 14)
        self.assertEqual(self.run_reports().path_backfilled, 0)               # one-off

    def test_a_tab_that_already_has_the_column_is_not_read_again(self):
        self.run_reports()
        reads = []
        original = tabs.read_rows

        def spy(sheets, sid, tab, last, first_row=2):
            reads.append((tab, first_row))
            return original(sheets, sid, tab, last, first_row)

        with mock.patch.object(tabs, "read_rows", spy):
            self.run_reports()
        self.assertNotIn((reports.PAGES, 1), reads)


# --------------------------------------------------------------------------- #
# Data fix: alarm wording
# --------------------------------------------------------------------------- #

class WordingTests(ReportsTestCase):
    TH = {k: v for k, v in sites.THRESHOLD_DEFAULTS.items()}

    def test_the_whole_site_has_no_segment_name_and_names_the_month(self):
        out = alarms.segment_change("2026-09", "2026-08", "All", 325, 481, self.TH)
        self.assertEqual(out[0].message, "Clicks fell 32.4% vs August (481 to 325).")

    def test_a_segment_is_named_by_its_label(self):
        out = alarms.segment_change("2026-01", "2025-12", "English", 150, 100, self.TH)
        self.assertEqual(out[0].message, "Clicks for English rose 50.0% vs December (100 to 150).")

    def test_page_alarms_show_the_decoded_path(self):
        out = alarms.page_losses("2026-09", "2026-08", [(PERSIAN, 0, 40)], self.TH)
        self.assertEqual(out[0].message,
                         "The page /مایع-ضد-الکتریسیته/ lost 100.0% of its clicks vs August (40 to 0).")

    def test_other_sentences_use_month_names_too(self):
        lost = alarms.lost_keywords("2026-09", "2026-08", [("kw", 12)], self.TH)[0].message
        self.assertEqual(lost, 'The keyword "kw" brought 12 clicks in August and is not in '
                               "Search Console's data in September.")
        aio = alarms.aio_drop("2026-09", "2026-08", 50, 100, self.TH)[0].message
        self.assertEqual(aio, "AI Overview impressions fell 50.0% vs August (100 to 50).")

    def test_new_pattern_alarms_show_a_folder_without_its_domain(self):
        urls = [f"https://x.test/news/p{i}" for i in range(6)]
        out = alarms.new_url_patterns("2026-09", urls, [], self.TH)
        self.assertTrue(any("(/news/)" in a.message and "https://" not in a.message for a in out))

    def test_no_alarm_in_the_sheet_has_a_raw_period_or_the_word_all(self):
        self.run_reports(site_with(page_types=TYPES))
        rows = self.rows(reports.ALARMS)
        self.assertTrue(rows)
        for r in rows:
            self.assertIsNone(re.search(r"\b\d{4}-\d{2}\b", r[3]), r[3])
            self.assertNotRegex(r[3], r"\bfor All\b")
            self.assertNotIn("https://", r[3])

    def set_state(self, key, value):
        rows = self.sheets.books[RID]["State"].data
        for r in rows:
            if r and r[0] == key:
                r[1] = value

    def run_reports_spying(self, calls):
        """run_reports, noting every tab that gets rewritten."""
        original = tabs.rewrite

        def spy(sheets, sid, tab, header, rows):
            calls.append(tab)
            return original(sheets, sid, tab, header, rows)

        with mock.patch.object(tabs, "rewrite", spy):
            return self.run_reports()

    def test_alarms_written_with_the_old_wording_are_rebuilt_once(self):
        self.run_reports()
        self.assertEqual(dict((r[0], r[1]) for r in self.rows("State"))["alarms_version"],
                         alarms.WORDING_VERSION)
        good = [list(r) for r in self.rows(reports.ALARMS)]
        old = [r for r in self.tab(reports.ALARMS) if r[2] == "Segment clicks"][0]
        old[3] = "Clicks for All fell by 32.4% compared with 2026-08 (481 to 325)."
        self.set_state("alarms_version", "1")
        calls = []
        self.run_reports_spying(calls)
        self.assertIn(reports.ALARMS, calls)
        self.assertEqual(self.rows(reports.ALARMS), good)
        calls.clear()
        self.run_reports_spying(calls)
        self.assertNotIn(reports.ALARMS, calls)                     # only once


class OldWordingInDecksTests(unittest.TestCase):
    """Decks keep reading R Alarms written by older versions, and read the new wording the same."""

    PAIRS = [
        ("Clicks for All fell by 32.4% compared with 2026-08 (481 to 325).",
         "Clicks fell 32.4% vs August (481 to 325)."),
        ("Clicks for English rose by 20.0% compared with 2026-08 (100 to 120).",
         "Clicks for English rose 20.0% vs August (100 to 120)."),
        ("The keyword \"kw\" brought 12 clicks in 2026-08 and is not in Search Console's data in 2026-09.",
         "The keyword \"kw\" brought 12 clicks in August and is not in Search Console's data in September."),
        ("The page https://x.test/a/b lost 60.0% of its clicks compared with 2026-08 (100 to 40).",
         "The page /a/b lost 60.0% of its clicks vs August (100 to 40)."),
        ("AI Overview impressions fell by 50.0% compared with 2026-08 (100 to 50).",
         "AI Overview impressions fell 50.0% vs August (100 to 50)."),
    ]

    def text(self, message, lang):
        return dd.alarm_text({"severity": "medium", "area": "x", "message": message, "impact": 1}, lang)

    def test_english_reads_the_same_either_way(self):
        for old, new in self.PAIRS:
            self.assertEqual(self.text(old, "en"), self.text(new, "en"), new)

    def test_persian_reads_the_same_either_way(self):
        for old, new in self.PAIRS:
            self.assertEqual(self.text(old, "fa"), self.text(new, "fa"), new)

    def test_nothing_a_person_reads_has_a_raw_period_or_a_segment_called_all(self):
        for _, new in self.PAIRS:
            for lang in ("en", "fa"):
                shown = self.text(new, lang)
                self.assertIsNone(re.search(r"\b\d{4}-\d{2}\b", shown), shown)
                self.assertNotIn(" All ", shown)


# --------------------------------------------------------------------------- #
# The view tabs
# --------------------------------------------------------------------------- #

class ViewsTestCase(ReportsTestCase):
    SITE = None

    def setUp(self):
        super().setUp()
        self.site = self.SITE or site_with()
        self.run_reports(self.site)
        self.result = self.run_views()

    def run_views(self, **kw):
        return views.run_views(self.sheets, RID, self.site, log=self.logs.append, **kw)

    @property
    def book(self):
        return self.sheets.books[RID]

    def titles(self):
        return list(self.book)

    def cell(self, tab, row, col):
        data = self.book[tab].data
        return data[row - 1][col - 1] if row <= len(data) and col <= len(data[row - 1]) else ""

    def structure_calls(self):
        return len(self.sheets.requests)

    def writes(self):
        return [c for c in self.sheets.value_calls if c[1] == "USER_ENTERED"]

    def state_value(self, key):
        return dict((r[0], r[1]) for r in self.rows("State")).get(key)


class BuildTests(ViewsTestCase):

    def test_tabs_are_in_order_with_the_agreed_colours(self):
        self.assertEqual(self.result.mode, "built")
        self.assertEqual(self.titles()[:len(VIEW_NAMES)], VIEW_NAMES)
        color = lambda name: self.book[name].color                      # noqa: E731
        self.assertEqual(color("Dashboard"), views.rgb(views.AMBER))
        for name in VIEW_NAMES[1:-1]:
            self.assertEqual(color(name), views.rgb(views.INK), name)
        self.assertEqual(color("Events"), views.rgb(views.SLATE))
        rest = self.titles()[len(VIEW_NAMES):]
        self.assertIn(reports.MONTHLY, rest)
        self.assertIn("Notes", rest)
        self.assertIn("State", rest)
        for name in rest:
            self.assertEqual(color(name), views.rgb(views.GREY), name)

    def test_an_empty_default_sheet_is_deleted_but_one_with_content_stays(self):
        self.assertNotIn("Sheet1", self.titles())
        other = RID + "-2"
        self.sheets.add_book(other)
        self.sheets.books[other]["Sheet1"].data = [["mine"]]
        for name, header, _ in reports.TABS:
            tabs.ensure_tab(self.sheets, other, history.grid_info(self.sheets, other)[0], name, header)
        self.sheets.books[other][reports.MONTHLY].data.append(["2026-09", "All"] + [1] * 15)
        views.run_views(self.sheets, other, self.site, newest_month="2026-09")
        self.assertIn("Sheet1", self.sheets.books[other])

    def test_the_dashboard_hides_its_gridlines_and_its_helper_columns(self):
        dash = self.book["Dashboard"]
        self.assertTrue(dash.hide_grid)
        self.assertEqual(dash.hidden_cols, [(10, 14)])
        self.assertEqual(dash.frozen, 2)

    def test_formulas_are_written_after_every_tab_exists(self):
        calls = self.writes()
        self.assertTrue(calls)
        for _, _, titles, _ in calls:
            for name in VIEW_NAMES:
                self.assertIn(name, titles)

    def test_formulas_are_written_with_user_entered_and_commas(self):
        formulas = [v[0][0] for c in self.writes() for _, v in c[3]
                    if isinstance(v[0][0], str) and v[0][0].startswith("=")]
        self.assertGreater(len(formulas), 30)
        for f in formulas:                       # a ; may only separate the rows of an array constant
            self.assertNotIn(";", re.sub(r"\{[^{}]*\}", "", f), f[:60])

    def test_dropdown_values_are_written_as_text(self):
        wrote = {}
        for _, _, _, data in self.writes():
            for rng_, values in data:
                wrote[rng_] = values
        self.assertEqual(wrote["'Dashboard'!C2"], [["'2026-09"]])
        for name in views.LISTS:
            self.assertEqual(wrote[f"'{name}'!B2"], [["'2026-09"]], name)
        self.assertEqual(self.cell("Dashboard", 2, 3), "2026-09")
        self.assertIsInstance(self.cell("Dashboard", 2, 3), str)

    def test_dropdowns_read_the_months_of_r_monthly(self):
        self.assertEqual(self.book["Dashboard"].dropdowns[(2, 3)], "='R Monthly'!A2:A")
        self.assertEqual(self.book["Growing Pages"].dropdowns[(2, 2)], "='R Monthly'!A2:A")

    def test_the_dashboard_follows_the_appendix(self):
        self.assertEqual(self.cell("Dashboard", 14, 2),
                         "=IFERROR(SORT(FILTER({'R Alarms'!B2:B,'R Alarms'!C2:C,'R Alarms'!E2:E,"
                         "'R Alarms'!D2:D},'R Alarms'!A2:A=$C$2),3,FALSE),\"No alarms this month.\")")
        self.assertEqual(self.cell("Dashboard", 1, 11), '=TEXT(EDATE(DATE(LEFT($C$2,4),RIGHT($C$2,2),1),-1),"yyyy-mm")')
        self.assertIn("Declining", self.cell("Dashboard", 27, 2))
        self.assertIn("'R Pages'!N2:N", self.cell("Dashboard", 27, 2))             # the Path column
        self.assertIn("Growing", self.cell("Dashboard", 35, 2))
        self.assertIn("\"Lost\"", self.cell("Dashboard", 43, 2))
        self.assertIn("\"New\"", self.cell("Dashboard", 51, 2))
        self.assertIn("None this month.", self.cell("Dashboard", 51, 2))
        self.assertIn("Events!A:B", self.cell("Dashboard", 5, 2))
        self.assertIn("SPARKLINE", self.cell("Dashboard", 10, 2))
        self.assertIn('"All"', self.cell("Dashboard", 7, 2))                       # no segment dropdown
        self.assertEqual(self.cell("Dashboard", 6, 6), "AIO impressions")
        self.assertEqual([self.cell("Dashboard", r, 1) for r in (7, 8, 9, 10)],
                         ["This month", "vs last month", "vs last year", "Last 13 months"])
        self.assertEqual(self.cell("Dashboard", 2, 5), "")

    def test_the_title_uses_the_site_label_or_a_neutral_name(self):
        self.assertEqual(self.cell("Dashboard", 1, 2), "SEO Dashboard")
        site = site_with(deck=sites.DeckConfig(site_label="Example Co"))
        other = RID + "-label"
        self.sheets.add_book(other)
        titles, _ = history.grid_info(self.sheets, other)
        for name, header, _ in reports.TABS:
            tabs.ensure_tab(self.sheets, other, titles, name, header)
        self.sheets.books[other][reports.MONTHLY].data.append(["2026-09", "All"] + [1] * 15)
        views.run_views(self.sheets, other, site, newest_month="2026-09")
        self.assertEqual(self.sheets.books[other]["Dashboard"].data[0][1], "Example Co · SEO Dashboard")

    def test_list_tabs_follow_the_sort_rules(self):
        f = lambda name: self.cell(name, 5, 1)                           # noqa: E731
        self.assertIn("'R Keywords'!B2:B=\"Lost\"),6,FALSE)", f("Lost Keywords"))
        self.assertIn("'R Keywords'!B2:B=\"New\"),5,FALSE)", f("New Keywords"))
        self.assertIn("\"Growing\"),4,FALSE)", f("Growing Keywords"))
        self.assertIn("\"Declining\"),4,TRUE)", f("Declining Keywords"))
        self.assertIn("\"YoY Growing\"),4,FALSE)", f("YoY Growing Pages"))
        self.assertIn("\"YoY Declining\"),4,TRUE)", f("YoY Declining Pages"))
        self.assertTrue(f("Growing Pages").startswith("=IFERROR(SORT(FILTER({'R Pages'!N2:N,'R Pages'!E2:E,"))
        self.assertIn("$B$2", f("Lost Keywords"))
        self.assertIn("AIO data not imported for this month. Export the Generative AI report and copy "
                      "its tabs into this sheet.", f("Growing AIO Pages"))
        self.assertIn("'R AIO Pages'!G2:G", f("Declining AIO Pages"))
        self.assertEqual([self.cell("Lost Keywords", 4, c) for c in range(1, 10)],
                         ["Keyword", "Clicks", "Clicks before", "Change", "Impressions", "Impr. before",
                          "Change", "Position", "Pos. before"])
        self.assertEqual(self.cell("Growing Pages", 4, 1), "Page")
        self.assertEqual(self.cell("Lost Keywords", 2, 1), "Month")

    def test_monthly_and_weekly_hold_one_formula_each(self):
        self.assertTrue(self.cell("Monthly", 3, 1).startswith("=IFERROR(LET(s,SORT(FILTER('R Monthly'!A2:Q"))
        self.assertIn("VLOOKUP(x,Events!A:B,2,FALSE)", self.cell("Monthly", 3, 1))
        self.assertEqual(self.cell("Monthly", 2, 13), "Note")
        self.assertEqual(self.cell("Monthly", 2, 10), "AIO impressions")
        self.assertIn("'R Weekly'!A2:Q", self.cell("Weekly", 3, 1))
        self.assertEqual(self.cell("Weekly", 2, 12), "Note")
        self.assertNotIn("AIO impressions", [self.cell("Weekly", 2, c) for c in range(1, 13)])

    def test_events_has_two_text_columns_and_the_version_is_stored(self):
        self.assertEqual([self.cell("Events", 1, 1), self.cell("Events", 1, 2)], ["Month", "What happened"])
        self.assertEqual(self.state_value("views_version"), str(views.VIEWS_VERSION))

    def test_fonts_colours_and_formats_are_requested(self):
        sent = repr(self.sheets.requests_for(RID, "Dashboard"))
        for needle in ("Lato", "Vazirmatn", "RIGHT_TO_LEFT", "ONE_OF_RANGE",
                       "NUMBER_GREATER", "NUMBER_LESS", "TEXT_EQ", "mergeCells", "#,##0", "0.00%",
                       "+0.0%;-0.0%;0.0%", "hiddenByUser"):
            self.assertIn(needle, sent, needle)
        self.assertIn("mmmm yyyy", repr(self.sheets.requests_for(RID, "Monthly")))

    def test_position_changes_use_reversed_colours(self):
        reqs = [r for r in self.sheets.requests_for(RID, "Dashboard") if "addConditionalFormatRule" in r]
        by_col = {}
        for r in reqs:
            rule = r["addConditionalFormatRule"]["rule"]
            rg = rule["ranges"][0]
            cond = rule["booleanRule"]["condition"]["type"]
            if rg["startColumnIndex"] == 4 and rg["startRowIndex"] == 7:
                by_col[cond] = rule["booleanRule"]["format"]["textFormat"]["foregroundColor"]
        self.assertEqual(by_col["NUMBER_GREATER"], views.rgb(views.RED))
        self.assertEqual(by_col["NUMBER_LESS"], views.rgb(views.GREEN))

    def test_events_get_the_callout_background_in_monthly(self):
        reqs = [r for r in self.sheets.requests_for(RID, "Monthly") if "addConditionalFormatRule" in r]
        formulas = [r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]
                    ["userEnteredValue"] for r in reqs
                    if r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["type"]
                    == "CUSTOM_FORMULA"]
        self.assertIn('=$M3<>""', formulas)


class SegmentTests(ViewsTestCase):
    SITE = site_with(page_types=TYPES)

    def test_one_monthly_tab_per_segment_right_after_monthly(self):
        names = self.titles()
        self.assertEqual(names[1:5], ["Monthly", "Monthly · English", "Monthly · Article",
                                      "Monthly · Other"])
        self.assertEqual(names[5], "Weekly")
        self.assertEqual(self.book["Monthly · English"].color, views.rgb(views.INK))
        self.assertIn('"English"', self.cell("Monthly · English", 3, 1))
        self.assertIn('"All"', self.cell("Monthly", 3, 1))

    def test_the_dashboard_gets_a_segment_dropdown_that_replaces_the_fixed_all(self):
        self.assertEqual(self.cell("Dashboard", 2, 4), "Segment")
        self.assertEqual(self.cell("Dashboard", 2, 5), "All")
        self.assertEqual(self.book["Dashboard"].dropdowns[(2, 5)], "=Dashboard!$N$2:$N")
        self.assertIn("$E$2", self.cell("Dashboard", 7, 2))
        self.assertNotIn('"All"', self.cell("Dashboard", 7, 2))
        self.assertIn("UNIQUE", self.cell("Dashboard", 2, 12))
        self.assertEqual(self.book["Dashboard"].dropdowns[(2, 3)], "=Dashboard!$L$2:$L")
        self.assertEqual(self.book["Declining Pages"].dropdowns[(2, 2)], "=Dashboard!$L$2:$L")

    def test_segment_names_stay_out_of_logs_and_error_messages(self):
        view_logs = [l for l in self.logs if "views:" in l]
        self.assertTrue(view_logs)
        self.assertFalse(any("English" in l or "Article" in l for l in view_logs))
        self.sheets.error_cells[("Monthly · English", "A3")] = "#REF!"
        with self.assertRaises(views.ViewsError) as ctx:
            self.run_views(rebuild=True)
        self.assertNotIn("English", str(ctx.exception))
        self.assertIn("(segment)", str(ctx.exception))


class RunRulesTests(ViewsTestCase):

    def test_a_normal_run_writes_nothing(self):
        structure, writes = self.structure_calls(), len(self.sheets.value_calls)
        result = self.run_views()
        self.assertEqual(result.mode, "ok")
        self.assertEqual((self.structure_calls(), len(self.sheets.value_calls)), (structure, writes))

    def test_a_normal_run_leaves_the_dropdowns_the_user_chose(self):
        self.book["Dashboard"].data[1][2] = "2026-08"
        self.book["Lost Keywords"].data[1][1] = "2026-07"
        self.run_views(newest_month="2026-09")
        self.assertEqual(self.cell("Dashboard", 2, 3), "2026-08")
        self.assertEqual(self.cell("Lost Keywords", 2, 2), "2026-07")

    def test_a_new_month_moves_every_month_dropdown(self):
        self.book["Dashboard"].data[1][2] = "2026-08"
        structure = self.structure_calls()
        result = self.run_views(new_month="2026-10")
        self.assertEqual(result.moved_to, "2026-10")
        self.assertEqual(self.cell("Dashboard", 2, 3), "2026-10")
        for name in views.LISTS:
            self.assertEqual(self.cell(name, 2, 2), "2026-10", name)
        self.assertEqual(self.writes()[-1][3][0][1], [["'2026-10"]])              # as text
        self.assertEqual(self.structure_calls(), structure)                      # no tab was touched

    def test_reports_report_a_new_month_only_when_one_was_added(self):
        first = self.run_reports(self.site)
        self.assertEqual((first.new_month, first.newest_month), ("", "2026-09"))
        later = self.run_reports(self.site, final=date(2026, 10, 31), today=date(2026, 11, 5))
        self.assertEqual((later.new_month, later.newest_month), ("2026-10", "2026-10"))

    def test_a_newer_views_version_rebuilds_every_tab(self):
        before = {n: self.book[n].sheet_id for n in VIEW_NAMES}
        newer = views.VIEWS_VERSION + 1
        self.book["Dashboard"].data[1][2] = "2026-08"
        with mock.patch.object(views, "VIEWS_VERSION", newer):
            result = self.run_views(newest_month="2026-09")
        self.assertEqual(result.mode, "built")
        self.assertEqual(self.titles()[:len(VIEW_NAMES)], VIEW_NAMES)
        for name in VIEW_NAMES[:-1]:
            self.assertNotEqual(self.book[name].sheet_id, before[name], name)
        self.assertEqual(self.book["Events"].sheet_id, before["Events"])
        self.assertEqual(self.state_value("views_version"), str(newer))
        self.assertEqual(self.cell("Dashboard", 2, 3), "2026-09")               # a new build starts on the newest month

    def test_a_sheet_built_by_version_one_is_rebuilt_with_the_this_month_label(self):
        for r in self.book["State"].data:
            if r and r[0] == "views_version":
                r[1] = "1"
        self.book["Dashboard"].data[6][0] = ""                      # how version 1 left cell A7
        result = self.run_views(newest_month="2026-09")
        self.assertEqual(result.mode, "built")
        self.assertEqual(self.cell("Dashboard", 7, 1), "This month")
        self.assertEqual(self.state_value("views_version"), str(views.VIEWS_VERSION))

    def test_the_stored_version_is_raised_after_an_upgrade(self):
        with mock.patch.object(views, "VIEWS_VERSION", 7):
            self.run_views(newest_month="2026-09")
        self.assertEqual(self.state_value("views_version"), "7")

    def test_events_are_kept_when_the_views_are_rebuilt(self):
        events = self.book["Events"]
        events.data += [["2026-09", "Algorithm update"], ["2026-08", "Redesign یک"]]
        sheet_id = events.sheet_id
        result = self.run_views(rebuild=True)
        self.assertEqual(result.mode, "built")
        self.assertIs(self.book["Events"], events)
        self.assertEqual(self.book["Events"].sheet_id, sheet_id)
        self.assertEqual(self.cell("Events", 2, 2), "Algorithm update")
        self.assertEqual(self.cell("Events", 3, 1), "2026-08")
        self.assertEqual(self.titles()[len(VIEW_NAMES) - 1], "Events")
        self.assertEqual(self.book["Events"].color, views.rgb(views.SLATE))
        deleted = [r["deleteSheet"]["sheetId"] for _, r in self.sheets.requests if "deleteSheet" in r]
        self.assertNotIn(sheet_id, deleted)

    def test_a_missing_tab_is_created_again_where_it_belongs(self):
        ids = {n: self.book[n].sheet_id for n in VIEW_NAMES}
        del self.book["Growing Pages"]
        self.book["Dashboard"].data[1][2] = "2026-08"
        result = self.run_views(newest_month="2026-09")
        self.assertEqual(result.mode, "repaired")
        self.assertEqual(result.created, 1)
        self.assertEqual(self.titles()[:len(VIEW_NAMES)], VIEW_NAMES)
        for name in VIEW_NAMES:
            if name != "Growing Pages":
                self.assertEqual(self.book[name].sheet_id, ids[name], name)
        self.assertEqual(self.cell("Growing Pages", 2, 2), "2026-09")
        self.assertEqual(self.cell("Dashboard", 2, 3), "2026-08")                # others untouched
        self.assertTrue(self.cell("Growing Pages", 5, 1).startswith("=IFERROR(SORT("))

    def test_a_deleted_events_tab_comes_back_empty(self):
        del self.book["Events"]
        self.run_views()
        self.assertIn("Events", self.book)
        self.assertEqual(self.cell("Events", 1, 2), "What happened")

    def test_no_report_data_builds_nothing(self):
        other = RID + "-empty"
        self.sheets.add_book(other, {reports.MONTHLY: (2, 17)})
        result = views.run_views(self.sheets, other, self.site)
        self.assertEqual(result.mode, "skipped")
        self.assertNotIn("Dashboard", self.sheets.books[other])
        bare = RID + "-bare"
        self.sheets.add_book(bare)
        self.assertEqual(views.run_views(self.sheets, bare, self.site).mode, "skipped")


class ReadBackTests(ViewsTestCase):

    def test_each_kind_of_formula_error_fails_the_run(self):
        for error in ("#REF!", "#NAME?", "#VALUE!", "#ERROR!"):
            self.sheets.error_cells = {("Dashboard", "B7"): error}
            with self.assertRaises(views.ViewsError) as ctx:
                self.run_views(rebuild=True)
            self.assertIn(error, str(ctx.exception))
            self.assertIn("Dashboard!B7", str(ctx.exception))

    def test_other_cells_in_other_tabs_are_found_too(self):
        self.sheets.error_cells = {("Lost Keywords", "A5"): "#REF!", ("Monthly", "A3"): "#NAME?"}
        with self.assertRaises(views.ViewsError) as ctx:
            self.run_views(rebuild=True)
        self.assertIn("Lost Keywords!A5", str(ctx.exception))
        self.assertIn("Monthly!A3", str(ctx.exception))

    def test_a_failed_read_back_does_not_store_the_version_so_the_next_run_builds_again(self):
        self.sheets.error_cells = {("Dashboard", "B7"): "#REF!"}
        with mock.patch.object(views, "VIEWS_VERSION", 9):
            with self.assertRaises(views.ViewsError):
                self.run_views(newest_month="2026-09")
        self.assertNotEqual(self.state_value("views_version"), "9")
        self.sheets.error_cells = {}
        with mock.patch.object(views, "VIEWS_VERSION", 9):
            self.assertEqual(self.run_views(newest_month="2026-09").mode, "built")
        self.assertEqual(self.state_value("views_version"), "9")

    def test_a_semicolon_locale_is_named_in_the_message(self):
        self.sheets.locale = "de_DE"
        self.sheets.error_cells = {("Dashboard", "B7"): "#ERROR!"}
        with self.assertRaises(views.ViewsError) as ctx:
            self.run_views(rebuild=True)
        self.assertIn("locale", str(ctx.exception))
        self.assertIn("de_DE", str(ctx.exception))

    def test_a_clean_read_back_is_quiet(self):
        self.sheets.locale = "de_DE"
        self.assertEqual(self.run_views(rebuild=True).mode, "built")

    def test_loading_cells_are_waited_for(self):
        shown = {"n": 0}
        original = self.sheets.shown

        def flaky(tab, row, col, value):
            if tab == "Dashboard" and row == 7 and col == 2 and shown["n"] < 2:
                shown["n"] += 1
                return "Loading..."
            return original(tab, row, col, value)

        with mock.patch.object(self.sheets, "shown", flaky):
            self.assertEqual(self.run_views(rebuild=True).mode, "built")
        self.assertGreaterEqual(shown["n"], 2)

    def test_the_build_respects_the_call_throttle(self):
        calls = []
        original = history._throttle
        with mock.patch.object(history, "_throttle", lambda kind: (calls.append(kind), original(kind))[1]):
            self.run_views(rebuild=True)
        self.assertGreater(len(calls), 0)
        self.assertLess(len(calls), 40)                       # a few dozen calls, once


if __name__ == "__main__":
    unittest.main()
