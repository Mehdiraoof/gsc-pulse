"""Publishing to WordPress, tested on a test WordPress (an in-memory copy of the core REST API): the draft document as blocks, the
notes box, infographics, a new draft, an update draft, "Apply update" and every reason to refuse it, the marker check, and that
nothing ever asks for a status of publish. Sites, pages and texts are invented."""

import struct
import unittest
from datetime import date

import content_docs
import fake_site
import infographic
import wp_api
import wp_blocks
import wp_check
import wp_publish as pub
from wp_fake import FakeWordPress

TODAY = date(2026, 11, 14)
HOME = "https://example.test"
CHANGES_EN = content_docs.DRAFT_HEADINGS["en"]["changes"]
SLOT = "[Infographic: steps | How to plant a seed | Dig a hole; Place the seed; Cover it; Water it | 3 steps]"


def png(width=1200, size=20_000) -> bytes:
    head = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, 800) + b"\x08\x06\x00\x00\x00"
    return head + b"\x00" * (size - len(head))


class FakeRenderer:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.pages = data or png(), error, []

    def render(self, page_html, width=infographic.WIDTH):
        self.pages.append(page_html)
        if self.error:
            raise self.error
        return self.data


def install(fake):
    return wp_check.Install("site-1", "", fake.base, "WP_SITE1", fake.user, fake.password)


def draft(lang="en", slot=True, changes=False, extra=""):
    page = fake_site.draft_html(lang, HOME)
    inject = (f"<p>{SLOT}</p>" if slot else "") + extra
    if changes:
        inject += f"<h2>{CHANGES_EN}</h2><ul><li>Added the price table</li><li>Rewrote the intro</li></ul>"
    return page.replace("</body>", inject + "</body>")


class Base(unittest.TestCase):

    def setUp(self):
        self.fake = FakeWordPress(base=HOME)
        self.log = []
        self.wp = wp_api.Wp(install(self.fake), self.fake, lambda action, pid: self.log.append((action, pid)))
        self.renderer = FakeRenderer()
        self.saved = []
        self.target = pub.Target(self.wp, HOME, "en", infographic.Brand(name="Example"), self.renderer,
                                 lambda name, data: self.saved.append((name, len(data))))

    def row(self, **cells):
        return dict({"Slug": "kitchen-ideas", "Content Type": "Cluster"}, **cells)


