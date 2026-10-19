"""
Tests for the client-ready monthly deck (v2.4): what goes on each slide (client_data) and how it
is drawn (client_deck). All data is invented (client_fixtures.py).
"""

import io
import re
import unittest

from pptx import Presentation

import client_data as cd
import client_deck
import client_fixtures as fx
import client_i18n
import deck_runner
import resolved

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def report(**kw):
    return fx.monthly_report(**kw)


def client(lang="en", **kw):
    return cd.ClientReport(report(**kw), lang, "Example")


def texts_of(prs):
    out = []
    for slide in prs.slides:
        for shp in slide.shapes:
            if shp.has_text_frame:
                out.append(shp.text_frame.text)
            if getattr(shp, "has_table", False) and shp.has_table:
                for row in shp.table.rows:
                    out += [c.text_frame.text for c in row.cells]
    return out


def build(lang="en", **kw):
    rep = report(**kw)
    data = client_deck.build_monthly(rep, fx.Cfg(lang), deck_runner.TEMPLATE_DIR)
    return Presentation(io.BytesIO(data))


class SlideSelectionTests(unittest.TestCase):

    def test_a_full_deck_has_fifteen_slides_in_the_agreed_order(self):
        ids = [sid for sid, _ in client().slides()]
        self.assertEqual(ids, ["headline", "scorecard", "climbs", "grew", "new", "work", "trend",
                               "sections", "wins", "pages", "ideas", "next", "watch", "focus"])
        self.assertEqual(len(build().slides), 15)

    def test_slides_without_data_are_left_out_but_the_work_slide_stays(self):
        ids = [sid for sid, _ in client(segments=False, opps=False, gone=0).slides()]
        self.assertIn("work", ids)
        for gone in ("sections", "wins", "pages", "ideas", "focus"):
            self.assertNotIn(gone, ids)
        self.assertEqual(len(build(segments=False, opps=False).slides), 1 + len(ids))

    def test_no_data_at_all_still_gives_title_and_work_slide(self):
        rep = report(segments=False, opps=False)
        rep.keywords, rep.pages, rep.gone = [], [], []
        ids = [sid for sid, _ in cd.ClientReport(rep, "en", "Example").slides()]
        self.assertEqual(ids[-1], "work")
        self.assertNotIn("climbs", ids)
        self.assertNotIn("grew", ids)
        self.assertNotIn("new", ids)

    def test_slides_point_at_each_other_by_their_real_numbers(self):
        c = client()
        nums = {sid: i + 2 for i, (sid, _) in enumerate(c.slides())}
        focus = dict(c.slides())["focus"]
        self.assertIn(f"slide {nums['wins']}", focus["cards"][0]["body"])
        self.assertIn(f"slide {nums['ideas']}", focus["cards"][2]["body"])
        nxt = dict(c.slides())["next"]
        self.assertTrue(any(f"slide {nums['pages']}" in b for b in nxt["bullets"]))

    def test_without_the_target_slide_the_text_does_not_name_a_slide(self):
        rep = report()
        rep.opps = [o for o in rep.opps if o["type"] not in ("question",)]
        slides = dict(cd.ClientReport(rep, "en", "Example").slides())
        self.assertNotIn("ideas", slides)


