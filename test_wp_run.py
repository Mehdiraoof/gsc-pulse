"""The publishing run of one site: the approved rows of the content dashboard become WordPress drafts and updates, the row and the
WordPress Log tab say what happened, a dry run writes nothing, and a site with two installs sends a row to the install of the
language of its document. WordPress is a test copy in memory; the dashboard is a fake sheet; every text is invented."""

import unittest
from datetime import date
from types import SimpleNamespace

import content
import fake_site
import gdocs
import infographic
import wp_api
import wp_check
import wp_publish as pub
import wp_run
from test_content_run import make_site
from test_history import FakeSheets, patch_time
from test_wp_publish import FakeRenderer, HOME, TODAY, draft
from wp_fake import FakeWordPress

DOC = gdocs.doc_link("DOC1")
LIVE = "<!-- wp:paragraph --><p>" + "The current text of the page. " * 40 + "</p><!-- /wp:paragraph -->"


class Base(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.fs.add_book("DASH", {"Articles": (50, 45), "Landing Pages": (50, 45)})
        for tab in ("Articles", "Landing Pages"):
            self.fs.write_cells(self.fs.books["DASH"][tab], 1, 1, [content.HEADERS])
        self.site = make_site(sheet_id="DASH")
        self.run = SimpleNamespace(site=self.site, name="site-x", sheets=self.fs, drive=None)
        self.en, self.fa = FakeWordPress(base=HOME), FakeWordPress(base=HOME + "/fa")
        self.docs = {"DOC1": draft(), "DOCFA": draft("fa")}
        self.renderer = FakeRenderer()

    def target(self, language):
        fake = self.fa if language == "fa" else self.en
        install = wp_check.Install("site-x", language.upper(), fake.base, "WP", fake.user, fake.password)
        return pub.Target(wp_api.Wp(install, fake), HOME, language, infographic.Brand(language=language), self.renderer)

    def add(self, tab="Articles", **cells):
        t = self.fs.books["DASH"][tab]
        t.data.append([cells.get(h, "") for h in content.HEADERS])
        t.row_count = max(t.row_count, len(t.data))

    def cell(self, row, name, tab="Articles"):
        data = self.fs.books["DASH"][tab].data
        header = data[0]
        r = data[row]
        return r[header.index(name)] if header.index(name) < len(r) else ""

    def go(self, write=True, targets=None):
        return wp_run.publish_site(self.run, "DASH", targets or self.target, lambda doc_id: self.docs[doc_id], TODAY, write, lambda *a: None)


class Drafts(Base):

    def test_an_approved_row_becomes_a_draft_and_the_row_says_so(self):
        self.add(**{content.KEY: "kitchen", content.STATUS: "Drafted", content.TODO: "To WordPress", "Attachments": DOC,
                    "Slug": "kitchen-ideas", "Notes/Checklist": "my note"})
        report = self.go()
        self.assertEqual((report.drafts, report.refused, report.failed), (1, 0, 0))
        post = next(iter(self.en.posts.values()))
        self.assertEqual(post["status"], "draft")
        self.assertEqual((self.cell(1, content.STATUS), self.cell(1, content.TODO)), ("In WordPress", "Review"))
        self.assertIn(f"{HOME}/?p={post['id']}&preview=true", self.cell(1, "Attachments"))
        self.assertIn(DOC, self.cell(1, "Attachments"))                                # the doc link stays
        notes = self.cell(1, "Notes/Checklist")
        self.assertTrue(notes.startswith("my note\nIn WordPress as a draft"))
        self.assertIn("2026-11-14", notes)

    def test_the_wordpress_log_tab_lists_every_write(self):
        self.add(**{content.KEY: "kitchen", content.TODO: "To WordPress", "Attachments": DOC, "Slug": "kitchen-ideas"})
        self.go()
        log = self.fs.books["DASH"][wp_run.LOG_TAB].data
        self.assertEqual(log[0], wp_run.LOG_HEADER)
        self.assertEqual([(r[0], r[1], r[3]) for r in log[1:]],
                         [("2026-11-14", "site-x", "uploaded an infographic"), ("2026-11-14", "site-x", "created a draft")])
        self.assertTrue(all(isinstance(r[2], int) and r[2] > 100 for r in log[1:]))

    def test_only_the_approved_columns_change(self):
        self.add(**{content.KEY: "kitchen", content.STATUS: "Drafted", content.TODO: "To WordPress", "Attachments": DOC,
                    "Slug": "kitchen-ideas", "Priority": "High", "Notes/Checklist": "n", "Meta Title": "My title"})
        self.add(**{content.KEY: "other", content.STATUS: "Briefed", content.TODO: "Write"})
        before = [list(r) for r in self.fs.books["DASH"]["Articles"].data]
        self.go()
        after = self.fs.books["DASH"]["Articles"].data
        header = after[0]
        changed = {header[i] for r in (1,) for i in range(len(header)) if (before[r][i] if i < len(before[r]) else "") != (after[r][i] if i < len(after[r]) else "")}
        self.assertEqual(changed, {"Status", "To Do", "Attachments", "Notes/Checklist"})
        self.assertEqual(after[2], before[2])                                          # the other row is not touched

    def test_a_dry_run_writes_nothing_anywhere(self):
        self.add(**{content.KEY: "kitchen", content.TODO: "To WordPress", "Attachments": DOC, "Slug": "kitchen-ideas"})
        before = [list(r) for r in self.fs.books["DASH"]["Articles"].data]
        report = self.go(write=False)
        self.assertEqual((report.would, report.drafts), (1, 0))
        self.assertEqual(self.fs.books["DASH"]["Articles"].data, before)
        self.assertEqual((self.en.writes(), self.fa.writes()), ([], []))
        self.assertNotIn(wp_run.LOG_TAB, self.fs.books["DASH"])

    def test_a_row_without_a_document_gets_a_note_and_nothing_is_sent(self):
        self.add(**{content.KEY: "kitchen", content.TODO: "To WordPress"})
        report = self.go()
        self.assertEqual(report.failed, 1)
        self.assertIn("no document in Attachments", self.cell(1, "Notes/Checklist"))
        self.assertEqual(self.en.writes(), [])

    def test_the_note_is_not_added_again_on_the_next_run(self):
        self.add(**{content.KEY: "kitchen", content.TODO: "To WordPress"})
        self.go()
        first = self.cell(1, "Notes/Checklist")
        self.go()
        self.assertEqual(self.cell(1, "Notes/Checklist"), first)

    def test_rows_that_are_not_approved_are_left_alone(self):
        self.add(**{content.KEY: "a", content.TODO: "Review", "Attachments": DOC})
        self.add(**{content.KEY: "b", content.TODO: "Localize", "Attachments": DOC})
        report = self.go()
        self.assertEqual(report.seen, 0)
        self.assertEqual(self.en.writes(), [])

    def test_a_live_page_becomes_an_update_draft(self):
        live = self.en.add_post("Kitchen ideas", LIVE, "publish", "kitchen-ideas")
        self.add(**{content.KEY: "kitchen", content.URL: f"{HOME}/kitchen-ideas/", content.STATUS: "Drafted",
                    content.TODO: "To WordPress", "Attachments": DOC})
        self.go()
        drafts = [p for p in self.en.posts.values() if p["status"] == "draft"]
        self.assertEqual([p["title"]["raw"] for p in drafts], ["Update · Kitchen ideas"])
        self.assertEqual(self.en.posts[live]["content"]["raw"], LIVE)
        self.assertIn("lang=en]", self.cell(1, "Notes/Checklist"))

    def test_one_row_that_breaks_does_not_stop_the_next(self):
        self.add(**{content.KEY: "a", content.TODO: "To WordPress", "Attachments": gdocs.doc_link("GONE")})
        self.add(**{content.KEY: "b", content.TODO: "To WordPress", "Attachments": DOC, "Slug": "b"})
        report = self.go()
        self.assertEqual((report.failed, report.drafts), (1, 1))
        self.assertEqual(self.cell(2, content.STATUS), "In WordPress")


class Languages(Base):

    def test_a_persian_document_goes_to_the_persian_install_and_an_english_one_to_the_english(self):
        self.add(**{content.KEY: "a", content.TODO: "To WordPress", "Attachments": DOC, "Slug": "a"})
        self.add(**{content.KEY: "b", content.TODO: "To WordPress", "Attachments": gdocs.doc_link("DOCFA"), "Slug": "b"})
        self.go()
        self.assertEqual(len([p for p in self.en.posts.values()]), 1)
        self.assertEqual(len([p for p in self.fa.posts.values()]), 1)
        self.assertIn(wp_run.doc_language(self.docs["DOCFA"]), ("fa",))
        self.assertEqual(self.cell(2, content.STATUS), "In WordPress")
        self.assertIn(f"{HOME}/fa/?p=", self.cell(2, "Attachments"))                   # the preview link of the Persian install

    def test_a_language_without_a_login_is_said_and_nothing_is_written(self):
        self.add(**{content.KEY: "b", content.TODO: "To WordPress", "Attachments": gdocs.doc_link("DOCFA"), "Slug": "b"})
        report = self.go(targets=lambda language: self.target(language) if language == "en" else None)
        self.assertEqual((report.failed, report.drafts), (1, 0))
        self.assertEqual(self.fa.writes(), [])
        self.assertEqual(self.cell(1, content.STATUS), "")

    def test_the_language_of_a_document(self):
        self.assertEqual(wp_run.doc_language(draft()), "en")
        self.assertEqual(wp_run.doc_language(draft("fa")), "fa")
        self.assertEqual(wp_run.doc_language("<p>short</p>", "fa"), "fa")                # too little to tell: the site's own


class Updates(Base):

    def make_draft(self):
        self.live = self.en.add_post("Kitchen ideas", LIVE, "publish", "kitchen-ideas", featured=9)
        self.add(**{content.KEY: "kitchen", content.URL: f"{HOME}/kitchen-ideas/", content.STATUS: "Drafted",
                    content.TODO: "To WordPress", "Attachments": DOC})
        self.go()
        self.update_id = next(p["id"] for p in self.en.posts.values() if p["status"] == "draft")

    def approve(self, text="<!-- wp:paragraph --><p>" + "The new better text of the page. " * 40 + "</p><!-- /wp:paragraph -->"):
        self.en.edit_elsewhere(self.update_id, text)                                    # the reviewer removed the boxes
        data = self.fs.books["DASH"]["Articles"].data
        data[1][data[0].index(content.TODO)] = "Apply update"

    def test_apply_update_changes_the_live_post_and_the_row(self):
        self.make_draft()
        self.approve()
        report = self.go()
        self.assertEqual((report.updates, report.refused), (1, 0))
        self.assertIn("The new better text", self.en.posts[self.live]["content"]["raw"])
        self.assertEqual(self.en.posts[self.live]["featured_media"], 9)
        self.assertEqual(self.en.posts[self.update_id]["status"], "trash")
        self.assertEqual((self.cell(1, content.STATUS), self.cell(1, content.TODO), self.cell(1, content.PUBLISHED_DATE)),
                         ("Updated", "Keep", "2026-11-14"))

    def test_a_refusal_puts_its_reason_in_the_note_and_changes_nothing_else(self):
        self.make_draft()
        self.en.edit_elsewhere(self.update_id, self.en.posts[self.update_id]["content"]["raw"])      # boxes still there
        data = self.fs.books["DASH"]["Articles"].data
        data[1][data[0].index(content.TODO)] = "Apply update"
        report = self.go()
        self.assertEqual((report.updates, report.refused), (0, 1))
        self.assertIn("Remove before publishing", self.cell(1, "Notes/Checklist"))
        self.assertEqual(self.cell(1, content.STATUS), "In WordPress")
        self.assertEqual(self.en.posts[self.live]["content"]["raw"], LIVE)

    def test_nothing_in_a_whole_run_asks_for_a_status_of_publish(self):
        self.make_draft()
        self.approve()
        self.go()
        for fake in (self.en, self.fa):
            self.assertFalse(fake.ever_asked_for_status("publish", "future", "private", "pending"))


if __name__ == "__main__":
    unittest.main()
