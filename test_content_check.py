"""Tests for the content creation checker (step 1): each rule of the "done" checklist, on small
documents and tables made up here. The checker itself is content_check.py."""

import unittest

import content
import content_check as cc

DOMAIN = "https://site.test"
ZWNJ = "‌"


def page(body: str, rtl: bool = False, font: str = "Arial", box: bool = True) -> str:
    attrs = ' dir="rtl"' if rtl else ""
    head = f"<style>body{{font-family:{font}}}</style>"
    table = '<table id="header-box"><tr><td>Slug</td><td>a-1</td></tr></table>' if box else ""
    return f"<html{attrs}><head>{head}</head><body>{table}{body}</body></html>"


def links(n: int, base: str = DOMAIN) -> str:
    return "<p>" + " ".join(f'<a href="{base}/article/p{i}">p{i}</a>' for i in range(n)) + "</p>"


GOOD_BODY = ("<h1>A guide</h1><p>Direct answer.</p><h2>Options</h2><table><tr><td>x</td></tr></table>"
             + links(4) + "<p>[Image: a | b | c.jpg]</p><p>[Personal experience: a]</p><p>[Expert quote: b]</p>"
             "<h2>FAQ</h2><h3>Q?</h3><p>A.</p><h2>Summary</h2><p>Done.</p><h2>Quality check</h2><ul><li>ok</li></ul>")


def draft(body=GOOD_BODY, **kw):
    return page(body, **kw)


def codes(failures) -> set:
    return {f.code for f in failures}


def check(html, language="en", comparison=True):
    return cc.check_draft(html, language=language, domain=DOMAIN, has_comparison=comparison)


class ParsingTests(unittest.TestCase):

    def test_headings_lists_tables_links_and_the_box(self):
        doc = cc.Doc(page("<h1>T</h1><h2>S</h2><ul><li>a</li><li>b</li></ul><ol><li>c</li></ol>"
                          "<table><tr><td>x</td></tr></table><p><a href='/x'>l</a></p>"))
        self.assertEqual([(b.level, b.text) for b in doc.headings()], [(1, "T"), (2, "S")])
        self.assertEqual(doc.lists, [2, 1])
        self.assertEqual(doc.tables, 1)                                  # the header box is not counted
        self.assertEqual(doc.links, [("/x", False)])
        self.assertTrue(any(b.in_box for b in doc.blocks))

    def test_the_header_box_is_the_table_before_the_first_heading_even_without_an_id(self):
        doc = cc.Doc("<table><tr><td>a</td></tr></table><h1>T</h1><table><tr><td>b</td></tr></table>")
        self.assertEqual(doc.tables, 1)
        self.assertEqual([b.in_box for b in doc.blocks if b.tag == "td"], [True, False])
        no_box = cc.Doc("<h1>T</h1><table><tr><td>b</td></tr></table>")
        self.assertEqual((no_box.tables, [b.in_box for b in no_box.blocks if b.tag == "td"]), (1, [False]))

    def test_links_that_google_wrapped_are_unwrapped(self):
        href = ("https://www.google.com/url?q=https://site.test/article/x&amp;sa=D&amp;source=editors"
                "&amp;ust=1&amp;usg=abc")
        doc = cc.Doc(f'<p><a href="{href}">x</a> <a href="https://site.test/y">y</a></p>')
        self.assertEqual(doc.links, [("https://site.test/article/x", False), ("https://site.test/y", False)])
    def test_direction_is_inherited_and_a_style_counts(self):
        doc = cc.Doc('<html dir="rtl"><body><p>a</p><div dir="ltr"><p>b</p></div>'
                     '<p style="direction: rtl">c</p></body></html>')
        self.assertEqual([b.dir for b in doc.blocks], ["rtl", "ltr", "rtl"])
        self.assertEqual([b.dir for b in cc.Doc("<p>a</p>").blocks], [""])

    def test_styles_and_scripts_are_not_text(self):
        doc = cc.Doc("<style>a{}</style><script>var AI;</script><title>PulseO</title><p>text</p>")
        self.assertEqual(doc.full_text.strip(), "text")
        self.assertIn("a{}", doc.style_text())

    def test_a_line_break_is_a_space_and_entities_are_decoded(self):
        doc = cc.Doc("<p>a<br>b &amp; c</p>")
        self.assertEqual(doc.blocks[0].text, "a b & c")