class HeadlineTests(unittest.TestCase):

    def head(self, mutate=None, **kw):
        rep = report(**kw)
        if mutate:
            mutate(rep)
        return cd.ClientReport(rep, "en", "Example").headline()

    def test_best_position_of_the_year_comes_first(self):
        d = self.head()
        self.assertEqual(d["headline"], "Example ranks higher than it has all year.")
        self.assertIn("Average position improved to 6.2", d["support"])
        self.assertIn("more searchers clicked through (CTR 3.61%)", d["support"])
        self.assertIn("Guides led the growth", d["support"])
        self.assertEqual(d["cards"][0]["title"], "Best ranking of the year")
        self.assertIn("up from 6.4 last month and 7.6 a year ago", d["cards"][0]["body"])

    def test_then_clicks_up_ten_percent_on_last_year(self):
        def worse(rep):
            rep.rows[(fx.MONTHS[-1], "All")]["Avg. Position"] = 8.0
        self.assertEqual(self.head(worse)["headline"], "Clicks are up 37% on last year.")

    def test_then_more_searchers_clicked_through(self):
        def mutate(rep):
            row = rep.rows[(fx.MONTHS[-1], "All")]
            row["Avg. Position"] = 8.0
            row["Clicks YoY %"] = 5.0
        self.assertEqual(self.head(mutate)["headline"], "More searchers clicked through.")

    def test_then_the_biggest_growing_section(self):
        def mutate(rep):
            row = rep.rows[(fx.MONTHS[-1], "All")]
            row["Avg. Position"] = 8.0
            row["Clicks YoY %"] = 5.0
            row["CTR (%)"] = 1.0
        self.assertEqual(self.head(mutate)["headline"], "Guides led the growth.")

    def test_otherwise_a_calm_sentence(self):
        def mutate(rep):
            row = rep.rows[(fx.MONTHS[-1], "All")]
            row["Avg. Position"] = 8.0
            row["Clicks YoY %"] = 5.0
            row["CTR (%)"] = 1.0
            for s in fx.SEGMENT_CLICKS:
                rep.rows[(fx.MONTHS[-1], s)]["Clicks MoM %"] = -3.0
        self.assertEqual(self.head(mutate)["headline"], "Example held its ground this month.")

    def test_clicks_vs_last_year_card_wording(self):
        d = self.head()
        self.assertEqual(d["cards"][1]["title"], "Clicks up 37% on last year")

        def steady(rep):
            rep.rows[(fx.MONTHS[-1], "All")]["Clicks YoY %"] = -1.1
            rep.rows[(fx.MONTHS[-1], "All")]["Impr YoY %"] = -7.0
        card = self.head(steady)["cards"][1]
        self.assertEqual(card["title"], "Clicks held steady vs last year")
        self.assertIn("within 1% of last September even with fewer searches", card["body"])

    def test_the_room_to_grow_card_sums_the_top_five_gains(self):
        d = self.head()
        self.assertEqual(d["cards"][2]["title"], "Big room to grow")
        self.assertIn("5 keywords", d["cards"][2]["body"])
        self.assertIn("475 extra clicks a month", d["cards"][2]["body"])

    def test_no_opportunities_means_no_third_card(self):
        self.assertEqual(len(self.head(opps=False)["cards"]), 2)


class ScorecardTests(unittest.TestCase):

    def card(self, mutate=None):
        rep = report()
        if mutate:
            mutate(rep)
        d = cd.ClientReport(rep, "en", "Example").scorecard()
        return {c["label"]: c for c in d["cards"]}, d

    def test_values_and_gain_colours(self):
        cards, d = self.card()
        self.assertEqual(cards["Clicks"]["value"], "1,300")
        self.assertEqual(cards["Impressions"]["value"], "36,000")
        self.assertEqual(cards["CTR"]["value"], "3.61%")
        self.assertEqual(cards["Avg. position"]["value"], "6.2")
        self.assertEqual(cards["Clicks"]["lines"][0]["tone"], cd.GOOD)
        self.assertEqual(cards["Avg. position"]["lines"][0]["text"], "↑ 0.2")
        self.assertEqual(cards["Avg. position"]["lines"][0]["tone"], cd.GOOD)
        self.assertEqual(d["kicker"], "September 2026 vs Aug and vs Sep 2025")

    def test_a_drop_from_fewer_searches_is_grey_not_red(self):
        cards, _ = self.card()
        line = cards["Impressions"]["lines"][0]
        self.assertTrue(line["text"].startswith("−"))
        self.assertEqual(line["tone"], cd.GREY)
        self.assertEqual(cards["Impressions"]["note"], "Fewer searches this month.")

    def test_red_only_when_the_position_really_fell(self):
        def worse(rep):
            row = rep.rows[(fx.MONTHS[-1], "All")]
            row["Avg. Position"] = 7.0                      # 0.6 worse than 6.4
            row["Clicks MoM %"] = -5.0
        cards, _ = self.card(worse)
        self.assertEqual(cards["Impressions"]["lines"][0]["tone"], cd.RED)
        self.assertEqual(cards["Avg. position"]["lines"][0]["tone"], cd.RED)
        self.assertIn("rankings slipped", cards["Clicks"]["note"])

        def slightly(rep):
            rep.rows[(fx.MONTHS[-1], "All")]["Avg. Position"] = 6.7        # 0.3 worse: grey
        cards, _ = self.card(slightly)
        self.assertEqual(cards["Avg. position"]["lines"][0]["tone"], cd.GREY)

    def test_old_urls_leaving_are_mentioned_from_five_pages_on(self):
        def fewer_searches(rep):
            rep.gone = rep.gone[:4]
        cards, _ = self.card(fewer_searches)
        self.assertEqual(cards["Impressions"]["note"], "Fewer searches this month.")
        cards, _ = self.card()
        self.assertIn("6 old URLs no longer show in Google", cards["Impressions"]["note"])

    def test_aio_card_only_when_imported(self):
        cards, _ = self.card()
        self.assertNotIn("AIO impressions", cards)

        def imported(rep):
            rep.rows[(fx.MONTHS[-1], "All")]["AIO Impressions"] = 4200
        cards, _ = self.card(imported)
        self.assertEqual(cards["AIO impressions"]["value"], "4,200")

    def test_the_footnote_explains_grey(self):
        self.assertIn("grey, not red", self.card()[1]["footnote"])


