"""The Primary Keyword is the search that fits the page's title and slug best, not the one with the most impressions.
A fake site (example.test); all pages and searches are invented. No AI is called anywhere in this."""

import json
import unittest
from datetime import date
from unittest import mock

import content
import content_import
import content_run
import content_sheet as cs
import keyword_fix
import sites
import test_content_run as tcr
from site_check import Probe
from test_content_run import ROOT, Base

DAY2 = date(2026, 10, 28)
BRAND = ["example"]

BIO = f"{ROOT}/product-category/microscopes/biological-microscopes/"
WEAK = f"{ROOT}/article/garden-tools"
ID = f"{ROOT}/article/12345"
FA = f"{ROOT}/article/%D9%85%DB%8C%DA%A9%D8%B1%D9%88%D8%B3%DA%A9%D9%88%D9%BE-%D8%B2%DB%8C%D8%B3%D8%AA%DB%8C"
FAD = content.display_url(FA)
TIE = f"{ROOT}/article/best-binoculars-for-bird-watching-beginners-guide"
FEW = f"{ROOT}/article/stone-bridges"
LOW = f"{ROOT}/article/stone-lanterns"

FA_PLAIN = "میکروسکوپ"                  # میکروسکوپ
FA_ARABIC = "ميكروسكوپ‌ زيستي"   # same, ك ي and a half space

PAGES = {BIO: (60, 9000, 6.0), WEAK: (5, 800, 9.0), ID: (3, 500, 8.0), FA: (20, 1200, 5.0), TIE: (9, 1000, 6.0),
         FEW: (1, 60, 12.0), LOW: (0, 6, 12.0), f"{ROOT}/about": (50, 600, 2.0)}
PAIRS = [("microscope", BIO, 40, 2000, 6.0), ("biological microscope", BIO, 5, 300, 7.0),
         ("biologcal microscope", BIO, 1, 1500, 8.0),                      # a typo with many impressions
         ("biological microscope amazon.com", BIO, 1, 4000, 8.0),          # names another site
         ("weather forecast", WEAK, 3, 500, 9.0), ("random thing", WEAK, 2, 300, 9.5),
         ("anything at all", ID, 3, 500, 8.0),
         (FA_PLAIN, FA, 14, 1000, 5.0), (FA_ARABIC, FA, 6, 200, 6.0),
         ("best binoculars for bird watching beginners guide", TIE, 2, 50, 6.0),
         ("binoculars for bird watching beginners guide", TIE, 7, 900, 6.0),
         ("stone bridge", FEW, 0, 5, 12.0), ("old unrelated words", FEW, 1, 55, 12.0), ("stone lantern", LOW, 0, 6, 12.0),
         ("about us", f"{ROOT}/about", 40, 500, 2.0)]


def site_with(**extra):
    block = {"enabled": True, "min_gap_impressions_90d": 100,
             "tabs": [{"name": "Pages", "url_regex": r"^https://example\.test/(article|product-category)/"}]}
    block.update(extra)
    site = sites.parse_sites_json(json.dumps([{
        "name": "site-x", "site_url": "sc-domain:example.test", "auth": "me",
        "deck": {"enabled": True, "language": "en", "site_label": "Example", "recipients": []},
        "content": block}]))[0]
    site.reports_sheet_id = "REP"
    return site


