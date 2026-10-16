"""
gsc_pulse.main() with several sites, using the in-memory fakes of test_history.
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
import history
from test_history import FakeGsc, FakeSheets, Clock, Req, http_error, patch_time

A = {"name": "site-a", "site_url": "sc-domain:alpha.test",
     "sheet_id": "A-MAIN-ID", "history_sheet_id": "A-HIST-ID"}
B = {"name": "site-b", "site_url": "https://www.beta.test/",
     "sheet_id": "B-MAIN-ID", "history_sheet_id": "B-HIST-ID"}
C = {"name": "site-c", "site_url": "sc-domain:gamma.test", "enabled": False,
     "sheet_id": "C-MAIN-ID", "history_sheet_id": "C-HIST-ID"}

ALL_SECRETS = [v for s in (A, B, C) for k, v in s.items() if k.endswith(("_url", "_id"))]
ENV_KEYS = ("SITES_JSON", "SITE_URL", "SHEET_ID", "HISTORY_SHEET_ID", "DAYS", "GITHUB_ACTIONS")


class SiteAwareGsc(FakeGsc):
    """Like FakeGsc, but can refuse some properties (the service account has no access)."""

    def __init__(self, *a, deny=(), **k):
        super().__init__(*a, **k)
        self.deny, self.site_urls = set(deny), []

    def query(self, siteUrl, body):
        self.site_urls.append(siteUrl)
        if siteUrl in self.deny:
            def refuse():
                raise http_error(403, f"User does not have sufficient permission for {siteUrl}")
            return Req(refuse)
        return super().query(siteUrl, body)


class MultiSiteTests(unittest.TestCase):

    def setUp(self):
        self.time = patch_time(self)
        self.sheets = FakeSheets()
        for book in ("A-MAIN-ID", "B-MAIN-ID"):
            self.sheets.add_book(book)
        today = date.today()
        self.gsc = SiteAwareGsc(first=today - timedelta(days=500), final=today - timedelta(days=3),
                                per_day=2)

    def run_main(self, env, args=(), gsc=None):
        gsc = gsc or self.gsc

        def fake_build(name, version, credentials=None):
            return gsc if name == "webmasters" else self.sheets

        clean = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        clean.update({"GITHUB_ACTIONS": "true", **env})
        out = io.StringIO()
        error = None
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(sys, "argv", ["gsc_pulse.py", "--config", "no-such-file.yaml", *args]), \
             mock.patch.object(gsc_pulse, "build", fake_build), \
             mock.patch.object(gsc_pulse, "get_credentials", lambda f: object()), \
             mock.patch.object(gsc_pulse, "PUBLIC_LOGS", True), \
             mock.patch.object(gsc_pulse, "_SENSITIVE", []), \
             contextlib.redirect_stdout(out):
            try:
                gsc_pulse.main()
            except (Exception, SystemExit) as e:    # noqa: BLE001
                error = e
        return out.getvalue(), error

    def meta(self, book):
        return dict(self.sheets.books[book]["Meta"].data)

    def state(self, book):
        return {r[0]: (r[1], int(r[2])) for r in self.sheets.books[book]["State"].data[1:]}

    def assert_site_synced(self, main_id, hist_id, site_url):
        self.assertGreater(len(self.sheets.books[main_id]["Queries"].data), 1)
        self.assertEqual(self.meta(main_id)["Property"], site_url)
        self.assertTrue(self.meta(main_id)["History status"].startswith("up to date"))
        self.assertEqual({t for t, _ in history.HISTORY_TABS}, set(self.state(hist_id)))

    # ---- two sites --------------------------------------------------------
    def test_two_sites_each_write_to_their_own_sheets(self):
        out, err = self.run_main({"SITES_JSON": json.dumps([A, B])})

        self.assertIsNone(err)
        self.assert_site_synced("A-MAIN-ID", "A-HIST-ID", A["site_url"])
        self.assert_site_synced("B-MAIN-ID", "B-HIST-ID", B["site_url"])
        self.assertEqual(set(self.sheets.books), {"A-MAIN-ID", "B-MAIN-ID", "A-HIST-ID", "B-HIST-ID"})
        # the sites' histories hold different data: nothing is shared
        self.assertNotEqual(self.state("A-HIST-ID"), {})
        self.assertIn("site-a: ok", out)
        self.assertIn("site-b: ok", out)
        self.assertGreater(self.gsc.site_urls.count(A["site_url"]), 0)

    def test_v1_tabs_of_every_site_come_before_any_history(self):
        out, err = self.run_main({"SITES_JSON": json.dumps([A, B])})
        self.assertIsNone(err)
        last_v1 = out.index("site-b · Search Console tabs")
        first_history = out.index("site-a · History")
        self.assertLess(last_v1, first_history)

    def test_all_ids_and_urls_are_hidden_in_public_logs(self):
        out, err = self.run_main({"SITES_JSON": json.dumps([A, B, C])})
        self.assertIsNone(err)
        for secret in ALL_SECRETS + ["alpha.test", "beta.test", "gamma.test"]:
            self.assertNotIn(secret, out)

    def test_summary_has_a_line_per_site_without_ids(self):
        out, _ = self.run_main({"SITES_JSON": json.dumps([A, B, C])})
        summary = out[out.index("Summary"):]
        lines = [l.strip() for l in summary.splitlines() if l.strip().startswith("site-")]
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("site-a: ok | rows written: v1 "))
        self.assertIn("history: up to date", lines[0])
        self.assertEqual(lines[2], "site-c: disabled")

    # ---- enabled: false ---------------------------------------------------
    def test_disabled_site_is_skipped_and_logged(self):
        out, err = self.run_main({"SITES_JSON": json.dumps([A, C])})
        self.assertIsNone(err)
        self.assertIn("site-c: disabled", out)
        self.assertNotIn("site-c: failed", out)
        self.assertNotIn("C-MAIN-ID", self.sheets.books)
        self.assertNotIn("C-HIST-ID", self.sheets.books)
        self.assertNotIn(C["site_url"], self.gsc.site_urls)
        self.assert_site_synced("A-MAIN-ID", "A-HIST-ID", A["site_url"])

    # ---- failures are isolated --------------------------------------------
    def test_failing_site_does_not_block_the_others(self):
        gsc = SiteAwareGsc(first=self.gsc.first, final=self.gsc.final, per_day=2,
                           deny={A["site_url"]})
        out, err = self.run_main({"SITES_JSON": json.dumps([A, B])}, gsc=gsc)

        self.assertIsInstance(err, gsc_pulse.GscPulseError)          # exit code is non-zero
        self.assertIn("site-a", str(err))
        self.assertIn("site-a: failed (", out)
        self.assertIn("403", out)
        self.assertNotIn("alpha.test", out)                          # the reason is scrubbed
        self.assert_site_synced("B-MAIN-ID", "B-HIST-ID", B["site_url"])   # b still synced
        self.assertNotIn("A-HIST-ID", self.sheets.books)             # a: nothing written
        self.assertIn("site-b: ok", out)

    def test_failing_history_of_one_site_does_not_block_the_next(self):
        real_get = self.sheets.get

        def get(spreadsheetId, fields=None):
            if spreadsheetId == "A-HIST-ID":
                def refuse():
                    raise http_error(403, "The caller does not have permission for A-HIST-ID")
                return Req(refuse)
            return real_get(spreadsheetId, fields)

        with mock.patch.object(self.sheets, "get", get):
            out, err = self.run_main({"SITES_JSON": json.dumps([A, B])})

        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("site-a: failed (", out)
        self.assertNotIn("A-HIST-ID", out)
        self.assertEqual(self.meta("A-MAIN-ID")["History status"], "error (see run log)")
        self.assertGreater(len(self.sheets.books["A-MAIN-ID"]["Queries"].data), 1)
        self.assert_site_synced("B-MAIN-ID", "B-HIST-ID", B["site_url"])

    # ---- config problems --------------------------------------------------
    def test_bad_json_fails_clearly_without_printing_the_secret(self):
        out, err = self.run_main({"SITES_JSON": '[{"name": "site-a", "site_url": "TOP-SECRET-VALUE",'})
        self.assertIsInstance(err, SystemExit)
        self.assertEqual(err.code, 1)
        self.assertIn("SITES_JSON is not valid JSON", out)
        self.assertNotIn("TOP-SECRET-VALUE", out)
        self.assertEqual(set(self.sheets.books), {"A-MAIN-ID", "B-MAIN-ID"})

    def test_sites_json_replaces_the_legacy_secrets(self):
        env = {"SITES_JSON": json.dumps([A]), "SITE_URL": "sc-domain:old.test",
               "SHEET_ID": "OLD-MAIN-ID", "HISTORY_SHEET_ID": "OLD-HIST-ID"}
        out, err = self.run_main(env)
        self.assertIsNone(err)
        self.assertNotIn("OLD-MAIN-ID", self.sheets.books)
        self.assertNotIn("OLD-HIST-ID", self.sheets.books)

    def test_legacy_secrets_still_work_without_sites_json(self):
        self.sheets.add_book("OLD-MAIN-ID")
        env = {"SITE_URL": "sc-domain:old.test", "SHEET_ID": "OLD-MAIN-ID",
               "HISTORY_SHEET_ID": "OLD-HIST-ID"}
        out, err = self.run_main(env)
        self.assertIsNone(err)
        self.assertIn("site-1: ok", out)
        self.assertEqual(self.meta("OLD-MAIN-ID")["Property"], "sc-domain:old.test")
        self.assertIn("OLD-HIST-ID", self.sheets.books)
        self.assertNotIn("old.test", out)

    # ---- one time budget for all sites ------------------------------------
    def test_history_time_budget_is_shared_across_sites(self):
        with mock.patch.object(history, "TIME_BUDGET_S", 12), \
             mock.patch.object(gsc_pulse.time, "monotonic", Clock(1.0)):
            out, err = self.run_main({"SITES_JSON": json.dumps([A, B])})

        self.assertIsNone(err)                                    # pausing is not a failure
        # v1 tabs of both sites are complete even though the budget is gone
        for book in ("A-MAIN-ID", "B-MAIN-ID"):
            self.assertGreater(len(self.sheets.books[book]["Queries"].data), 1)
            self.assertIn("backfilling", self.meta(book)["History status"])
        # site-a used the budget; site-b found it empty and stored nothing yet
        self.assertGreater(len(self.sheets.books["A-HIST-ID"]["H Daily"].data), 1)
        self.assertEqual(self.sheets.books["B-HIST-ID"]["H Daily"].data[1:], [])
        self.assertEqual(out.count("History: paused at"), 2)


if __name__ == "__main__":
    unittest.main()
