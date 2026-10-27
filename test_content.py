"""Tests for the content dashboard rules, its configuration and the autocomplete helper (v2.5).
All data is invented."""

import json
import unittest
from datetime import date, timedelta

import autocomplete
import content
import sites


class TextTests(unittest.TestCase):

    def test_persian_normalization(self):
        self.assertEqual(content.norm("كتاب  خوب‌"), content.norm("کتاب خوب"))
        self.assertEqual(content.norm("ي"), "ی")
        self.assertEqual(content.norm("Hello   WORLD"), "hello world")
        self.assertEqual(content.norm("۱۲۳"), "123")
        self.assertEqual(content.norm("کـتاب"), "کتاب")

    def test_urls_are_decoded_and_lose_their_fragment(self):
        self.assertEqual(content.decode_url("https://example.test/article/%DA%A9%D8%AA%D8%A7%D8%A8#top"),
                         "https://example.test/article/کتاب")
        self.assertEqual(content.path_of("https://example.test/a%20b/?x=1"), "/a b/?x=1")

    def test_duplicate_keys_ignore_case_trailing_slash_and_zwnj(self):
        keys = {content.dup_key(u) for u in ("https://Example.test/Guide/", "https://example.test/guide",
                                             "https://example.test/guide‌")}
        self.assertEqual(len(keys), 1)
        self.assertEqual(content.duplicate_keys(["https://example.test/a/", "https://example.test/A",
                                                 "https://example.test/b"]), {"https://example.test/a"})
        self.assertEqual(content.duplicate_keys(["https://example.test/a", "https://example.test/a"]), set())

    def test_questions_in_persian_and_english_as_whole_words(self):
        self.assertTrue(content.is_question("کتاب چیست"))
        self.assertTrue(content.is_question("How to plant a tree"))
        self.assertFalse(content.is_question("whatever shoes"))      # "what" is not a whole word here
        self.assertFalse(content.is_question("garden shoes"))

    def test_brand_and_address_searches_are_skipped(self):
        self.assertTrue(content.skip_query("Example Shop prices", ["example"]))
        self.assertTrue(content.skip_query("app.example.ir", []))
        self.assertFalse(content.skip_query("garden shoes", ["example"]))


class AdviceTests(unittest.TestCase):

    def test_each_rule_code(self):
        a = content.advice
        self.assertEqual(a(10, 3.0, 0.1).code, "prune")
        self.assertEqual(a(10, 3.0, 0.1).todo, "Keep")
        self.assertEqual(a(500, 2.0, 0.02).code, "ctr")
        self.assertEqual(a(500, 2.0, 0.02).todo, "Optimization")
        self.assertIn("well below the usual ~15% at that position", a(500, 2.0, 0.02).why)
        self.assertEqual(a(500, 5.0, 0.01).code, "ctr")
        self.assertEqual(a(500, 4.0, 0.05).code, "p1")
        self.assertEqual(a(500, 4.0, 0.05).action, "Update to reach the top 3")
        self.assertEqual(a(500, 2.0, 0.2).code, "keep")               # already in the top 3
        self.assertEqual(a(250, 4.0, 0.06).code, "keep")              # page 1 but under 300 impressions
        self.assertEqual(a(250, 15.0, 0.01).code, "p2")
        self.assertEqual(a(150, 15.0, 0.01).code, "keep")
        self.assertEqual(a(250, 30.0, 0.0).code, "deep")
        self.assertEqual(a(250, 30.0, 0.0).todo, "Rewrite")
        self.assertEqual(a(150, 30.0, 0.0).code, "keep")
        self.assertEqual(content.KEEP.action, "No action now")

    def test_the_why_names_the_numbers(self):
        self.assertEqual(content.advice(1234, 7.26, 0.05).why, "On page 1 (position 7.3) with 1,234 impressions.")

    def test_expected_click_rates(self):
        self.assertEqual([content.expected_ctr(p) for p in (1.2, 2.9, 3.0, 4.5, 5.0, 9.0)],
                         [.28, .15, .10, .07, .05, .05])


