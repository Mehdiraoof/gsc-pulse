"""What an install says about the brand of its site: logo, site icon and theme colours, read with GET requests only, listed in the
Brand tab for the owner to approve, used on a card only after a yes. Every address and colour here is invented."""

import json
import unittest

import infographic
import wp_brand
import wp_check
from test_history import FakeSheets, patch_time

BASE = "https://example.test"
INSTALL = wp_check.Install("site-x", "", BASE, "WP_SITEX", "u", "p")
INSTALL_FA = wp_check.Install("site-x", "FA", BASE + "/fa", "WP_SITEX_FA", "u", "p")

HOME = """<html><head>
<meta name="theme-color" content="#8D6BFF">
<link rel="icon" href="/wp-content/uploads/icon-32.png" sizes="32x32">
<style>:root{--wp--preset--color--primary:#112233;--wp--preset--color--accent: #abc;--e-global-color-text:#000000ff;
--unrelated:#999999;--wp--preset--color--again:#112233}</style></head>
<body><a class="custom-logo-link"><img width="200" class="custom-logo" src="/wp-content/uploads/logo.png"></a></body></html>"""


def opener(root=None, media=None, page=HOME, down=False):
    def call(url, headers):
        if down:
            return 0, {}, b""
        if url.endswith("/wp-json/"):
            return 200, {}, json.dumps(root if root is not None else {"name": "Example", "namespaces": ["wp/v2"]}).encode()
        if "/wp-json/wp/v2/media/" in url:
            return 200, {}, json.dumps(media or {}).encode()
        if url.rstrip("/") == BASE:
            return 200, {}, page.encode()
        return 404, {}, b""
    return call


class Colours(unittest.TestCase):

    def test_the_theme_colour_comes_first_then_the_variables_each_colour_once(self):
        found = wp_brand.colours_in(HOME)
        self.assertEqual([v for _, v in found], ["#8d6bff", "#112233", "#aabbcc", "#000000"])
        self.assertEqual(found[0][0], "theme-color tag")
        self.assertEqual(found[1][0], "wp--preset--color--primary")

    def test_no_colours_in_a_page_without_any(self):
        self.assertEqual(wp_brand.colours_in("<html><body>text</body></html>"), [])
        self.assertEqual(wp_brand.colours_in(""), [])

    def test_the_text_of_a_colour_is_made_a_six_digit_lower_case_hex(self):
        self.assertEqual(wp_brand.normal_hex("#ABC"), "#aabbcc")
        self.assertEqual(wp_brand.normal_hex("color: #8D6BFF80;"), "#8d6bff")
        self.assertEqual(wp_brand.normal_hex("red"), "")

    def test_the_given_colours_of_the_sites(self):
        self.assertEqual(wp_brand.given_colours({"BRAND_GIVEN": "site-1=#8D6BFF, Site-2=#abc, bad=zzz"}),
                         {"site-1": "#8d6bff", "site-2": "#aabbcc"})
        self.assertEqual(wp_brand.given_colours({}), {})


