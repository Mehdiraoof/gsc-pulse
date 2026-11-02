"""Tests for "Our work and its results" and the AI Notes handoff, on an invented site whose numbers can be
worked out by hand (the numbers in the comments are the hand calculation)."""

import io
import unittest
from datetime import date, timedelta
from unittest import mock

from pptx import Presentation

import ai_notes
import client_fixtures as fx
import deck_fixtures as dfx
import client_i18n
import deck_runner
import decks
import re
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


# --------------------------------------------------------------------------- #
# The AI Notes tab
# --------------------------------------------------------------------------- #

class AiNotesTests(unittest.TestCase):

    def setUp(self):
        self.fs = FakeSheets()
        self.fs.add_book("R")

    def slot(self, text="input", period="2026-10"):
        return ai_notes.Slot("monthly", period, ai_notes.OUR_WORK_INTRO, text, "fa")

    def rows(self):
        return ai_notes.read_rows(self.fs, "R")

    def test_a_row_per_slot_waiting_and_a_second_run_adds_nothing(self):
        self.assertEqual(ai_notes.prepare(self.fs, "R", [self.slot()], TODAY, lambda s: None), {"added": 1, "refreshed": 0})
        (row,) = self.rows()
        self.assertEqual((row["Report"], row["Period"], row["Slot"], row["Status"], row["Text"], row["Language"],
                          row["Created"]), ("monthly", "2026-10", "our_work_intro", "waiting", "", "fa", "2026-11-12"))
        self.assertEqual(ai_notes.prepare(self.fs, "R", [self.slot()], TODAY, lambda s: None), {"added": 0, "refreshed": 0})
        self.assertEqual(len(self.rows()), 1)

    def test_the_input_of_a_waiting_row_is_refreshed_but_a_ready_row_and_the_text_are_left_alone(self):
        ai_notes.prepare(self.fs, "R", [self.slot("old")], TODAY, lambda s: None)
        self.assertEqual(ai_notes.prepare(self.fs, "R", [self.slot("new")], TODAY, lambda s: None)["refreshed"], 1)
        self.assertEqual(self.rows()[0]["Input"], "new")
        tab = self.fs.books["R"][ai_notes.TAB]
        tab.data[1][ai_notes.COL["Text"]] = "Written by the task."
        tab.data[1][ai_notes.COL["Status"]] = "ready"
        before = [list(r) for r in tab.data]
        self.assertEqual(ai_notes.prepare(self.fs, "R", [self.slot("newer")], TODAY, lambda s: None)["refreshed"], 0)
        self.assertEqual(tab.data, before)                                           # nothing changed, text included

    def test_the_text_and_status_columns_are_never_patched_by_the_tool(self):
        writes = []
        real = ai_notes.tabs.patch_cells

        def spy(sheets, book, updates):
            writes.extend(updates)
            return real(sheets, book, updates)

        with mock.patch.object(ai_notes.tabs, "patch_cells", spy):
            ai_notes.prepare(self.fs, "R", [self.slot("a")], TODAY, lambda s: None)
            ai_notes.prepare(self.fs, "R", [self.slot("b")], TODAY, lambda s: None)
        self.assertTrue(writes)
        for cell, _ in writes:
            self.assertEqual(cell.split("!")[1][0], "D", cell)                     # only the Input column

    def test_old_rows_stay_and_only_ready_rows_with_a_text_are_used(self):
        ai_notes.prepare(self.fs, "R", [self.slot(period="2026-09"), self.slot(period="2026-10"),
                                        ai_notes.Slot("weekly", "2026-W43", ai_notes.WEEKLY_SUMMARY, "x", "en")],
                         TODAY, lambda s: None)
        tab = self.fs.books["R"][ai_notes.TAB]
        tab.data[1][ai_notes.COL["Text"]], tab.data[1][ai_notes.COL["Status"]] = "September text", "ready"
        tab.data[2][ai_notes.COL["Status"]] = "ready"                                   # ready but no text yet
        ai_notes.prepare(self.fs, "R", [self.slot(period="2026-10")], TODAY, lambda s: None)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(ai_notes.texts_for(self.fs, "R", "monthly", "2026-09"), ({"our_work_intro": "September text"}, []))
        self.assertEqual(ai_notes.texts_for(self.fs, "R", "monthly", "2026-10"), ({}, ["our_work_intro"]))
        self.assertEqual(ai_notes.texts_for(self.fs, "R", "weekly", "2026-W99"), ({}, []))

    def test_what_the_task_is_told_holds_the_numbers_and_forbids_names(self):
        text = ai_notes.our_work_intro_input("Example", "October 2026", "fa", "- New page: x (5 Oct): clicks new -> 168")
        self.assertIn("Persian", text)
        self.assertIn("clicks new -> 168", text)
        self.assertIn("never mention a tool", text.lower().replace("\n", " "))
        weekly = ai_notes.weekly_summary_input("Clicks: 325", "Example", "Week 41", "en")
        self.assertIn("Clicks: 325", weekly)
        self.assertIn("Next steps:", weekly)

    def test_the_weekly_summary_is_read_from_lines_or_from_json(self):
        lines = "Summary\n- Clicks fell 10%.\n- Impressions fell too.\n\nNext steps:\n- Check indexing.\n- Update the page."
        self.assertEqual(ai_notes.parse_weekly_summary(lines),
                         (["Clicks fell 10%.", "Impressions fell too."], ["Check indexing.", "Update the page."]))
        self.assertEqual(ai_notes.parse_weekly_summary('{"bullets": ["a"], "next_steps": ["b"]}'), (["a"], ["b"]))
        persian = "- کلیک کم شد.\nگام‌های بعدی:\n- بررسی"
        self.assertEqual(ai_notes.parse_weekly_summary(persian), (["کلیک کم شد."], ["بررسی"]))
        self.assertIsNone(ai_notes.parse_weekly_summary("no bullets here"))
        self.assertIsNone(ai_notes.parse_weekly_summary(""))