class Blocks(unittest.TestCase):

    def convert(self, source, **kw):
        return wp_blocks.convert(source, HOME, kw.pop("language", "en"), **kw)

    def test_title_header_box_headings_lists_tables_and_links(self):
        conv = self.convert(draft())
        self.assertEqual(conv.title, fake_site.T["en"]["h1"])
        self.assertIn(fake_site.T["en"]["box"][0][0], conv.box)                         # the box rows are kept for the notes
        body = "\n".join(s.html for s in conv.segments)
        self.assertNotIn("header-box", body)                                              # and are not in the post
        self.assertEqual(body.count('<figure class="wp-block-table">'), 1)               # only the comparison table of the article
        self.assertIn('<!-- wp:heading -->', body)
        self.assertIn('<!-- wp:heading {"level":3} -->', body)
        self.assertIn('<!-- wp:list {"ordered":true} -->', body)
        self.assertIn('<!-- wp:list-item -->', body)
        self.assertIn('<figure class="wp-block-table">', body)
        self.assertIn("<thead>", body)
        self.assertNotIn("<h1", body)                                                     # the H1 is the title

    def test_internal_links_stay_and_other_links_keep_their_text(self):
        source = ('<html><body><h1>T</h1><p>See <a href="https://example.test/a">our page</a>, '
                  '<a href="https://www.google.com/url?q=https://elsewhere.test/x&sa=D">elsewhere</a> and '
                  '<a href="/b">relative</a>.</p></body></html>')
        body = self.convert(source).segments[0].html
        self.assertIn('<a href="https://example.test/a">our page</a>', body)
        self.assertIn('<a href="/b">relative</a>', body)
        self.assertNotIn("elsewhere.test", body)
        self.assertIn("elsewhere", body)

    def test_bold_and_italic_from_tags_and_from_styles(self):
        source = ('<html><body><h1>T</h1><p><b>a</b> <span style="font-weight:700">b</span> '
                  '<span style="font-style:italic">c</span> <em>d</em></p></body></html>')
        body = self.convert(source).segments[0].html
        for piece in ("<strong>a</strong>", "<strong>b</strong>", "<em>c</em>", "<em>d</em>"):
            self.assertIn(piece, body)

    def test_a_second_h1_becomes_an_h2(self):
        body = self.convert("<html><body><h1>One</h1><p>x</p><h1>Two</h1></body></html>").segments[0].html
        self.assertIn("<h2", body)
        self.assertNotIn("<h1", body)

    def test_an_image_slot_a_touch_marker_and_a_check_note_become_boxes_or_counts(self):
        source = ('<html><body><h1>T</h1><p>[Image: bright kitchen | small kitchen | kitchen.jpg]</p>'
                  '<p>[Personal experience: ...]</p><p>The price is 5 [needs checking].</p></body></html>')
        conv = self.convert(source)
        body = "\n".join(s.html for s in conv.segments)
        self.assertEqual(conv.image_slots, [{"description": "bright kitchen", "alt": "small kitchen", "file": "kitchen.jpg"}])
        self.assertEqual((conv.touches, conv.needs_checking), (1, 1))
        self.assertIn("Image needed: bright kitchen", body)
        self.assertIn("To add by the writer", body)
        self.assertEqual(body.count(wp_blocks.MARKER["en"]), 2)

    def test_an_infographic_slot_is_a_slot_with_its_plan(self):
        conv = self.convert(f"<html><body><h1>T</h1><p>{SLOT}</p></body></html>")
        seg = conv.segments[0]
        self.assertEqual((seg.kind, seg.spec["template"], seg.spec["title"], seg.spec["points"], seg.spec["numbers"]),
                         ("infographic", "steps", "How to plant a seed", ["Dig a hole", "Place the seed", "Cover it", "Water it"], "3 steps"))
        self.assertIn("Infographic needed", seg.html)                                     # the box that stays if it cannot be drawn

    def test_the_quality_check_and_the_changes_go_to_the_notes_not_the_post(self):
        heads = content_docs.DRAFT_HEADINGS["en"]
        conv = self.convert(draft(changes=True), changes_heading=heads["changes"], quality_heading=heads["quality"])
        body = "\n".join(s.html for s in conv.segments)
        self.assertNotIn(heads["quality"], body)
        self.assertNotIn(heads["changes"], body)
        self.assertNotIn("Added the price table", body)
        notes = wp_blocks.notes_box(conv, "en")
        self.assertIn("Added the price table", notes)
        self.assertIn(heads["quality"], notes)

    def test_the_notes_box_starts_with_the_marker_and_holds_the_header_box_and_what_is_to_do(self):
        conv = self.convert(draft())
        notes = wp_blocks.notes_box(conv, "en", ["Category: not sure, so the default was kept: choose one."])
        self.assertIn(wp_blocks.MARKER["en"], notes)
        self.assertIn("pulseo-remove", notes)
        self.assertIn("Notes for editor", notes)
        self.assertIn("image to add", notes)
        self.assertIn("Category: not sure", notes)
        self.assertTrue(wp_blocks.has_marker(notes))

    def test_a_persian_draft_has_the_persian_marker_and_no_inline_direction(self):
        conv = self.convert(draft("fa"), language="fa")
        notes = wp_blocks.notes_box(conv, "fa")
        self.assertIn("⚠ قبل از انتشار حذف شود", notes)
        self.assertNotIn("dir=", "\n".join(s.html for s in conv.segments))

    def test_the_marker_is_found_as_text_or_with_entities(self):
        self.assertTrue(wp_blocks.has_marker("<p>⚠ Remove before publishing</p>"))
        self.assertTrue(wp_blocks.has_marker("<p>&#9888; Remove before publishing</p>"))
        self.assertTrue(wp_blocks.has_marker("⚠ قبل از انتشار حذف شود"))
        self.assertFalse(wp_blocks.has_marker("<p>Nothing to remove here</p>"))

    def test_a_document_without_a_title_or_header_box_says_so(self):
        conv = self.convert("<html><body><p>Just text</p></body></html>")
        self.assertEqual(len(conv.warnings), 2)

    def test_the_category_suggestion_never_makes_one(self):
        cats = [{"id": 1, "name": "Uncategorized"}, {"id": 7, "name": "Guides"}]
        self.assertEqual(wp_blocks.suggest_category(cats, "Blog Guides"), (7, ""))
        self.assertEqual(wp_blocks.suggest_category(cats, "Articles", "Cluster")[0], None)
        self.assertIn("not sure", wp_blocks.suggest_category(cats, "")[1])