class ScoreTests(unittest.TestCase):

    def test_gain_only_between_3_5_and_20(self):
        self.assertAlmostEqual(content.gain(300, 0.01, 8), 7.0)
        self.assertEqual(content.gain(300, 0.01, 3.0), 0.0)
        self.assertEqual(content.gain(300, 0.01, 25), 0.0)
        self.assertEqual(content.gain(300, 0.2, 8), 0.0)

    def test_score_parts(self):
        self.assertEqual(content.score(15000, 150, 8, False, False, False), 85)
        self.assertEqual(content.score(15000, 150, 8, True, False, False), 90)
        self.assertEqual(content.score(15000, 150, 8, False, True, False), 95)
        self.assertEqual(content.score(15000, 150, 8, True, True, True), 100)        # capped
        self.assertEqual(content.score(1, 0, 25, False, False, False), 0)
        self.assertEqual(content.score(1500, 0, 2.0, False, False, False),
                         round(content.math.log10(1500) / content.math.log10(15000) * 20 + 5))
        self.assertEqual(content.score(100, 0, 15, False, False, True) - content.score(100, 0, 15, False, False, False), 10)

    def test_priority_bands(self):
        self.assertEqual([content.priority(s) for s in (100, 80, 79, 60, 59, 40, 39, 0)],
                         ["Urgent", "Urgent", "High", "High", "Moderate", "Moderate", "Low", "Low"])


class RowDataTests(unittest.TestCase):

    def stats(self):
        pages = {"https://example.test/article/a": (30, 1000, 6.0), "https://example.test/article/a#x": (0, 0, 0.0)}
        pairs = [("brand example", "https://example.test/article/a", 10, 500, 1.0),
                 ("garden shoes", "https://example.test/article/a", 12, 300, 6.0),
                 ("what are garden shoes", "https://example.test/article/a#x", 4, 100, 7.0),
                 ("what are garden shoes", "https://example.test/article/a", 2, 60, 8.0),
                 ("shoe care", "https://example.test/article/a", 1, 50, 9.0),
                 ("app.example.ir", "https://example.test/article/a", 1, 40, 3.0)]
        return content.build_pages(pages, pairs)["https://example.test/article/a"]

    def test_pages_merge_variants_and_queries(self):
        s = self.stats()
        self.assertEqual(s.impressions, 1000)
        queries = {q[0]: q for q in s.queries}
        self.assertEqual(queries["what are garden shoes"][2], 160)             # fragment variants added up
        self.assertEqual([q[0] for q in s.queries][:2], ["brand example", "garden shoes"])

    def test_primary_secondary_and_fanouts_skip_brand_and_addresses(self):
        s, brand = self.stats(), ["example"]
        self.assertEqual(content.primary_keyword(s, brand), "garden shoes")
        self.assertEqual(content.secondary_keywords(s, brand, "garden shoes"),
                         ["what are garden shoes", "shoe care"])
        self.assertEqual(content.fan_outs(s, brand), ["what are garden shoes"])
        self.assertEqual(content.keyword_metrics(s, "Garden  Shoes"), (300, 6.0))
        self.assertEqual(content.keyword_metrics(s, "nothing"), ("", ""))

    def test_competing_pages(self):
        pairs = [("garden shoes", "https://example.test/a", 0, 50, 5), ("garden shoes", "https://example.test/b", 0, 30, 5),
                 ("garden shoes", "https://example.test/c", 0, 10, 5), ("garden shoes", "https://example.test/own", 0, 90, 5),
                 ("garden shoes", "https://example.test/d", 0, 25, 5), ("garden shoes", "https://example.test/e", 0, 22, 5)]
        index = content.index_pairs(pairs)
        self.assertEqual(content.competing_pages(index, "Garden Shoes", "https://example.test/own"),
                         ["/a", "/b", "/d"])
        self.assertEqual(content.competing_pages(index, "other", "https://example.test/own"), [])
        self.assertIn("Overlaps with /a, /b.", content.competing_recommendation(["/a", "/b"]))

    def test_change_is_empty_without_a_base(self):
        self.assertEqual(content.change(15, 10), 0.5)
        self.assertEqual(content.change(5, 10), -0.5)
        self.assertEqual(content.change(5, 0), "")
        self.assertEqual(content.change(5, None), "")

    def test_classification_of_new_rows(self):
        self.assertEqual(content.intent_for(True), "Navigational")
        self.assertEqual(content.intent_for(False), "Informational")
        self.assertEqual(content.content_type_for(True, 10), "Pillar")
        self.assertEqual(content.content_type_for(False, 2000), "Cornerstone")
        self.assertEqual(content.content_type_for(False, 1999), "Cluster")
        self.assertEqual(content.keyword_type_for("eid|ramadan", "https://example.test/a", "ramadan gifts"), "Seasonal")
        self.assertEqual(content.keyword_type_for("eid", "https://example.test/a", "gifts"), "General")
        self.assertEqual(content.keyword_type_for("", "https://example.test/eid", "gifts"), "General")