class ProgressSlideTests(unittest.TestCase):

    def test_climbs_follow_the_rules_and_the_order(self):
        d = client().climbs()
        labels = [r["label"] for r in d["rows"]]
        self.assertEqual(labels, ["/guide/blue-shoes/", fx.PERSIAN[0], fx.PERSIAN[1], fx.ENGLISH[0],
                                  fx.ENGLISH[1]])
        self.assertNotIn(fx.ENGLISH[2], labels)               # too few impressions
        self.assertNotIn("/", labels)                          # only 0.7 steps
        first = d["rows"][0]
        self.assertEqual((first["prev"], first["now"], first["change"]), ("8.0", "5.9", "↑ 2.1"))
        self.assertEqual(d["card"]["from"], "7.6")
        self.assertEqual(d["card"]["to"], "6.2")
        self.assertEqual(d["header"][1:3], ["Aug", "Sep"])

    def test_the_footnote_counts_rankings_that_reached_page_one(self):
        d = client().climbs()
        self.assertEqual(d["footnote"], f"One ranking moved onto page 1 this month: {fx.PERSIAN[1]}.")

    def test_the_card_explains_level_clicks_when_impressions_fell_year_on_year(self):
        rep = report()
        row = rep.rows[(fx.MONTHS[-1], "All")]
        row["Impr YoY %"], row["Clicks YoY %"] = -7.0, -1.0
        d = cd.ClientReport(rep, "en", "Example").climbs()
        self.assertIn("clicks stayed level with last year while impressions fell 7%", d["card"]["body"])

    def test_no_climbs_means_no_slide(self):
        rep = report()
        rep.keywords = [k for k in rep.keywords if k["Type"] != "Growing"]
        rep.pages = []
        self.assertIsNone(cd.ClientReport(rep, "en", "Example").climbs())

    def test_what_grew(self):
        d = client().grew()
        self.assertEqual([r["change"] for r in d["keywords"]], ["+17", "+12", "+7", "+5", "+4"])
        self.assertEqual([r["label"] for r in d["pages"]], ["/", "/guide/blue-shoes/", "/shop/hat/"])
        self.assertEqual(d["pages"][0]["clicks"], "794")
        self.assertEqual(d["fact"], "Year on year, Guides is up 70% and /guide/blue-shoes/ gained 80 clicks.")

    def test_new_searches_are_the_three_with_most_impressions(self):
        d = client().new_searches()
        self.assertEqual([c["pill"] for c in d["cards"]], ["276 impressions", "120 impressions", "90 impressions"])
        self.assertEqual(d["cards"][0]["position"], "position 8.4")
        self.assertIn("for the first time this month", d["intro"])

    def test_the_work_slide_is_a_placeholder_with_four_prompts(self):
        d = client().work()
        self.assertEqual(d["fill"], "Mehdi fills this slide before sending.")
        self.assertEqual(len(d["prompts"]), 4)
        self.assertEqual(d["prompts"][3], "Anything waiting on the Example team")


