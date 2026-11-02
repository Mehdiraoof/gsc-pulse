"""Tests for "Our work and its results" and the AI Notes handoff, on an invented site whose numbers can be
worked out by hand (the numbers in the comments are the hand calculation)."""

import io
import unittest
from datetime import date, timedelta
from unittest import mock

from pptx import Presentation

import ai_notes
import client_data as cd
import client_deck
import content
import content_run
import i18n
import work_items as wi
import work_view
from test_history import FakeSheets

TODAY, FINAL = date(2026, 11, 12), date(2026, 11, 10)
ROOT = "https://example.test"
START = date(2026, 8, 1)


def u(path):
    return f"{ROOT}{path}"


def days(page, first, last, clicks, impr, pos):
    """One row a day for page from first to last (both included)."""
    out, d = [], first
    while d <= last:
        out.append((d, page, clicks, impr, pos))
        d += timedelta(days=1)
    return out


def D(m, d):
    return date(2026, m, d)


def history_rows():
    rows = []
    # an updated page: 10 clicks a day, 15 from the day after its update on 12 Oct; position 8.0 then 6.0
    rows += days(u("/article/updated"), START, D(10, 11), 10, 100, 8.0)
    rows += days(u("/article/updated"), D(10, 12), FINAL, 15, 130, 6.0)
    # a new page, first seen on 5 Oct: 6 clicks a day
    rows += days(u("/article/new"), D(10, 5), FINAL, 6, 60, 9.0)
    # a page that moved on 10 Oct: the old address had 20 clicks a day and has none after, the new one 5, then 25
    rows += days(u("/article/old-url"), START, D(10, 9), 20, 200, 5.0)
    rows += days(u("/article/moved"), START, D(10, 9), 5, 50, 5.0)
    rows += days(u("/article/moved"), D(10, 10), FINAL, 25, 250, 5.0)
    # the rest of the site: one steady page, one that grows from 1 Nov
    rows += days(u("/article/steady"), START, FINAL, 50, 500, 4.0)
    rows += days(u("/article/other"), START, D(10, 31), 30, 300, 6.0)
    rows += days(u("/article/other"), D(11, 1), FINAL, 36, 360, 6.0)
    return rows


class Fake:
    """A stand-in for the State of the content dashboard (kind, key, value, date rows)."""

    def state(self, rows=None):
        s = content_run.State([["kind", "key", "value", "updated"]] + (rows or []))
        return s


def table():
    return wi.PageDays(history_rows())


# --------------------------------------------------------------------------- #
# The numbers (checklist 6 and 7)
# --------------------------------------------------------------------------- #