class Infographics(unittest.TestCase):

    SPEC = {"template": "steps", "title": "How to plant a seed", "points": ["Dig", "Place", "Cover"], "numbers": "3 steps"}

    def test_every_template_draws_its_title_and_points_in_the_brand(self):
        brand = infographic.Brand(name="Example", primary="#112233", accent="#aabbcc")
        for name in ("steps", "comparison", "pros and cons", "price table", "checklist", "key takeaways", "facts card"):
            spec = dict(self.SPEC, template=name, points=["Pro: cheap", "Con: slow", "Fast vs Slow"] if name != "price table" else ["A: 10", "B: 20", "C: 30"])
            page = infographic.build_html(spec, brand)
            self.assertIn("How to plant a seed", page, name)
            self.assertIn("#112233", page)
            self.assertIn(f"width:{infographic.WIDTH}px", page)
            self.assertIn('dir="ltr"', page)

    def test_a_persian_card_is_right_to_left_with_vazirmatn(self):
        page = infographic.build_html(self.SPEC, infographic.Brand(language="fa"))
        self.assertIn('dir="rtl"', page)
        self.assertIn("Vazirmatn", page)

    def test_template_names_and_the_general_card(self):
        self.assertEqual(infographic.template_name("Pros and Cons"), "pros_cons")
        self.assertEqual(infographic.template_name("price or range table"), "price_table")
        self.assertEqual(infographic.template_name("Key-Takeaways"), "takeaways")
        self.assertEqual(infographic.template_name("something else"), "facts")

    def test_three_to_six_points_and_a_title(self):
        self.assertEqual(infographic.valid(self.SPEC), "")
        self.assertIn("3 to 6", infographic.valid(dict(self.SPEC, points=["a", "b"])))
        self.assertIn("3 to 6", infographic.valid(dict(self.SPEC, points=list("abcdefg"))))
        self.assertEqual(infographic.valid(dict(self.SPEC, title="")), "no title")

    def test_only_numbers_of_the_article_are_drawn(self):
        spec, dropped = infographic.keep_numbers_in_article(dict(self.SPEC, numbers="3 steps; 99 percent"), "Plant it in 3 steps.")
        self.assertEqual((spec["numbers"], dropped), ("3 steps", ["99 percent"]))
        spec, dropped = infographic.keep_numbers_in_article(dict(self.SPEC, numbers="۳ مرحله"), "در 3 مرحله")
        self.assertEqual(dropped, [])

    def test_the_picture_is_checked_for_type_width_and_size(self):
        self.assertEqual(infographic.check_png(png()), "")
        self.assertIn("not a PNG", "not a PNG " + infographic.check_png(b"nope"))
        self.assertIn("800 px", infographic.check_png(png(width=800)))
        self.assertIn("KB", infographic.check_png(png(size=200_000)))

    def test_file_names_are_english_slugs(self):
        self.assertEqual(infographic.file_name("Kitchen Ideas!", 2), "kitchen-ideas-infographic-2.png")
        self.assertEqual(infographic.file_name("", 1), "article-infographic-1.png")

    def test_the_brand_comes_from_the_deck_settings_with_placeholder_colours(self):
        from types import SimpleNamespace
        site = SimpleNamespace(name="site-1", deck=SimpleNamespace(site_label="Alpha", brand_color="", accent_color="#abcdef", language="fa"))
        brand = infographic.brand_for(site)
        self.assertEqual((brand.name, brand.primary, brand.accent, brand.language),
                         ("Alpha", infographic.PLACEHOLDER["primary"], "#abcdef", "fa"))


