"""Two languages in one site: the pages of both are rows of the same tabs, each with its own keyword and numbers, and
each page is paired with its translation (hreflang tags, else the same address in the other language's folder) when
there is one. A fake site (example.test); all pages and searches are invented."""

import json
import unittest
from unittest import mock

import content
import content_run
import site_check
import sites
import test_content_run as tcr
from site_check import Probe
from test_content_run import ROOT, Base

FA = f"{ROOT}/fa"
ART, FA_ART = f"{ROOT}/istanbul-weather-in-february/", f"{FA}/istanbul-weather-in-february/"
ABOUT, FA_ABOUT = f"{ROOT}/about-us/", f"{FA}/about-us/"
ONLY_EN = f"{ROOT}/english-only-guide/"
FA_ONLY = f"{FA}/persian-only-guide/"
DECLARED, FA_DECLARED = f"{ROOT}/hreflang-page/", f"{FA}/translated-with-another-name/"
NO_TRANSLATION = f"{ROOT}/declares-itself-only/"
FALLBACK_EN, FALLBACK_FA = f"{ROOT}/fallback-guide/", f"{FA}/fallback-guide/"
POSS_EN, POSS_FA = f"{ROOT}/turkey-plastic-surgery-cost-what-you-need-to-know/", f"{FA}/turkey-plastic-surgery-cost/"
HOTEL_EN, HOTEL_FA = f"{ROOT}/hotels-in-osmanbey-istanbul/", f"{FA}/hotels-in-uskudar-istanbul/"
TXT_EN, TXT_FA = f"{ROOT}/visa-guide/", f"{FA}/visa-guide/"                       # the Persian page is written in English
PERSIAN_TEXT, ENGLISH_TEXT = (900, 20, 920), (0, 900, 900)                      # letters in Arabic script, in Latin script, all
BROKEN = f"{ROOT}/unreadable-page/"
HOME, FA_HOME = f"{ROOT}/", f"{FA}/"

FILES = {f"{ROOT}/post-sitemap.xml": [HOME, ART, ONLY_EN, DECLARED, NO_TRANSLATION, FALLBACK_EN, BROKEN, POSS_EN, HOTEL_EN, TXT_EN],
         f"{ROOT}/page-sitemap.xml": [HOME, ABOUT],
         f"{FA}/post-sitemap.xml": [FA_HOME, FA_ART, FA_ONLY, FA_DECLARED, FALLBACK_FA, POSS_FA, HOTEL_FA, TXT_FA],
         f"{FA}/page-sitemap.xml": [FA_HOME, FA_ABOUT]}
EVERY = sorted({u for urls in FILES.values() for u in urls})

PAGES = {u: (5, 300, 6.0) for u in EVERY if u not in (HOME, FA_HOME)}
PAGES[FA_ART] = (9, 700, 5.0)
PAIRS = [("istanbul weather in february", ART, 5, 300, 6.0), ("istanbul weather in february fa", FA_ART, 9, 700, 5.0),
         ("about us", ABOUT, 5, 300, 6.0), ("about us fa", FA_ABOUT, 5, 300, 6.0),
         ("english only guide", ONLY_EN, 5, 300, 6.0), ("persian only guide", FA_ONLY, 5, 300, 6.0),
         ("hreflang page", DECLARED, 5, 300, 6.0), ("translated with another name", FA_DECLARED, 5, 300, 6.0),
         ("declares itself only", NO_TRANSLATION, 5, 300, 6.0),
         ("fallback guide", FALLBACK_EN, 5, 300, 6.0), ("fallback guide fa", FALLBACK_FA, 5, 300, 6.0),
         ("unreadable page", BROKEN, 5, 300, 6.0),
         ("turkey plastic surgery cost", POSS_EN, 5, 300, 6.0), ("turkey plastic surgery cost fa", POSS_FA, 5, 300, 6.0),
         ("hotels in osmanbey", HOTEL_EN, 5, 300, 6.0), ("hotels in uskudar", HOTEL_FA, 5, 300, 6.0),
         ("visa guide", TXT_EN, 5, 300, 6.0), ("visa guide fa", TXT_FA, 5, 300, 6.0)]

