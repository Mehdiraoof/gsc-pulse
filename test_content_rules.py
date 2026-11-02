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
    """A page found gone or moved twice in a row is marked Removed or Redirected; its row stays in its tab."""

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
        r = self.find("Articles", B)                                       # the row stays, marked, without a score
        self.assertEqual((r["Primary Keyword"], r[content.STATUS], r["Alerts"], r["Score"], r[content.TODO]),
                         ("tiny question", "Removed", "Page returns 404", "", "Keep"))
        self.assertNotIn(cs.REMOVED, self.fs.books[self.file_id])          # no Removed Pages tab, ever
        self.run_it(force=True, today=DAY3, probe=gone)
        self.assertEqual([x[content.STATUS] for x in self.table("Articles")[1] if x[content.URL] == B],
                         ["Removed"])                                      # Search Console still lists it: no second row
        self.assertNotIn(cs.REMOVED, self.fs.books[self.file_id])
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
        b = self.find("Articles", B)                                        # the old row stays, marked, with the new address
        self.assertEqual((b[content.STATUS], b["Alerts"], b["PIC"], b["Score"]), ("Redirected", f"Redirects to {A}", "Sara", ""))
        a = self.find("Articles", A)
        self.assertEqual(a["PIC"], "Sara")                                  # the target had none
        self.assertEqual(a[content.KEY], "garden shoes")                    # ... and keeps the keyword it has
        self.assertEqual(a[content.TODO], "Update")
        self.assertEqual(a["Notes/Checklist"], f"Moved here from {B} (that page redirects here): call author")

    def test_a_redirect_to_a_page_without_a_row_makes_the_row(self):
        target = f"{ROOT}/article/new-home"
        moved = probes({B: Probe(301, target)})
        self.run_it(probe=moved)
        second = self.run_it(force=True, today=DAY2, probe=moved)
        self.assertEqual((second.removed, second.added["redirect"]), ({"redirect": 1}, 1))
        new = self.find("Articles", target)
        self.assertEqual(new[content.KEY], "tiny question")                 # the target had no keyword
        self.assertIn("Moved here from", new["Notes/Checklist"])
        self.assertEqual(self.find("Articles", B)[content.STATUS], "Redirected")

    def test_a_redirect_to_a_page_that_is_in_no_tab_only_marks_the_row(self):
        moved = probes({B: Probe(302, HOME)})
        self.run_it(probe=moved)
        second = self.run_it(force=True, today=DAY2, probe=moved)
        self.assertEqual(second.removed, {"redirect": 1})
        r = self.find("Articles", B)
        self.assertEqual((r[content.STATUS], r["Alerts"]), ("Redirected", f"Redirects to {HOME}"))
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
        rows = self.table("Articles")[1] + self.table("Landing Pages")[1]
        self.assertEqual(sum(1 for r in rows if r[content.STATUS] == "Removed"), 3)

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
        rows = {r["Published URL"]: r for r in self.table("Articles")[1]}
        self.assertEqual(rows[old]["Alerts"], "Already gone when found")
        self.assertEqual(rows[mine]["Alerts"], "Page returns 404")           # a row with work in it keeps the normal rule
        self.assertEqual((rows[old][content.STATUS], rows[mine][content.STATUS]), ("Removed", "Removed"))
        self.assertEqual(result.removed, {"found gone": 1, "gone": 1})
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


# --------------------------------------------------------------------------- #
# The tab clean-up: no Removed Pages tab, no Import Log tab; gone pages stay, marked, grey, at the bottom
# --------------------------------------------------------------------------- #

import content_import as ci
import tabs as tabs_mod