class MeasureTests(unittest.TestCase):

    def item(self, kind, day, scope, **kw):
        return wi.WorkItem(kind, day, scope=scope, **kw)

    def test_a_new_page_by_hand(self):
        it = self.item(wi.NEW, D(10, 5), u("/article/new"))
        imp = wi.measure(it, table(), FINAL, {})
        self.assertEqual(imp.days, 28)
        # before 7 Sep to 4 Oct: nothing. After 5 Oct to 1 Nov: 28 days of 6 clicks, 60 impressions, position 9.0
        self.assertEqual((imp.before.clicks, imp.after.clicks, imp.after.impressions), (0, 168, 1680))
        self.assertEqual(imp.after.position, 9.0)
        self.assertEqual(imp.status, wi.GOOD)
        # the other pages: before 280 + 700 + 1400 + 840 = 3220, after 385 + 700 + 1400 + 846 = 3331
        self.assertEqual((imp.site_before, imp.site_after), (3220, 3331))
        self.assertAlmostEqual(imp.site_change, 111 / 3220, places=4)
        self.assertIsNone(imp.change)                                  # nothing before: no percentage
        self.assertTrue(imp.beat_site)

    def test_an_updated_page_by_hand(self):
        it = self.item(wi.UPDATED, D(10, 12), u("/article/updated"))
        imp = wi.measure(it, table(), FINAL, {})
        # before 14 Sep to 11 Oct: 28 days of 10 = 280, after 12 Oct to 8 Nov: 28 days of 15 = 420
        self.assertEqual((imp.before.clicks, imp.after.clicks, imp.gain), (280, 420, 140))
        self.assertEqual((imp.before.position, imp.after.position), (8.0, 6.0))
        self.assertAlmostEqual(imp.change, 0.5, places=4)
        # the other pages: before 42 + 700 + 1400 + 840 = 2982, after 168 + 700 + 1400 + 888 = 3156
        self.assertEqual((imp.site_before, imp.site_after), (2982, 3156))
        self.assertAlmostEqual(imp.site_change, 174 / 2982, places=4)
        self.assertEqual(imp.status, wi.GOOD)
        self.assertTrue(imp.beat_site)                                 # +50% against +5.8%

    def test_a_redirected_page_keeps_its_before_data(self):
        redirects = {content.url_key(u("/article/moved")): {content.url_key(u("/article/old-url"))}}
        it = self.item(wi.MANUAL, D(10, 10), u("/article/moved"), what="Redirect set up")
        imp = wi.measure(it, table(), FINAL, redirects)
        # the old and the new address together: 25 clicks a day before and after, so nothing changed
        self.assertEqual((imp.before.clicks, imp.after.clicks), (700, 700))
        self.assertEqual(imp.status, wi.FLAT)
        self.assertEqual(sorted(it.pages), sorted([content.url_key(u("/article/moved")), content.url_key(u("/article/old-url"))]))
        # the other pages: before 280 + 30 + 1400 + 840 = 2550 (B started on 5 Oct), after 410 + 168 + 1400 + 876 = 2854
        self.assertEqual((imp.site_before, imp.site_after), (2550, 2854))
        # without the map the move looks like a win (140 to 700): exactly the mistake the map prevents
        wrong = self.item(wi.MANUAL, D(10, 10), u("/article/moved"))
        w = wi.measure(wrong, table(), FINAL, {})
        self.assertEqual((w.before.clicks, w.after.clicks, w.status), (140, 700, wi.GOOD))

    def test_a_fresh_change_is_early_and_a_too_fresh_one_has_no_result(self):
        fresh = wi.measure(self.item(wi.MANUAL, D(10, 25), u("/article/updated")), table(), FINAL, {})
        # 25 Oct to 10 Nov is 17 days: early results. Before 8 to 24 Oct: 4 days of 10 and 13 of 15 = 235, after 17 of 15 = 255
        self.assertEqual((fresh.days, fresh.status), (17, wi.EARLY))
        self.assertEqual((fresh.before.clicks, fresh.after.clicks), (235, 255))
        self.assertTrue(fresh.positive)
        none = wi.measure(self.item(wi.MANUAL, D(11, 6), u("/article/updated")), table(), FINAL, {})
        self.assertEqual(none.status, wi.NODATA)                        # 5 days: too early to say anything

    def test_a_pattern_and_the_whole_site(self):
        pattern = self.item(wi.MANUAL, D(10, 12), "/article/*", what="Internal links")
        imp = wi.measure(pattern, table(), FINAL, {})
        self.assertEqual(len(pattern.pages), 6)
        whole = self.item(wi.MANUAL, D(10, 12), "Whole site")
        w = wi.measure(whole, table(), FINAL, {})
        self.assertEqual(w.before.clicks, imp.before.clicks)
        self.assertEqual((w.site_before, w.site_after), (0.0, 0.0))     # nothing to compare a whole-site change with
        self.assertIsNone(w.beat_site)
        unknown = self.item(wi.MANUAL, D(10, 12), "the checkout")
        self.assertEqual(wi.measure(unknown, table(), FINAL, {}).status, wi.NODATA)

    def test_ranking_searches_are_counted_when_asked(self):
        it = self.item(wi.UPDATED, D(10, 12), u("/article/updated"))
        calls = []

        def queries(item, first, last):
            calls.append((first, last))
            return 4 if first < D(10, 12) else 9

        imp = wi.measure(it, table(), FINAL, {}, queries)
        self.assertEqual((imp.before.queries, imp.after.queries), (4, 9))
        self.assertEqual(calls, [(D(9, 14), D(10, 11)), (D(10, 12), D(11, 8))])


# --------------------------------------------------------------------------- #
# Finding the items (checklist 5)
# --------------------------------------------------------------------------- #

def matches(url):
    return "/article/" in url