TABS = [{"name": "Articles", "sitemap": "post-sitemap", "exclude_regex": r"^https://example\.test/(fa/?)?$"},
        {"name": "Landing Pages", "sitemap": "page-sitemap"}]


def site_with(**extra):
    block = {"enabled": True, "min_gap_impressions_90d": 100, "tabs": TABS,
             "languages": {"en": "", "fa": "/fa"}, "extra_sitemaps": [f"{FA}/sitemap_index.xml"]}
    block.update(extra)
    site = sites.parse_sites_json(json.dumps([{
        "name": "site-x", "site_url": "sc-domain:example.test", "auth": "me",
        "deck": {"enabled": True, "language": "en", "site_label": "Example", "recipients": []},
        "content": block}]))[0]
    site.reports_sheet_id = "REP"
    return site


def page(url, lang, **kw):
    slug = [p for p in url.split("/") if p][-1] if url.rstrip("/") not in (ROOT, FA) else "home"
    return Probe(200, "", slug.replace("-", " ").title(), lang, kw.get("alternates", {}), kw.get("script", PERSIAN_TEXT if lang == "fa" else ENGLISH_TEXT))


def live_site():
    """What the fake site answers when a page is read."""
    table = {u: page(u, "fa" if u.startswith(FA + "/") else "en") for u in EVERY}
    table[DECLARED] = page(DECLARED, "en", alternates={"en": DECLARED, "fa": FA_DECLARED})
    table[FA_DECLARED] = page(FA_DECLARED, "fa", alternates={"en": DECLARED, "fa": FA_DECLARED})
    table[NO_TRANSLATION] = page(NO_TRANSLATION, "en", alternates={"en": NO_TRANSLATION})
    table[FALLBACK_FA] = page(FALLBACK_FA, "en")                          # the folder answers with English text
    table[TXT_FA] = page(TXT_FA, "fa", script=ENGLISH_TEXT)               # declares Persian, but the text is English
    table[BROKEN] = Probe(503)
    return lambda url: table.get(url, Probe(404))


class ConfigTests(unittest.TestCase):

    def test_the_languages_and_the_extra_sitemaps_are_read(self):
        site = site_with()
        self.assertEqual(site.content.languages, {"en": "", "fa": "/fa"})
        self.assertEqual(site.content.extra_sitemaps, [f"{FA}/sitemap_index.xml"])
        self.assertIn(f"{FA}/sitemap_index.xml", site.content.sensitive_values())
        self.assertEqual(site_with(languages={}).content.languages, {})

    def test_bad_values_are_refused(self):
        for bad in ({"languages": {"en": ""}}, {"languages": {"en": "", "xx": "/xx"}}, {"languages": {"en": "", "fa": "fa"}},
                    {"languages": ["en", "fa"]}, {"extra_sitemaps": "x"}, {"extra_sitemaps": ["ftp://x"]},
                    {"extra_sitemaps": [f"{ROOT}/s{i}.xml" for i in range(6)]}):
            with self.assertRaises(sites.SitesConfigError, msg=bad):
                site_with(**bad)