def check_dashboard(test, fs, file_id, tab_names, urls_before=(), allow=()):
    """The checker for the clean-up. Every tab: no Removed Pages or Import Log tab (unless allowed), every row that was
    there is still there, a row of a page that is gone is Removed or Redirected with a reason (and the new address for
    Redirected), has no Score (so a sort by Score puts it below every live row), and the grey rule exists."""
    titles = list(fs.books[file_id])
    for banned in (cs.REMOVED, cs.IMPORT_LOG):
        if banned not in allow:
            test.assertNotIn(banned, titles)
    present = set()
    for name in tab_names:
        header, rows = cs.table_of(cs.read_all(fs, file_id, name))
        for r in rows:
            if r.get(content.URL):
                present.add(content.url_key(r[content.URL]))
            if content.is_settled(r):
                test.assertIn(r[content.STATUS], ("Removed", "Redirected"), r.get(content.URL))
                test.assertTrue(str(r.get("Alerts", "")).strip(), "a settled row says why")
                test.assertEqual(r.get("Score", ""), "", "a settled row has no score")
                if r[content.STATUS] == "Redirected":
                    test.assertRegex(str(r["Alerts"]), r"https?://", "the new address is in Alerts")
        order = sorted(range(len(rows)), key=lambda i: (rows[i].get("Score", "") == "", -(rows[i].get("Score") or 0)))
        settled_tail = [content.is_settled(rows[i]) for i in order]
        test.assertEqual(settled_tail, sorted(settled_tail), "settled rows sort below the live ones")
    test.assertTrue({content.url_key(u) for u in urls_before} <= present, "no row was lost")
    rules = [req["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
             for book, req in fs.requests if book == file_id and "addConditionalFormatRule" in req]
    test.assertTrue(any('"Redirected"' in r and '"Removed"' in r for r in rules), "the grey rule exists")


class CleanupTests(base.Base):

    GONE = f"{ROOT}/article/old-gone"
    MOVED = f"{ROOT}/article/old-moved"
    LANDING = f"{ROOT}/old-landing"

    def make_removed_tab(self, rows):
        header = cs.REMOVED_LEAD + [c for c in ci.TARGETS.values() if c not in cs.REMOVED_LEAD]
        self.fs.spreadsheets().batchUpdate(spreadsheetId=self.file_id, body={"requests": [
            {"addSheet": {"properties": {"title": cs.REMOVED}}}]}).execute()
        values = [header] + [[r.get(h, "") for h in header] for r in rows]
        tabs_mod.patch_cells(self.fs, self.file_id, [(f"'{cs.REMOVED}'!A1:{cs.letters(len(header) - 1)}{len(values)}", values)])

    def setUp(self):
        super().setUp()
        self.run_it()
        self.legacy = [
            {"Published URL": self.GONE, "Primary Keyword": "old gone", "Reason": "Page returns 404", "Date": "2026-10-01",
             "Moved To": "", "Status": "Published", "To Do": "Keep", "PIC": "Sara", "Notes/Checklist": "call the author"},
            {"Published URL": self.MOVED, "Primary Keyword": "old moved", "Reason": f"Redirects to {A}", "Date": "2026-10-02",
             "Moved To": A, "Status": "Published", "To Do": "Keep"},
            {"Published URL": "", "Primary Keyword": "old topic", "Reason": ci.REPAIR_REASON, "Date": "2026-10-03",
             "Moved To": A, "Status": "Researched"},
            {"Published URL": self.LANDING, "Primary Keyword": "old landing", "Reason": "Page returns 410", "Date": "2026-10-04",
             "Moved To": "", "Status": "Published"}]
        self.make_removed_tab(self.legacy)
        self.live = [r[content.URL] for r in self.table("Articles")[1] + self.table("Landing Pages")[1] if r[content.URL]]

    def rows(self):
        return self.table("Articles")[1], self.table("Landing Pages")[1]

    def test_the_old_rows_come_back_to_their_own_tabs_marked_and_the_tab_is_deleted(self):
        before = self.rows()
        self.run_it()                                                   # any run does it, not only the monthly refresh
        articles, landing = self.rows()
        self.assertEqual((len(articles) - len(before[0]), len(landing) - len(before[1])), (3, 1))
        by_url = {r[content.URL]: r for r in articles + landing if r[content.URL]}
        gone, moved, old_landing = by_url[self.GONE], by_url[self.MOVED], by_url[self.LANDING]
        self.assertEqual((gone[content.STATUS], gone["Alerts"], gone["PIC"], gone["Notes/Checklist"], gone["Score"]),
                         ("Removed", "Page returns 404", "Sara", "call the author", ""))      # nothing the owner wrote is lost
        self.assertEqual((moved[content.STATUS], moved["Alerts"]), ("Redirected", f"Redirects to {A}"))
        self.assertEqual((old_landing[content.STATUS], old_landing["Alerts"]), ("Removed", "Page returns 410"))
        (topic,) = [r for r in articles if r[content.KEY] == "old topic"]
        self.assertEqual((topic[content.STATUS], topic["Alerts"]), ("Removed", f"{ci.REPAIR_REASON} ({A})"))
        # they are at the bottom of their tab, in the order they had
        self.assertEqual([r[content.KEY] for r in articles[-3:]], ["old gone", "old moved", "old topic"])
        self.assertEqual(landing[-1][content.KEY], "old landing")
        check_dashboard(self, self.fs, self.file_id, ["Articles", "Landing Pages"], self.live + [self.GONE, self.MOVED, self.LANDING])

    def test_a_second_run_changes_nothing_and_a_refresh_does_not_bring_the_tab_back(self):
        self.run_it()
        count = len(self.rows()[0])
        self.run_it()
        self.run_it(force=True, today=DAY2)
        articles, landing = self.rows()
        self.assertEqual(sum(1 for r in articles + landing if r[content.STATUS] in ("Removed", "Redirected")), 4)
        self.assertEqual(len(articles), count)
        check_dashboard(self, self.fs, self.file_id, ["Articles", "Landing Pages"], self.live + [self.GONE, self.MOVED, self.LANDING])
        # the removed pages are not looked at again and not added again from Search Console, the history or the sitemap
        probe = probes({self.GONE: Probe(200), self.MOVED: Probe(200)})
        self.run_it(force=True, today=DAY3, probe=probe, history_pages=lambda: [self.GONE, self.MOVED],
                    sitemap=lambda: ([self.GONE, self.MOVED], True))
        urls = [r[content.URL] for r in self.rows()[0]]
        self.assertEqual((urls.count(self.GONE), urls.count(self.MOVED)), (1, 1))
        rows = {r[content.URL]: r for r in self.rows()[0]}
        self.assertEqual((rows[self.GONE][content.STATUS], rows[self.MOVED][content.STATUS]), ("Removed", "Redirected"))

    def test_a_move_that_was_interrupted_does_not_duplicate_rows(self):
        self.run_it()                                                   # the move is done ...
        self.make_removed_tab(self.legacy)                              # ... and the old tab is back (the delete never happened)
        self.run_it()
        urls = [r[content.URL] for r in self.rows()[0] + self.rows()[1] if r[content.URL]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertNotIn(cs.REMOVED, self.fs.books[self.file_id])

    def test_the_grey_rule_is_in_a_new_tab_and_added_once_to_a_tab_made_before_it(self):
        def grey_rules():
            return [1 for book, req in self.fs.requests if book == self.file_id and "addConditionalFormatRule" in req
                    and "Redirected" in req["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]]
        made = len(grey_rules())                                        # the tabs were created with it
        self.assertEqual(made, 2)
        state = content_run.State(cs.read_all(self.fs, self.file_id, cs.STATE, 4))
        state.delete("format", "settled")                               # a dashboard from an older version has no flag
        state.save(self.fs, self.file_id)
        self.run_it()
        self.run_it(force=True, today=DAY2)
        self.assertEqual(len(grey_rules()), made + 2)

    def test_a_dashboard_without_a_removed_pages_tab_is_left_alone(self):
        self.run_it()
        before = [t.data[:] for t in self.fs.books[self.file_id].values()]
        self.run_it()
        self.assertEqual(before, [t.data[:] for t in self.fs.books[self.file_id].values()])

    def test_what_the_refresh_never_makes(self):
        probe = probes({B: Probe(404)})
        self.run_it(probe=probe, force=True, today=DAY2)
        self.run_it(probe=probe, force=True, today=DAY3)
        titles = list(self.fs.books[self.file_id])
        self.assertNotIn(cs.IMPORT_LOG, titles)
        self.assertFalse(hasattr(cs, "ensure_import_log") or hasattr(cs, "ensure_removed_pages") or hasattr(cs, "delete_rows"))
