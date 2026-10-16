"""
Tests for the AI Overview (Generative AI report) import and how it shows up in the reports.
"""

import unittest
from datetime import date, timedelta

import aio_import
import history
import reports
from test_history import Tab
from test_reports import (PAGES, RID, TODAY, TYPES, ReportsTestCase, month_span, naive, site_with)


def month_dates(label, per_day, fmt="%Y-%m-%d"):
    first, last = month_span(label)
    out, d = [], first
    while d <= last:
        out.append([d.strftime(fmt), str(per_day)])
        d += timedelta(days=1)
    return out


class ParsingTests(unittest.TestCase):

    def test_numbers(self):
        p = aio_import.parse_number
        self.assertEqual(p("1,234"), 1234)
        self.assertEqual(p("۱٬۲۳۴"), 1234)           # Persian digits and thousands separator
        self.assertEqual(p("١٢٣"), 123)               # Arabic-Indic digits
        self.assertEqual(p(" 12.5 "), 12.5)
        self.assertEqual(p("1 234"), 1234)
        for bad in ("", "abc", "12%", "—", "1.2.3"):
            self.assertIsNone(p(bad), bad)

    def test_dates(self):
        p = aio_import.parse_date
        self.assertEqual(p("2026-09-01"), date(2026, 9, 1))
        self.assertEqual(p("۲۰۲۶-۰۹-۰۱"), date(2026, 9, 1))
        self.assertEqual(p("2026/09/01"), date(2026, 9, 1))
        self.assertEqual(p("Sep 1, 2026"), date(2026, 9, 1))
        self.assertEqual(p("September 1, 2026"), date(2026, 9, 1))
        self.assertEqual(p("9/1/2026"), date(2026, 9, 1))
        self.assertIsNone(p("1405/06/10"))
        self.assertIsNone(p("hello"))

    def test_pages_with_english_header(self):
        data, skipped = aio_import.parse_pages([
            ["Top pages", "Impressions"],
            ["https://x.test/a", "1,200"], ["https://x.test/b#sec", "30"],
            ["https://x.test/b", "20"], ["Total", "1,250"]])
        self.assertEqual(data, {"https://x.test/a": 1200, "https://x.test/b": 50})
        self.assertEqual(skipped, 1)

    def test_pages_with_a_persian_export(self):
        data, _ = aio_import.parse_pages([
            ["صفحه", "نمایش"], ["https://x.test/%D9%85", "۱٬۲۰۰"], ["https://x.test/b", "۳۰"]])
        self.assertEqual(data, {"https://x.test/%D9%85": 1200, "https://x.test/b": 30})

    def test_columns_found_by_content_when_the_header_is_unknown(self):
        data, _ = aio_import.parse_pages([["Foo", "Bar"], ["https://x.test/a", "12"]])
        self.assertEqual(data, {"https://x.test/a": 12})

    def test_no_header_row_at_all(self):
        data, _ = aio_import.parse_pages([["https://x.test/a", "12"], ["https://x.test/b", "5"]])
        self.assertEqual(data, {"https://x.test/a": 12, "https://x.test/b": 5})

    def test_impressions_column_is_chosen_by_its_name_among_several_numbers(self):
        data, _ = aio_import.parse_pages([["Page", "Clicks", "Impressions"],
                                          ["https://x.test/a", "3", "99"]])
        self.assertEqual(data, {"https://x.test/a": 99})

    def test_ambiguous_numbers_fail_loudly(self):
        with self.assertRaises(aio_import.AioImportError) as cm:
            aio_import.parse_pages([["Page", "Alpha", "Beta"], ["https://x.test/a", "3", "99"]])
        self.assertIn("impressions", str(cm.exception))

    def test_no_urls_fail_loudly(self):
        with self.assertRaises(aio_import.AioImportError):
            aio_import.parse_pages([["Page", "Impressions"], ["nothing", "1"]])

    def test_empty_tab_fails_loudly(self):
        with self.assertRaises(aio_import.AioImportError):
            aio_import.parse_pages([[], [""]])

    def test_dates_tab(self):
        data, _ = aio_import.parse_dates([["Date", "Impressions"]] + month_dates("2026-09", 100),
                                         date(2026, 9, 1))
        self.assertEqual(len(data), 30)
        self.assertEqual(set(data.values()), {100})

    def test_dates_in_a_localized_export(self):
        rows = [["تاریخ", "نمایش"], ["۲۰۲۶-۰۹-۰۱", "۱۰۰"], ["۲۰۲۶-۰۹-۰۲", "۲۰۰"]]
        data, _ = aio_import.parse_dates(rows, date(2026, 9, 1))
        self.assertEqual(data, {date(2026, 9, 1): 100, date(2026, 9, 2): 200})

    def test_dates_outside_the_month_in_the_tab_name_fail_loudly(self):
        with self.assertRaises(aio_import.AioImportError) as cm:
            aio_import.parse_dates([["Date", "Impressions"]] + month_dates("2026-08", 1),
                                   date(2026, 9, 1))
        self.assertIn("month", str(cm.exception))