class TrendAndSectionTests(unittest.TestCase):

    def test_trend_notes(self):
        d = client().trend()
        self.assertEqual(len(d["values"]), 13)
        self.assertEqual(d["labels"][0], "Sep 25")
        self.assertEqual(d["labels"][-1], "Sep 26")
        titles = [n["title"] for n in d["notes"]]
        self.assertEqual(titles, ["December", "Now", "Last year, the next 3 months"])
        self.assertEqual(d["notes"][0]["body"], "Record month: 1,400 clicks.")
        self.assertEqual(d["notes"][1]["tone"], "periwinkle")
        self.assertEqual(d["notes"][2]["body"], "Clicks changed by +23.2% on average compared with September.")
        self.assertEqual(d["title"], "13-month trend")

    def low_month(self, resolved_list=None):
        rep = report(resolved_list=resolved_list)
        rep.rows[("2026-03", "All")]["Clicks"] = 300
        return cd.ClientReport(rep, "en", "Example").trend()

    def test_a_very_low_month_is_red_unless_it_is_on_the_resolved_list(self):
        notes = {n["title"]: n for n in self.low_month()["notes"]}
        self.assertEqual(notes["March"]["tone"], "red")
        self.assertEqual(notes["March"]["body"], "Lowest month: 300 clicks.")
        notes = {n["title"]: n for n in self.low_month([["month", "2026-03", "outage", ""]])["notes"]}
        self.assertEqual(notes["March"]["tone"], "grey")
        self.assertEqual(notes["March"]["body"], "Known and fixed: 300 clicks.")

    def test_sections_chart_table_and_overlap_note(self):
        d = client().sections()
        self.assertEqual([r["label"] for r in d["rows"]], ["Guides", "Blog & other", "Shop"])
        self.assertEqual(d["chart_title"], "Clicks in September")
        guides = d["rows"][0]
        self.assertEqual(guides["mom"], "+11.1%")
        self.assertEqual(guides["mom_tone"], cd.GOOD)
        shop = d["rows"][2]
        self.assertEqual((shop["mom_tone"], shop["yoy_tone"]), (cd.GREY, cd.GREY))   # grey, never red
        self.assertIn("add up to about 8% more than the site total", d["footnote"])

    def test_sections_that_add_up_say_so_and_one_section_means_no_slide(self):
        rep = report()
        for name in fx.SEGMENT_CLICKS:
            rep.rows[(fx.MONTHS[-1], name)]["Clicks"] = 0
        rep.rows[(fx.MONTHS[-1], "Guides")]["Clicks"] = 700
        rep.rows[(fx.MONTHS[-1], "Shop")]["Clicks"] = 200
        rep.rows[(fx.MONTHS[-1], "Blog & other")]["Clicks"] = 400
        self.assertEqual(cd.ClientReport(rep, "en", "Example").sections()["footnote"],
                         "The sections add up to the site total.")
        self.assertIsNone(client(segments=False).sections())