class DatesAndAlertsTests(unittest.TestCase):

    def test_sheet_dates(self):
        serial = (date(2026, 10, 27) - date(1899, 12, 30)).days
        self.assertEqual(content.parse_date(serial), date(2026, 10, 27))
        self.assertEqual(content.parse_date(str(serial)), date(2026, 10, 27))
        self.assertEqual(content.parse_date("2026-10-27"), date(2026, 10, 27))
        self.assertIsNone(content.parse_date(""))
        self.assertIsNone(content.parse_date("soon"))
        self.assertIsNone(content.parse_date(12))

    def test_impact_text(self):
        pub = date(2026, 9, 1)
        self.assertEqual(content.impact_text(pub, date(2026, 9, 20), None, None), "Too early (19 days)")
        self.assertEqual(content.impact_text(pub, date(2026, 10, 15), (100, 8.0), (150, 6.0)),
                         "Clicks 100 → 150 (+50%), Pos 8.0 → 6.0")
        self.assertEqual(content.impact_text(pub, date(2026, 10, 15), (0, 8.0), (3, 6.0)),
                         "Clicks 0 → 3, Pos 8.0 → 6.0")
        self.assertEqual(content.impact_text(pub, date(2026, 10, 15), None, (3, 6.0)), "No data for these weeks")
        before, after = content.impact_windows(pub)
        self.assertEqual((before[1] - before[0]).days, 27)
        self.assertEqual(after[0], pub)
        self.assertEqual((after[1] - after[0]).days, 27)

    def test_season_alerts(self):
        today = date(2026, 10, 27)
        self.assertEqual(content.season_alert(11, today), "Seasonal: peaks in November, update by 2026-09-20")
        self.assertEqual(content.season_alert(6, today), "Seasonal: peaks in June, update by 2027-04-20")
        self.assertEqual(content.season_alert(0, today), "Seasonal: update 6 weeks before the peak")
        self.assertEqual(content.peak_month_of({date(2026, 6, 1): 50, date(2026, 7, 1): 20}), 6)
        self.assertEqual(content.peak_month_of({date(2026, 6, 1): 5}), 0)
        self.assertEqual(content.peak_month_of({}), 0)

    def test_decay_needs_three_declines_in_a_row(self):
        self.assertTrue(content.decaying([10, 8, 6, 4]))
        self.assertFalse(content.decaying([10, 8, 9, 4]))
        self.assertFalse(content.decaying([0, 8, 6, 4]))
        self.assertFalse(content.decaying([8, 6, 4]))

    def test_alert_list_and_order(self):
        text = content.alerts_for(
            advice_code="prune", competing=["/a"], seasonal=True, peak_month=0, today=date(2026, 10, 27),
            url="https://example.test/guide-old/", duplicate=True, decay=True, position=25, impressions=30, gone=True)
        self.assertEqual(text, "Cannibalization, Seasonal: update 6 weeks before the peak, Prune candidate, "
                               "Old copy still indexed, Duplicate URL, Decay: clicks down 3 months in a row, "
                               "Low visibility (pos > 20), Gone")
        quiet = content.alerts_for(advice_code="keep", competing=[], seasonal=False, peak_month=0,
                                   today=date(2026, 10, 27), url="https://example.test/a", duplicate=False,
                                   decay=False, position=5, impressions=500, gone=False)
        self.assertEqual(quiet, "")