class ScanTests(unittest.TestCase):

    def run_scan(self, state, today, entries, rows=(), first_seen=None, ok=True):
        return wi.scan(state, today, entries, ok, list(rows), matches, first_seen)

    def test_a_new_page_an_update_a_dashboard_date_and_a_status_change(self):
        state = Fake().state()
        entries = {u("/article/updated"): "2026-05-01", u("/article/steady"): "2026-03-01"}
        # the first run only records what is there
        self.assertEqual(self.run_scan(state, D(10, 4), entries), 0)
        self.assertEqual(len(state.keys({"seen"})), 2)
        # a new page appears in the sitemap with a lastmod of 5 Oct
        entries[u("/article/new")] = "2026-10-05"
        self.assertEqual(self.run_scan(state, D(10, 6), entries), 1)
        # the sitemap says the updated page changed on 12 Oct
        entries[u("/article/updated")] = "2026-10-12"
        rows = [{content.URL: u("/article/steady"), content.STATUS: "Published", "Published/Updated Date": "2026-10-20"},
                {content.URL: u("/article/live"), content.STATUS: "Briefed"}]
        entries[u("/article/live")] = ""
        self.run_scan(state, D(10, 13), entries, rows)
        # a page whose row turns to Published
        rows[1] = {content.URL: u("/article/live"), content.STATUS: "Published"}
        self.run_scan(state, D(10, 28), entries, rows)
        got = sorted((i.kind, i.day.isoformat(), content.path_of(i.scope)) for i in wi.auto_items(state, D(11, 12), 120))
        self.assertEqual(got, [
            (wi.NEW, "2026-10-05", "/article/new"),
            (wi.NEW, "2026-10-13", "/article/live"),        # first seen on a day the sitemap gave no date for
            (wi.NEW, "2026-10-28", "/article/live"),        # ... and its status turned to Published
            (wi.UPDATED, "2026-10-12", "/article/updated"),
            (wi.UPDATED, "2026-10-20", "/article/steady"),
        ])

    def test_the_first_run_uses_the_history_for_new_pages_and_the_sitemap_for_recent_updates(self):
        state = Fake().state()
        entries = {u("/article/new"): "2026-10-05", u("/article/updated"): "2026-10-12",
                   u("/article/steady"): "2026-03-01"}
        first_seen = {u("/article/steady"): "2026-08-01", u("/article/updated"): "2026-08-01",
                      u("/article/new"): "2026-10-05"}
        self.run_scan(state, D(11, 12), entries, first_seen=first_seen)
        got = sorted((i.kind, i.day.isoformat(), content.path_of(i.scope)) for i in wi.auto_items(state, D(11, 12), 120))
        self.assertEqual(got, [(wi.NEW, "2026-10-05", "/article/new"), (wi.UPDATED, "2026-10-12", "/article/updated")])

    def test_a_run_twice_finds_nothing_new_and_a_sitemap_that_failed_adds_nothing(self):
        state = Fake().state()
        entries = {u("/article/a"): "2026-05-01"}
        self.run_scan(state, D(10, 1), entries)
        entries[u("/article/b")] = "2026-10-02"
        self.assertEqual(self.run_scan(state, D(10, 3), entries), 1)
        self.assertEqual(self.run_scan(state, D(10, 3), entries), 0)
        self.assertEqual(self.run_scan(state, D(10, 4), {}, ok=False), 0)

    def test_events_of_one_page_close_together_are_one_piece_of_work(self):
        state = Fake().state()
        self.run_scan(state, D(10, 1), {u("/article/a"): "2026-05-01"})
        self.run_scan(state, D(10, 6), {u("/article/a"): "2026-05-01", u("/article/b"): "2026-10-05"},
                      [{content.URL: u("/article/b"), content.STATUS: "Published", "Published/Updated Date": "2026-10-06"}])
        items = wi.auto_items(state, D(10, 7), 120)
        self.assertEqual([(i.kind, i.day) for i in items], [(wi.NEW, D(10, 5))])

    def test_the_manual_tab(self):
        fs = FakeSheets()
        fs.add_book("R", {wi.OUR_WORK_TAB: (50, 4)})
        fs.write_cells(fs.books["R"][wi.OUR_WORK_TAB], 1, 1, [wi.OUR_WORK_HEADER] + [
            ["2026-10-12", "/article/*", "Internal links added", "Yes"],
            ["2026-10-12", "Whole site", "Schema added", "No"],
            ["not a date", "x", "y", ""], ["2026-10-13", "", "", ""],
            ["2026-10-14", "/x", "Done", ""]])
        items = wi.read_manual(fs, "R")
        self.assertEqual([(i.day, i.scope, i.what, i.show) for i in items],
                         [(D(10, 12), "/article/*", "Internal links added", True),
                          (D(10, 12), "Whole site", "Schema added", False),
                          (D(10, 14), "/x", "Done", True)])           # an empty "Show to client" means yes
        self.assertEqual(wi.read_manual(fs, "NOBOOK"), [])


if __name__ == "__main__":
    unittest.main()