class ScoreTests(unittest.TestCase):

    def sets(self, url, title=""):
        return content.page_wordsets(url, title, BRAND)

    def test_the_example_of_the_spec(self):
        sets = self.sets(BIO)
        self.assertEqual(content.match_score("microscope", sets), 0.6667)          # 100% of the search, 50% of the page
        self.assertEqual(content.match_score("biological microscope", sets), 1.0)

    def test_plurals_spelling_digits_and_the_half_space(self):
        self.assertEqual(content.match_score("biological microscopes", self.sets(BIO)), 1.0)
        self.assertEqual(content.match_score("BIOLOGICAL   Microscope", self.sets(BIO)), 1.0)
        fa = self.sets(FA)
        self.assertEqual(content.match_score(FA_PLAIN + " زیستی", fa), 1.0)
        self.assertEqual(content.match_score(FA_ARABIC, fa), 1.0)                  # ك ي and the half space
        self.assertEqual(content.match_score("۱۰ best monoculars", self.sets(f"{ROOT}/10-best-monoculars")), 1.0)
        self.assertEqual(content.stem("batteries"), "battery")
        self.assertEqual(content.stem("glasses"), "glass")
        self.assertEqual(content.stem("class"), "class")

    def test_a_slug_of_numbers_or_an_id_does_not_count(self):
        self.assertEqual(self.sets(ID), [])
        self.assertEqual(self.sets(f"{ROOT}/product/DT-3321/"), [])
        self.assertEqual(self.sets(f"{ROOT}/shop/4f9c2a7e1b3d5f60"), [])
        self.assertEqual(self.sets(f"{ROOT}/"), [])
        self.assertEqual(self.sets(f"{ROOT}/DT-3321/zwo-smart-telescope/"), [{"zwo", "smart", "telescope"}])

    def test_the_title_counts_without_the_name_of_the_site(self):
        sets = self.sets(f"{ROOT}/article/12345", "Biological Microscopes: Buying Guide | Example Shop")
        self.assertEqual(sets, [{"biological", "microscope", "buying", "guide"}])
        self.assertEqual(content.match_score("biological microscope", sets), 0.6667)      # the title scores too
        self.assertEqual(content.page_wordsets(f"{ROOT}/", "Example Shop", BRAND), [])   # only the brand: nothing

    def test_the_better_of_the_slug_and_the_title_counts(self):
        sets = self.sets(f"{ROOT}/article/guide", "Biological microscope")
        self.assertEqual(content.match_score("biological microscope", sets), 1.0)

    def stats(self, url, rows):
        return content.build_pages({url: (1, sum(r[1] for r in rows), 5.0)},
                                   [(q, url, 1, n, 5.0) for q, n in rows])[content.url_key(url)]

    def test_impressions_only_break_ties_between_good_matches(self):
        stats = self.stats(BIO, [("microscope", 2000), ("biological microscope", 300)])
        pick = content.pick_keyword(stats, BRAND, BIO)
        self.assertEqual((pick.keyword, pick.strong, pick.top), ("biological microscope", True, "microscope"))
        tie = self.stats(TIE, [("best binoculars for bird watching beginners guide", 50),
                               ("binoculars for bird watching beginners guide", 900)])
        self.assertEqual(content.pick_keyword(tie, BRAND, TIE).keyword, "binoculars for bird watching beginners guide")

    def test_a_typo_or_a_search_naming_another_site_does_not_beat_a_clean_match(self):
        stats = self.stats(BIO, [("biologcal microscope", 5000), ("biological microscope amazon.com", 4000),
                                 ("biological microscope amazon", 3000), ("biological microscope", 100)])
        self.assertEqual(content.pick_keyword(stats, BRAND, BIO).keyword, "biological microscope")

    def test_a_search_with_almost_no_impressions_cannot_win(self):
        stats = self.stats(BIO, [("microscope", 2000), ("biological microscope", 5)])
        pick = content.pick_keyword(stats, BRAND, BIO)
        self.assertFalse(pick.strong)
        self.assertEqual(pick.keyword, "")
        self.assertEqual(content.pick_keyword(stats, BRAND, BIO, min_impressions=1).keyword, "biological microscope")

    def test_no_strong_match_gives_no_keyword(self):
        stats = self.stats(WEAK, [("weather forecast", 500), ("random thing", 300)])
        pick = content.pick_keyword(stats, BRAND, WEAK)
        self.assertEqual((pick.keyword, pick.strong, pick.top), ("", False, "weather forecast"))

    def test_the_threshold_is_a_config_value(self):
        stats = self.stats(BIO, [("microscope", 2000)])
        self.assertEqual(content.pick_keyword(stats, BRAND, BIO).keyword, "")
        self.assertEqual(content.pick_keyword(stats, BRAND, BIO, strong=0.6).keyword, "microscope")
        self.assertEqual(site_with(keyword_strong_match=0.6, keyword_min_impressions=3).content.keyword_strong_match, 0.6)
        for bad in ({"keyword_strong_match": 2}, {"keyword_strong_match": "x"}, {"keyword_min_impressions": -1}):
            with self.assertRaises(sites.SitesConfigError):
                site_with(**bad)


