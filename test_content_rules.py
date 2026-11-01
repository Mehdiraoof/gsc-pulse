"""Tests for the content dashboard rules of v2.7: the removal rule, the page list (Search Console,
history, sitemap), empty Primary Keywords, the owner's priority and the idea rules.
Search Console, Sheets, the sitemap and the live pages are all in-memory fakes; every site, page and
search is invented."""

import json
import unittest
from datetime import date
from unittest import mock

import content
import content_run
import content_sheet as cs
import history
import test_content_run as base
from site_check import Probe
from test_content_run import A, B, HOME, ROOT
from test_history import FakeSheets

DAY2, DAY3 = date(2026, 10, 28), date(2026, 10, 29)
ZWNJ_WORD = "می‌خواهم"            # می‌خواهم (half space)
TATWEEL_WORD = "میـخواهم"


def base_key(url):
    return content.url_key(url)


def probes(table):
    """A fake live site: the answer for each address, 200 for the ones not named."""
    return lambda url: table.get(url, Probe(200))


class RemovalTests(base.Base):
    """The only reason a page row leaves a tab: gone or moved, found twice in a row."""

    def urls(self, tab="Articles"):
        return {r[content.URL] for r in self.table(tab)[1] if r[content.URL]}

    def test_a_gone_page_is_warned_then_removed_then_never_added_again(self):
        gone = probes({B: Probe(404)})
        first = self.run_it(probe=gone)
        self.assertIn("Page returns 404, will be removed next refresh if still gone",
                      self.find("Articles", B)["Alerts"])
        self.assertEqual((first.checked.get("404"), first.checked.get("ok"), first.warned, first.removed),
                         (1, 2, 1, {}))
        second = self.run_it(force=True, today=DAY2, probe=gone)
        self.assertEqual(second.removed, {"gone": 1})
        self.assertNotIn(B, self.urls())
        header, rows = self.table(cs.REMOVED)
        self.assertEqual(header[:5], cs.REMOVED_LEAD)
        (r,) = rows
        self.assertEqual((r["Published URL"], r["Primary Keyword"], r["Reason"], r["Date"], r["Moved To"]),
                         (B, "tiny question", "Page returns 404", "2026-10-28", ""))
        self.assertEqual((r[content.STATUS], r[content.TODO]), ("Published", "Keep"))
        self.run_it(force=True, today=DAY3, probe=gone)
        self.assertNotIn(B, self.urls())                                   # Search Console still lists it
        self.assertEqual(len(self.table(cs.REMOVED)[1]), 1)
        for name, tab in self.fs.books[self.file_id].items():
            self.assertNotIn("pulse", json.dumps(tab.data, ensure_ascii=False).lower(), name)

    def test_a_410_counts_like_a_404(self):
        self.run_it(probe=probes({B: Probe(404)}))
        second = self.run_it(force=True, today=DAY2, probe=probes({B: Probe(410)}))
        self.assertEqual(second.removed, {"gone": 1})

    def test_the_same_day_does_not_confirm(self):
        gone = probes({B: Probe(404)})
        self.run_it(probe=gone)
        again = self.run_it(force=True, probe=gone)
        self.assertEqual(again.removed, {})
        self.assertIn(B, self.urls())

    def test_an_answer_that_says_nothing_clears_the_warning(self):
        self.run_it(probe=probes({B: Probe(404)}))
        for blocked in (Probe(503), Probe(403), Probe(429), Probe(0)):
            self.run_it(force=True, today=DAY2, probe=probes({B: blocked}))
            self.assertIn(B, self.urls())
            self.assertNotIn("Page returns", self.find("Articles", B)["Alerts"])
        third = self.run_it(force=True, today=DAY3, probe=probes({B: Probe(404)}))
        self.assertEqual(third.removed, {})                                # only two answers in a row count
        self.assertIn(B, self.urls())

    def test_a_page_that_answers_again_is_not_removed(self):
        self.run_it(probe=probes({B: Probe(404)}))
        self.run_it(force=True, today=DAY2, probe=probes({B: Probe(200)}))
        third = self.run_it(force=True, today=DAY3, probe=probes({B: Probe(404)}))
        self.assertEqual(third.removed, {})

    def test_a_moved_page_hands_its_cells_to_the_row_it_redirects_to(self):
        moved = probes({B: Probe(301, A)})
        self.run_it(probe=moved)
        self.assertIn(f"Redirects to {A}", self.find("Articles", B)["Alerts"])
        self.edit("Articles", B, "PIC", "Sara")
        self.edit("Articles", B, "Notes/Checklist", "call author")
        second = self.run_it(force=True, today=DAY2, probe=moved)
        self.assertEqual(second.removed, {"redirect": 1})
        self.assertNotIn(B, self.urls())
        a = self.find("Articles", A)
        self.assertEqual(a["PIC"], "Sara")                                  # the target had none
        self.assertEqual(a[content.KEY], "garden shoes")                    # ... and keeps the keyword it has
        self.assertEqual(a[content.TODO], "Update")
        self.assertEqual(a["Notes/Checklist"], f"Moved here from {B} (that page redirects here): call author")
        (r,) = self.table(cs.REMOVED)[1]
        self.assertEqual((r["Published URL"], r["Reason"], r["Moved To"], r["PIC"]),
                         (B, f"Redirects to {A}", A, "Sara"))

    def test_a_redirect_to_a_page_without_a_row_makes_the_row(self):
        target = f"{ROOT}/article/new-home"
        moved = probes({B: Probe(301, target)})
        self.run_it(probe=moved)
        second = self.run_it(force=True, today=DAY2, probe=moved)
        self.assertEqual((second.removed, second.added["redirect"]), ({"redirect": 1}, 1))
        new = self.find("Articles", target)
        self.assertEqual(new[content.KEY], "tiny question")                 # the target had no keyword
        self.assertIn("Moved here from", new["Notes/Checklist"])
        self.assertNotIn(B, self.urls())

    def test_a_redirect_to_a_page_that_is_in_no_tab_only_moves_the_row_out(self):
        moved = probes({B: Probe(302, HOME)})
        self.run_it(probe=moved)
        second = self.run_it(force=True, today=DAY2, probe=moved)
        self.assertEqual(second.removed, {"redirect": 1})
        (r,) = self.table(cs.REMOVED)[1]
        self.assertEqual((r["Reason"], r["Moved To"]), (f"Redirects to {HOME}", ""))
        self.assertNotIn(HOME, self.urls() | self.urls("Landing Pages"))

    def test_a_redirect_to_the_same_page_is_no_move(self):
        moved = probes({B: Probe(301, B + "/")})                            # only a slash was added
        self.run_it(probe=moved)
        second = self.run_it(force=True, today=DAY2, probe=moved)
        self.assertEqual((second.removed, second.warned), ({}, 0))
        self.assertNotIn("Redirects", self.find("Articles", B)["Alerts"])

    def test_rows_without_a_page_are_never_removed_by_the_status_check(self):
        nothing = probes({u: Probe(404) for u in (A, B, base.ABOUT)})
        self.run_it(probe=nothing)
        second = self.run_it(force=True, today=DAY2, probe=nothing)
        self.assertEqual(second.removed, {"gone": 3})
        self.assertIsNotNone(self.topic("Articles", "best bread recipes"))   # ideas stay
        self.assertEqual(len(self.table(cs.REMOVED)[1]), 3)

    def test_no_check_means_no_change(self):
        self.run_it()
        second = self.run_it(force=True, today=DAY2)
        self.assertEqual((second.checked, second.removed, second.warned), ({}, {}, 0))