class PageFactsTests(unittest.TestCase):

    def test_language_and_translations_come_from_the_head_of_the_page(self):
        text = ('<!doctype html><html dir="rtl" lang="fa-IR"><head><title>  A &amp; B </title>'
                '<link rel="alternate" hreflang="en-US" href="/english/" />'
                "<link href='https://x.test/fa/page/' hreflang='fa-IR' rel='alternate'>"
                '<link rel="alternate" hreflang="x-default" href="https://x.test/">'
                '<link rel="alternate" type="application/rss+xml" href="/feed/"></head></html>')
        title, lang, alternates = site_check.parse_page(text, "https://x.test/fa/page/")
        self.assertEqual((title, lang), ("A & B", "fa"))
        self.assertEqual(alternates, {"en": "https://x.test/english/", "fa": "https://x.test/fa/page/"})

    def test_a_page_without_tags_has_a_language_and_no_translations(self):
        self.assertEqual(site_check.parse_page('<html lang="en-US"><head><title>T</title></head>'), ("T", "en", {}))
        self.assertEqual(site_check.parse_page("<p>no head</p>"), ("", "", {}))

    def test_the_extra_index_is_read_with_the_others(self):
        index = lambda *files: ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" +        # noqa: E731
                                "".join(f"<sitemap><loc>{f}</loc></sitemap>" for f in files) + "</sitemapindex>").encode()
        urlset = lambda *urls: ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" +              # noqa: E731
                                "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>").encode()
        table = {f"{ROOT}/robots.txt": f"Sitemap: {ROOT}/sitemap_index.xml".encode(),
                 f"{ROOT}/sitemap_index.xml": index(f"{ROOT}/post-sitemap.xml"),
                 f"{ROOT}/post-sitemap.xml": urlset(ART),
                 f"{FA}/sitemap_index.xml": index(f"{FA}/post-sitemap.xml"),
                 f"{FA}/post-sitemap.xml": urlset(FA_ART)}
        get = lambda a: (200, table[a]) if a in table else (404, b"")                                         # noqa: E731
        files, ok = site_check.read_sitemap_files(ROOT, get, [f"{FA}/sitemap_index.xml"])
        self.assertEqual(files, {f"{ROOT}/post-sitemap.xml": [ART], f"{FA}/post-sitemap.xml": [FA_ART]})
        self.assertEqual(site_check.read_sitemap_files(ROOT, get)[0], {f"{ROOT}/post-sitemap.xml": [ART]})


class MirrorTests(unittest.TestCase):

    def test_the_folder_is_swapped(self):
        langs = {"en": "", "fa": "/fa"}
        self.assertEqual(content_run.mirror_url(ART, "en", "fa", langs), FA_ART)
        self.assertEqual(content_run.mirror_url(FA_ART, "fa", "en", langs), ART)
        self.assertEqual(content_run.mirror_url(HOME, "en", "fa", langs), FA_HOME)
        self.assertEqual(content_run.mirror_url(FA_HOME, "fa", "en", langs), HOME)
        self.assertEqual(content_run.mirror_url(FA_ART, "en", "fa", langs), "")          # not an English address
        self.assertEqual(content_run.mirror_url(ART, "fa", "en", langs), "")             # not under /fa

    def test_the_names_of_the_alerts(self):
        self.assertEqual(content_run.missing_alert("fa"), "No Persian version")
        self.assertEqual(content_run.missing_alert("en"), "No English version")