class BannedTests(unittest.TestCase):

    def test_the_tool_name_ai_claude_and_dashes(self):
        for text in ("PulseO", "pulseo", "Claude", "written by AI", "Gemini says"):
            self.assertEqual(codes(cc.check_banned(cc.Doc(f"<p>{text}</p>"))), {"tool-name"}, text)
        for text in ("MAIN", "said", "again", "chair", "Aida"):
            self.assertEqual(cc.check_banned(cc.Doc(f"<p>{text}</p>")), [], text)
        self.assertEqual(codes(cc.check_banned(cc.Doc("<p>a — b</p>"))), {"dash"})
        self.assertEqual(codes(cc.check_banned(cc.Doc("<p>a – b</p>"))), {"dash"})
        self.assertEqual(cc.check_banned(cc.Doc("<p>a - b, a-b</p>")), [])           # a hyphen is fine

    def test_a_failure_never_holds_the_text(self):
        html = page("<h1>T</h1><p>SECRETKEYWORD PulseO —</p>")
        report = cc.Report().add(cc.check_banned(cc.Doc(html)))
        self.assertFalse(report.ok)
        self.assertNotIn("SECRETKEYWORD", report.text())
        self.assertNotIn("PulseO", report.text())


class PersianTests(unittest.TestCase):

    def persian(self, body, **kw):
        return cc.check_persian(cc.Doc(page(body, rtl=True, font="Vazirmatn", **kw)))

    def test_a_right_document_passes(self):
        self.assertEqual(self.persian("<h1>عنوان ۱۰ ایده</h1><p>متن ساده با S24 و iPhone15.</p>"), [])

    def test_arabic_letters(self):
        self.assertEqual(codes(self.persian("<p>كتاب</p>")), {"arabic-letter"})
        self.assertEqual(codes(self.persian("<p>علي</p>")), {"arabic-letter"})
        self.assertEqual(codes(self.persian("<p>موسى</p>")), {"arabic-letter"})

    def test_digits(self):
        self.assertEqual(codes(self.persian("<p>در سال 2026</p>")), {"latin-digit"})
        self.assertEqual(codes(self.persian("<p>در سال ٢٠</p>")), {"arabic-digit"})
        self.assertEqual(self.persian("<p>مدل galaxy s24 و 5G</p>"), [])               # a number inside a Latin word
        self.assertEqual(self.persian("<p>[عکس: الف | ب | file-2.jpg]</p>"), [])         # an image slot
        self.assertEqual(self.persian("<p>https://x.test/a2 را ببینید</p>"), [])         # an address
        self.assertEqual(self.persian("<p>در سال ۱۴۰۳</p>"), [])

    def test_the_header_box_may_hold_latin_digits(self):
        self.assertEqual(self.persian("<p>متن</p>"), [])                                   # the box has 'a-1'

    def test_direction_and_font(self):
        no_dir = cc.Doc(page("<p>متن فارسی</p>", rtl=False, font="Vazirmatn"))
        self.assertEqual(codes(cc.check_persian(no_dir)), {"not-rtl"})
        no_font = cc.Doc(page("<p>متن فارسی</p>", rtl=True, font="Arial"))
        self.assertEqual(codes(cc.check_persian(no_font)), {"font"})
        english_block = cc.Doc(page("<p>Only English here</p>", rtl=False, font="Vazirmatn"))
        self.assertEqual(cc.check_persian(english_block), [])                              # no Persian, nothing to flip