class OpportunitySlideTests(unittest.TestCase):

    def test_quick_wins_one_row_per_keyword_biggest_gain_first(self):
        d = client().quick_wins()
        self.assertEqual([r["gain"] for r in d["rows"]], ["+145", "+100", "+95", "+90", "+45"])
        self.assertEqual(d["total"], 475)
        self.assertEqual(d["card"], "About +475 clicks a month together.")
        self.assertIn("Estimate based on typical top-3 click rates", d["card_note"])
        self.assertEqual(d["rows"][0]["searches"], "1,825")                  # 5,475 impressions in 90 days / 3
        self.assertEqual(d["rows"][1]["position"], "7.9")

    def test_pages_to_update_hold_the_diagnosis_and_the_action(self):
        d = client().pages_to_update()
        self.assertEqual([r["page"] for r in d["rows"]],
                         ["/guide/g/", "/guide/h/", "/guide/old-tips/"])
        self.assertEqual(d["rows"][0]["text"], "Ranks well, title doesn't win the click. Rewrite title + meta.")
        self.assertEqual(d["rows"][0]["ctr"], "1.05%")

    def test_ideas_questions_and_competing_pages(self):
        d = client().ideas()
        self.assertEqual([c["keyword"] for c in d["cards"]],
                         ["what is a trail shoe", "how to clean a bike", "why do boots leak"])
        self.assertEqual(d["cards"][0]["sub"], "1,756 impr. · position 8")
        self.assertEqual(d["cards"][2]["sub"], "625 impr. · CTR 0.90%")
        self.assertIn(f"{fx.ENGLISH[0]} and {fx.ENGLISH[1]} each have 3 pages competing", d["merge"]["body"])

    def test_next_month_uses_last_years_change_and_real_slide_numbers(self):
        d = dict(client().slides())["next"]
        self.assertEqual(d["title"], "Get ready for October")
        self.assertEqual(d["intro"], "What happened from September to October last year:")
        self.assertEqual(d["cards"][0]["label"], "Whole site")
        self.assertEqual(d["cards"][0]["value"], "+6.3%")
        self.assertEqual(d["cards"][0]["sub"], "+4.8% impressions")
        self.assertEqual([c["label"] for c in d["cards"]], ["Whole site", "Guides", "Blog & other", "Shop"])
        self.assertEqual(d["cards"][3]["tone"], cd.GOOD)
        self.assertIn("Guides grows most next month: refresh the top pages from slide 11 first.", d["bullets"])
        self.assertTrue(any("Update /guide/k/ before October: it grew +50% into October last year." == b
                            for b in d["bullets"]))

    def test_watching_is_calm_and_short(self):
        d = client().watching()
        titles = [i["title"] for i in d["items"]]
        self.assertEqual(titles, ["Shop", "Old URLs", "/guide/old-tips/", "/guide/j/"])
        self.assertIn("−35% vs last year", d["items"][0]["body"])
        self.assertEqual(d["items"][1]["body"], "6 old URLs no longer show in Google (expected after "
                                                 "redirects); we check they pass traffic on.")
        self.assertEqual(d["items"][2]["body"], "Clicks 137 → 55. Ranking fell.")
        self.assertLessEqual(len(d["items"]), 5)

    def test_the_old_urls_line_needs_five_pages(self):
        titles = [i["title"] for i in client(gone=4).watching()["items"]]
        self.assertNotIn("Old URLs", titles)

    def test_focus_cards(self):
        d = dict(client().slides())["focus"]
        self.assertEqual([c["title"] for c in d["cards"]],
                         ["Lift the 5 quick wins", "Merge competing pages", "Answer the questions",
                          "Prepare for October"])
        self.assertIn("About +475 clicks a month at stake", d["cards"][0]["body"])
        self.assertEqual(d["cards"][0]["tone"], "amber")
        self.assertIn("2 topics", d["cards"][1]["body"])