class GapTests(unittest.TestCase):

    HOME, ARTICLE = "https://example.test/", "https://example.test/article/x"

    def gaps(self, queries, best, **kw):
        defaults = dict(brand_terms=["example"], minimum=100, known=set(), already=set(),
                        tab_matches=lambda u: "/article/" in u)
        defaults.update(kw)
        return content.find_gaps(queries=queries, best=best, **defaults)

    def test_searches_that_land_outside_the_tab_form_groups(self):
        queries = {"best garden shoes": (5, 400, 9.0), "garden shoes review": (3, 300, 11.0),
                   "how to bake bread": (1, 200, 14.0), "example shop": (50, 900, 1.0),
                   "tiny topic": (0, 20, 9.0), "app.example.ir": (3, 500, 4.0)}
        best = {q: (self.HOME, 5000, 3.0) for q in queries}
        found = self.gaps(queries, best)
        self.assertEqual([g.keyword for g in found], ["best garden shoes", "how to bake bread"])
        group = found[0]
        self.assertEqual(group.queries, ["best garden shoes", "garden shoes review"])
        self.assertEqual(group.impressions, 700)
        self.assertEqual(group.position, 9.0)
        self.assertEqual(content.gap_why(group),
                         "700 impressions across 2 searches land on /, which doesn't cover this topic.")
        self.assertGreater(group.score, 10)

    def test_a_search_the_tab_already_answers_is_no_gap_unless_it_is_a_tiny_share_far_down(self):
        queries = {"covered topic": (30, 400, 3.0), "weak topic": (1, 120, 12.0)}
        best = {"covered topic": (self.ARTICLE, 500, 3.0), "weak topic": (self.ARTICLE, 5000, 3.0)}
        self.assertEqual([g.keyword for g in self.gaps(queries, best)], ["weak topic"])

    def test_known_dismissed_and_added_searches_are_not_proposed_again(self):
        queries = {"alpha shoes": (1, 300, 9.0), "beta bags": (1, 250, 9.0), "gamma hats": (1, 220, 9.0)}
        best = {q: (self.HOME, 5000, 3.0) for q in queries}
        found = self.gaps(queries, best, known={content.norm("Alpha Shoes")}, already={"gamma hats"})
        self.assertEqual([g.keyword for g in found], ["beta bags"])

    def test_the_cap_keeps_the_highest_scores(self):
        queries = {f"topic{i}": (0, 100 + i * 10, 9.0) for i in range(30)}
        best = {q: (self.HOME, 5000, 3.0) for q in queries}
        found = self.gaps(queries, best, limit=3)
        self.assertEqual([g.keyword for g in found], ["topic29", "topic28", "topic27"])

    def test_persian_variants_group_together(self):
        queries = {"كتاب خوب": (1, 300, 9.0), "کتاب خوب بخوانید": (1, 150, 9.0)}
        best = {q: (self.HOME, 5000, 3.0) for q in queries}
        found = self.gaps(queries, best)
        self.assertEqual(len(found), 1)