class CountTests(unittest.TestCase):

    def counts(self, h1, body):
        return cc.check_counts(cc.Doc(page(f"<h1>{h1}</h1>{body}")))

    def test_a_list_with_that_many_items(self):
        self.assertEqual(self.counts("10 ideas", "<ol>" + "<li>x</li>" * 10 + "</ol>"), [])
        self.assertEqual(self.counts("۱۰ ایده", "<ol>" + "<li>x</li>" * 10 + "</ol>"), [])         # Persian digits
        self.assertEqual(codes(self.counts("10 ideas", "<ol>" + "<li>x</li>" * 7 + "</ol>")), {"count-mismatch"})

    def test_that_many_sections(self):
        body = "".join(f"<h2>Idea {i}</h2><p>t</p>" for i in range(5)) + "<h2>FAQ</h2><h2>Summary</h2>"
        self.assertEqual(self.counts("5 tips for sleep", body), [])
        self.assertEqual(codes(self.counts("6 tips for sleep", body)), {"count-mismatch"})

    def test_titles_without_a_count_and_years_are_left_alone(self):
        self.assertEqual(self.counts("A guide to sleep", ""), [])
        self.assertEqual(self.counts("Best laptops 2026", ""), [])
        self.assertEqual(self.counts("Top 1 reason", ""), [])


class DraftTests(unittest.TestCase):

    def test_a_right_draft_passes(self):
        self.assertEqual(check(draft()), [])

    def test_one_h1(self):
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("<h1>A guide</h1>", "<h1>A</h1><h1>B</h1>")))), {"h1-count"})
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("<h1>A guide</h1>", "")))), {"h1-count"})

    def test_faq_and_summary_are_h2(self):
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("<h2>FAQ</h2>", "<h3>FAQ</h3>")))), {"faq-not-h2"})
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("<h2>Summary</h2>", "<h3>Summary</h3>")))),
                         {"summary-not-h2"})
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("FAQ", "Questions")))), {"no-faq"})
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("Summary", "The end")))), {"no-summary"})

    def test_a_table_when_the_brief_has_a_comparison(self):
        no_table = GOOD_BODY.replace("<table><tr><td>x</td></tr></table>", "")
        self.assertEqual(codes(check(draft(no_table))), {"no-table"})
        self.assertEqual(check(draft(no_table), comparison=False), [])

    def test_three_to_five_internal_links_each_once(self):
        for n, bad in ((2, True), (3, False), (5, False), (6, True)):
            body = GOOD_BODY.replace(links(4), links(n))
            self.assertEqual(codes(check(draft(body))), {"link-count"} if bad else set(), n)
        twice = GOOD_BODY.replace(links(4), links(3) + f'<a href="{DOMAIN}/article/p0/">again</a>')
        self.assertEqual(codes(check(draft(twice))), {"link-repeated"})
        outside = GOOD_BODY.replace(links(4), links(4) + links(3, "https://other.test"))
        self.assertEqual(check(draft(outside)), [])                              # other sites' links are not counted
        relative = GOOD_BODY.replace(links(4), "<p>" + "".join(f'<a href="/article/p{i}">x</a>' for i in range(3)) + "</p>")
        self.assertEqual(check(draft(relative)), [])

    def test_image_slots_quality_check_and_header_box(self):
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("[Image: a | b | c.jpg]", "")))), {"no-image-slot"})
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("Quality check", "Notes")))), {"no-quality-check"})
        self.assertEqual(codes(check(draft(box=False))), {"no-header-box"})

    def test_the_persian_slot_and_headings(self):
        body = (GOOD_BODY.replace("[Image: a | b | c.jpg]", "[عکس: الف | ب | c.jpg]").replace("FAQ", "پرسش‌های متداول")
                .replace("[Personal experience: a]", "[تجربه شخصی: ا]").replace("[Expert quote: b]", "[نظر متخصص: ب]")
                .replace("Summary", "جمع‌بندی").replace("Quality check", "بررسی کیفیت"))
        self.assertEqual(check(draft(body, rtl=True, font="Vazirmatn"), language="fa"), [])

    def test_two_to_four_human_touch_markers(self):
        one = GOOD_BODY.replace("<p>[Expert quote: b]</p>", "")
        self.assertEqual(codes(check(draft(one))), {"human-touch-count"})
        none = one.replace("<p>[Personal experience: a]</p>", "")
        self.assertEqual(codes(check(draft(none))), {"human-touch-count"})
        four = GOOD_BODY.replace("<p>[Expert quote: b]</p>", "<p>[Expert quote: b] [Personal experience: c] [Expert quote: d]</p>")
        self.assertEqual(check(draft(four)), [])
        five = GOOD_BODY.replace("<p>[Expert quote: b]</p>", "<p>[Expert quote: b] [Personal experience: c] [Expert quote: d] [Expert quote: e]</p>")
        self.assertEqual(codes(check(draft(five))), {"human-touch-count"})
        persian_in_english = GOOD_BODY.replace("[Personal experience: a]", "[تجربه شخصی: ا]").replace("[Expert quote: b]", "[نظر متخصص: ب]")
        self.assertEqual(codes(check(draft(persian_in_english))), {"human-touch-count"})   # the markers are those of the language
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("a]", "a]").replace("[Personal experience:", "[Personal experience :")))), set())

    def test_banned_words_count_in_a_draft_too(self):
        self.assertEqual(codes(check(draft(GOOD_BODY.replace("Done.", "Done by AI.")))), {"tool-name"})

    def test_persian_rules_only_for_persian(self):
        arabic = GOOD_BODY.replace("Done.", "ك")
        self.assertEqual(check(draft(arabic)), [])
        self.assertIn("arabic-letter", codes(check(draft(arabic, rtl=True, font="Vazirmatn"), language="fa")))


