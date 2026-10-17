"""
gsc_pulse.main() with the report phase: order of phases, "reports: off", failure
isolation and scrubbing, using the in-memory fakes.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

import gsc_pulse
import reports
import sites
from test_history import FakeSheets, Req, http_error, patch_time
from test_reports import FakeSA, build_records

A = {"name": "site-a", "site_url": "sc-domain:alpha.test", "sheet_id": "A-MAIN-ID",
     "history_sheet_id": "A-HIST-ID", "reports_sheet_id": "A-REPORTS-ID",
     "page_types": [{"name": "English", "regex": "/secret-en-section/"},
                    {"name": "Article", "regex": "/article/"}]}
B = {"name": "site-b", "site_url": "sc-domain:beta.test", "sheet_id": "B-MAIN-ID",
     "history_sheet_id": "B-HIST-ID"}
ENV_KEYS = ("SITES_JSON", "SITE_URL", "SHEET_ID", "HISTORY_SHEET_ID", "DAYS", "GITHUB_ACTIONS",
            "DISCOVER_SITE", "REBUILD_VIEWS", "REBUILD_ALARMS", "REBUILD_DECKS")


class ReportsMainTests(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        today = date.today()
        self.sa = FakeSA(build_records(start=today - timedelta(days=520), end=today))
        self.sheets = FakeSheets()
        for book in ("A-MAIN-ID", "B-MAIN-ID", "A-REPORTS-ID"):
            self.sheets.add_book(book)

    def run_main(self, entries, extra_env=None):
        clean = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        clean.update({"SITES_JSON": json.dumps(entries), "GITHUB_ACTIONS": "true", **(extra_env or {})})
        out, error = io.StringIO(), None
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(sys, "argv", ["gsc_pulse.py", "--config", "none.yaml"]), \
             mock.patch.object(gsc_pulse, "build",
                               lambda n, v, credentials=None: self.sa if n == "webmasters" else self.sheets), \
             mock.patch.object(gsc_pulse, "get_credentials", lambda f: object()), \
             mock.patch.object(gsc_pulse, "PUBLIC_LOGS", True), \
             mock.patch.object(gsc_pulse, "_SENSITIVE", []), \
             contextlib.redirect_stdout(out):
            try:
                gsc_pulse.main()
            except (Exception, SystemExit) as e:        # noqa: BLE001
                error = e
        return out.getvalue(), error

    def test_order_is_v1_tabs_then_reports_then_history(self):
        out, err = self.run_main([A, B])
        self.assertIsNone(err)
        v1_last = out.index("site-b · Search Console tabs")
        reports_pos = out.index("site-a · Reports")
        history_pos = out.index("site-a · History")
        self.assertLess(v1_last, reports_pos)
        self.assertLess(reports_pos, history_pos)
        self.assertLess(out.index("site-b · Reports"), history_pos)      # all reports before any history

    def test_reports_are_written_and_a_site_without_reports_sheet_says_off(self):
        out, err = self.run_main([A, B])
        self.assertIsNone(err)
        self.assertIn("reports: off", out)
        book = self.sheets.books["A-REPORTS-ID"]
        self.assertGreater(len(book[reports.MONTHLY].data), 1)
        self.assertGreater(len(book[reports.WEEKLY].data), 1)
        self.assertEqual({r[1] for r in book[reports.MONTHLY].data[1:]}, {"All", "English", "Article", "Other"})
        self.assertNotIn("B-REPORTS-ID", self.sheets.books)

    def test_summary_line_mentions_reports(self):
        out, _ = self.run_main([A, B])
        summary = out[out.index("Summary"):]
        a = [l for l in summary.splitlines() if l.strip().startswith("site-a")][0]
        b = [l for l in summary.splitlines() if l.strip().startswith("site-b")][0]
        self.assertIn("reports: +", a)
        self.assertTrue(b.rstrip().endswith("reports: off"))

    def test_views_are_built_after_the_reports_and_before_history(self):
        out, err = self.run_main([A, B])
        self.assertIsNone(err)
        self.assertLess(out.index("site-a · Reports"), out.index("site-a · Views"))
        self.assertLess(out.index("site-a · Views"), out.index("site-a · History"))
        book = self.sheets.books["A-REPORTS-ID"]
        self.assertEqual(list(book)[:3], ["Dashboard", "Monthly", "Monthly · English"])
        summary = out[out.index("Summary"):]
        a = [l for l in summary.splitlines() if l.strip().startswith("site-a")][0]
        self.assertIn("views: built", a)

    def test_a_second_run_leaves_the_views_alone_and_rebuild_views_rebuilds_them(self):
        self.run_main([A, B])
        built = {n: t.sheet_id for n, t in self.sheets.books["A-REPORTS-ID"].items()}
        out, err = self.run_main([A, B])
        self.assertIsNone(err)
        self.assertEqual({n: t.sheet_id for n, t in self.sheets.books["A-REPORTS-ID"].items()}, built)
        a = [l for l in out[out.index("Summary"):].splitlines() if l.strip().startswith("site-a")][0]
        self.assertIn("views: ok", a)
        out, err = self.run_main([A, B], {"REBUILD_VIEWS": "true"})
        self.assertIsNone(err)
        self.assertNotEqual(self.sheets.books["A-REPORTS-ID"]["Dashboard"].sheet_id, built["Dashboard"])
        a = [l for l in out[out.index("Summary"):].splitlines() if l.strip().startswith("site-a")][0]
        self.assertIn("views: built", a)

    def test_a_broken_view_fails_the_site_without_leaking_anything(self):
        self.sheets.error_cells[("Dashboard", "B7")] = "#REF!"
        out, err = self.run_main([A, B])
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("site-a: failed (", out)
        self.assertIn("Dashboard!B7", out)
        self.assertNotIn("A-REPORTS-ID", out)
        self.assertNotIn("English", out)
        self.assertIn("History: on", out)

    def test_nothing_secret_reaches_the_log(self):
        out, _ = self.run_main([A, B])
        for secret in ("/secret-en-section/", "A-REPORTS-ID", "A-MAIN-ID", "A-HIST-ID", "alpha.test",
                       "English", "Article"):
            self.assertNotIn(secret, out)

    def test_failing_reports_do_not_block_history_or_other_sites(self):
        real_get = self.sheets.get

        def get(spreadsheetId, fields=None):
            if spreadsheetId == "A-REPORTS-ID":
                def refuse():
                    raise http_error(403, "The caller does not have permission for A-REPORTS-ID")
                return Req(refuse)
            return real_get(spreadsheetId, fields)

        with mock.patch.object(self.sheets, "get", get):
            out, err = self.run_main([A, B])
        self.assertIsInstance(err, gsc_pulse.GscPulseError)               # the run ends red
        self.assertIn("site-a: failed (", out)
        self.assertNotIn("A-REPORTS-ID", out)
        self.assertIn("History: on", out)
        self.assertIn("State", self.sheets.books["A-HIST-ID"])           # history of the same site still ran
        self.assertIn("State", self.sheets.books["B-HIST-ID"])           # and the other site is fine
        self.assertIn("site-b: ok", out)

    def test_page_types_are_validated_without_echoing_the_rules(self):
        bad = dict(A, page_types=[{"name": "English", "regex": "([unclosed-secret"}])
        with self.assertRaises(sites.SitesConfigError) as cm:
            sites.parse_sites_json(json.dumps([bad]))
        self.assertNotIn("unclosed-secret", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