class FakeSiteBase(Base):

    def setUp(self):
        super().setUp()
        for target, value in (("PAGES", PAGES), ("PAIRS", PAIRS)):
            patcher = mock.patch.object(tcr, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.site = site_with()

    def row(self, url):
        return self.find("Pages", url)

    def alerts(self, url):
        return self.row(url).get("Alerts", "")


class NewRowsTests(FakeSiteBase):

    def test_the_slug_beats_the_impressions_and_a_typo_or_another_site_does_not_win(self):
        self.run_it()
        row = self.row(BIO)
        self.assertEqual(row[content.KEY], "biological microscope")
        self.assertNotIn(content.REVIEW_ALERT, row["Alerts"])
        self.assertIn("microscope", row["Secondary Keywords"])                      # the big search is still listed
        self.assertEqual(int(row["Primary KW Impr."]), 300)                              # the numbers follow the keyword

    def test_a_page_with_no_strong_match_gets_no_keyword_and_the_alert(self):
        self.run_it()
        for url in (WEAK, ID):
            row = self.row(url)
            self.assertEqual(row[content.KEY], "", url)
            self.assertIn(content.REVIEW_ALERT, row["Alerts"], url)
            self.assertEqual(row["Primary KW Impr."], "")                           # no keyword: empty numbers, no error
        self.assertIn("weather forecast", self.row(WEAK)["Secondary Keywords"])

    def test_a_refresh_does_not_fill_a_flagged_row_by_itself(self):
        self.run_it()
        self.run_it(force=True, today=DAY2)
        self.assertEqual(self.row(WEAK)[content.KEY], "")
        self.assertIn(content.REVIEW_ALERT, self.alerts(WEAK))

    def test_persian_spelling_and_the_half_space_are_normalized_and_the_original_text_is_kept(self):
        self.run_it()
        self.assertEqual(self.row(FAD)[content.KEY], FA_ARABIC)                      # fits better than the shorter search

    def test_ties_go_to_the_search_with_more_impressions_and_a_search_with_few_impressions_cannot_win(self):
        self.run_it()
        self.assertEqual(self.row(TIE)[content.KEY], "binoculars for bird watching beginners guide")
        self.assertEqual(self.row(FEW)[content.KEY], "")                            # "stone bridge" has 5 impressions: not enough

    def test_a_page_with_no_searches_at_all_keeps_the_rule_of_the_page_title(self):
        pages = {**PAGES, f"{ROOT}/article/quiet-page": (0, 5, 20.0)}
        with mock.patch.object(tcr, "PAGES", pages):
            self.run_it(probe=lambda url: Probe(200, title="A quiet page | Example"))
        row = self.row(f"{ROOT}/article/quiet-page")
        self.assertEqual(row[content.KEY], "A quiet page | Example")
        self.assertIn(content_run.NOTE_FROM_TITLE, row["Notes/Checklist"])
        self.assertNotIn(content.REVIEW_ALERT, row["Alerts"])


class ExistingRowsTests(FakeSiteBase):

    def test_an_existing_row_keeps_its_keyword_and_gets_the_alert(self):
        self.run_it()
        self.edit("Pages", WEAK, content.KEY, "my own keyword")
        self.run_it(force=True, today=DAY2)
        self.assertEqual(self.row(WEAK)[content.KEY], "my own keyword")
        self.assertIn(content.REVIEW_ALERT, self.alerts(WEAK))

    def test_a_row_with_keyword_checked_is_never_flagged(self):
        self.run_it()
        self.edit("Pages", WEAK, "Notes/Checklist", "Keyword checked 2026-11-03")
        self.edit("Pages", ID, "Notes/Checklist", "keyword checked")
        self.edit("Pages", WEAK, content.KEY, "my own keyword")
        self.run_it(force=True, today=DAY2)
        self.assertNotIn(content.REVIEW_ALERT, self.alerts(WEAK))
        self.assertNotIn(content.REVIEW_ALERT, self.alerts(ID))
        self.assertEqual(self.row(WEAK)[content.KEY], "my own keyword")

    def test_a_keyword_that_fits_the_page_is_not_flagged_and_the_alert_goes_away_when_it_is_fixed(self):
        self.run_it()
        self.edit("Pages", WEAK, content.KEY, "garden tools")                       # not a search of the page, but it fits
        self.run_it(force=True, today=DAY2)
        self.assertNotIn(content.REVIEW_ALERT, self.alerts(WEAK))
        self.assertEqual(self.row(WEAK)["Primary KW Impr."], "")                    # no data for it: empty, no error

    def test_a_primary_keyword_that_is_set_is_never_overwritten_by_a_refresh(self):
        self.run_it()
        self.edit("Pages", BIO, content.KEY, "microscope")                          # chosen by someone
        self.edit("Pages", BIO, "Secondary Keywords", "my merged idea, microscope kit")
        self.run_it(force=True, today=DAY2)
        row = self.row(BIO)
        self.assertEqual(row[content.KEY], "microscope")
        self.assertIn("my merged idea", row["Secondary Keywords"])
        self.assertEqual(int(row["Primary KW Impr."]), 2000)                             # the numbers follow whatever is in the cell


class OneTimeFixTests(FakeSiteBase):

    def setUp(self):
        super().setUp()
        self.run_it()
        # how the rows looked before this rule: the keyword is the top search of the page
        self.edit("Pages", BIO, content.KEY, "microscope")
        self.edit("Pages", WEAK, content.KEY, "weather forecast")
        self.edit("Pages", FAD, content.KEY, FA_PLAIN)

    def data(self):
        return content_run.Data(tcr.fetch, tcr.FINAL, BRAND)

    def plan(self):
        return keyword_fix.plan(self.fs, self.file_id, self.site, self.data(), BRAND, probe=lambda url: Probe(200))

    def snapshot(self):
        return {name: [r[:] for r in tab.data] for name, tab in self.fs.books[self.file_id].items()}

    def test_the_dry_run_lists_its_plan_and_changes_nothing(self):
        before = self.snapshot()
        changes = self.plan()
        self.assertEqual(self.snapshot(), before)
        by_url = {c.url: c for c in changes}
        bio = by_url[BIO]
        self.assertEqual((bio.action, bio.old, bio.new, bio.score), (keyword_fix.CHANGE, "microscope", "biological microscope", 1.0))
        self.assertEqual(by_url[WEAK].action, keyword_fix.REVIEW)
        self.assertEqual(by_url[FAD].new, FA_ARABIC)
        rows = keyword_fix.plan_rows(changes)
        self.assertEqual(rows[0], keyword_fix.PLAN_HEADER)
        self.assertEqual(len(rows), len(changes) + 1)

    def test_the_fix_changes_the_keyword_keeps_the_old_one_and_a_refresh_leaves_it_alone(self):
        changes = self.plan()
        done = keyword_fix.apply(self.fs, self.file_id, changes, date(2026, 11, 3))
        self.assertGreaterEqual(done["changed"], 2)
        row = self.row(BIO)
        self.assertEqual(row[content.KEY], "biological microscope")
        self.assertIn("microscope", content.split_keywords(row["Secondary Keywords"]))
        self.assertIn("Keyword set [2026-11-03]: changed from «microscope», better match with page title and slug",
                      row["Notes/Checklist"])
        self.edit("Pages", BIO, "Secondary Keywords", row["Secondary Keywords"] + ", added by hand")
        self.run_it(force=True, today=DAY2)
        row = self.row(BIO)
        self.assertEqual(row[content.KEY], "biological microscope")                  # a refresh never overwrites it
        self.assertIn("added by hand", row["Secondary Keywords"])
        self.assertIn("microscope", content.split_keywords(row["Secondary Keywords"]))
        self.assertEqual(int(row["Primary KW Impr."]), 300)                              # the numbers follow the new keyword
        self.assertEqual(self.plan_for(BIO), [])                                    # a second run finds nothing to do
        self.assertIn(content.REVIEW_ALERT, self.alerts(WEAK))                      # the flagged row got its alert

    def plan_for(self, url):
        return [c for c in self.plan() if c.url == url]

    def test_a_row_someone_chose_or_with_a_brief_or_a_draft_is_never_changed(self):
        self.edit("Pages", BIO, "Notes/Checklist", "Keyword checked on 2026-11-02")
        self.edit("Pages", FAD, "Attachments", "Brief · https://docs.example.test/brief1")
        self.edit("Pages", TIE, content.KEY, "binoculars for bird watching beginners guide")
        self.edit("Pages", FEW, content.KEY, "something nobody searched")                # not the page's top search
        urls = {c.url for c in self.plan()}
        self.assertNotIn(BIO, urls)
        self.assertNotIn(FAD, urls)
        self.assertNotIn(FEW, urls)
        self.edit("Pages", WEAK, "Attachments", "Draft · https://docs.example.test/draft1")
        self.assertNotIn(WEAK, {c.url for c in self.plan()})
        self.edit("Pages", WEAK, "Attachments", "")
        self.assertIn(WEAK, {c.url for c in self.plan()})
        self.edit("Pages", BIO, "Notes/Checklist", "")
        self.edit("Pages", BIO, content.KEY, "a keyword Mehdi chose")                   # not the automatic pick
        self.assertNotIn(BIO, {c.url for c in self.plan()})

    def test_a_keyword_that_fits_the_page_is_not_flagged_just_because_the_page_has_few_searches(self):
        self.edit("Pages", LOW, content.KEY, "stone lantern")                      # the top search, 6 impressions: too few to win
        self.assertNotIn(LOW, {c.url for c in self.plan()})
        self.edit("Pages", LOW, content.KEY, "stone")                              # still fits? one word of two: it does not
        self.assertNotIn(LOW, {c.url for c in self.plan()})                        # (not the page's top search any more)

    def test_a_row_that_changed_since_the_plan_is_left_alone(self):
        changes = self.plan()
        self.edit("Pages", BIO, content.KEY, "chosen after the plan")
        done = keyword_fix.apply(self.fs, self.file_id, changes, date(2026, 11, 3))
        self.assertEqual(self.row(BIO)[content.KEY], "chosen after the plan")
        self.assertGreaterEqual(done["skipped"], 1)

    def test_a_row_without_a_strong_match_only_gets_the_alert(self):
        changes = self.plan()
        keyword_fix.apply(self.fs, self.file_id, changes, date(2026, 11, 3))
        row = self.row(WEAK)
        self.assertEqual(row[content.KEY], "weather forecast")
        self.assertIn(content.REVIEW_ALERT, row["Alerts"])
        self.assertNotIn("Keyword set", row.get("Notes/Checklist", ""))

    def test_no_tool_name_and_no_ai_in_what_it_writes(self):
        changes = self.plan()
        keyword_fix.apply(self.fs, self.file_id, changes, date(2026, 11, 3))
        text = json.dumps(self.snapshot(), ensure_ascii=False) + json.dumps(keyword_fix.plan_rows(changes), ensure_ascii=False)
        self.assertNotIn("PulseO", text)
        import ai_client
        with mock.patch.object(ai_client.Gemini, "generate", side_effect=AssertionError("AI called")), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network called")):
            self.plan()


if __name__ == "__main__":
    unittest.main()