class AioTestCase(ReportsTestCase):

    def add_tab(self, title, values):
        tab = Tab(self.sheets.next_id, len(values) + 1, 3)
        self.sheets.next_id += 1
        tab.data = [list(r) for r in values]
        self.sheets.books[RID][title] = tab
        return tab

    def add_month(self, label, per_day, pages):
        self.add_tab(f"AIO {label} Dates", [["Date", "Impressions"]] + month_dates(label, per_day))
        self.add_tab(f"AIO {label} Pages",
                     [["Top pages", "Impressions"]] + [[u, str(n)] for u, n in pages.items()])

    def raw(self):
        return self.rows(aio_import.RAW_TAB)

    def tab_titles(self):
        return set(self.sheets.books[RID])


class ImportTests(AioTestCase):

    def test_valid_tabs_are_stored_and_removed(self):
        self.add_month("2026-09", 100, {PAGES["en_a"]: 900, PAGES["shop"]: 100, PAGES["home"]: 50})
        self.run_reports()
        raw = self.raw()
        self.assertEqual(len([r for r in raw if r[:2] == ["2026-09", "date"]]), 30)
        pages = {r[2]: r[3] for r in raw if r[:2] == ["2026-09", "page"]}
        self.assertEqual(pages, {PAGES["en_a"]: 900, PAGES["shop"]: 100, PAGES["home"]: 50})
        self.assertIn(["2026-09", "date", "2026-09-01", 100], raw)
        self.assertNotIn("AIO 2026-09 Pages", self.tab_titles())          # the import tabs are gone
        self.assertNotIn("AIO 2026-09 Dates", self.tab_titles())
        self.assertTrue(any("AIO 2026-09: imported 3 pages, 30 days" in l for l in self.logs))

    def test_monthly_aio_columns(self):
        self.add_month("2026-08", 200, {PAGES["en_a"]: 100})
        self.add_month("2026-09", 100, {PAGES["en_a"]: 100})
        self.run_reports()
        aug, sep = self.monthly("2026-08"), self.monthly("2026-09")
        self.assertEqual(aug["AIO Impressions"], 200 * 31)
        self.assertEqual(sep["AIO Impressions"], 100 * 30)
        self.assertEqual(sep["AIO MoM"], 3000 - 6200)
        self.assertEqual(sep["AIO MoM %"], round(-3200 / 6200 * 100, 1))
        self.assertEqual(aug["AIO MoM"], "")                              # July has no import
        self.assertEqual(self.monthly("2026-07")["AIO Impressions"], "")

    def test_segment_aio_uses_the_page_type_rules(self):
        self.add_month("2026-09", 100, {PAGES["en_a"]: 900, PAGES["en_b"]: 40, PAGES["fa_a"]: 7,
                                        PAGES["shop"]: 100, PAGES["home"]: 50})
        self.run_reports(site_with(page_types=TYPES))
        self.assertEqual(self.monthly("2026-09", "All")["AIO Impressions"], 3000)       # from the daily tab
        self.assertEqual(self.monthly("2026-09", "English")["AIO Impressions"], 940)    # en_a + en_b
        self.assertEqual(self.monthly("2026-09", "Article")["AIO Impressions"], 907)    # en_a + fa_a
        self.assertEqual(self.monthly("2026-09", "Other")["AIO Impressions"], 150)      # shop + home

    def test_weekly_aio_needs_all_seven_days(self):
        self.add_month("2026-09", 100, {PAGES["en_a"]: 1})
        self.run_reports()
        self.assertEqual(self.weekly("2026-W38")["AIO Impressions"], 700)      # 14-20 Sep, inside the month
        self.assertEqual(self.weekly("2026-W36")["AIO Impressions"], "")       # 31 Aug - 6 Sep: August missing
        self.assertEqual(self.weekly("2026-W30")["AIO Impressions"], "")

    def test_month_arriving_after_it_was_reported_fills_in_only_the_aio_cells(self):
        self.run_reports()
        before = {r[0]: list(r) for r in self.rows(reports.MONTHLY)}
        self.add_month("2026-08", 200, {PAGES["en_a"]: 100})
        self.add_month("2026-09", 100, {PAGES["en_a"]: 100})
        result = self.run_reports()
        after = {r[0]: [str(c) if c != "" else "" for c in r] for r in self.rows(reports.MONTHLY)}
        self.assertGreater(result.patched_cells, 0)
        self.assertEqual(self.monthly("2026-09")["AIO Impressions"], 3000)
        self.assertEqual(self.monthly("2026-09")["AIO MoM"], -3200)
        for label, old in before.items():
            now = self.rows(reports.MONTHLY)[[r[0] for r in self.rows(reports.MONTHLY)].index(label)]
            for col in range(len(old)):
                if col in reports.AIO_COLS:
                    continue
                self.assertEqual(now[col], old[col], f"{label} column {col} was rewritten")
        # weekly AIO of weeks inside the imported months were filled in as well
        self.assertEqual(self.weekly("2026-W38")["AIO Impressions"], 700)
        # a third run changes nothing more
        again = self.run_reports()
        self.assertEqual(again.patched_cells, 0)

    def test_reimporting_a_month_replaces_it(self):
        self.add_month("2026-09", 100, {PAGES["en_a"]: 900})
        self.run_reports()
        raw_before = [list(r) for r in self.raw() if r[0] != "2026-09"]
        self.add_tab("AIO 2026-09 Dates", [["Date", "Impressions"]] + month_dates("2026-09", 150))
        self.run_reports()
        sep_dates = [r for r in self.raw() if r[:2] == ["2026-09", "date"]]
        self.assertEqual(len(sep_dates), 30)                                       # not 60
        self.assertEqual({r[3] for r in sep_dates}, {150})
        # the pages of that month were not part of the second import and stay
        self.assertEqual({r[2]: r[3] for r in self.raw() if r[:2] == ["2026-09", "page"]},
                         {PAGES["en_a"]: 900})
        self.assertEqual([r for r in self.raw() if r[0] != "2026-09"], raw_before)
        self.assertEqual(self.monthly("2026-09")["AIO Impressions"], 4500)
        self.assertNotIn("AIO 2026-09 Dates", self.tab_titles())

    def test_other_months_are_untouched_by_an_import(self):
        self.add_month("2026-08", 200, {PAGES["en_a"]: 100})
        self.run_reports()
        aug = [list(r) for r in self.raw()]
        self.add_month("2026-09", 100, {PAGES["en_a"]: 100})
        self.run_reports()
        self.assertEqual([r for r in self.raw() if r[0] == "2026-08"], aug)


