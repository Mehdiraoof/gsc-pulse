"""
Tests for the period logic: calendar months, ISO weeks, and "complete periods only".
"""

import unittest
from datetime import date, timedelta

import periods

EARLIEST = date(2025, 6, 17)       # a Tuesday


class MonthTests(unittest.TestCase):

    def test_labels_and_arithmetic(self):
        self.assertEqual(periods.month_label(date(2026, 9, 30)), "2026-09")
        self.assertEqual(periods.add_months(date(2026, 1, 1), -1), date(2025, 12, 1))
        self.assertEqual(periods.add_months(date(2026, 11, 1), 3), date(2027, 2, 1))
        self.assertEqual(periods.add_months(date(2026, 3, 1), -12), date(2025, 3, 1))
        self.assertEqual(periods.month_end(date(2024, 2, 1)), date(2024, 2, 29))
        self.assertEqual(periods.parse_month("2026-09"), date(2026, 9, 1))
        for bad in ("2026-9", "26-09", "september", "2026"):
            with self.assertRaises(ValueError):
                periods.parse_month(bad)

    def test_last_complete_month_depends_on_the_last_final_date(self):
        first_data = date(2025, 1, 1)
        for final, last_label in ((date(2026, 9, 29), "2026-08"), (date(2026, 9, 30), "2026-09"),
                                  (date(2026, 10, 1), "2026-09"), (date(2026, 10, 14), "2026-09")):
            months = periods.complete_months(EARLIEST, final, first_data)
            self.assertEqual(months[-1][0], last_label, final)

    def test_a_partial_month_is_never_reported(self):
        months = periods.complete_months(EARLIEST, date(2026, 10, 14), date(2025, 1, 1))
        self.assertNotIn("2026-10", [m[0] for m in months])
        for label, first, last in months:
            self.assertEqual(first.day, 1)
            self.assertEqual(last + timedelta(days=1), periods.add_months(first, 1))

    def test_months_start_at_the_first_whole_month_search_console_still_has(self):
        months = periods.complete_months(EARLIEST, date(2026, 10, 14), date(2025, 1, 1))
        self.assertEqual(months[0][0], "2025-07")          # June 2025 is only partly available
        self.assertEqual(len(months), 15)
        self.assertEqual([m[0] for m in months], sorted(m[0] for m in months))

    def test_earliest_on_the_first_of_a_month_keeps_that_month(self):
        months = periods.complete_months(date(2025, 7, 1), date(2025, 9, 30), date(2025, 1, 1))
        self.assertEqual([m[0] for m in months], ["2025-07", "2025-08", "2025-09"])

    def test_a_young_site_skips_its_partly_empty_first_month(self):
        months = periods.complete_months(EARLIEST, date(2026, 10, 14), date(2026, 3, 12))
        self.assertEqual(months[0][0], "2026-04")
        months = periods.complete_months(EARLIEST, date(2026, 10, 14), date(2026, 3, 1))
        self.assertEqual(months[0][0], "2026-03")

    def test_nothing_complete_yet(self):
        self.assertEqual(periods.complete_months(EARLIEST, date(2025, 6, 30), date(2025, 6, 17)), [])


class WeekTests(unittest.TestCase):

    def test_labels_are_iso_weeks(self):
        self.assertEqual(periods.week_label(date(2026, 9, 14)), "2026-W38")
        self.assertEqual(periods.week_label(date(2026, 1, 1)), "2026-W01")
        self.assertEqual(periods.week_label(date(2027, 1, 1)), "2026-W53")      # still 2026's week 53
        self.assertEqual(periods.week_label(date(2024, 12, 30)), "2025-W01")    # Monday of ISO 2025-W01

    def test_weeks_run_monday_to_sunday_and_end_on_or_before_the_final_date(self):
        final = date(2026, 10, 14)                                              # a Wednesday
        weeks = periods.complete_weeks(EARLIEST, final, date(2025, 1, 1))
        for label, monday, sunday in weeks:
            self.assertEqual(monday.weekday(), 0)
            self.assertEqual(sunday, monday + timedelta(days=6))
            self.assertLessEqual(sunday, final)
            self.assertGreaterEqual(monday, EARLIEST)
        self.assertEqual(weeks[-1][2], date(2026, 10, 11))
        self.assertGreater(weeks[-1][2] + timedelta(days=7), final)             # next one is partial
        self.assertEqual(weeks[0][1], date(2025, 6, 23))                        # first Monday >= earliest

    def test_week_is_included_on_its_last_day(self):
        weeks = periods.complete_weeks(EARLIEST, date(2026, 10, 11), date(2025, 1, 1))
        self.assertEqual(weeks[-1][2], date(2026, 10, 11))
        weeks = periods.complete_weeks(EARLIEST, date(2026, 10, 10), date(2025, 1, 1))
        self.assertEqual(weeks[-1][2], date(2026, 10, 4))

    def test_consecutive_weeks_have_no_gaps(self):
        weeks = periods.complete_weeks(EARLIEST, date(2026, 10, 14), date(2025, 1, 1))
        for (_, _, sunday), (_, monday, _) in zip(weeks, weeks[1:]):
            self.assertEqual(monday, sunday + timedelta(days=1))

    def test_same_week_last_year(self):
        self.assertEqual(periods.same_week_last_year(date(2026, 9, 14)), date(2025, 9, 15))
        # ISO week 53 of 2026 has no twin in 2025
        self.assertIsNone(periods.same_week_last_year(date(2026, 12, 28)))


if __name__ == "__main__":
    unittest.main()