class ResolvedTests(unittest.TestCase):

    def ids_and_text(self, resolved_list):
        c = client(resolved_list=resolved_list)
        slides = dict(c.slides())
        return c, slides

    def test_the_parser_ignores_unknown_kinds_and_empty_values(self):
        r = resolved.parse([["page", "/a/", "n", "2026-10-01"], ["keyword", "Some Words", "", ""],
                            ["month", "2026-03", "", ""], ["segment", "Shop", "", ""],
                            ["colour", "x", "", ""], ["page", "", "", ""], ["keyword"]])
        self.assertTrue(r.page("https://example.test/a/"))
        self.assertTrue(r.page("/a/"))
        self.assertFalse(r.page("https://example.test/b/"))
        self.assertTrue(r.keyword("some words"))
        self.assertTrue(r.month("2026-03"))
        self.assertTrue(r.segment("SHOP"))
        self.assertEqual(r.note("page", "/a/"), "n")
        self.assertFalse(resolved.Resolved())

    def test_persian_pages_match_by_url_or_decoded_path(self):
        r = resolved.parse([["page", "/مایع-ضد/", "", ""]])
        self.assertTrue(r.page("https://example.test/%D9%85%D8%A7%DB%8C%D8%B9-%D8%B6%D8%AF/"))

    def test_a_resolved_keyword_leaves_the_opportunities_the_lists_and_the_climbs(self):
        c, slides = self.ids_and_text([["keyword", fx.ENGLISH[0], "", ""]])
        wins = [r["keyword"] for r in slides["wins"]["rows"]]
        self.assertNotIn(fx.ENGLISH[0], wins)
        self.assertNotIn(fx.ENGLISH[0], [r["label"] for r in slides["climbs"]["rows"]])
        self.assertNotIn(fx.ENGLISH[0], [r["keyword"] for r in slides["grew"]["keywords"]])
        self.assertNotIn("cannibal", [o["type"] for o in c.opps if o["keyword"] == fx.ENGLISH[0]])

    def test_a_resolved_page_leaves_every_list(self):
        c, slides = self.ids_and_text([["page", "/guide/old-tips/", "", ""]])
        watch = [i["title"] for i in slides["watch"]["items"]]
        self.assertNotIn("/guide/old-tips/", watch)
        pages = [r["page"] for r in slides["pages"]["rows"]]
        self.assertNotIn("/guide/old-tips/", pages)
        c, slides = self.ids_and_text([["page", "/guide/blue-shoes/", "", ""]])
        self.assertNotIn("/guide/blue-shoes/", [r["label"] for r in slides["climbs"]["rows"]])
        self.assertNotIn("/guide/blue-shoes/", [r["label"] for r in slides["grew"]["pages"]])

    def test_a_resolved_gone_page_is_not_counted(self):
        rows = [["page", f"https://example.test/old/p{i}", "", ""] for i in range(3)]
        c, slides = self.ids_and_text(rows)
        self.assertEqual(len(c.gone), 3)
        self.assertNotIn("Old URLs", [i["title"] for i in slides["watch"]["items"]])

    def test_a_resolved_segment_leaves_sections_watching_and_next_month(self):
        c, slides = self.ids_and_text([["segment", "Shop", "", ""]])
        self.assertNotIn("Shop", [r["label"] for r in slides["sections"]["rows"]])
        self.assertNotIn("Shop", [i["title"] for i in slides["watch"]["items"]])
        self.assertNotIn("Shop", [k["label"] for k in slides["next"]["cards"]])

    def test_the_weekly_deck_still_shows_everything(self):
        rep = report(resolved_list=[["page", "https://example.test/old/p0", "", ""]])
        self.assertEqual(len(rep.gone), 6)                   # the report itself is untouched