class ProblemTests(AioTestCase):

    def test_a_tab_name_that_does_not_parse_is_reported_and_left_alone(self):
        tab = self.add_tab("AIO Sept Pages", [["Top pages", "Impressions"], ["https://x.test/a", "5"]])
        before = [list(r) for r in tab.data]
        self.run_reports()
        self.assertIn("AIO Sept Pages", self.tab_titles())
        self.assertEqual(self.sheets.books[RID]["AIO Sept Pages"].data, before)
        alarms = [r for r in self.rows(reports.ALARMS) if r[2] == "AIO import"]
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0][0], "2026-10")                    # the month of the run
        self.assertIn("AIO Sept Pages", alarms[0][3])
        self.assertIn("AIO 2026-09 Pages", alarms[0][3])
        self.assertTrue(any("could not be imported" in l for l in self.logs))
        self.assertEqual(self.raw(), [])

    def test_the_same_problem_is_not_reported_twice(self):
        self.add_tab("AIO Sept Pages", [["Top pages", "Impressions"], ["https://x.test/a", "5"]])
        self.run_reports()
        self.run_reports()
        self.assertEqual(len([r for r in self.rows(reports.ALARMS) if r[2] == "AIO import"]), 1)

    def test_month_13_is_a_bad_name(self):
        self.add_tab("AIO 2026-13 Pages", [["Top pages", "Impressions"], ["https://x.test/a", "5"]])
        self.run_reports()
        self.assertIn("AIO 2026-13 Pages", self.tab_titles())
        self.assertTrue([r for r in self.rows(reports.ALARMS) if r[2] == "AIO import"])

    def test_unreadable_content_is_reported_with_the_month_and_left_alone(self):
        data = [["Date", "Alpha", "Beta"], ["2026-09-01", "3", "99"]]
        self.add_tab("AIO 2026-09 Dates", data)
        self.add_tab("AIO 2026-09 Pages", [["Top pages", "Impressions"], ["https://x.test/a", "5"]])
        self.run_reports()
        self.assertIn("AIO 2026-09 Dates", self.tab_titles())             # untouched
        self.assertNotIn("AIO 2026-09 Pages", self.tab_titles())          # the good tab still imported
        alarm = [r for r in self.rows(reports.ALARMS) if r[2] == "AIO import"]
        self.assertEqual(len(alarm), 1)
        self.assertEqual(alarm[0][0], "2026-09")
        self.assertIn("impressions", alarm[0][3])
        self.assertEqual([r[1] for r in self.raw()], ["page"])

    def test_raw_and_other_tabs_are_not_import_tabs(self):
        self.add_tab("AIO Raw notes Pages-ish", [["x"]])
        self.add_tab("Aio 2026-09 pages", [["x"]])
        self.run_reports()
        self.assertEqual([r for r in self.rows(reports.ALARMS) if r[2] == "AIO import"], [])