class BriefTests(unittest.TestCase):

    SECTIONS = ["Task", "Keywords, intent and reader", "Title tag, meta description, H1 and slug", "Outline",
                "Entities, facts and sources", "FAQ", "Internal links", "Word count, format and tone",
                "Writer checklist"]
    NINTH = "Keep, remove, rewrite"

    def brief(self, sections=None, extra=""):
        return page("<h1>kw</h1>" + "".join(f"<h2>{s}</h2><p>x</p>" for s in (sections or self.SECTIONS)) + extra)

    def test_a_complete_brief_has_nine_sections_and_ten_for_an_update(self):
        self.assertEqual(cc.check_brief(self.brief()), [])
        self.assertEqual(len(cc.BRIEF_SECTIONS), 10)

    def test_a_missing_section_is_counted_not_quoted(self):
        failures = cc.check_brief(self.brief(self.SECTIONS[:-2]))
        self.assertEqual(codes(failures), {"missing-section"})
        self.assertEqual(failures[0].detail, "2 missing")

    def test_the_ninth_section_only_for_an_update(self):
        self.assertEqual(codes(cc.check_brief(self.brief(), update=True)), {"missing-section"})
        self.assertEqual(cc.check_brief(self.brief(extra=f"<h2>{self.NINTH}</h2>"), update=True), [])
        self.assertEqual(cc.check_brief(self.brief(extra=f"<h2>{self.NINTH}</h2>")), [])        # harmless on a new page

    def test_every_section_is_found_by_its_own_heading_in_both_languages(self):
        import content_docs as cd
        for lang in ("en", "fa"):
            heads = cd.BRIEF_HEADINGS[lang]
            self.assertEqual(len(heads), 10)
            doc = page("<h1>kw</h1>" + "".join(f"<h2>{h}</h2><p>x</p>" for h in heads))
            self.assertEqual(cc.check_brief(doc, update=True), [], lang)
            for i in range(10):                                           # leave one out: exactly that one is missing
                rest = [h for j, h in enumerate(heads) if j != i]
                found = cc.check_brief(page("<h1>kw</h1>" + "".join(f"<h2>{h}</h2><p>x</p>" for h in rest)), update=True)
                self.assertEqual([f.detail for f in found], ["1 missing"], (lang, i))

    def test_two_h1_and_banned_words(self):
        self.assertEqual(codes(cc.check_brief(self.brief(extra="<h1>again</h1>"))), {"two-h1"})
        self.assertEqual(codes(cc.check_brief(self.brief(extra="<p>PulseO</p>"))), {"tool-name"})