class Discover(unittest.TestCase):

    def test_the_logo_and_the_icon_of_the_rest_index_are_used_first(self):
        root = {"name": "Example", "site_icon_url": f"{BASE}/icon-512.png", "site_logo": 7}
        found = wp_brand.discover(INSTALL, opener(root, {"source_url": f"{BASE}/logo-big.png"}))
        self.assertEqual((found["title"], found["logo"], found["icon"]), ("Example", f"{BASE}/logo-big.png", f"{BASE}/icon-512.png"))
        self.assertTrue(found["ok"])
        self.assertEqual(found["colours"][0], ("theme-color tag", "#8d6bff"))

    def test_the_home_page_gives_the_logo_and_the_icon_when_the_index_has_none(self):
        found = wp_brand.discover(INSTALL, opener())
        self.assertEqual(found["logo"], f"{BASE}/wp-content/uploads/logo.png")
        self.assertEqual(found["icon"], f"{BASE}/wp-content/uploads/icon-32.png")

    def test_an_install_in_a_folder_is_read_at_its_own_address(self):
        calls = []

        def call(url, headers):
            calls.append(url)
            return 200, {}, (b"{}" if "wp-json" in url else b"<html></html>")
        wp_brand.discover(INSTALL_FA, call)
        self.assertTrue(all(u.startswith(BASE + "/fa") for u in calls))

    def test_an_install_that_does_not_answer_is_said_and_nothing_breaks(self):
        found = wp_brand.discover(INSTALL, opener(down=True))
        self.assertFalse(found["ok"])
        self.assertEqual((found["logo"], found["icon"], found["colours"]), ("", "", []))
        self.assertIn("could not be read", wp_brand.lines_for(INSTALL, found)[0])

    def test_only_get_requests_are_made_and_no_login_is_sent_for_the_public_pages(self):
        seen = []

        def call(url, headers):
            seen.append(headers)
            return opener()(url, headers)
        wp_brand.discover(INSTALL, call)
        self.assertTrue(all("Authorization" not in h for h in seen))

    def test_the_log_line_has_counts_only(self):
        found = wp_brand.discover(INSTALL, opener())
        line = " ".join(wp_brand.lines_for(INSTALL, found))
        self.assertIn("logo yes, site icon yes, 4 colours found", line)
        self.assertNotIn("http", line)
        self.assertNotIn("#", line)


class Rows(unittest.TestCase):

    def test_a_colour_the_owner_gave_is_listed_with_use_yes_and_the_others_wait(self):
        found = wp_brand.discover(INSTALL, opener())
        rows = wp_brand.rows_for(INSTALL, found, "#8d6bff")
        colours = {r[3]: r[4] for r in rows if r[2] == "Colour"}
        self.assertEqual(colours["#8d6bff"], "yes")
        self.assertEqual({v for k, v in colours.items() if k != "#8d6bff"}, {""})
        self.assertTrue(all(r[1] == "all" for r in rows))                           # an install without a language
        self.assertEqual([r[2] for r in rows][:3], ["Brand name", "Logo", "Site icon"])

    def test_a_given_colour_that_is_not_in_the_install_is_added_as_a_row(self):
        rows = wp_brand.rows_for(INSTALL, {"colours": []}, "#123456")
        self.assertIn(["site-x", "all", "Colour", "#123456", "yes", "The colour you gave (not found in the install)."], rows)
        self.assertIn("not found", [r[3] for r in rows if r[2] == "Logo"])

    def test_the_language_of_a_folder_install_is_the_install_label(self):
        rows = wp_brand.rows_for(INSTALL_FA, {"colours": [("x", "#111111")]})
        self.assertTrue(all(r[1] == "FA" for r in rows))

    def test_the_brand_name_is_one_row_for_each_install_and_proposes_the_title_of_the_install(self):
        rows = wp_brand.rows_for(INSTALL_FA, {"title": "Name FA", "colours": []})
        names = [r for r in rows if r[2] == "Brand name"]
        self.assertEqual(len(names), 1)
        self.assertEqual((names[0][1], names[0][3], names[0][4]), ("FA", "Name FA", ""))      # a proposal: not used until yes
        self.assertEqual(wp_brand.rows_for(INSTALL, {"colours": []})[0][3], "")