class NewDraft(Base):

    def test_a_row_becomes_a_draft_with_notes_blocks_and_an_infographic(self):
        out = pub.to_wordpress(self.row(), draft(), self.target, TODAY, tab_hint="Guides")
        self.assertTrue(out.ok)
        post = self.fake.posts[out.post_id]
        self.assertEqual(post["status"], "draft")
        self.assertEqual(post["title"]["raw"], fake_site.T["en"]["h1"])
        self.assertEqual(post["slug"], "kitchen-ideas")
        self.assertEqual(post["categories"], [7])                                          # "Guides" matched the tab
        content = post["content"]["raw"]
        self.assertTrue(content.startswith("<!-- wp:group"))                               # the notes box first
        self.assertIn("Notes for editor", content)
        self.assertIn(wp_blocks.MARKER["en"], content)
        self.assertIn('<figure class="wp-block-image size-full"><img src="', content)
        self.assertEqual(out.infographics, 1)
        media = next(iter(self.fake.media.values()))
        self.assertEqual((media["file"], media["type"], media["alt_text"]), ("kitchen-ideas-infographic-1.png", "image/png", "How to plant a seed"))
        self.assertEqual(self.saved, [("kitchen-ideas-infographic-1.png", 20_000)])        # a copy for the Drafts folder
        self.assertEqual(out.changes["Status"], "In WordPress")
        self.assertEqual(out.changes["_attach"], f"{HOME}/?p={out.post_id}&preview=true")
        self.assertIn("2026-11-14", out.note)
        self.assertNotIn("PulseO", out.note)

    def test_the_infographic_is_drawn_from_the_plan_in_the_brand(self):
        pub.to_wordpress(self.row(), draft(), self.target, TODAY)
        page = self.renderer.pages[0]
        self.assertIn("Dig a hole", page)
        self.assertIn(infographic.PLACEHOLDER["primary"], page)                            # placeholder colours until the owner sends his

    def test_without_a_renderer_the_box_with_its_description_stays(self):
        self.target.renderer = None
        out = pub.to_wordpress(self.row(), draft(), self.target, TODAY)
        content = self.fake.posts[out.post_id]["content"]["raw"]
        self.assertIn("Infographic needed: How to plant a seed", content)
        self.assertIn("was not made", content)
        self.assertEqual((out.infographics, self.fake.media), (0, {}))

    def test_a_renderer_that_fails_or_gives_a_bad_picture_leaves_the_box(self):
        for renderer in (FakeRenderer(error=RuntimeError("no browser")), FakeRenderer(data=png(width=500)), FakeRenderer(data=png(size=300_000))):
            self.target.renderer = renderer
            out = pub.to_wordpress(self.row(), draft(), self.target, TODAY)
            self.assertIn("Infographic needed", self.fake.posts[out.post_id]["content"]["raw"])
            self.assertEqual(out.infographics, 0)

    def test_a_draft_without_a_planned_infographic_says_so_in_the_notes(self):
        out = pub.to_wordpress(self.row(), draft(slot=False), self.target, TODAY)
        self.assertIn("No infographic was planned", self.fake.posts[out.post_id]["content"]["raw"])

    def test_a_number_that_is_not_in_the_article_is_left_off_the_card(self):
        slot = "[Infographic: facts card | Facts | One; Two; Three | 777 visitors]"
        out = pub.to_wordpress(self.row(), draft(slot=False, extra=f"<p>{slot}</p>"), self.target, TODAY)
        self.assertNotIn("777", self.renderer.pages[0])
        self.assertIn("Numbers left out", self.fake.posts[out.post_id]["content"]["raw"])

    def test_an_unsure_category_keeps_the_default_and_says_so(self):
        out = pub.to_wordpress(self.row(), draft(), self.target, TODAY, tab_hint="Articles")
        post = self.fake.posts[out.post_id]
        self.assertIn("Category: not sure", post["content"]["raw"])
        self.assertEqual(post["categories"], [1])

    def test_a_document_without_a_title_is_not_sent(self):
        out = pub.to_wordpress(self.row(), "<html><body><p>x</p></body></html>", self.target, TODAY)
        self.assertFalse(out.ok)
        self.assertEqual(self.fake.writes(), [])

    def test_the_writes_are_logged(self):
        pub.to_wordpress(self.row(), draft(), self.target, TODAY)
        actions = [a for a, _ in self.log]
        self.assertEqual(actions, ["uploaded a file", "created a draft"])