class SourceTests(base.Base):
    """Page rows come from Search Console, the stored history and the sitemap."""

    def urls(self, tab="Articles"):
        return {r[content.URL] for r in self.table(tab)[1] if r[content.URL]}

    def test_history_and_sitemap_pages_get_rows_with_the_no_data_alert(self):
        old = f"{ROOT}/article/old-post"
        history_urls = [old, old + "#top", A, f"{ROOT}/other/page"]
        sitemap = lambda: ([f"{ROOT}/article/new-post", old + "/", base.ABOUT, f"{ROOT}/elsewhere/x"], True)
        result = self.run_it(history_pages=lambda: history_urls, sitemap=sitemap)
        self.assertEqual(self.urls(), {A, B, old, f"{ROOT}/article/new-post"})
        self.assertEqual(result.added, {"search": 3, "history": 1, "sitemap": 1, "redirect": 0})
        self.assertEqual(result.new_pages, 5)
        self.assertEqual(result.sources, {"history": 4, "sitemap": 4})
        for url in (old, f"{ROOT}/article/new-post"):
            r = self.find("Articles", url)
            self.assertEqual((r[content.STATUS], r["Impressions (90d)"], r["Suggested Action"]),
                             ("Published", "0", "Waiting for data"))
            self.assertIn("No search data this month", r["Alerts"])
        self.assertIn("1 from history", result.text())
        self.assertIn("1 from sitemap", result.text())

    def test_history_and_sitemap_pages_that_are_already_gone_never_get_a_row(self):
        gone_h, gone_s = f"{ROOT}/article/h-gone", f"{ROOT}/article/s-gone"
        live, moved = f"{ROOT}/article/h-live", f"{ROOT}/article/h-moved"
        probe = probes({gone_h: Probe(404), gone_s: Probe(410), B: Probe(404), moved: Probe(301, A)})
        result = self.run_it(history_pages=lambda: [gone_h, live, moved], sitemap=lambda: ([gone_s], True),
                             probe=probe)
        self.assertEqual(self.urls(), {A, B, live, moved})                  # a page from Search Console keeps the 2-refresh rule
        self.assertIn("Page returns 404", self.find("Articles", B)["Alerts"])
        self.assertIn("Redirects to", self.find("Articles", moved)["Alerts"])    # only gone pages are left out
        self.assertEqual(result.found_gone, 2)
        self.assertEqual(result.added, {"search": 3, "history": 2, "sitemap": 0, "redirect": 0})
        self.assertEqual(result.new_pages, 5)
        self.assertIn("2 history/sitemap pages already gone", result.text())
        state = {tuple(r[:2]) for r in map(list, self.raw("_state").data[1:])}
        self.assertNotIn(("gone", base_key(gone_h)), state)                  # nothing is remembered about them
        self.run_it(force=True, today=DAY2, history_pages=lambda: [gone_h, live], probe=probe)
        self.assertNotIn(gone_h, self.urls())
        back = self.run_it(force=True, today=DAY3, history_pages=lambda: [gone_h, live], probe=probes({}))
        self.assertIn(gone_h, self.urls())                                    # it answers again: now it is a page
        self.assertEqual(back.found_gone, 0)

    def test_a_one_off_clean_up_moves_the_rows_that_were_added_already_gone(self):
        old, mine = f"{ROOT}/article/old-h", f"{ROOT}/article/mine"
        self.run_it()
        self.add_row("Articles", **{content.KEY: "old h", content.URL: old, content.STATUS: "Published"})
        self.add_row("Articles", **{content.KEY: "mine", content.URL: mine, content.STATUS: "Published", "PIC": "Sara"})
        gone = probes({old: Probe(404), mine: Probe(404)})
        self.run_it(force=True, probe=gone)                                  # the first warning, on TODAY
        result = self.run_it(force=True, today=DAY2, probe=gone, cleanup_gone_since="2026-10-27")
        reasons = {r["Published URL"]: r["Reason"] for r in self.table(cs.REMOVED)[1]}
        self.assertEqual(reasons[old], "Already gone when found")
        self.assertEqual(reasons[mine], "Page returns 404")                  # a row with work in it keeps the normal rule
        self.assertEqual(result.removed, {"found gone": 1, "gone": 1})
        self.assertNotIn(old, self.urls())
    def test_a_sitemap_that_cannot_be_read_changes_nothing(self):
        self.run_it()
        before = self.urls()
        count = len(self.table("Articles")[1])
        result = self.run_it(force=True, sitemap=lambda: ([f"{ROOT}/article/never"], False))
        self.assertEqual(result.sources["sitemap"], "not readable")
        self.assertEqual((self.urls(), len(self.table("Articles")[1])), (before, count))
        self.assertTrue(any("no sitemap could be read" in l for l in self.logs))

    def test_a_sitemap_or_history_that_fails_is_skipped_and_the_run_goes_on(self):
        def boom():
            raise RuntimeError("secret-address-in-the-message")
        result = self.run_it(sitemap=boom, history_pages=boom)
        self.assertTrue(result.refreshed)
        self.assertEqual(result.sources, {"history": "not readable", "sitemap": "not readable"})
        self.assertFalse(any("secret-address" in l for l in self.logs))

    def test_a_page_removed_earlier_is_not_added_back_by_the_history_or_sitemap(self):
        gone = probes({B: Probe(404)})
        self.run_it(probe=gone)
        self.run_it(force=True, today=DAY2, probe=gone)
        self.run_it(force=True, today=DAY3, probe=gone, history_pages=lambda: [B], sitemap=lambda: ([B], True))
        self.assertNotIn(B, self.urls())

    def test_an_address_with_and_without_a_half_space_or_tatweel_gets_a_row_each(self):
        zw, tat = f"{ROOT}/article/{ZWNJ_WORD}", f"{ROOT}/article/{TATWEEL_WORD}"
        self.run_it(history_pages=lambda: [zw, tat, zw + "?x=1", zw + "#y"])
        rows = [r for r in self.table("Articles")[1] if r[content.URL] in (zw, tat)]
        self.assertEqual(len(rows), 2)                                      # nothing is folded into one
        for r in rows:
            self.assertIn("Duplicate URL", r["Alerts"])

    def test_the_history_reader_lists_each_page_once(self):
        fs = FakeSheets()
        fs.add_book("HIST", {"H Pages": (20, 6), "State": (20, 4)})
        pages = ["https://e.test/a", "https://e.test/b", "https://e.test/a", "https://e.test/c#x", "https://e.test/d"]
        book = fs.books["HIST"]
        book["H Pages"].data = [["Date", "Page", "Clicks", "Impressions", "CTR (%)", "Avg. Position"]] + [
            [f"2026-01-0{i}", p, 1, 10, 1.0, 5.0] for i, p in enumerate(pages, start=1)]
        book["State"].data = [history.STATE_HEADER, ["H Pages", "2026-01-05", 5, "now"]]
        expected = ["https://e.test/a", "https://e.test/b", "https://e.test/c#x", "https://e.test/d"]
        self.assertEqual(history.read_page_urls(fs, "HIST", chunk=2), expected)
        self.assertEqual(history.read_page_urls(fs, "HIST"), expected)
        del book["State"]                                                    # no state: read until the end
        self.assertEqual(history.read_page_urls(fs, "HIST", chunk=2), expected)
        del book["H Pages"]
        self.assertEqual(history.read_page_urls(fs, "HIST"), [])


