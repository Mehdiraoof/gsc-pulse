"""Rows the owner took away (or a reviewer merged away) do not come back: a page row deleted by hand is not added again
although its page is still in the sitemap and in Search Console, a keyword row deleted or merged is not proposed again,
and a keyword that any row has (even a row marked Removed) is never a new idea. A fake site (example.test)."""

import unittest

import content
import content_run
import test_content_run as tcr
from test_content_run import A, B, ABOUT, Base


class RemovedRowsTests(Base):

    def setUp(self):
        super().setUp()
        self.run_it()                                           # the dashboard is made; every row is remembered
        self.run_it(force=True)

    def delete_row(self, tab, matches):
        t = self.raw(tab)
        for i, r in enumerate(t.data):
            if i and r and matches(r, t.data[0]):
                t.data[i] = []
                return
        raise AssertionError("row not found")

    def delete_page(self, tab, url):
        self.delete_row(tab, lambda r, h: len(r) > h.index(content.URL) and r[h.index(content.URL)] == url)

    def urls(self, tab):
        return {r.get(content.URL) for r in self.table(tab)[1] if r.get(content.URL)}

    def state_keys(self, kind):
        return {r[1] for r in map(list, self.raw("_state").data[1:]) if r and r[0] == kind}

    def test_a_page_row_deleted_by_hand_is_not_added_again_although_the_page_is_still_there(self):
        self.assertIn(A, self.urls("Articles"))
        self.delete_page("Articles", A)
        self.run_it(force=True)
        self.assertNotIn(A, self.urls("Articles"))
        self.assertIn(content.url_key(A), self.state_keys(content_run.DROPPED_PAGE))
        self.run_it(force=True)
        self.assertNotIn(A, self.urls("Articles"))
        self.assertIn(B, self.urls("Articles"))                  # the other rows are as they were

    def test_nothing_else_changes_in_the_rows_that_are_left(self):
        self.edit("Articles", B, "PIC", "Sara")
        self.edit("Articles", B, "Notes/Checklist", "call the author")
        before = dict(self.find("Articles", B))
        self.delete_page("Articles", A)
        self.run_it(force=True)
        r = self.find("Articles", B)
        self.assertEqual((r["PIC"], r["Notes/Checklist"]), ("Sara", "call the author"))
        self.assertEqual(dict(self.find("Articles", B)), before)

    def test_a_page_row_the_owner_puts_back_is_a_row_again(self):
        self.delete_page("Articles", A)
        self.run_it(force=True)
        self.add_row("Articles", **{content.URL: A, content.KEY: "garden shoes", content.STATUS: "Published"})
        self.run_it(force=True)
        self.assertNotIn(content.url_key(A), self.state_keys(content_run.DROPPED_PAGE))
        self.assertEqual(len([u for u in self.urls("Articles") if u == A]), 1)

    def test_a_keyword_row_deleted_is_not_proposed_again_and_neither_are_its_secondary_keywords(self):
        self.edit_topic("best bread recipes", "Secondary Keywords", "easy bread at home")
        self.run_it(force=True)                                  # the row and its secondary keywords are remembered
        self.delete_row("Articles", lambda r, h: r[0] == "best bread recipes")
        self.run_it(force=True)
        self.assertIsNone(self.topic("Articles", "best bread recipes"))
        self.assertEqual(self.state_keys(content_run.DROPPED_IDEA), {"best bread recipes", "easy bread at home"})

    def test_a_keyword_that_a_row_marked_removed_has_is_never_a_new_idea(self):
        self.edit_topic("best bread recipes", content.STATUS, "Removed")
        t = self.raw("_state")
        t.data = [r for r in t.data if not (r and r[0] in ("gap", "dismissed", "idea", content_run.ROW_SEEN))]   # no memory of it
        self.run_it(force=True)
        rows = [r for r in self.table("Articles")[1] if r.get(content.KEY) == "best bread recipes"]
        self.assertEqual(len(rows), 1)                           # the removed row only: no second one

    def test_a_keyword_in_the_secondary_cell_of_any_row_is_never_a_new_idea(self):
        self.delete_row("Articles", lambda r, h: r[0] == "best bread recipes")
        self.edit("Articles", A, "Secondary Keywords", "best bread recipes")
        t = self.raw("_state")
        t.data = [r for r in t.data if not (r and r[0] in ("gap", "dismissed", "idea", content_run.ROW_SEEN))]
        self.run_it(force=True)
        self.assertIsNone(self.topic("Articles", "best bread recipes"))

    def test_a_tab_that_could_not_be_read_is_not_a_deletion(self):
        t = self.raw("Articles")
        saved = [list(r) for r in t.data]
        t.data = [t.data[0]]                                    # only the header: nothing to judge
        self.run_it(force=True)
        self.assertEqual(self.state_keys(content_run.DROPPED_PAGE), set())
        self.assertEqual(self.state_keys(content_run.DROPPED_IDEA), set())
        self.assertTrue(saved)

    def test_a_row_the_tool_marked_removed_is_still_a_row_not_a_deletion(self):
        self.edit("Articles", B, content.STATUS, "Removed")
        self.run_it(force=True)
        self.assertEqual(self.state_keys(content_run.DROPPED_PAGE), set())

    def test_an_idea_the_owner_marks_add_is_added_even_when_a_row_of_it_was_deleted_before(self):
        self.raw("_state").data.append([content_run.DROPPED_IDEA, "shoes one", "Articles", "2026-10-01"])
        self.add_row("Keyword Ideas", Keyword="shoes one", Decision="Add")
        result = self.run_it(force=True)
        self.assertIsNotNone(self.topic("Articles", "shoes one"))
        self.assertEqual(result.ideas_added, 1)
        self.assertNotIn("shoes one", self.state_keys(content_run.DROPPED_IDEA))

    def test_a_deleted_keyword_is_not_written_again_as_an_autocomplete_idea(self):
        self.raw("_state").data.append([content_run.DROPPED_IDEA, "shoes one", "Articles", "2026-10-01"])
        self.add_row("Keyword Seeds", Seed="shoes", Language="en", Country="", Active="Yes")
        self.run_it(force=True, http=lambda url: (200, '["shoes", ["shoes one", "shoes two"]]'))
        ideas = {r.get("Keyword") for r in self.table("Keyword Ideas")[1]}
        self.assertNotIn("shoes one", ideas)
        self.assertIn("shoes two", ideas)


class MarksTests(unittest.TestCase):

    def test_marks_tell_page_rows_from_keyword_rows(self):
        rows = [{content.URL: "https://example.test/a/", content.KEY: "kw"},
                {content.URL: "", content.KEY: "Idea One", "Secondary Keywords": "two, Three"},
                {content.URL: "", content.KEY: ""}]
        got = content_run._marks(rows)
        self.assertEqual(set(got), {"page:" + content.url_key("https://example.test/a/"), "idea:" + content.norm("Idea One")})
        self.assertEqual(got["idea:" + content.norm("Idea One")], "three|two")


if __name__ == "__main__":
    unittest.main()
