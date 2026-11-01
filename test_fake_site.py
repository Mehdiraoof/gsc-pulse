"""Tests for the fake test site (step 1): what is built in Drive, the awkward Persian cases it holds,
and that the checker finds every mistake in the sample documents. Drive and Sheets are in-memory."""

import json
import unittest

import content
import content_check as cc
import content_sheet as cs
import fake_site as fs
import provision
from test_history import FakeSheets, patch_time
from test_own_access import FakeDrive

ZWNJ, TATWEEL = "‌", "ـ"


class Base(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.sheets = FakeSheets()
        self.drive = FakeDrive(self.sheets)
        self.logs = []
        self.world = fs.build(self.drive, self.sheets, self.logs.append)

    def rows(self, name, tab="Articles"):
        return cs.table_of(cs.read_all(self.sheets, self.world.sites[name].dashboard_id, tab))[1]

    def folder_name(self, folder_id):
        return self.drive.files_[folder_id]["name"]


class WorldTests(Base):

    def test_everything_is_inside_the_test_folder(self):
        self.assertEqual(self.folder_name(self.world.root_id), "PulseO Test")
        self.assertEqual(self.drive.files_[self.world.root_id]["parents"], [])
        for w in self.world.sites.values():
            self.assertEqual(self.drive.files_[w.folder_id]["parents"], [self.world.root_id])
            self.assertEqual(self.drive.files_[w.dashboard_id]["parents"], [w.folder_id])
            self.assertEqual(self.drive.files_[w.briefs_id]["parents"], [w.folder_id])
            self.assertEqual(self.drive.files_[w.drafts_id]["parents"], [w.folder_id])
        self.assertEqual(self.drive.files_[self.world.index_id]["parents"], [self.world.root_id])
        self.assertEqual(self.drive.files_[self.world.samples_id]["parents"], [self.world.root_id])
        for fid, f in self.drive.files_.items():                               # nothing outside the folder tree
            if fid != self.world.root_id:
                self.assertTrue(f["parents"], f["name"])

    def test_three_sites_two_with_an_sop(self):
        self.assertEqual(list(self.world.sites), ["fake-fa", "fake-en", "fake-nosop"])
        fa, en, nosop = (self.world.sites[n] for n in ("fake-fa", "fake-en", "fake-nosop"))
        self.assertTrue(fa.sop_id and en.sop_id)
        self.assertEqual(nosop.sop_id, "")
        self.assertEqual([self.world.sites[n].site.language for n in self.world.sites], ["fa", "en", "fa"])
        self.assertEqual(self.world.settings("fake-nosop")["sop_doc_id"], "")
        self.assertEqual(self.world.settings("fake-fa")["language"], "fa")
        self.assertEqual(self.world.settings("fake-fa")["briefs_folder_id"], fa.briefs_id)

    def test_a_dashboard_has_the_layout_of_a_real_one(self):
        w = self.world.sites["fake-fa"]
        self.assertEqual(self.drive.files_[w.dashboard_id]["name"], "[Fake Persian Site] Content Dashboard")
        self.assertEqual(list(self.sheets.books[w.dashboard_id]),
                         ["Articles", "Landing Pages", "Guide", "Keyword Seeds", "Keyword Ideas", "_state"])
        for tab in ("Articles", "Landing Pages"):
            self.assertEqual(self.sheets.books[w.dashboard_id][tab].data[0], content.HEADERS)

    def test_the_briefs_that_exist_from_the_start_are_in_the_briefs_folder_and_linked(self):
        for name, w in self.world.sites.items():
            self.assertEqual(sorted(w.brief_ids), sorted(fs.brief_keywords(w.site)))
            in_folder = fs.docs_in(self.drive, w.briefs_id)
            self.assertEqual(sorted(in_folder), sorted(w.brief_ids.values()), name)
            by_kw = {r["Primary Keyword"]: r for r in self.rows(name)}
            for kw, doc_id in w.brief_ids.items():
                self.assertEqual(by_kw[kw]["Attachments"], fs.doc_link(doc_id), name)
        for w in self.world.sites.values():
            self.assertEqual(fs.docs_in(self.drive, w.drafts_id), {})                 # no draft yet

    def test_the_index_lists_every_site(self):
        rows = cs.table_of(cs.read_all(self.sheets, self.world.index_id, fs.INDEX_TAB))[1]
        self.assertEqual([r["name"] for r in rows], ["fake-fa", "fake-en", "fake-nosop"])
        w = self.world.sites["fake-en"]
        row = rows[1]
        self.assertEqual((row["dashboard_id"], row["sop_id"], row["briefs_id"], row["drafts_id"]),
                         (w.dashboard_id, w.sop_id, w.briefs_id, w.drafts_id))

    def test_the_log_names_no_id(self):
        text = "\n".join(self.logs)
        for fid in self.drive.files_:
            self.assertNotIn(fid, text)

    def test_nothing_is_a_real_site_and_no_document_names_the_tool(self):
        real = ("kalameh", "aprisham", "dubaitelescope", "dubai telescope")
        for fid, f in self.drive.files_.items():
            blob = (f["name"] + " " + f.get("content", b"").decode("utf-8")).lower()
            for word in real:
                self.assertNotIn(word, blob, f["name"])
            if "content" in f and self.world.samples_id not in f["parents"]:      # (a sample may break the rule on purpose)
                self.assertNotIn("pulseo", blob, f["name"])
        for book_id, book in self.sheets.books.items():
            text = json.dumps([t.data for t in book.values()], ensure_ascii=False).lower()
            for word in real:
                self.assertNotIn(word, text)
        for w in self.world.sites.values():
            self.assertTrue(w.site.domain.endswith(".test"))


class TrickyCaseTests(Base):

    def test_the_persian_rows_hold_the_awkward_cases(self):
        rows = self.rows("fake-fa")
        keywords = [r["Primary Keyword"] for r in rows]
        joined = "\n".join(keywords)
        self.assertIn("ك", joined)                                          # Arabic kaf
        self.assertIn(ZWNJ, joined)                                              # a half space
        self.assertIn(TATWEEL, joined)
        self.assertIn("٣", joined)                                          # an Arabic digit
        self.assertTrue(any(k != k.strip() or "  " in k for k in keywords))     # extra spaces
        self.assertTrue(any("galaxy s24" in k for k in keywords))                # Latin words and digits in Persian
        self.assertTrue(any(k.startswith("۱۰ ") for k in keywords))   # a number in the title, in Persian digits
        self.assertGreaterEqual(sum(1 for r in rows if r["Published URL"]), 6)   # pages to link to

    def test_two_addresses_that_differ_by_a_half_space(self):
        urls = [r["Published URL"] for r in self.rows("fake-fa") if r["Published URL"]]
        twins = [u for u in urls if "می" in u and "خواهم-مو" in u.replace(ZWNJ, "")]
        self.assertEqual(len(twins), 2)
        self.assertEqual(len({content.url_key(u) for u in twins}), 2)             # two pages, as in Search Console
        self.assertEqual(len({content.dup_key(u) for u in twins}), 1)             # ... that the Duplicate URL alert pairs

    def test_the_kinds_of_work(self):
        rows = self.rows("fake-fa")
        todo = {}
        for r in rows:
            todo.setdefault(r["To Do"], []).append(r)
        new_briefs = [r for r in todo["Brief"] if r["Status"] != "Briefed" and not r["Published URL"]]
        update_briefs = [r for r in todo["Brief"] if r["Status"] != "Briefed" and r["Published URL"]]
        already = [r for r in todo["Brief"] if r["Status"] == "Briefed"]
        with_brief = [r for r in todo["Draft"] if r["Attachments"]]
        without_brief = [r for r in todo["Draft"] if not r["Attachments"]]
        self.assertGreaterEqual(len(new_briefs), 8)
        self.assertEqual(len(update_briefs) >= 2, True)
        self.assertEqual((len(already), len(with_brief), len(without_brief)), (1, 1, 1))
        self.assertTrue(already[0]["Attachments"] and already[0]["Briefed Date"])
        # a row whose cells are already filled (they must not be overwritten) and a row with old notes
        filled = [r for r in update_briefs if r["Meta Title"] and r["Slug"]]
        noted = [r for r in rows if r["Notes/Checklist"]]
        self.assertTrue(filled and noted)

    def test_the_english_and_the_site_without_an_sop_have_the_same_kinds_of_work(self):
        for name in ("fake-en", "fake-nosop"):
            rows = self.rows(name)
            todos = [r["To Do"] for r in rows]
            self.assertIn("Brief", todos, name)
            self.assertEqual(todos.count("Draft"), 2 if name == "fake-en" else 1, name)
            self.assertGreaterEqual(sum(1 for r in rows if r["Published URL"]), 4, name)
        en = [r["Primary Keyword"] for r in self.rows("fake-en")]
        self.assertIn("10 ideas for a small kitchen", en)
        self.assertTrue(any("budget laptops" in k for k in en))                   # a comparison with prices

    def test_landing_pages_exist_too(self):
        for name in self.world.sites:
            self.assertGreaterEqual(len(self.rows(name, "Landing Pages")), 2)


class RebuildTests(Base):

    def test_building_again_changes_nothing(self):
        files = json.dumps({k: {x: y for x, y in v.items() if x != "content"} for k, v in self.drive.files_.items()},
                           sort_keys=True)
        before = {n: json.dumps(self.sheets.books[w.dashboard_id]["Articles"].data) for n, w in self.world.sites.items()}
        again = fs.build(self.drive, self.sheets, lambda s: None)
        self.assertEqual({n: w.dashboard_id for n, w in again.sites.items()},
                         {n: w.dashboard_id for n, w in self.world.sites.items()})
        after = json.dumps({k: {x: y for x, y in v.items() if x != "content"} for k, v in self.drive.files_.items()},
                           sort_keys=True)
        self.assertEqual(files, after)
        self.assertEqual(before, {n: json.dumps(self.sheets.books[w.dashboard_id]["Articles"].data)
                                  for n, w in again.sites.items()})

    def test_a_dashboard_the_run_changed_stays_changed_until_a_reset(self):
        w = self.world.sites["fake-en"]
        t = self.sheets.books[w.dashboard_id]["Articles"]
        header = t.data[0]
        t.data[1][header.index("Status")] = "Changed"
        fs.build(self.drive, self.sheets, lambda s: None)
        self.assertEqual(t.data[1][header.index("Status")], "Changed")
        fs.build(self.drive, self.sheets, lambda s: None, reset=True)
        self.assertEqual(t.data[1][header.index("Status")], "Inboxed")

    def test_a_reset_puts_the_starting_rows_back(self):
        w = self.world.sites["fake-fa"]
        t = self.sheets.books[w.dashboard_id]["Articles"]
        start = json.dumps(t.data)
        t.data[2][0] = "something else"
        del t.data[5]
        fs.build(self.drive, self.sheets, lambda s: None, reset=True)
        self.assertEqual(json.dumps(t.data), start)
        before = cc.Snapshot.of(t.data)
        self.assertEqual(cc.diff_dashboard(before, cc.Snapshot.of(t.data), set()), [])


class SampleTests(Base):

    def test_the_samples_cover_every_rule(self):
        names = [s.name for s in fs.samples()]
        self.assertEqual(len(names), len(set(names)))
        wanted = {"h1-count", "faq-not-h2", "summary-not-h2", "no-table", "link-count", "link-repeated",
                  "no-image-slot", "no-quality-check", "no-header-box", "tool-name", "dash", "count-mismatch",
                  "arabic-letter", "latin-digit", "arabic-digit", "not-rtl", "missing-section", "two-h1"}
        covered = set().union(*(s.expected for s in fs.samples()))
        self.assertEqual(wanted - covered, set())

    def test_every_right_sample_passes_and_every_wrong_one_fails_on_its_rule(self):
        self.assertEqual(fs.selftest([(s, s.html) for s in fs.samples()]), [])

    def test_the_same_through_drive(self):
        self.assertEqual(len(self.world.sample_ids), len(fs.samples()))
        self.assertEqual(fs.check_samples_in_drive(self.drive, self.world), [])

    def test_the_selftest_notices_a_checker_that_is_wrong(self):
        broken = fs.samples()[0]
        wrong = fs.selftest([(broken, broken.html.replace("<h1>", "<h2>").replace("</h1>", "</h2>"))])
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0][0], broken.name)

    def test_a_right_draft_in_each_language_has_what_the_spec_asks(self):
        for lang, domain in (("fa", "https://fa.fake-site.test"), ("en", "https://en.fake-site.test")):
            html = fs.draft_html(lang, domain)
            doc = cc.Doc(html)
            self.assertEqual(len(doc.headings(1)), 1)
            self.assertEqual(doc.lists[0], 10)
            self.assertGreaterEqual(doc.tables, 1)
            self.assertIn("header-box", html)
        self.assertNotIn("ي", fs.draft_html("fa", "https://fa.fake-site.test"))
        self.assertNotIn("ك", fs.draft_html("fa", "https://fa.fake-site.test"))