class KeywordTests(base.Base):

    NEW = f"{ROOT}/article/quiet-post"

    def sitemap(self, *urls):
        return lambda: (list(urls), True)

    def test_an_empty_keyword_takes_the_top_search_of_the_page_even_from_older_months(self):
        old = [("example old brand", self.NEW, 1, 500, 1.0), ("old search", self.NEW, 2, 90, 15.0),
               ("older search", self.NEW, 1, 40, 18.0)]
        with mock.patch(f"{base.__name__}.OLD_PAIRS", old):
            result = self.run_it(sitemap=self.sitemap(self.NEW))
        r = self.find("Articles", self.NEW)
        self.assertEqual(r[content.KEY], "old search")                       # the brand search is skipped
        self.assertEqual(r["Notes/Checklist"], "")
        self.assertEqual(result.keywords, {"searches": 4, "title": 0, "empty": 0})   # the three pages of the site, too

    def test_without_searches_the_title_is_used_and_the_note_says_so_once(self):
        probe = probes({self.NEW: Probe(200, "", "A Quiet Post")})
        result = self.run_it(sitemap=self.sitemap(self.NEW), probe=probe)
        r = self.find("Articles", self.NEW)
        self.assertEqual((r[content.KEY], r["Notes/Checklist"]), ("A Quiet Post", content_run.NOTE_FROM_TITLE))
        self.assertEqual(result.keywords["title"], 1)
        self.run_it(force=True, today=DAY2, sitemap=self.sitemap(self.NEW), probe=probe)
        r = self.find("Articles", self.NEW)
        self.assertEqual((r[content.KEY], r["Notes/Checklist"]), ("A Quiet Post", content_run.NOTE_FROM_TITLE))

    def test_without_searches_and_without_a_title_the_cell_stays_empty_with_a_note(self):
        probe = probes({self.NEW: Probe(200, "", "")})
        result = self.run_it(sitemap=self.sitemap(self.NEW), probe=probe)
        r = self.find("Articles", self.NEW)
        self.assertEqual((r[content.KEY], r["Notes/Checklist"]), ("", content_run.NOTE_NO_KEYWORD))
        self.assertEqual(result.keywords["empty"], 1)
        self.run_it(force=True, today=DAY2, sitemap=self.sitemap(self.NEW), probe=probe)
        self.assertEqual(self.find("Articles", self.NEW)["Notes/Checklist"], content_run.NOTE_NO_KEYWORD)

    def test_a_title_is_only_used_when_the_page_answers(self):
        probe = probes({self.NEW: Probe(503, "", "")})
        self.run_it(sitemap=self.sitemap(self.NEW), probe=probe)
        self.assertEqual(self.find("Articles", self.NEW)[content.KEY], "")

    def test_a_keyword_the_owner_set_is_never_changed(self):
        self.run_it()
        self.edit("Articles", A, content.KEY, "mine")
        with mock.patch(f"{base.__name__}.OLD_PAIRS", [("other", A, 1, 900, 3.0)]):
            self.run_it(force=True, today=DAY2, probe=probes({}))
        self.assertEqual(self.find("Articles", A)[content.KEY], "mine")

    def test_the_questions_to_search_console_have_a_limit_and_the_rest_waits_for_next_time(self):
        pages = [f"{ROOT}/article/quiet-{i}" for i in range(3)]
        with mock.patch.object(content_run, "MAX_PAGE_QUERY_CALLS", 2):
            result = self.run_it(sitemap=self.sitemap(*pages))
        notes = sorted(self.find("Articles", p)["Notes/Checklist"] for p in pages)
        self.assertEqual(notes, ["", "", content_run.NOTE_NO_KEYWORD])       # one asked (two calls), two later
        self.assertEqual(result.keywords["empty"], 1)