class Names(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.fs.add_book("DASH", {"Articles": (20, 10)})

    def tab(self):
        return self.fs.books["DASH"][wp_brand.TAB].data

    def test_a_proposed_name_is_not_used_until_the_owner_writes_yes(self):
        wp_brand.write_rows(self.fs, "DASH", wp_brand.rows_for(INSTALL_FA, {"title": "Name FA", "colours": []}))
        self.assertEqual(wp_brand.approved_name(self.fs, "DASH", "site-x", "fa"), "")
        next(r for r in self.tab()[1:] if r[2] == "Brand name")[self.tab()[0].index("Use")] = "yes"
        self.assertEqual(wp_brand.approved_name(self.fs, "DASH", "site-x", "fa"), "Name FA")

    def test_each_install_has_its_own_name_and_an_all_row_is_the_fallback(self):
        wp_brand.write_rows(self.fs, "DASH", [["site-x", "all", "Brand name", "Shared", "yes", ""],
                                              ["site-x", "FA", "Brand name", "نام فارسی", "yes", ""],
                                              ["site-y", "EN", "Brand name", "Other site", "yes", ""]])
        self.assertEqual(wp_brand.approved_name(self.fs, "DASH", "site-x", "fa"), "نام فارسی")
        self.assertEqual(wp_brand.approved_name(self.fs, "DASH", "site-x", "en"), "Shared")
        self.assertEqual(wp_brand.approved_name(self.fs, "DASH", "site-x"), "Shared")
        self.assertEqual(wp_brand.approved_name(self.fs, "DASH", "site-z", "en"), "")

    def test_a_name_the_owner_edited_is_not_proposed_again_on_the_next_check(self):
        rows = wp_brand.rows_for(INSTALL_FA, {"title": "Name FA", "colours": []})
        wp_brand.write_rows(self.fs, "DASH", rows)
        next(r for r in self.tab()[1:] if r[2] == "Brand name")[3] = "My own name"
        self.assertEqual(wp_brand.write_rows(self.fs, "DASH", rows), 0)
        self.assertEqual([r[3] for r in self.tab()[1:] if r[2] == "Brand name"], ["My own name"])

    def test_the_name_goes_into_the_brand_of_the_card(self):
        base = infographic.Brand(name="Default", language="fa")
        self.assertEqual(wp_brand.apply(base, [], "نام فارسی").name, "نام فارسی")
        self.assertEqual(wp_brand.apply(base, ["#112233"]).name, "Default")
        self.assertEqual(wp_brand.apply(base, [], "").primary, base.primary)


class Sheet(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.fs.add_book("DASH", {"Articles": (20, 10)})

    def tab(self):
        return self.fs.books["DASH"][wp_brand.TAB].data

    def test_rows_are_added_once_and_the_owners_edit_stays(self):
        rows = wp_brand.rows_for(INSTALL, wp_brand.discover(INSTALL, opener()), "")
        self.assertEqual(wp_brand.write_rows(self.fs, "DASH", rows), len(rows))
        self.assertEqual(self.tab()[0], wp_brand.HEADER)
        use = self.tab()[0].index("Use")
        row = next(r for r in self.tab()[1:] if r[3] == "#112233")
        row[use] = "yes"                                                             # the owner approves it
        self.assertEqual(wp_brand.write_rows(self.fs, "DASH", rows), 0)
        self.assertEqual(len(self.tab()), len(rows) + 1)
        self.assertEqual(wp_brand.approved(self.fs, "DASH", "site-x"), ["#112233"])

    def test_approved_colours_come_in_the_order_of_the_tab_for_that_site_and_install(self):
        self.assertEqual(wp_brand.write_rows(self.fs, "DASH", [
            ["site-x", "all", "Colour", "#111111", "yes", ""], ["site-x", "FA", "Colour", "#222222", "Yes", ""],
            ["site-x", "EN", "Colour", "#333333", "yes", ""], ["site-y", "all", "Colour", "#444444", "yes", ""],
            ["site-x", "all", "Colour", "#555555", "", ""], ["site-x", "all", "Logo", "#666666", "yes", ""]]), 6)
        self.assertEqual(wp_brand.approved(self.fs, "DASH", "site-x", "fa"), ["#111111", "#222222"])
        self.assertEqual(wp_brand.approved(self.fs, "DASH", "site-x", "en"), ["#111111", "#333333"])
        self.assertEqual(wp_brand.approved(self.fs, "DASH", "site-x"), ["#111111"])

    def test_nothing_is_approved_without_a_brand_tab(self):
        self.assertEqual(wp_brand.approved(self.fs, "DASH", "site-x", "en"), [])

    def test_a_card_uses_the_approved_colours_and_the_placeholder_otherwise(self):
        base = infographic.Brand(language="en")
        self.assertEqual(wp_brand.apply(base, []), base)
        one = wp_brand.apply(base, ["#8d6bff"])
        self.assertEqual((one.primary, one.accent), ("#8d6bff", base.accent))
        two = wp_brand.apply(base, ["#8d6bff", "#112233"])
        self.assertEqual((two.primary, two.accent), ("#8d6bff", "#112233"))


if __name__ == "__main__":
    unittest.main()