# --------------------------------------------------------------------------- #
# What is shown to whom (checklist 8)
# --------------------------------------------------------------------------- #

MOVED = {content.url_key(u("/article/moved")): {content.url_key(u("/article/old-url"))}}


def measured(kind, day, scope, what="", title="", show=True):
    it = wi.WorkItem(kind, day, scope=scope, what=what, title=title, show=show)
    wi.measure(it, table(), FINAL, MOVED)
    return it


def all_items():
    return [measured(wi.NEW, D(10, 5), u("/article/new"), title="Blue shoes guide"),
            measured(wi.UPDATED, D(10, 12), u("/article/updated"), title="Garden tools"),
            measured(wi.MANUAL, D(10, 10), u("/article/moved"), what="Old address redirected"),       # no gain
            measured(wi.MANUAL, D(10, 25), u("/article/updated"), what="Schema added"),               # fresh, positive
            measured(wi.MANUAL, D(10, 12), "/article/*", what="Internal links", show=False)]          # not for the client


def T_of(lang):
    return {**i18n.table(lang), **client_i18n.table(lang)}


class WhoSeesWhatTests(unittest.TestCase):

    def test_the_client_gets_the_good_and_the_fresh_positive_best_first_and_never_a_hidden_or_failed_item(self):
        shown = wi.for_client(all_items(), TODAY)
        # the updated page gained 140 clicks, the new page 168, the fresh schema work 20
        self.assertEqual([(i.kind, i.day) for i in shown], [(wi.NEW, D(10, 5)), (wi.UPDATED, D(10, 12)), (wi.MANUAL, D(10, 25))])
        self.assertNotIn("Old address redirected", [i.what for i in shown])                        # it did not help
        self.assertNotIn("Internal links", [i.what for i in shown])                                # "Show to client" = No

    def test_the_owner_sees_everything_of_the_last_90_days_including_what_did_not_help(self):
        shown = wi.for_owner(all_items(), TODAY)
        self.assertEqual({i.what or i.title for i in shown},
                         {"Blue shoes guide", "Garden tools", "Old address redirected", "Schema added", "Internal links"})
        old = measured(wi.MANUAL, D(7, 1), u("/article/updated"), what="Too old")
        self.assertNotIn("Too old", [i.what for i in wi.for_owner([old], TODAY)])
        self.assertEqual(wi.for_client([old], TODAY), [])

    def test_at_most_eight_and_two_slides_from_six(self):
        many = [measured(wi.UPDATED, D(10, 12), u("/article/updated"), title=f"p{i}") for i in range(11)]
        self.assertEqual(len(wi.for_client(many, TODAY)), wi.MAX_CLIENT_ITEMS)
        T = T_of("en")
        self.assertEqual(len(work_view.client_pages(many[:5], T, "en", "Intro")), 1)
        pages = work_view.client_pages(many[:8], T, "en", "Intro")
        self.assertEqual([len(p["rows"]) for p in pages], [4, 4])
        self.assertEqual([p["intro"] for p in pages], ["Intro", ""])
        self.assertTrue(pages[1]["title"].endswith("(2/2)"))
        self.assertEqual(work_view.client_pages([], T, "en", "Intro"), [])


# --------------------------------------------------------------------------- #
# The slide in the decks (checklist 9, 10)
# --------------------------------------------------------------------------- #

def deck_texts(prs):
    out = []
    for slide in prs.slides:
        for shp in slide.shapes:
            if shp.has_text_frame:
                out.append(shp.text_frame.text)
            if getattr(shp, "has_table", False) and shp.has_table:
                for row in shp.table.rows:
                    out += [c.text_frame.text for c in row.cells]
    return out


