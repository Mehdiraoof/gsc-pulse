"""Tests for the text of briefs and drafts: Markdown to HTML, Persian rules, dashes, links."""

import unittest

import content_check as cc
import content_docs as cd

DOMAIN = "https://site.test"
ZWNJ = "‌"


class MarkdownTests(unittest.TestCase):

    def test_headings_paragraphs_and_inline(self):
        html = cd.md_to_html("# Title\n\nA line\nsecond line with **bold** and [a link](https://site.test/x?a=1&b=2).\n\n## Next")
        self.assertEqual(html, '<h1>Title</h1><p>A line second line with <b>bold</b> and '
                               '<a href="https://site.test/x?a=1&amp;b=2">a link</a>.</p><h2>Next</h2>')

    def test_text_is_escaped_and_bad_links_are_defused(self):
        html = cd.md_to_html("<script>x</script> & [x](javascript:alert(1))")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="#"', html)

    def test_lists(self):
        self.assertEqual(cd.md_to_html("- a\n- b\n\n1. c\n2. d"), "<ul><li>a</li><li>b</li></ul><ol><li>c</li><li>d</li></ol>")
        self.assertEqual(cd.md_to_html("۱. ا\n۲. ب"), "<ol><li>ا</li><li>ب</li></ol>")
        self.assertEqual(cd.md_to_html("- [ ] check\n- [ ] again"), "<ul><li>[ ] check</li><li>[ ] again</li></ul>")

    def test_tables(self):
        html = cd.md_to_html("| A | B |\n|---|:--:|\n| 1 | **2** |\n| 3 | 4 |")
        self.assertEqual(html, "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td><b>2</b></td></tr>"
                               "<tr><td>3</td><td>4</td></tr></table>")

    def test_a_list_directly_after_a_paragraph_and_rules_are_skipped(self):
        self.assertEqual(cd.md_to_html("Intro\n- a\n---\nEnd"), "<p>Intro</p><ul><li>a</li></ul><p>End</p>")

    def test_an_image_slot_is_a_paragraph_not_a_link(self):
        html = cd.md_to_html("[Image: a | b | c.jpg]")
        self.assertEqual(html, "<p>[Image: a | b | c.jpg]</p>")
        self.assertTrue(cc.IMAGE_SLOT.search(cc.Doc(html).full_text))

    def test_links_in(self):
        self.assertEqual(cd.links_in("a [x](u1) b [y](u2)"), [("x", "u1"), ("y", "u2")])


class PersianTests(unittest.TestCase):

    def test_letters_and_digits(self):
        self.assertEqual(cd.normalize_fa("كتاب علي موسى"),
                         "کتاب علی موسی")
        self.assertEqual(cd.normalize_fa("در سال 2026 و ٢٠"),
                         "در سال ۲۰۲۶ و ۲۰")

    def test_a_number_inside_a_latin_word_stays(self):
        text = "گوشی galaxy S24 و 5G و iPhone15"
        self.assertEqual(cd.normalize_fa(text), text)

    def test_addresses_and_link_targets_stay(self):
        text = "ببینید [مقاله 2](https://site.test/a-2026) و https://site.test/b2026/x"
        out = cd.normalize_fa(text)
        self.assertIn("(https://site.test/a-2026)", out)
        self.assertIn("https://site.test/b2026/x", out)
        self.assertIn("[مقاله ۲]", out)                          # the words of the link are text

    def test_an_image_slot_keeps_its_file_name(self):
        out = cd.normalize_fa("[عکس: كرم | برای 10 | cream-2.jpg]")
        self.assertIn("کرم", out)                                                # the letters are fixed
        self.assertIn("cream-2.jpg", out)
        self.assertIn("برای 10", out)

    def test_the_result_passes_the_persian_checks(self):
        raw = ("# كرم ۱۰ روش\n\nمتن علي در 2026 و galaxy S24.\n")
        html = cd.wrap("fa", cd.md_to_html(cd.clean_text(raw, "fa")))
        self.assertEqual(cc.check_persian(cc.Doc(html)), [])

    def test_dashes(self):
        self.assertEqual(cd.strip_dashes("a — b – c"), "a, b, c")
        self.assertEqual(cd.strip_dashes("10–20 and 5 — 7"), "10-20 and 5-7")
        self.assertEqual(cd.strip_dashes("ا — ب", "fa"), "ا، ب")
        self.assertEqual(cd.strip_dashes("۱–۵"), "۱-۵")
        self.assertEqual(cd.strip_dashes("a - b"), "a - b")


class CalendarAndEscapeTests(unittest.TestCase):

    def test_the_persian_calendar(self):
        from datetime import date
        self.assertEqual(cd.to_jalali(date(2021, 3, 21)), (1400, 1, 1))
        self.assertEqual(cd.to_jalali(date(2025, 3, 21)), (1404, 1, 1))
        self.assertEqual(cd.to_jalali(date(2026, 11, 2)), (1405, 8, 11))

    def test_a_backslash_before_a_bracket_is_taken_away(self):
        out = cd.clean_text(r"قیمت \[needs checking\] و \[لینک\](https://site.test/a)", "fa")
        self.assertNotIn("\\", out)
        self.assertEqual(cd.links_in(out), [("لینک", "https://site.test/a")])
        self.assertIn("[needs checking]", out)