class FakeSiteBase(Base):

    def setUp(self):
        super().setUp()
        for target, value in (("PAGES", PAGES), ("PAIRS", PAIRS)):
            patcher = mock.patch.object(tcr, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.site = site_with()
        content_run.fill_tab_members(self.site.content, FILES)
        self.live = live_site()

    def sitemap(self):
        return lambda: (EVERY, True)

    def go(self, **kw):
        return self.run_it(sitemap=self.sitemap(), probe=self.live, **kw)

    def row(self, url):
        shown = content.display_url(url)
        for tab in ("Articles", "Landing Pages"):
            for r in self.table(tab)[1]:
                if r.get(content.URL) == shown:
                    return r
        raise AssertionError(f"no row for {url}")


class PersianRowsTests(FakeSiteBase):

    def test_the_persian_pages_are_rows_of_the_same_tabs_with_their_own_keyword_and_numbers_and_no_language_tab(self):
        result = self.go()
        titles = list(self.fs.books[self.file_id])
        self.assertEqual(titles[:3], ["Articles", "Landing Pages", "Guide"])
        self.assertFalse([t for t in titles if t.lower() in ("persian", "english", "fa", "en")])
        fa, en = self.row(FA_ART), self.row(ART)
        self.assertEqual(fa[content.KEY], "istanbul weather in february fa")
        self.assertEqual(en[content.KEY], "istanbul weather in february")
        self.assertEqual((int(fa["Clicks (90d)"]), int(en["Clicks (90d)"])), (9, 5))               # their own numbers
        self.assertEqual({r[content.URL] for r in self.table("Landing Pages")[1] if r.get(content.URL)},
                         {content.display_url(u) for u in (HOME, FA_HOME, ABOUT, FA_ABOUT)})
        articles = {r[content.URL] for r in self.table("Articles")[1] if r.get(content.URL)}
        self.assertNotIn(content.display_url(FA_HOME), articles)                                   # excluded like the English home
        self.assertEqual(result.languages["added fa"], 8)             # the home page, the about page and six articles


class PairingTests(FakeSiteBase):

    def test_pairs_by_the_hreflang_tags_else_by_the_address_in_the_other_folder(self):
        result = self.go()
        self.assertEqual(self.row(ART)["Related URLs"], FA_ART)
        self.assertEqual(self.row(FA_ART)["Related URLs"], ART)
        self.assertEqual(self.row(ABOUT)["Related URLs"], FA_ABOUT)
        self.assertEqual(self.row(DECLARED)["Related URLs"], FA_DECLARED)               # the tag names the page, not the folder
        self.assertEqual(self.row(FA_DECLARED)["Related URLs"], DECLARED)
        for url in (ART, FA_ART, ABOUT, FA_ABOUT, DECLARED, FA_DECLARED):
            self.assertNotIn("version", self.row(url)["Alerts"], url)
        self.assertEqual(self.row(HOME)["Related URLs"], FA_HOME)
        self.assertEqual(result.languages["pairs"], 4)

    def test_a_page_without_a_translation_gets_the_alert_for_the_missing_language(self):
        result = self.go()
        self.assertIn("No Persian version", self.row(ONLY_EN)["Alerts"])
        self.assertEqual(self.row(ONLY_EN).get("Related URLs", ""), "")
        self.assertIn("No English version", self.row(FA_ONLY)["Alerts"])
        self.assertIn("No Persian version", self.row(NO_TRANSLATION)["Alerts"])          # it declares a set without Persian
        self.assertNotIn("version", self.row(FALLBACK_FA)["Alerts"])                     # in the Persian folder but written in English: left alone
        self.assertIn("No Persian version", self.row(FALLBACK_EN)["Alerts"])
        self.assertEqual((result.languages["no fa"], result.languages["no en"], result.languages["unreadable"]), (4, 2, 2))

    def test_a_close_slug_in_the_other_language_is_a_possible_translation_not_a_missing_version(self):
        result = self.go()
        self.assertIn(f"Possible translation: {POSS_FA}", self.row(POSS_EN)["Alerts"])
        self.assertIn(f"Possible translation: {POSS_EN}", self.row(POSS_FA)["Alerts"])
        for url in (POSS_EN, POSS_FA):
            self.assertNotIn("version", self.row(url)["Alerts"])
            self.assertEqual(self.row(url).get("Related URLs", ""), "")                  # nothing is filled for a guess
        self.assertEqual((result.languages["possible"], result.languages["possible pairs"]), (2, 1))
        self.assertEqual(result.languages["pairs"], 4)                                   # the guess is not a pair

    def test_slugs_that_are_only_a_little_alike_stay_missing_versions(self):
        self.go()
        self.assertIn("No Persian version", self.row(HOTEL_EN)["Alerts"])           # one word of three differs
        self.assertIn("No English version", self.row(HOTEL_FA)["Alerts"])
        self.assertNotIn("Possible", self.row(ONLY_EN)["Alerts"])

    def test_a_page_that_has_its_translation_is_never_offered_as_a_possible_one(self):
        table = {u: self.live(u) for u in EVERY}
        same = f"{FA}/turkey-plastic-surgery-cost-what-you-need-to-know/"
        table[same] = page(same, "fa")                                                   # the real mirror exists
        live = lambda u: table.get(u, Probe(404))                                        # noqa: E731
        self.go()
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=lambda: (EVERY + [same], True), probe=live)
        self.assertEqual(self.row(POSS_EN)["Related URLs"], same)
        self.assertNotIn("Possible", self.row(POSS_EN)["Alerts"])

    def test_a_possible_translation_alert_stays_on_a_page_that_cannot_be_read(self):
        self.go()
        self.edit("Articles", content.display_url(BROKEN), "Alerts", f"Possible translation: {POSS_FA}")
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=self.sitemap(), probe=self.live)
        self.assertIn(f"Possible translation: {POSS_FA}", self.row(BROKEN)["Alerts"])

    def test_slug_similarity(self):
        sim = content_run.slug_similarity
        self.assertGreaterEqual(sim(POSS_EN, POSS_FA)[0], content_run.POSSIBLE_MIN_SCORE)
        self.assertLess(sim(HOTEL_EN, HOTEL_FA)[0], content_run.POSSIBLE_MIN_SCORE)
        self.assertEqual(sim(HOME, FA_HOME), (0.0, 0))
        self.assertEqual(sim(f"{ROOT}/12345/", f"{FA}/12345/"), (0.0, 0))

    def test_a_page_of_the_persian_folder_written_in_english_is_not_a_translation(self):
        result = self.go()
        self.assertIn("English text on Persian page", self.row(TXT_FA)["Alerts"])
        self.assertEqual(self.row(TXT_FA)["Related URLs"], TXT_EN)                     # the pair stays in Related URLs
        self.assertIn("Persian version not translated", self.row(TXT_EN)["Alerts"])
        self.assertEqual(self.row(TXT_EN)["Related URLs"], TXT_FA)
        self.assertNotIn("No Persian version", self.row(TXT_EN)["Alerts"])
        self.assertNotIn("text on", self.row(FA_ART)["Alerts"])                         # a real Persian page has no such alert
        self.assertNotIn("not translated", self.row(ART)["Alerts"])
        lang = result.languages
        self.assertEqual((lang["pairs"], lang["untranslated"]), (4, 1))                # not counted as paired
        self.assertEqual((lang["really Persian"], lang["not Persian"]), (7, 1))

    def test_the_alerts_go_when_the_page_is_translated(self):
        self.go()
        table = {u: self.live(u) for u in EVERY}
        table[TXT_FA] = page(TXT_FA, "fa")                                             # now written in Persian
        live = lambda u: table.get(u, Probe(404))                                        # noqa: E731
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=self.sitemap(), probe=live)
        self.assertNotIn("text on", self.row(TXT_FA)["Alerts"])
        self.assertNotIn("not translated", self.row(TXT_EN)["Alerts"])

    def test_a_page_with_too_little_text_or_that_cannot_be_read_is_not_judged(self):
        table = {u: self.live(u) for u in EVERY}
        table[TXT_FA] = page(TXT_FA, "fa", script=(0, 10, 10))                         # a few words only
        live = lambda u: table.get(u, Probe(404))                                        # noqa: E731
        self.run_it(sitemap=self.sitemap(), probe=live)
        self.assertNotIn("text on", self.row(TXT_FA)["Alerts"])
        self.edit("Articles", content.display_url(TXT_FA), "Alerts", "English text on Persian page")
        table[TXT_FA] = Probe(503)                                                       # now it cannot be read
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=self.sitemap(), probe=live)
        self.assertIn("English text on Persian page", self.row(TXT_FA)["Alerts"])       # nothing changes

    def test_the_share_that_counts_comes_from_the_config(self):
        site_with(language_min_share=0.5)
        with self.assertRaises(sites.SitesConfigError):
            site_with(language_min_share=0.95)
        table = {u: self.live(u) for u in EVERY}
        table[TXT_FA] = page(TXT_FA, "fa", script=(400, 500, 900))                     # 44% Persian letters
        live = lambda u: table.get(u, Probe(404))                                        # noqa: E731
        self.site = site_with(language_min_share=0.4)
        content_run.fill_tab_members(self.site.content, FILES)
        self.run_it(sitemap=self.sitemap(), probe=live)
        self.assertNotIn("text on", self.row(TXT_FA)["Alerts"])

    def test_the_text_of_a_page_is_measured_without_menus_and_scripts(self):
        text = ("<html lang='fa'><head><script>var english = 'many english letters here';</script></head><body>"
                "<header><nav>Home About Contact English menu words</nav></header>"
                "<main><h1>سلام دنیا</h1><p>این یک متن فارسی است که باید شمرده شود و بسیار طولانی نوشته شده تا از حد کم بگذرد، "
                "و باز هم کمی متن بیشتر برای اطمینان از اینکه شمارش درست کار میکند.</p></main>"
                "<footer>All rights reserved by the English footer</footer></body></html>")
        arabic, latin, total = site_check.script_counts(site_check.main_text(text))
        self.assertEqual(latin, 0)
        self.assertEqual(arabic, total)
        self.assertGreater(total, 60)

    def test_a_page_that_cannot_be_read_is_left_as_it_is(self):
        self.go()
        self.assertNotIn("version", self.row(BROKEN)["Alerts"])
        self.assertEqual(self.row(BROKEN).get("Related URLs", ""), "")
        self.edit("Articles", content.display_url(BROKEN), "Alerts", "No Persian version")      # said once, when it was read
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=self.sitemap(), probe=self.live)
        self.assertIn("No Persian version", self.row(BROKEN)["Alerts"])                        # it keeps the alert it had
        self.assertEqual(self.row(BROKEN).get("Related URLs", ""), "")

    def test_a_translation_that_appears_later_clears_the_alert(self):
        self.go()
        self.assertIn("No Persian version", self.row(ONLY_EN)["Alerts"])
        table = {u: self.live(u) for u in EVERY}
        later = f"{FA}/english-only-guide/"
        table[later] = page(later, "fa")
        live = lambda u: table.get(u, Probe(404))                                              # noqa: E731
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=self.sitemap(), probe=live)
        self.assertNotIn("version", self.row(ONLY_EN)["Alerts"])
        self.assertEqual(self.row(ONLY_EN)["Related URLs"], later)

    def test_what_the_owner_wrote_stays_and_the_address_is_added_once(self):
        self.go()
        self.edit("Articles", content.display_url(ART), "Related URLs", f"{ROOT}/mine/\n{ROOT}/other/")
        self.edit("Articles", content.display_url(ART), "PIC", "Sara")
        self.edit("Articles", content.display_url(FA_ART), "Related URLs", "")
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=self.sitemap(), probe=self.live)
        related = self.row(ART)["Related URLs"]
        self.assertEqual(related, f"{ROOT}/mine/\n{ROOT}/other/\n{FA_ART}")                    # added after the owner's lines
        self.assertEqual(self.row(ART)["PIC"], "Sara")
        self.assertEqual(self.row(FA_ART)["Related URLs"], ART)
        self.run_it(force=True, today=tcr.TODAY.replace(day=29), sitemap=self.sitemap(), probe=self.live)
        self.assertEqual(self.row(ART)["Related URLs"], related)                               # not added again

    def test_without_a_status_check_nothing_is_paired(self):
        result = self.run_it(sitemap=self.sitemap())
        self.assertEqual(result.languages, {})
        self.assertEqual(self.row(ART).get("Related URLs", ""), "")

    def test_a_site_without_languages_is_untouched(self):
        self.site = site_with(languages={})
        content_run.fill_tab_members(self.site.content, FILES)
        result = self.go()
        self.assertEqual(result.languages, {})
        self.assertNotIn("version", self.row(ONLY_EN)["Alerts"])


if __name__ == "__main__":
    unittest.main()