class PriorityTests(base.Base):

    def state(self):
        return {tuple(r[:2]): r[2] for r in map(list, self.raw("_state").data[1:])}

    def test_a_priority_the_owner_changed_is_kept(self):
        self.run_it()
        mine = "Urgent" if self.find("Articles", A)["Priority"] != "Urgent" else "Low"
        self.edit("Articles", A, content.PRIORITY, mine)
        self.run_it(force=True)
        self.assertEqual(self.find("Articles", A)["Priority"], mine)
        self.run_it(force=True)
        self.assertEqual(self.find("Articles", A)["Priority"], mine)
        state = self.state()
        self.assertEqual(state[("prio", "u:" + content.url_key(A))], "*")
        self.assertNotEqual(state[("prio", "u:" + content.url_key(B))], "*")

    def test_a_priority_the_owner_did_not_touch_follows_the_data(self):
        self.run_it()
        before = self.find("Articles", A)["Priority"]
        stronger = {**base.PAGES, A: (30, 20000, 12.0)}
        with mock.patch(f"{base.__name__}.PAGES", stronger):
            self.run_it(force=True)
        after = self.find("Articles", A)["Priority"]
        self.assertNotEqual(before, after)
        self.assertIn(after, ("High", "Urgent"))


class IdeaRuleTests(base.Base):

    def test_the_minimum_impressions_come_from_the_config(self):
        site = base.make_site(min_gap_impressions_90d=600)
        result = self.run_it(site=site)
        self.assertEqual(result.new_topics, 0)                              # 500 and 300 are under 600
        self.assertIsNone(self.topic("Articles", "best bread recipes"))

    def test_excluded_terms_never_become_ideas_but_pages_that_rank_for_them_stay(self):
        extra = [("hot bread story", A, 3, 120, 8.0)]
        with mock.patch(f"{base.__name__}.PAIRS", base.PAIRS + extra):
            result = self.run_it(site=base.make_site(gap_exclude_terms=["bread"]))
        self.assertEqual(result.new_topics, 0)
        self.assertIn("hot bread story", self.find("Articles", A)["Secondary Keywords"])
        state = {tuple(r[:2]) for r in map(list, self.raw("_state").data[1:])}
        self.assertFalse([k for k in state if k[0] == "gap"])

    def test_excluded_terms_never_become_keyword_ideas(self):
        self.run_it()
        self.add_row("Keyword Seeds", Seed="shoes", Language="en", Country="", Active="Yes")

        def http(url):
            q = url.split("&q=")[1].split("&")[0]
            return 200, json.dumps([q, [f"{q} one", "garden shoes extra"]])
        self.run_it(http=http, site=base.make_site(gap_exclude_terms=["one"]))
        keywords = [r["Keyword"] for r in self.table("Keyword Ideas")[1]]
        self.assertIn("garden shoes extra", keywords)
        self.assertFalse([k for k in keywords if "one" in k.split()])

    def test_a_search_far_down_on_a_page_about_something_else_is_an_idea(self):
        far = [("blue pottery wheel", A, 0, 400, 30.0)]                     # a content page, but beyond position 20
        with mock.patch(f"{base.__name__}.PAIRS", base.PAIRS + far):
            self.run_it()
        self.assertIsNotNone(self.topic("Articles", "blue pottery wheel"))

    def test_a_search_far_down_on_a_page_that_is_about_it_is_not(self):
        far = [("garden shoes cheap", A, 0, 400, 30.0)]
        with mock.patch(f"{base.__name__}.PAIRS", base.PAIRS + far):
            self.run_it()
        self.assertIsNone(self.topic("Articles", "garden shoes cheap"))