class LinkTests(unittest.TestCase):

    ALLOWED = [("one", f"{DOMAIN}/article/one"), ("two", f"{DOMAIN}/article/two"), ("three", f"{DOMAIN}/article/three"),
               ("four", f"{DOMAIN}/article/four"), ("five", f"{DOMAIN}/article/five"), ("six", f"{DOMAIN}/article/six")]

    def links(self, md):
        return [u for _, u in cd.links_in(md)]

    def test_good_links_stay(self):
        md = "[a](https://site.test/article/one) [b](https://site.test/article/two) [c](https://site.test/article/three/)"
        self.assertEqual(cd.repair_links(md, self.ALLOWED, DOMAIN), md)

    def test_an_address_not_on_the_list_a_repeat_and_extras_lose_their_link(self):
        md = ("[a](https://site.test/article/one) [x](https://site.test/article/unknown) [b](https://site.test/article/two) "
              "[a2](https://site.test/article/one/) [c](https://site.test/article/three) [d](https://site.test/article/four) "
              "[e](https://site.test/article/five) [f](https://site.test/article/six)")
        out = cd.repair_links(md, self.ALLOWED, DOMAIN)
        kept = self.links(out)
        self.assertEqual(len(kept), 5)
        self.assertEqual(len({cc.content.url_key(u) for u in kept}), 5)
        self.assertNotIn("unknown", " ".join(kept))
        self.assertIn(" x ", " " + out + " ")                                       # the words stay
        self.assertTrue(cc.MIN_LINKS <= len(kept) <= cc.MAX_LINKS)

    def test_other_sites_are_left_alone(self):
        md = "[a](https://other.test/x) [b](https://site.test/article/one) [c](https://site.test/article/two) [d](https://site.test/article/three)"
        self.assertEqual(cd.repair_links(md, self.ALLOWED, DOMAIN), md)

    def test_too_few_links_are_made_up_before_the_faq(self):
        md = "# T\n\ntext [a](https://site.test/article/one)\n\n## Frequently asked questions\n\n### Q\n\nA"
        out = cd.repair_links(md, self.ALLOWED, DOMAIN)
        self.assertEqual(len(self.links(out)), 3)
        self.assertLess(out.index("article/two"), out.index("## Frequently"))
        no_faq = cd.repair_links("# T\n\ntext", self.ALLOWED, DOMAIN)
        self.assertEqual(len(self.links(no_faq)), 3)

    def test_nothing_to_offer_nothing_to_add(self):
        self.assertEqual(cd.repair_links("# T\n\ntext", [], DOMAIN), "# T\n\ntext")


class ParsingTests(unittest.TestCase):

    def test_sections(self):
        text = "=== META ===\nMeta title: T\n=== ARTICLE ===\n# H\n\nbody\n=== 3 ===\nx"
        parts = cd.split_sections(text)
        self.assertEqual(parts["META"], "Meta title: T")
        self.assertEqual(parts["ARTICLE"], "# H\n\nbody")
        self.assertEqual(parts["3"], "x")
        self.assertEqual(cd.split_sections("no marks"), {})

    def test_fields(self):
        f = cd.parse_fields("Title tag: Best things\n**Meta description:** About them\n- H1: The H1\nSlug: best-things\nSlug: later\nnot a field")
        self.assertEqual(f, {"title tag": "Best things", "meta description": "About them", "h1": "The H1", "slug": "best-things"})

    def test_a_brief_as_text_and_as_sections(self):
        html = cc_page = ("<h1>kw</h1><h2>Task</h2><p>do it</p><h2>Outline</h2><ul><li>H2: a</li></ul>"
                          "<h2>Internal links</h2><table><tr><td>https://site.test/a</td></tr></table>")
        self.assertEqual(cd.html_to_text(html).splitlines()[:3], ["# kw", "## Task", "do it"])
        sections = cd.sections_of_brief(html)
        self.assertEqual(sections[1], "do it")
        self.assertIn("H2: a", sections[4])
        self.assertIn("https://site.test/a", sections[7])
        fa = cd.sections_of_brief("<h2>" + cd.BRIEF_HEADINGS["fa"][3] + "</h2><p>x</p>")
        self.assertEqual(fa, {4: "x"})


class PageTests(unittest.TestCase):

    def test_every_block_has_the_font_and_a_persian_page_the_direction(self):
        html = cd.wrap("fa", "<h1>a</h1><p>b</p><table><tr><td>c</td></tr></table>")
        self.assertIn('<html lang="fa" dir="rtl">', html)
        self.assertEqual(html.count('style="font-family:Vazirmatn" dir="rtl"'), 3)
        en = cd.wrap("en", "<p>b</p>")
        self.assertNotIn("dir=", en)
        self.assertIn("font-family:Arial", en)

    def test_the_header_box(self):
        box = cd.header_box([("Slug", "a&b"), ("Words", 5)])
        self.assertIn('id="header-box"', box)
        self.assertIn("a&amp;b", box)
        doc = cc.Doc(cd.wrap("en", box + "<h1>T</h1>"))
        self.assertTrue(all(b.in_box for b in doc.blocks if b.tag == "td"))


if __name__ == "__main__":
    unittest.main()