HEADER = ["Primary Keyword", "Published URL", "Status", "To Do", "Attachments", "Notes/Checklist", "Meta Title", "Score"]


def table(*rows):
    return cc.Snapshot(HEADER, [dict(zip(HEADER, r)) for r in rows])


class DashboardDiffTests(unittest.TestCase):

    BEFORE = [("a", "", "Inboxed", "Brief", "", "note", "", 5), ("b", "", "Inboxed", "Keep", "", "", "", 6),
              ("c", "https://site.test/article/c", "Published", "Keep", "L0", "", "kept", 7)]
    A_ = "t:a"

    def run_diff(self, after_rows, processed=("t:a",)):
        return cc.diff_dashboard(table(*self.BEFORE), table(*after_rows), set(processed))

    def after(self, changes=None):
        rows = [list(r) for r in self.BEFORE]
        for (i, col), value in (changes or {}).items():
            rows[i][HEADER.index(col)] = value
        return rows

    def test_no_change_is_fine(self):
        self.assertEqual(self.run_diff(self.BEFORE, processed=()), [])

    def test_the_allowed_changes_of_a_processed_row(self):
        rows = self.after({(0, "Status"): "Briefed", (0, "To Do"): "Write", (0, "Attachments"): "link",
                             (0, "Notes/Checklist"): "note\nmore", (0, "Meta Title"): "new"})
        self.assertEqual(self.run_diff(rows), [])

    def test_another_row_changed(self):
        rows = self.after({(1, "Status"): "Briefed"})
        self.assertEqual(codes(self.run_diff(rows)), {"other-row-changed"})

    def test_a_column_that_is_not_allowed(self):
        self.assertEqual(codes(self.run_diff(self.after({(0, "Score"): 9}))), {"cell-not-allowed"})

    def test_cells_filled_only_if_empty(self):
        rows = self.after({(2, "Meta Title"): "new"})
        self.assertEqual(codes(self.run_diff(rows, processed=("u:https://site.test/article/c",))), {"filled-cell-overwritten"})

    def test_attachments_and_notes_are_only_added_to(self):
        rows = self.after({(2, "Attachments"): "L0\nL1"})
        self.assertEqual(self.run_diff(rows, processed=("u:https://site.test/article/c",)), [])
        rows = self.after({(0, "Notes/Checklist"): "changed"})
        self.assertEqual(codes(self.run_diff(rows)), {"not-an-append"})

    def test_rows_are_matched_by_key_not_by_position(self):
        rows = self.after()[::-1]
        self.assertEqual(self.run_diff(rows, processed=()), [])

    def test_a_row_lost_or_added(self):
        self.assertEqual(codes(self.run_diff(self.after()[:2])), {"row-lost"})
        self.assertEqual(codes(self.run_diff(self.after() + [("d", "", "Inboxed", "Brief", "", "", "", 1)])), {"row-added"})

    def test_a_changed_keyword_is_a_lost_row_and_a_new_one(self):
        rows = self.after({(0, "Primary Keyword"): "a2"})
        self.assertEqual(codes(self.run_diff(rows)), {"row-lost", "row-added"})

    def test_numbers_and_their_text_are_the_same(self):
        rows = self.after({(1, "Score"): "6"})
        self.assertEqual(self.run_diff(rows, processed=()), [])

    def test_the_header_must_stay(self):
        other = cc.Snapshot(HEADER[:-1], [{k: v for k, v in r.items() if k != "Score"} for r in table(*self.BEFORE).rows])
        self.assertIn("header-changed", codes(cc.diff_dashboard(table(*self.BEFORE), other, set())))

    def test_twin_addresses_are_different_rows(self):
        a, b = f"https://site.test/article/x{ZWNJ}y", "https://site.test/article/xy"
        before = table(("k1", a, "Published", "Keep", "", "", "", 1), ("k2", b, "Published", "Keep", "", "", "", 1))
        after = table(("k1", a, "Published", "Keep", "", "", "", 1), ("k2", b, "Published", "Keep", "", "", "", 1))
        self.assertEqual(cc.diff_dashboard(before, after, set()), [])
        swapped = table(("k1", b, "Published", "Keep", "", "", "", 1), ("k2", a, "Published", "Keep", "", "", "", 1))
        self.assertTrue(cc.diff_dashboard(before, swapped, set()))

    def test_a_failure_names_the_row_and_column_only(self):
        failures = self.run_diff(self.after({(1, "Status"): "SECRETWORD"}))
        self.assertEqual(failures[0].where, "row 3 column Status")
        self.assertNotIn("SECRETWORD", cc.Report().add(failures).text())

    def test_snapshot_reads_a_sheet(self):
        snap = cc.Snapshot.of([["Primary Keyword", "Status"], ["a", "Inboxed"], ["b"]])
        self.assertEqual(snap.rows, [{"Primary Keyword": "a", "Status": "Inboxed"}, {"Primary Keyword": "b", "Status": ""}])