class CommandTests(unittest.TestCase):
    """The workflow command that builds the fake site in the real Drive."""

    def run_command(self, wrong=None):
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest import mock
        import gsc_pulse
        patch_time(self)
        sheets = FakeSheets()
        self.drive = FakeDrive(sheets)
        clients = SimpleNamespace(drive=self.drive, sheets=sheets, gsc=None)
        out = io.StringIO()
        extra = [mock.patch.object(fs, "check_samples_in_drive", lambda d, w: wrong)] if wrong is not None else []
        with mock.patch.object(gsc_pulse.auth, "build_me_clients", lambda env, build: clients), \
                contextlib.redirect_stdout(out):
            for e in extra:
                e.start()
            try:
                gsc_pulse.build_test_site()
            finally:
                for e in extra:
                    e.stop()
        return out.getvalue()

    def test_it_builds_the_world_and_tries_the_checker(self):
        text = self.run_command()
        self.assertIn("3 fake sites", text)
        self.assertIn("0 the checker got wrong", text)
        self.assertIn("The test site is ready", text)
        for fid in self.drive.files_:
            self.assertNotIn(fid, text)                                     # no id in the log

    def test_a_checker_that_is_wrong_ends_the_run_red(self):
        import gsc_pulse
        with self.assertRaises(gsc_pulse.GscPulseError):
            self.run_command(wrong=[("sample", set(), {"h1-count"})])

if __name__ == "__main__":
    unittest.main()