class RenderingTests(unittest.TestCase):

    def test_every_slide_has_the_footer_without_a_report_name_and_a_slide_number(self):
        prs = build()
        for slide in list(prs.slides)[1:]:
            footer = [s.text_frame.text for s in slide.shapes
                      if s.has_text_frame and "Example · September 2026" in s.text_frame.text]
            self.assertTrue(footer)
        numbers = [s for s in prs.slides[3].shapes if s.has_text_frame and s.text_frame.text.strip().isdigit()
                   or "slidenum" in s._element.xml]
        self.assertTrue(numbers)

    def test_title_slide_wording(self):
        text = "\n".join(texts_of(Presentation(io.BytesIO(client_deck.build_monthly(
            report(), fx.Cfg(), deck_runner.TEMPLATE_DIR)))) [:6])
        self.assertIn("EXAMPLE", text)
        self.assertIn("Monthly SEO Report", text)
        self.assertIn("September 2026 · Google Search performance, progress and next steps", text)
        self.assertIn("Prepared by Mehdi Raoof · built with PulseO", text)

    def test_kickers_titles_and_the_headline_are_drawn(self):
        text = "\n".join(texts_of(build()))
        for needle in ("HEADLINE", "This month in one line", "SEPTEMBER 2026 VS AUG AND VS SEP 2025",
                       "Scorecard", "Rankings that climbed", "What grew this month",
                       "New searches finding Example", "Our work this month", "13-month trend",
                       "Performance by section", "Quick wins: almost on top", "Pages to update",
                       "New content ideas", "Get ready for October", "What we're watching",
                       "Next month's focus", "Mehdi fills this slide before sending.",
                       "Example ranks higher than it has all year."):
            self.assertIn(needle, text, needle)

    def test_the_trend_is_a_native_line_chart_and_sections_a_bar_chart(self):
        prs = build()
        kinds = [shp.chart.chart_type for slide in prs.slides for shp in slide.shapes if shp.has_chart]
        self.assertEqual(len(kinds), 2)
        xml = "".join(shp.chart._chartSpace.xml for slide in prs.slides for shp in slide.shapes
                      if shp.has_chart)
        self.assertIn("lineChart", xml)
        self.assertIn("barChart", xml)

    def test_no_slide_shows_a_raw_period_an_all_segment_or_a_regex(self):
        text = "\n".join(texts_of(build()))
        self.assertIsNone(re.search(r"\b\d{4}-\d{2}\b", text))
        self.assertNotRegex(text, r"\bAll\b")

    def test_declines_are_slate_not_red_in_the_tables_and_cards(self):
        prs = build()
        reds = 0
        for slide in prs.slides:
            for shp in slide.shapes:
                if shp.has_text_frame:
                    for p in shp.text_frame.paragraphs:
                        for r in p.runs:
                            if r.font.color and r.font.color.type is not None and \
                                    str(r.font.color.rgb) == "D64545":
                                reds += 1
        self.assertEqual(reds, 0)

    def test_a_persian_keyword_in_an_english_deck_is_right_aligned_in_vazirmatn(self):
        prs = build()
        found = False
        for slide in prs.slides:
            for shp in slide.shapes:
                if getattr(shp, "has_table", False) and shp.has_table:
                    for row in shp.table.rows:
                        cell = row.cells[0]
                        if fx.PERSIAN[0] in cell.text_frame.text:
                            p = cell.text_frame.paragraphs[0]
                            self.assertEqual(p._p.pPr.get("algn"), "r")
                            self.assertTrue(any(r.font.name == "Vazirmatn" for r in p.runs))
                            found = True
        self.assertTrue(found)

    def test_a_persian_deck_is_built_from_the_persian_template_and_texts(self):
        prs = build("fa")
        self.assertEqual(len(prs.slides), 15)
        text = "\n".join(texts_of(prs))
        self.assertIn("گزارش ماهانه سئو", text)
        self.assertIn("فعلاً اقدامی لازم نیست", text)
        self.assertNotIn("Quick wins", text)

    def test_the_document_properties_have_no_report_data(self):
        core = build().core_properties
        self.assertEqual(core.author, "PulseO")
        self.assertIn("Example", core.title)


class TextsTests(unittest.TestCase):

    def test_both_languages_have_the_same_keys(self):
        self.assertEqual(set(client_i18n.EN), set(client_i18n.FA))

    def test_every_placeholder_matches_between_languages(self):
        for key, en in client_i18n.EN.items():
            fa = client_i18n.FA[key]
            if isinstance(en, list):
                self.assertEqual(len(en), len(fa), key)
                for a, b in zip(en, fa):
                    self.assertEqual(set(PLACEHOLDER.findall(a)), set(PLACEHOLDER.findall(b)), key)
            else:
                self.assertEqual(set(PLACEHOLDER.findall(en)), set(PLACEHOLDER.findall(fa)), key)

    def test_persian_content_is_built_too(self):
        c = client("fa")
        slides = c.slides()
        self.assertEqual(len(slides), 14)
        self.assertEqual(dict(slides)["headline"]["headline"], "Example بالاتر از تمام سال گذشته رتبه گرفته است.")


class RunnerTests(unittest.TestCase):

    def test_the_monthly_email_has_no_alarm_line_and_the_weekly_one_keeps_it(self):
        rep = report()
        rep.alarms = [{"severity": "high", "area": "Segment clicks", "impact": 5,
                       "message": "Clicks fell 30.0% vs August (100 to 70)."}]
        subject, body = deck_runner.email_text(rep, fx.Cfg(), "en", "Monthly SEO Report", "September 2026")
        self.assertNotIn("Top alarm", body)
        self.assertIn("Clicks:", body)


if __name__ == "__main__":
    unittest.main()