class EmailAndRepeatTests(unittest.TestCase):

    def test_one_email_per_site_with_the_links(self):
        sent = [("s1", "Briefs ready", "See https://d/1 and https://d/2")]
        self.assertEqual(cc.check_emails(sent, {"s1": ["https://d/1", "https://d/2"], "s2": []}), [])

    def test_wrong_emails(self):
        sent = [("s1", "x", "https://d/1"), ("s1", "x", "https://d/1"), ("s2", "x", "hello")]
        failures = cc.check_emails(sent, {"s1": ["https://d/1"], "s2": []})
        self.assertEqual(codes(failures), {"email-count", "email-without-work"})
        self.assertEqual(codes(cc.check_emails([("s1", "x", "none")], {"s1": ["https://d/1"]})), {"link-missing"})
        self.assertEqual(codes(cc.check_emails([("s1", "PulseO", "https://d/1")], {"s1": ["https://d/1"]})), {"tool-name"})
        self.assertEqual(codes(cc.check_emails([], {"s1": ["https://d/1"]})), {"email-count"})

    def test_a_second_run_adds_no_doc(self):
        before = {"f1": {"a", "b"}, "f2": set()}
        self.assertEqual(cc.check_no_new_docs(before, {"f1": {"a", "b"}, "f2": set()}), [])
        self.assertEqual(codes(cc.check_no_new_docs(before, {"f1": {"a", "b", "c"}, "f2": set()})), {"doc-added"})
        self.assertEqual(codes(cc.check_no_new_docs(before, {"f1": {"a"}, "f2": set()})), {"doc-removed"})


class ReportTests(unittest.TestCase):

    def test_the_report(self):
        r = cc.Report()
        self.assertTrue(r.ok)
        self.assertEqual(r.text(), "all checks passed")
        r.add([cc.Failure(5, "no-table", "doc"), cc.Failure(9, "row-lost", "row 4")])
        self.assertFalse(r.ok)
        self.assertEqual(r.items(), [5, 9])
        self.assertEqual(r.codes(), {"no-table", "row-lost"})
        self.assertIn("item 5 no-table (doc)", r.text())

    def test_content_row_key_is_what_rows_are_matched_on(self):
        self.assertEqual(content.row_key({"Published URL": "https://site.test/a/", "Primary Keyword": "x"}),
                         content.row_key({"Published URL": "https://site.test/a", "Primary Keyword": "y"}))


if __name__ == "__main__":
    unittest.main()