class ConfigTests(unittest.TestCase):

    def parse(self, content_block):
        entry = {"name": "site-x", "site_url": "sc-domain:example.test", "auth": "me",
                 "deck": {"enabled": False}}
        if content_block is not None:
            entry["content"] = content_block
        return sites.parse_sites_json(json.dumps([entry]))[0]

    def test_no_block_means_off(self):
        self.assertFalse(self.parse(None).content.enabled)

    def test_a_full_block(self):
        s = self.parse({"enabled": True, "sheet_id": "", "seasonal_regex": "eid|ramadan",
                        "min_gap_impressions_90d": 150,
                        "tabs": [{"name": "Articles", "url_regex": "^https?://(www\\.)?example\\.test/article/.+"},
                                 {"name": "Landing Pages", "url_regex": "^https?://(www\\.)?example\\.test/[^/?#.]+$",
                                  "exclude_regex": "/(en|home)$", "description": "Main pages"}]})
        c = s.content
        self.assertTrue(c.enabled)
        self.assertEqual([t.name for t in c.tabs], ["Articles", "Landing Pages"])
        self.assertEqual([t.gaps for t in c.tabs], [True, False])          # content gaps go to the first tab
        self.assertEqual([t.landing for t in c.tabs], [False, True])       # a tab named "landing" is one
        self.assertEqual(c.min_gap_impressions_90d, 150)
        self.assertIs(c.tab_of("https://example.test/article/%DA%A9"), c.tabs[0])
        self.assertIs(c.tab_of("https://example.test/about"), c.tabs[1])
        self.assertIsNone(c.tab_of("https://example.test/en"))             # excluded
        self.assertIsNone(c.tab_of("https://example.test/a/b/c"))
        self.assertIn("eid|ramadan", s.sensitive_values())

    def test_problems_are_reported_without_echoing_values(self):
        bad = [{"enabled": True},
               {"enabled": True, "tabs": []},
               {"enabled": True, "tabs": [{"name": "Guide", "url_regex": "x"}]},
               {"enabled": True, "tabs": [{"name": "A", "url_regex": "("}]},
               {"enabled": True, "tabs": [{"name": "A", "url_regex": "x"}, {"name": "a", "url_regex": "y"}]},
               {"enabled": True, "tabs": [{"name": "A", "url_regex": "x"}], "extra": 1},
               {"enabled": True, "tabs": [{"name": "A", "url_regex": "x"}], "min_gap_impressions_90d": 0},
               {"enabled": True, "tabs": [{"name": "A", "url_regex": "x", "gaps": "yes"}]}]
        for block in bad:
            with self.assertRaises(sites.SitesConfigError) as cm:
                self.parse(block)
            self.assertNotIn("secret-value", str(cm.exception))

    def test_disabled_block_needs_nothing_else(self):
        self.assertFalse(self.parse({"enabled": False}).content.enabled)