class DropdownTests(base.Base):

    def choices(self):
        out = []
        for book, req in self.fs.requests:
            if book == self.file_id and "setDataValidation" in req:
                out.append([v["userEnteredValue"] for v in req["setDataValidation"]["rule"]["condition"]["values"]])
        return out

    def test_the_new_choices_exist(self):
        self.assertIn("Draft", content.TODOS)
        self.assertIn("Review", content.TODOS)
        self.assertIn("Drafted", content.STATUSES)
        self.assertLess(content.STATUSES.index("Ordered"), content.STATUSES.index("Drafted"))
        self.assertLess(content.STATUSES.index("Drafted"), content.STATUSES.index("Published"))

    def test_a_new_file_has_them(self):
        self.run_it()
        self.assertTrue(any("Review" in v and "Draft" in v for v in self.choices()))
        self.assertTrue(any("Drafted" in v for v in self.choices()))

    def test_every_refresh_puts_them_into_a_file_that_already_exists(self):
        self.run_it()
        self.fs.requests.clear()                                              # as if the file had been made before
        self.run_it(force=True)
        self.assertTrue(any("Review" in v and "Draft" in v for v in self.choices()))
        self.assertTrue(any("Drafted" in v for v in self.choices()))
        # one for Status and one for To Do, for each of the two tabs; no cell of the owner's changes
        self.assertEqual(len([v for v in self.choices() if "Drafted" in v]), 2)

    def test_the_search_question_limit_is_high_enough_for_a_big_site(self):
        self.assertGreaterEqual(content_run.MAX_PAGE_QUERY_CALLS, 4000)

if __name__ == "__main__":
    unittest.main()