class SlideTests(unittest.TestCase):

    def build(self, lang, items, intro=""):
        data = client_deck.build_monthly(fx.monthly_report(), fx.Cfg(lang), deck_runner.TEMPLATE_DIR, None, None,
                                         work=items, work_intro=intro)
        return Presentation(io.BytesIO(data))

    def work_slides(self, prs, title):
        return [s for s in prs.slides if any(sh.has_text_frame and sh.text_frame.text.startswith(title) for sh in s.shapes)]

    def test_the_english_slide(self):
        items = wi.for_client(all_items(), TODAY)
        prs = self.build("en", items, intro="This month we refreshed three pages.")
        (slide,) = self.work_slides(prs, "Our work and its results")
        mine = "\n".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame) + "\n" + "\n".join(
            c.text_frame.text for sh in slide.shapes if getattr(sh, "has_table", False) and sh.has_table
            for row in sh.table.rows for c in row.cells)
        text = "\n".join(deck_texts(prs))
        self.assertIsNone(re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", mine))        # no "AI" on the slide itself
        self.assertIn("This month we refreshed three pages.", text)
        self.assertIn("Updated page: Garden tools", text)
        self.assertIn("New page: Blue shoes guide", text)
        self.assertIn("Schema added (early results)", text)
        self.assertIn("280 → 420", text)                       # clicks before to after (the updated page, by hand)
        self.assertIn("8.0 → 6.0", text)                       # average position
        self.assertIn("+50% · site +6%", text)                 # the page against the rest of the site
        self.assertIn("new → 168", text)
        for hidden in ("Internal links", "Old address redirected", "Mehdi fills", "PulseO", "Claude"):
            self.assertNotIn(hidden, text, hidden)

    def test_a_fixed_intro_when_nothing_was_written_and_no_slide_without_items(self):
        items = wi.for_client(all_items(), TODAY)[:1]
        text = "\n".join(deck_texts(self.build("en", items)))
        self.assertIn("Here is what we did this period and what it achieved.", text)
        nothing = "\n".join(deck_texts(self.build("en", [])))
        self.assertNotIn("Our work and its results", nothing)
        self.assertNotIn("Mehdi fills this slide", nothing)

    def test_the_persian_slide_is_persian_and_right_to_left(self):
        items = wi.for_client(all_items(), TODAY)
        title = "کارهای ما و نتیجه آن‌ها"
        prs = self.build("fa", items, intro="این ماه سه صفحه را به‌روز کردیم.")
        (slide,) = self.work_slides(prs, title)
        text = "\n".join(deck_texts(prs))
        self.assertIn("صفحه جدید:", text)                                  # New page:
        self.assertIn("به‌روزرسانی صفحه:", text)        # Updated page:
        self.assertIn("نتیجه اولیه", text)                         # early results
        self.assertIn("280 ← 420", text.replace("‎", ""))                        # the arrow points from before to after, right to left
        table_xml = [sh._element.xml for sh in slide.shapes if getattr(sh, "has_table", False) and sh.has_table][0]
        self.assertIn('rtl="1"', table_xml)
        self.assertNotIn("Our work and its results", text)

    def test_persian_digits_when_the_switch_is_on(self):
        T = T_of("fa")
        item = wi.for_client(all_items(), TODAY)[1]                    # the updated page: 280 to 420, on 12 Oct
        with mock.patch.object(i18n, "PERSIAN_DIGITS", True):
            row = work_view.client_row(item, T, "fa")
        self.assertEqual(row["clicks"], "۲۸۰ ← ۴۲۰")
        self.assertEqual(row["date"], "۱۲ " + T["months_short"][9])
        self.assertEqual(work_view.client_row(item, T, "fa")["clicks"], "280 ← 420")

    def test_the_owner_weekly_section(self):
        cfg = fx.Cfg("en")
        data = decks.build_deck(dfx.weekly_report(), cfg, deck_runner.TEMPLATE_DIR, None, None,
                                work=wi.for_owner(all_items(), TODAY), handoff=True)
        text = "\n".join(deck_texts(Presentation(io.BytesIO(data))))
        self.assertIn("Changes in progress", text)
        self.assertIn("Old address redirected", text)                # what did not help is here
        self.assertIn("no clear gain", text)
        self.assertIn("not shown to the client", text)               # "Show to client" = No
        self.assertNotIn("Summary not generated automatically", text)  # handoff, no text: no summary slide at all
        without = decks.build_deck(dfx.weekly_report(), cfg, deck_runner.TEMPLATE_DIR, None, None, work=[], handoff=True)
        self.assertNotIn("Changes in progress", "\n".join(deck_texts(Presentation(io.BytesIO(without)))))




# --------------------------------------------------------------------------- #
# The whole way through the sheets: sitemap, content dashboard, stored history, the manual tab
# --------------------------------------------------------------------------- #

import our_work
import site_check
import test_content_run as tcr

SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>{r}/article/new</loc><lastmod>2026-10-05</lastmod></url>
<url><loc>{r}/article/updated</loc><lastmod>2026-10-12</lastmod></url>
<url><loc>{r}/article/steady</loc><lastmod>2026-03-01</lastmod></url>
</urlset>""".format(r=ROOT).encode()


def sitemap_get(url):
    if url.endswith("/sitemap.xml"):
        return 200, SITEMAP
    return 404, b""


class FlowTests(tcr.Base):

    def setUp(self):
        super().setUp()
        self.run_it()                                                    # the content dashboard exists
        self.fs.add_book("HIST", {"H Pages": (3000, 6)})
        tab = self.fs.books["HIST"]["H Pages"]
        self.fs.write_cells(tab, 1, 1, [["Date", "Page", "Clicks", "Impressions", "CTR (%)", "Avg. Position"]]
                            + [[d.isoformat(), p, c, n, 1.0, pos] for d, p, c, n, pos in history_rows()])
        self.site.history_sheet_id = "HIST"
        self.fs.add_book("REP2")
        self.site.reports_sheet_id = "REP"
        wi.ensure_our_work_tab(self.fs, "REP")
        self.fs.write_cells(self.fs.books["REP"][wi.OUR_WORK_TAB], 2, 1, [
            ["2026-10-10", u("/article/moved"), "Old address redirected to the new one", "Yes"]])

    def test_the_first_scan_uses_the_history_and_the_items_are_measured(self):
        found = our_work.scan_site(self.drive, self.fs, self.site, TODAY, sitemap_get, lambda s: None)
        self.assertEqual(found, 2)                                       # the new page and the updated page
        state, _, _ = our_work.load_content(self.fs, self.file_id, self.site)
        key_old, key_new = content.url_key(u("/article/old-url")), content.url_key(u("/article/moved"))
        state.set("gone", key_old, "redirect|" + key_new, TODAY)          # the redirect map the content refresh keeps
        state.save(self.fs, self.file_id)
        items = our_work.items_for(self.drive, self.fs, self.site, TODAY, None, lambda s: None)
        got = {(i.kind, i.day, content.path_of(i.scope)): (i.impact.status, i.impact.before.clicks, i.impact.after.clicks)
               for i in items}
        self.assertEqual(got, {
            (wi.NEW, D(10, 5), "/article/new"): (wi.GOOD, 0, 168),
            (wi.UPDATED, D(10, 12), "/article/updated"): (wi.GOOD, 280, 420),
            (wi.MANUAL, D(10, 10), "/article/moved"): (wi.FLAT, 700, 700),         # the old address was joined through the map
        })
        manual = [i for i in items if i.kind == wi.MANUAL][0]
        self.assertEqual(manual.what, "Old address redirected to the new one")
        # a second scan changes nothing, and the sync run's rows for the writer can be made from these items
        self.assertEqual(our_work.scan_site(self.drive, self.fs, self.site, TODAY, sitemap_get, lambda s: None), 0)

    def test_an_update_found_by_a_later_scan_is_dated_by_its_lastmod(self):
        our_work.scan_site(self.drive, self.fs, self.site, D(10, 4), lambda url: (200, SITEMAP.replace(b"2026-10-12", b"2026-05-01")
                                                                                     .replace(b"2026-10-05", b"2026-05-02")) if url.endswith("/sitemap.xml") else (404, b""),
                           lambda s: None)
        found = our_work.scan_site(self.drive, self.fs, self.site, D(10, 13), sitemap_get, lambda s: None)
        items = our_work.wi.auto_items(our_work.load_content(self.fs, self.file_id, self.site)[0], D(10, 13), 120)
        self.assertEqual(found, 1)                                      # the history already showed the new page at the first scan
        self.assertEqual(sorted((i.kind, i.day) for i in items), [(wi.NEW, D(10, 5)), (wi.UPDATED, D(10, 12))])

    def test_a_site_without_a_content_dashboard_or_history_still_gets_its_manual_items(self):
        self.site.history_sheet_id = ""
        items = our_work.items_for(self.drive, self.fs, self.site, TODAY, None, lambda s: None)
        self.assertEqual([i.kind for i in items], [wi.MANUAL])
        self.assertIsNone(items[0].impact)                               # no history: nothing to measure, nothing is shown
        self.assertEqual(wi.for_client(items, TODAY), [])
        self.assertEqual(our_work.items_for(None, self.fs, self.site, TODAY, None, lambda s: None)[0].kind, wi.MANUAL)


if __name__ == "__main__":
    unittest.main()