class AutocompleteTests(unittest.TestCase):

    TODAY = date(2026, 10, 27)

    def http(self, answers=None, status=200):
        calls = []

        def get(url):
            calls.append(url)
            if callable(status):
                return status(len(calls)), ""
            q = url.split("&q=")[1].split("&")[0]
            return status, json.dumps([q, [f"{q} one", f"{q} two"]], ensure_ascii=False)
        get.calls = calls
        return get

    def test_the_queries_per_seed(self):
        fa = autocomplete.queries_for("کتاب", "fa")
        self.assertEqual(len(fa), 1 + 32 + 10 + 3)
        self.assertEqual(fa[0], ("کتاب", "seed"))
        self.assertIn(("چرا کتاب", "چرا + seed"), fa)
        self.assertIn(("کتاب چیست", "seed + چیست"), fa)
        en = autocomplete.queries_for("shoes", "en")
        self.assertEqual(len(en), 1 + 26 + 7)
        self.assertIn(("best shoes", "best + seed"), en)

    def test_the_url(self):
        url = autocomplete.url_for("کتاب", "fa", "IR")
        self.assertTrue(url.startswith("https://suggestqueries.google.com/complete/search?client=firefox&hl=fa&q="))
        self.assertIn("%DA%A9", url)
        self.assertTrue(url.endswith("&gl=ir"))
        self.assertNotIn("gl=", autocomplete.url_for("x", "en"))

    def test_seeds_are_due_weekly_and_only_when_active(self):
        rows = [{"Seed": "a", "Language": "fa", "Active": "Yes", "Last run": ""},
                {"Seed": "b", "Language": "en", "Active": "No", "Last run": ""},
                {"Seed": "c", "Language": "", "Active": "yes", "Last run": "2026-10-25"},
                {"Seed": "d", "Language": "", "Active": "Yes", "Last run": "2026-10-10"},
                {"Seed": "کتاب", "Language": "", "Active": "Yes", "Last run": ""},
                {"Seed": "", "Active": "Yes"}]
        due = autocomplete.due_seeds(rows, self.TODAY)
        self.assertEqual([(s.seed, s.lang, s.row) for s in due], [("a", "fa", 2), ("d", "en", 5), ("کتاب", "fa", 6)])

    def test_one_call_per_query_throttled_and_cached(self):
        cache, sleeps = autocomplete.Cache(), []
        seed = autocomplete.SeedRun(2, "shoes", "en", "")
        http = self.http()
        found, outcome = autocomplete.collect([seed], cache=cache, today=self.TODAY, http_get=http,
                                              sleep=sleeps.append, rng=lambda: 0.5)
        self.assertEqual(outcome.calls, 34)
        self.assertEqual(len(sleeps), 33)                              # none before the first call
        self.assertTrue(all(1.0 <= s <= 1.5 for s in sleeps))
        self.assertEqual(outcome.ran, [seed])
        self.assertIn("shoes one", [v[0] for v in found.values()])
        # a second run within 30 days asks nobody
        http2 = self.http()
        _, again = autocomplete.collect([seed], cache=cache, today=self.TODAY + timedelta(days=7),
                                        http_get=http2, sleep=sleeps.append)
        self.assertEqual((again.calls, again.cached, len(http2.calls)), (0, 34, 0))
        # after 30 days it asks again
        http3 = self.http()
        autocomplete.collect([seed], cache=cache, today=self.TODAY + timedelta(days=31), http_get=http3,
                             sleep=lambda s: None)
        self.assertEqual(len(http3.calls), 34)

    def test_http_429_stops_softly_and_keeps_what_was_found(self):
        seed = autocomplete.SeedRun(2, "shoes", "en", "")
        http = self.http(status=lambda n: 200 if n < 4 else 429)
        # the fake answers 200 with an empty body, so parse nothing; what matters is the stop
        found, outcome = autocomplete.collect([seed], cache=autocomplete.Cache(), today=self.TODAY,
                                              http_get=http, sleep=lambda s: None)
        self.assertIn("429", outcome.stopped)
        self.assertEqual(outcome.ran, [])                             # an unfinished seed does not count as run
        self.assertEqual(len(http.calls), 4)

    def test_call_limit_and_network_failures(self):
        seed = autocomplete.SeedRun(2, "shoes", "en", "")
        http = self.http()
        _, outcome = autocomplete.collect([seed], cache=autocomplete.Cache(), today=self.TODAY, http_get=http,
                                          sleep=lambda s: None, max_calls=5)
        self.assertEqual(outcome.calls, 5)
        self.assertIn("limit", outcome.stopped)

        def broken(url):
            raise OSError("down")
        _, outcome = autocomplete.collect([seed], cache=autocomplete.Cache(), today=self.TODAY, http_get=broken,
                                          sleep=lambda s: None)
        self.assertEqual(outcome.calls, autocomplete.MAX_FAILURES_IN_A_ROW)
        self.assertTrue(outcome.stopped)

    def test_ideas_are_deduplicated_and_filtered(self):
        seed = autocomplete.SeedRun(2, "shoes", "en", "")
        found = {content.norm(k): (k, seed, "seed") for k in
                 ["Garden Shoes", "garden  shoes ", "example shoes", "shoes sale", "old idea", "known one", "www shoes"]}
        rows = autocomplete.new_ideas(
            found, brand_terms=["example"], existing={"old idea"}, known={"known one"}, today=self.TODAY,
            lookup=lambda k: (120, "/guide", 7.5) if k == "garden shoes" else None)
        self.assertEqual([r[0] for r in rows], ["garden  shoes ", "shoes sale"])      # one row per normalized key
        self.assertEqual(rows[0], ["garden  shoes ", "shoes", "seed", "en", 120, "/guide", 7.5, "2026-10-27", ""])
        self.assertEqual(rows[1][4:7], ["", "", ""])

    def test_the_cache_survives_a_round_trip_through_the_state_rows(self):
        cache = autocomplete.Cache()
        cache.put("fa||کتاب", self.TODAY, ["کتاب خوب"])
        again = autocomplete.Cache.from_rows(cache.to_rows())
        self.assertEqual(again.get("fa||کتاب", self.TODAY), ["کتاب خوب"])
        again.prune(self.TODAY + timedelta(days=40))
        self.assertEqual(again.entries, {})


if __name__ == "__main__":
    unittest.main()