class Updates(Base):

    LIVE = "<!-- wp:paragraph --><p>" + "The current text of the page. " * 40 + "</p><!-- /wp:paragraph -->"

    def live(self, **kw):
        return self.fake.add_post("Kitchen ideas", self.LIVE, "publish", "kitchen-ideas", featured=55, excerpt="old", **kw)

    def make_update(self):
        self.original = self.live()
        out = pub.to_wordpress(self.row(Slug=""), draft(changes=True), self.target, TODAY, live_slug="kitchen-ideas")
        self.assertTrue(out.ok, out.note)
        self.draft_id = out.post_id
        self.notes = out.note
        return out

    def clean_draft(self, text=None):
        """The reviewer removes the boxes and keeps the text."""
        content = text if text is not None else "<!-- wp:paragraph --><p>" + "The new better text of the page. " * 40 + "</p><!-- /wp:paragraph -->"
        self.fake.edit_elsewhere(self.draft_id, content)

    def apply(self, **cells):
        return pub.apply_update(self.row(**dict({"Notes/Checklist": self.notes}, **cells)), self.target, TODAY)

    def test_an_update_is_a_separate_draft_that_keeps_the_original_untouched(self):
        out = self.make_update()
        draft_post = self.fake.posts[self.draft_id]
        self.assertEqual((draft_post["status"], draft_post["title"]["raw"]), ("draft", "Update · Kitchen ideas"))
        self.assertEqual(self.fake.posts[self.original]["content"]["raw"], self.LIVE)
        self.assertIn("Added the price table", draft_post["content"]["raw"])             # the changes are in the notes box
        self.assertIn(f"[wp update draft={self.draft_id} original={self.original} modified=", out.note)
        self.assertIn("Apply update", out.note)

    def test_an_update_of_a_page_wordpress_does_not_have_is_refused(self):
        out = pub.to_wordpress(self.row(Slug=""), draft(), self.target, TODAY, live_slug="not-there")
        self.assertFalse(out.ok)
        self.assertEqual(self.fake.writes(), [])

    def test_apply_copies_into_the_original_and_keeps_its_identity(self):
        self.make_update()
        self.clean_draft()
        before = {k: self.fake.posts[self.original][k] for k in ("id", "slug", "date", "author", "featured_media", "link", "comment_status")}
        out = self.apply()
        self.assertTrue(out.ok, out.note)
        post = self.fake.posts[self.original]
        self.assertIn("The new better text", post["content"]["raw"])
        self.assertEqual({k: post[k] for k in before}, before)                            # URL, ID, slug, date, featured image, author
        self.assertEqual(post["status"], "publish")
        self.assertEqual(post["title"]["raw"], "Kitchen ideas")
        self.assertEqual(self.wp.revision_count(self.original), 1)                        # the old version can be restored
        self.assertEqual(self.fake.revisions[self.original][0]["content"], self.LIVE)
        self.assertEqual(self.fake.posts[self.draft_id]["status"], "trash")               # the temporary draft is in the trash
        self.assertEqual((out.changes["Status"], out.changes["Published/Updated Date"]), ("Updated", "2026-11-14"))

    def test_the_title_is_only_sent_when_it_changed(self):
        self.make_update()
        self.clean_draft()
        self.apply()
        sent = [r for r in self.fake.writes() if r["route"] == f"/wp/v2/posts/{self.original}"]
        self.assertEqual(len(sent), 1)
        self.assertNotIn("title", sent[0]["payload"])
        self.assertEqual(set(sent[0]["payload"]), {"content"})                           # excerpt was empty in the draft
        # a changed title and a new excerpt are sent
        self.fake.posts[self.draft_id]["status"] = "draft"
        self.fake.posts[self.draft_id]["title"]["raw"] = "Update · A better title"
        self.fake.posts[self.draft_id]["excerpt"]["raw"] = "new excerpt"
        self.fake.edit_elsewhere(self.draft_id, "<p>" + "Newer text. " * 80 + "</p>")
        self.fake.posts[self.original]["modified"] = self.notes.split("modified=")[1].split("]")[0]
        self.apply()
        last = [r for r in self.fake.writes() if r["route"] == f"/wp/v2/posts/{self.original}"][-1]
        self.assertEqual((last["payload"]["title"], last["payload"]["excerpt"]), ("A better title", "new excerpt"))

    def test_refused_while_a_box_is_still_in_the_draft(self):
        self.make_update()
        out = self.apply()
        self.assertFalse(out.ok)
        self.assertIn("Remove before publishing", out.note)
        self.assertEqual(self.fake.posts[self.original]["content"]["raw"], self.LIVE)
        self.assertEqual(self.fake.posts[self.draft_id]["status"], "draft")

    def test_refused_when_the_original_was_edited_after_the_draft(self):
        self.make_update()
        self.clean_draft()
        self.fake.edit_elsewhere(self.original, self.LIVE + "<p>An edit in the editor.</p>")
        out = self.apply()
        self.assertFalse(out.ok)
        self.assertIn("edited after the draft", out.note)
        self.assertIn("An edit in the editor", self.fake.posts[self.original]["content"]["raw"])

    def test_refused_when_the_new_text_is_much_shorter_unless_the_note_says_it_is_meant(self):
        self.make_update()
        self.clean_draft("<p>Short.</p>")
        out = self.apply()
        self.assertFalse(out.ok)
        self.assertIn("% of the current one", out.note)
        self.assertEqual(self.fake.posts[self.original]["content"]["raw"], self.LIVE)
        out = self.apply(**{"Notes/Checklist": self.notes + " Shorter is intended."})
        self.assertTrue(out.ok, out.note)

    def test_refused_without_a_recorded_update_draft_or_when_it_is_not_a_draft_any_more(self):
        out = pub.apply_update(self.row(**{"Notes/Checklist": "nothing"}), self.target, TODAY)
        self.assertFalse(out.ok)
        self.make_update()
        self.clean_draft()
        self.fake.posts[self.draft_id]["status"] = "trash"
        self.assertFalse(self.apply().ok)

    def test_the_marker_text_of_the_persian_box_also_blocks_an_update(self):
        self.make_update()
        self.clean_draft("<p>" + "متن " * 200 + "⚠ قبل از انتشار حذف شود</p>")
        self.assertFalse(self.apply().ok)


