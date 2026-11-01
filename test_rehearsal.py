"""Tests for the rehearsal on a copy of a real dashboard (the fake Persian site stands in for the real one)."""

import unittest
from datetime import date

import content
import content_check as cc
import content_create as ccr
import content_sheet as cs
import fake_site as fs
import gdocs
import rehearsal as rh
import sites
import tabs
from test_content_create import FakeAI
from test_history import FakeSheets, patch_time
from test_own_access import FakeDrive

TODAY = date(2026, 11, 2)
LINES = ["# راهنمای نوشتن", "", "اول پاسخ مستقیم، بعد توضیح.", "", "- یک عنوان اصلی", "- جدول برای هر مقایسه"]


class RehearsalTests(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.sheets = FakeSheets()
        self.drive = FakeDrive(self.sheets)
        self.world = fs.build(self.drive, self.sheets, lambda s: None)
        w = self.world.sites["fake-fa"]
        self.source = w.dashboard_id
        self.site = sites.Site(
            name="rehearsal-test", site_url="https://fa.fake-site.test/",
            content=sites.ContentConfig(enabled=True, sheet_id=self.source, language="fa", extra_rules="Be kind.",
                                        tabs=[sites.ContentTab("Articles", r"^https://[^/]+/article/.+")]),
            deck=sites.DeckConfig(site_label="Fake Persian Site"))
        self.page = lambda url: (200, b"<html><body><h1>Old</h1><p>Old text.</p></body></html>")

    def source_rows(self):
        return cs.read_all(self.sheets, self.source, "Articles")

    def write_sop(self, ids, lines=LINES):
        rows = [["sop", ln] for ln in lines]
        old = len(tabs.read_rows(self.sheets, ids["inputs"], rh.INPUTS_TAB, "B"))
        tabs.patch_cells(self.sheets, ids["inputs"], [(f"'{rh.INPUTS_TAB}'!A{old + 2}:B{old + 1 + len(rows)}", rows)])

    def copy(self, ids):
        return cs.table_of(cs.read_all(self.sheets, ids["dashboard"], "Articles"))[1]

    def test_prepare_copies_the_rows_and_leaves_the_real_file_alone(self):
        before = self.source_rows()
        logs = []
        ids = rh.prepare(self.drive, self.sheets, self.site, logs.append)
        self.assertEqual(self.source_rows(), before)                              # only read
        rows = self.copy(ids)
        marked = [r for r in rows if str(r["To Do"]).lower() == "brief"]
        self.assertEqual(len(marked), 10)
        self.assertTrue(all(r["Primary Keyword"] and r["Intent"] for r in marked))      # whole rows
        pages = [r for r in rows if str(r["To Do"]).lower() != "brief"]
        self.assertTrue(pages and all(r["Published URL"] and not r["Intent"] and not r["Clicks (90d)"] for r in pages))
        self.assertIn("10 marked Brief", logs[0])
        for secret in ("fake-site", "Persian"):                                    # counts only
            self.assertNotIn(secret, logs[0])
        inputs = {r[0]: r[1] if len(r) > 1 else "" for r in tabs.read_rows(self.sheets, ids["inputs"], rh.INPUTS_TAB, "B")}
        self.assertEqual((inputs["language"], inputs["domain"], inputs["extra_rules"]),
                         ("fa", "https://fa.fake-site.test", "Be kind."))

    def test_prepare_twice_keeps_one_copy_and_the_sop_lines(self):
        ids = rh.prepare(self.drive, self.sheets, self.site, lambda s: None)
        self.write_sop(ids)
        again = rh.prepare(self.drive, self.sheets, self.site, lambda s: None)
        self.assertEqual(ids["dashboard"], again["dashboard"])
        self.assertEqual(len(self.copy(again)), len(self.copy(ids)))
        self.assertEqual(rh.read_inputs(self.drive, self.sheets)[2].count("جدول"), 1)

    def test_a_full_rehearsal(self):
        ids = rh.prepare(self.drive, self.sheets, self.site, lambda s: None)
        self.write_sop(ids)
        ai = FakeAI()
        total, report = rh.run(self.drive, self.sheets, ai, TODAY, http_get=self.page, log=lambda s: None)
        self.assertTrue(report.ok, report.text())
        self.assertEqual((len(total.briefs), len(total.drafts), total.stopped, total.problems), (10, 10, "", []))
        self.assertEqual((ai.steps.count("BRIEF"), ai.steps.count("DRAFT"), ai.steps.count("EDIT")), (10, 10, 10))
        rows = self.copy(ids)
        done = [r for r in rows if r["Status"] == "Drafted"]
        self.assertEqual(len(done), 10)
        self.assertTrue(all(r["To Do"] == "Review" and len(gdocs.doc_ids_in(r["Attachments"])) == 2 for r in done))
        self.assertEqual(len(gdocs.docs_in(self.drive, ids["briefs"])), 10)
        self.assertEqual(len(gdocs.docs_in(self.drive, ids["drafts"])), 10)
        prompt = next(p for p in ai.prompts if p.startswith("STEP: BRIEF"))
        self.assertIn("اول پاسخ مستقیم", prompt)                                  # the SOP text reached the prompt
        self.assertEqual(self.source_rows(), cs.read_all(self.sheets, self.source, "Articles"))

    def test_briefs_only_then_drafts_later(self):
        ids = rh.prepare(self.drive, self.sheets, self.site, lambda s: None)
        self.write_sop(ids)
        total, report = rh.run(self.drive, self.sheets, FakeAI(), TODAY, mode="briefs", http_get=self.page, log=lambda s: None)
        self.assertEqual((len(total.briefs), len(total.drafts)), (10, 0))
        self.assertTrue(report.ok, report.text())
        self.assertTrue(all(r["Status"] == "Briefed" for r in self.copy(ids) if r["To Do"] in ("Update", "Write")))

    def test_a_prepared_copy_is_made_again_clean(self):
        ids = rh.prepare(self.drive, self.sheets, self.site, lambda s: None)
        self.write_sop(ids)
        rh.run(self.drive, self.sheets, FakeAI(), TODAY, mode="briefs", http_get=self.page, log=lambda s: None)
        self.assertEqual(len(gdocs.docs_in(self.drive, ids["briefs"])), 10)
        logs = []
        rh.prepare(self.drive, self.sheets, self.site, logs.append)
        self.assertEqual(gdocs.docs_in(self.drive, ids["briefs"]), {})
        self.assertIn("10 earlier documents trashed", logs[0])
        self.assertTrue(all(str(r["To Do"]).lower() != "briefed" for r in self.copy(ids)))

    def test_problems_are_said_plainly(self):
        with self.assertRaises(rh.RehearsalError):
            rh.run(self.drive, self.sheets, FakeAI(), TODAY, log=lambda s: None)          # not prepared
        rh.prepare(self.drive, self.sheets, self.site, lambda s: None)
        with self.assertRaises(rh.RehearsalError):
            rh.run(self.drive, self.sheets, FakeAI(), TODAY, log=lambda s: None)          # no SOP lines
        self.site.content.sheet_id = ""
        with self.assertRaises(Exception):
            rh.source_dashboard_id(self.drive, self.sheets, self.site)                    # the site has no dashboard yet

    def test_copy_rows(self):
        header = content.HEADERS
        rows = [{"Primary Keyword": "a", "To Do": "Brief", "Status": "Published", "Published URL": "u1", "Intent": "x"},
                {"Primary Keyword": "b", "To Do": "Brief", "Status": "Briefed", "Published URL": "u2", "Intent": "x"},
                {"Primary Keyword": "c", "To Do": "Keep", "Status": "Published", "Published URL": "u3", "Intent": "x"},
                {"Primary Keyword": "d", "To Do": "Write", "Status": "Inboxed", "Published URL": "", "Intent": "x"}]
        out, marked = rh.copy_rows(header, rows)
        self.assertEqual(marked, 1)
        self.assertEqual([r["Primary Keyword"] for r in out], ["a", "b", "c"])
        self.assertEqual([r["Intent"] for r in out], ["x", "", ""])


if __name__ == "__main__":
    unittest.main()