class LocalizedImportTests(AioTestCase):

    def test_persian_export_end_to_end(self):
        self.add_tab("AIO 2026-09 Dates", [["تاریخ", "نمایش"]] + [
            [d.replace("0", "۰").replace("1", "۱").replace("2", "۲").replace("3", "۳")
             .replace("4", "۴").replace("5", "۵").replace("6", "۶").replace("7", "۷")
             .replace("8", "۸").replace("9", "۹"), "۱۰۰"] for d, _ in month_dates("2026-09", 0)])
        self.add_tab("AIO 2026-09 Pages", [["صفحه", "نمایش"], [PAGES["en_a"], "۱٬۲۰۰"]])
        self.run_reports()
        self.assertEqual(self.monthly("2026-09")["AIO Impressions"], 3000)
        self.assertIn(["2026-09", "page", PAGES["en_a"], 1200], self.raw())


class AioPagesTests(AioTestCase):

    def test_growing_and_declining_aio_pages(self):
        a, b, c = "https://www.alpha.test/a", "https://www.alpha.test/b", "https://www.alpha.test/c"
        self.add_month("2026-08", 10, {a: 100, b: 50})
        self.add_month("2026-09", 10, {a: 150, b: 10, c: 30})
        self.run_reports()
        rows = [r for r in self.rows(reports.AIO_PAGES) if r[0] == "2026-09"]
        self.assertEqual(rows, [["2026-09", "Growing", a, 150, 100, 50],
                                ["2026-09", "Growing", c, 30, 0, 30],
                                ["2026-09", "Declining", b, 10, 50, -40]])
        self.assertEqual([r for r in self.rows(reports.AIO_PAGES) if r[0] == "2026-08"], [])

    def test_unchanged_data_does_not_rewrite_the_tab(self):
        self.add_month("2026-08", 10, {"https://www.alpha.test/a": 100})
        self.add_month("2026-09", 10, {"https://www.alpha.test/a": 150})
        self.run_reports()
        before = [list(r) for r in self.tab(reports.AIO_PAGES)]
        self.run_reports()
        self.assertEqual(self.tab(reports.AIO_PAGES), before)

    def test_reimport_updates_the_rows_of_that_month(self):
        a = "https://www.alpha.test/a"
        self.add_month("2026-08", 10, {a: 100})
        self.add_month("2026-09", 10, {a: 150})
        self.run_reports()
        self.add_tab("AIO 2026-09 Pages", [["Top pages", "Impressions"], [a, "400"]])
        self.run_reports()
        rows = [r for r in self.rows(reports.AIO_PAGES) if r[0] == "2026-09"]
        self.assertEqual(rows, [["2026-09", "Growing", a, 400, 100, 300]])

    def test_the_month_after_an_import_is_recomputed_too(self):
        a = "https://www.alpha.test/a"
        self.add_month("2026-08", 10, {a: 100})
        self.add_month("2026-09", 10, {a: 150})
        self.run_reports()
        self.add_tab("AIO 2026-08 Pages", [["Top pages", "Impressions"], [a, "50"]])
        self.run_reports()
        sep = [r for r in self.rows(reports.AIO_PAGES) if r[0] == "2026-09"]
        self.assertEqual(sep, [["2026-09", "Growing", a, 150, 50, 100]])


if __name__ == "__main__":
    unittest.main()