class Safety(Base):

    def test_nothing_ever_asks_for_a_status_of_publish(self):
        original = self.fake.add_post("Kitchen ideas", "<p>" + "text " * 100 + "</p>", "publish", "kitchen-ideas")
        pub.to_wordpress(self.row(), draft(), self.target, TODAY)
        out = pub.to_wordpress(self.row(Slug=""), draft(changes=True), self.target, TODAY, live_slug="kitchen-ideas")
        self.fake.edit_elsewhere(out.post_id, "<p>" + "text more " * 100 + "</p>")
        pub.apply_update(self.row(**{"Notes/Checklist": out.note}), self.target, TODAY)
        self.assertTrue(self.fake.writes())
        self.assertFalse(self.fake.ever_asked_for_status("publish", "future", "private", "pending"))
        for request in self.fake.writes():
            self.assertEqual(request["payload"].get("status", "draft"), "draft")           # a draft, or no status at all
            self.assertNotEqual(request["method"], "PUT")
        self.assertEqual(self.fake.posts[original]["status"], "publish")                  # still published, never touched but by apply

    def test_the_client_refuses_to_publish_and_to_change_anything_but_the_text(self):
        pid = self.fake.add_post("t", "c", "draft")
        for fields in ({"status": "publish"}, {"status": "future"}, {"slug": "x"}, {"featured_media": 3}, {"author": 2}, {"date": "x"}):
            with self.assertRaises(wp_api.WpError):
                self.wp.apply_to_post(pid, fields)
        self.assertEqual(self.fake.writes(), [])

    def test_a_published_post_is_never_moved_to_the_trash_and_nothing_is_deleted_for_good(self):
        live = self.fake.add_post("t", "c", "publish")
        with self.assertRaises(wp_api.WpError):
            self.wp.trash_draft(live)
        draft_id = self.fake.add_post("d", "c", "draft")
        self.wp.trash_draft(draft_id)
        deletes = [r for r in self.fake.requests if r["method"] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertNotIn("force", deletes[0]["query"])
        self.assertEqual(self.fake.posts[draft_id]["status"], "trash")

    def test_errors_and_the_log_never_hold_the_password_or_the_address(self):
        bad = wp_api.Wp(wp_check.Install("site-1", "", HOME, "WP_SITE1", "u", "hunter2secret"), lambda *a, **k: (500, {}, b"{}"))
        with self.assertRaises(wp_api.WpError) as ctx:
            bad.create_draft("t", "c")
        self.assertNotIn("hunter2secret", str(ctx.exception))
        self.assertNotIn("example.test", str(ctx.exception))
        for request in self.fake.requests:
            self.assertNotIn("secretpassword", request["route"])


class MarkerCheck(Base):

    def test_a_published_post_that_still_holds_a_box_is_reported(self):
        box = wp_blocks.box(["Image needed: x"], "en")
        bad = self.fake.add_post("Bad", box + "<p>text</p>", "publish", published="2026-11-10T08:00:00")
        persian = self.fake.add_post("Persian", wp_blocks.box(["x"], "fa"), "publish", published="2026-11-12T08:00:00")
        self.fake.add_post("Clean", "<p>text</p>", "publish", published="2026-11-11T08:00:00")
        self.fake.add_post("Old", box, "publish", published="2026-09-01T08:00:00")                    # older than 30 days
        self.fake.add_post("Draft", box, "draft", published="2026-11-13T08:00:00")                    # not published
        self.assertEqual(sorted(i for i, _ in pub.posts_with_marker(self.wp, TODAY)), sorted([bad, persian]))

    def test_nothing_is_written_while_looking(self):
        pub.posts_with_marker(self.wp, TODAY)
        self.assertEqual(self.fake.writes(), [])

    def test_the_slug_of_a_live_address(self):
        self.assertEqual(pub.slug_of("https://example.test/kitchen-ideas/"), "kitchen-ideas")
        self.assertEqual(pub.slug_of("https://example.test/%D8%A7/"), "ا")
        self.assertEqual(pub.slug_of("https://example.test/"), "")


if __name__ == "__main__":
    unittest.main()
