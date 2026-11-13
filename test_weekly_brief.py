"""The one weekly email for all sites: the facts (no AI) from the sheets of every site, the single row in the Index sheet for
the owner's plan, when the email goes out, and that the weekly decks are not built any more. Sheets, Drive and email are
fakes; sites, pages and numbers are invented."""

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import ai_notes
import content
import deck_data as dd
import deck_runner
import provision
import reports
import sites
import weekly_brief as wb
from test_history import FakeSheets, patch_time
from test_own_access import FakeDrive
from test_technical import make_sites

MONDAY = date(2026, 11, 16)
TODAY = date(2026, 11, 16)
PERIOD = "2026-W47"
OWNER = "owner@example.test"
DOC = "https://docs.google.com/document/d/DOC1/edit"


class FakeSmtp:
    user = OWNER


def make_run(site, fs, drive, status="pending"):
    return SimpleNamespace(site=site, name=site.name, status=status, sheets=fs, drive=drive)


def canned(*items):
    """A list of sources that give these items (and one that raises), as build_facts takes them."""
    return (("canned", lambda run, today: [i for i in items if i.site == (run.site.deck.site_label or run.site.name)]),)


class FactsText(unittest.TestCase):

    def facts(self, *items, missing=()):
        f = wb.Facts(TODAY, PERIOD, list(items), list(missing))
        return f.text()

    def test_headings_lines_and_links(self):
        text = self.facts(wb.Item("Traffic", "Alpha", "week 46: 120 clicks (+12% against the week before)", "https://r.test/a"),
                          wb.Item("Technical problems", "Beta", "Key pages set to noindex, open since 2026-11-12", "https://t.test/b"))
        self.assertEqual(text, "Traffic\n• Alpha: week 46: 120 clicks (+12% against the week before) https://r.test/a\n\n"
                               "Technical problems\n• Beta: Key pages set to noindex, open since 2026-11-12 https://t.test/b\n")

    def test_an_empty_week_says_so_and_a_source_that_failed_is_named(self):
        self.assertEqual(self.facts(), "Nothing to report this week.\n")
        self.assertEqual(self.facts(missing=["Alpha technical"]), "Nothing to report this week.\n\nCould not be read: Alpha technical\n")

    def test_overdue_items_are_in_their_own_section_with_what_they_are(self):
        text = self.facts(wb.Item("Briefs ready for writers", "Alpha", "a kw", "https://d.test/1", overdue=True),
                          wb.Item("Briefs ready for writers", "Alpha", "another kw", "https://d.test/2"))
        self.assertIn("Briefs ready for writers\n• Alpha: another kw https://d.test/2", text)
        self.assertIn("Overdue\n• Alpha: briefs ready for writers: a kw https://d.test/1", text)
        self.assertEqual(text.count("a kw"), 1)                                   # the overdue one, under Overdue only

    def test_a_long_list_is_cut_to_five_per_site_and_section(self):
        items = [wb.Item("Drafts waiting for review", "Alpha", f"draft {i}", f"https://d.test/{i}") for i in range(8)]
        text = self.facts(*items)
        self.assertEqual(text.count("• Alpha: draft "), 5)
        self.assertIn("• Alpha: and 3 more https://d.test/5", text)

    def test_a_line_is_never_longer_than_the_limit(self):
        line = wb.Item("Traffic", "Alpha", "x" * 400, "https://r.test/a").line
        self.assertEqual(line.rsplit(" ", 1)[1], "https://r.test/a")
        self.assertLessEqual(len(line.rsplit(" ", 1)[0]), wb.LINE_MAX)

    def test_the_week_and_the_subject(self):
        self.assertEqual(wb.period_of(date(2026, 11, 18)), "2026-W47")
        self.assertEqual(wb.monday_of(date(2026, 11, 22)), date(2026, 11, 16))
        self.assertEqual(wb.subject_of(date(2026, 11, 18)), "Your week · 16 Nov 2026")
        self.assertEqual(wb.signed(12.4), "+12%")
        self.assertEqual(wb.signed(-3.6), "-4%")
        self.assertEqual(wb.signed(0.3), "no change")
        self.assertEqual(wb.signed(None), "no change")


class Sources(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.drive = FakeDrive(self.fs)
        self.site = make_sites(("site-a", "Alpha", None))[0]
        self.site.reports_sheet_id = "REP"
        self.run = make_run(self.site, self.fs, self.drive)

    def rows(self, **rows):
        out = []
        for status, todo, kw, attach, briefed in rows["rows"]:
            r = {content.KEY: kw, content.URL: f"https://example.test/{kw}", content.STATUS: status, content.TODO: todo,
                 "Attachments": attach, "Briefed Date": briefed}
            out.append(r)
        return out

    def test_drafts_waiting_and_briefs_ready_with_their_links_and_overdue_dates(self):
        rows = self.rows(rows=[
            ("Drafted", "Review", "fresh draft", f"x {DOC}", "2026-11-10"),
            ("Drafted", "Review", "old draft", "", "2026-10-01"),
            ("Briefed", "Write", "new brief", DOC + "B", "2026-11-12"),
            ("Briefed", "Update", "old brief", DOC + "C", "2026-10-20"),
            ("Published", "Keep", "live page", "", "2026-10-20"),
            ("Drafted", "Keep", "draft not for review", "", "")])
        got = wb.work_items(self.run, TODAY, rows, "DASH")
        by = {i.text: i for i in got}
        self.assertEqual(set(by), {"fresh draft", "old draft", "new brief", "old brief"})
        self.assertEqual((by["fresh draft"].section, by["fresh draft"].link, by["fresh draft"].overdue),
                         ("Drafts waiting for review", DOC, False))
        self.assertEqual((by["old draft"].link, by["old draft"].overdue), (provision.link("DASH"), True))      # no doc: the dashboard
        self.assertEqual((by["new brief"].section, by["new brief"].overdue), ("Briefs ready for writers", False))
        self.assertTrue(by["old brief"].overdue)

    def test_the_open_technical_problems_come_from_the_technical_sheet(self):
        self.fs.add_book("TECH", {"_state": (5, 4)})
        state = [["kind", "key", "value", "updated"],
                 ["open", "key_noindex", json.dumps({"since": "2026-11-12", "title": "Key pages set to noindex"}), "2026-11-12"],
                 ["open", "home", json.dumps({"since": "2026-10-20", "title": "Home page not answering"}), "2026-10-20"],
                 ["robots", "text", "User-agent: *", "2026-11-12"]]
        self.fs.write_cells(self.fs.books["TECH"]["_state"], 1, 1, state)
        with mock.patch.object(provision, "existing_technical_file", return_value="TECH"):
            got = wb.technical_items(self.run, TODAY)
        by = {i.text: i for i in got}
        self.assertEqual(set(by), {"Key pages set to noindex, open since 2026-11-12", "Home page not answering, open since 2026-10-20"})
        self.assertFalse(by["Key pages set to noindex, open since 2026-11-12"].overdue)
        self.assertTrue(by["Home page not answering, open since 2026-10-20"].overdue)
        self.assertEqual(got[0].link, provision.link("TECH"))

    def test_a_site_without_a_technical_sheet_has_no_technical_facts(self):
        with mock.patch.object(provision, "existing_technical_file", return_value=""):
            self.assertEqual(wb.technical_items(self.run, TODAY), [])

    def test_the_traffic_of_the_week_the_worst_drop_and_the_biggest_moves(self):
        row = {"Clicks": 1200, "Impressions": 50000, "Clicks WoW %": 12.4, "Impr WoW %": -3.6}
        rep = dd.Report("weekly", "2026-W46", "2026-W45", "", rows={("2026-W46", "All"): row},
                        alarms=[{"severity": "high", "area": "Traffic", "weekly": True, "metric": "clicks", "segment": "All",
                                 "pct": 31.0, "a": 100, "b": 69, "impact": 31, "message": ""}],
                        movers=[{"Query": "kw up", "Clicks diff": 30}, {"Query": "kw down", "Clicks diff": -22},
                                {"Query": "kw small", "Clicks diff": 2}])
        with mock.patch.object(dd, "load_weekly", return_value=rep):
            got = wb.traffic_items(self.run, TODAY)
        texts = [i.text for i in got]
        self.assertEqual(texts[0], "week 46: 1,200 clicks (+12% against the week before), 50,000 impressions (-4%)")
        self.assertTrue(texts[1].startswith("biggest drop: "))
        self.assertIn("biggest gain: the search \"kw up\" (+30 clicks)", texts)
        self.assertIn("biggest loss: the search \"kw down\" (-22 clicks)", texts)
        self.assertEqual({i.link for i in got}, {provision.link("REP")})

    def test_no_weekly_data_no_traffic_facts(self):
        with mock.patch.object(dd, "load_weekly", return_value=None):
            self.assertEqual(wb.traffic_items(self.run, TODAY), [])

    def monthly(self, sent="", synced=""):
        self.fs.add_book("REP", {reports.MONTHLY: (5, 17), "State": (5, 3)})
        head = reports.MONTHLY_HEADER
        self.fs.write_cells(self.fs.books["REP"][reports.MONTHLY], 1, 1,
                            [head, ["2026-10", "All", 100, 1000, 1, 5, 0] + [""] * 10])
        self.fs.write_cells(self.fs.books["REP"]["State"], 1, 1,
                            [["Key", "Value", "Updated at"], ["monthly_deck_sent", sent, ""], ["monthly_deck_synced", synced, ""]])

    def test_a_monthly_report_that_is_ready_and_not_sent(self):
        self.site.deck.site_label = "Alpha"
        self.monthly(sent="2026-09", synced="2026-10")
        got = wb.report_items(self.run, date(2026, 11, 16))
        self.assertEqual([(i.text, i.overdue) for i in got], [("the report for 2026-10 is ready", True)])     # 16 days after the month
        got = wb.report_items(self.run, date(2026, 11, 3))
        self.assertFalse(got[0].overdue)

    def test_a_monthly_report_already_sent_or_not_synced_yet_is_not_listed(self):
        self.monthly(sent="2026-10", synced="2026-10")
        self.assertEqual(wb.report_items(self.run, TODAY), [])
        self.monthly(sent="2026-09", synced="2026-09")
        self.assertEqual(wb.report_items(self.run, TODAY), [])

    def test_new_diagnosis_cases_only_when_the_tab_exists(self):
        self.fs.add_book("REP", {"Diagnosis": (5, 3)})
        self.assertEqual(wb.case_items(self.run, TODAY), [])
        self.fs.write_cells(self.fs.books["REP"]["Diagnosis"], 1, 1,
                            [["Case", "Status"], ["Traffic fell on the shoes pages", "new"], ["Old case", "done"], ["Open case", ""]])
        got = wb.case_items(self.run, TODAY)
        self.assertEqual([i.text for i in got], ["Traffic fell on the shoes pages", "Open case"])

    def test_a_source_that_fails_is_named_and_the_others_go_on(self):
        def boom(run, today):
            raise RuntimeError("no")
        facts = wb.build_facts([self.run], TODAY, (("one", boom), ("two", lambda run, today: [wb.Item("Traffic", "Alpha", "x")])),
                               log=lambda *a: None)
        self.assertEqual([i.text for i in facts.items], ["x"])
        self.assertEqual(facts.missing, ["Alpha one"])
        self.assertIn("Could not be read: Alpha one", facts.text())


class TheEmail(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.drive = FakeDrive(self.fs)
        self.a, self.b = make_sites(("site-a", "Alpha", None), ("site-b", "Beta", None))
        self.runs = [make_run(self.a, self.fs, self.drive), make_run(self.b, self.fs, self.drive)]
        self.sent, self.logs = [], []
        self.items = [wb.Item("Traffic", "Alpha", "week 46: 120 clicks (+12% against the week before)", "https://r.test/a"),
                      wb.Item("Technical problems", "Beta", "Key pages set to noindex, open since 2026-11-12", "https://t.test/b"),
                      wb.Item("Drafts waiting for review", "Alpha", "best shoes", "https://d.test/1")]

    def run_it(self, gate, smtp=FakeSmtp(), today=TODAY, items=None):
        sources = canned(*(self.items if items is None else items))
        return wb.run_weekly_brief(self.runs, smtp, today, gate, self.logs.append,
                                   send=lambda s, m: self.sent.append(m), sources=sources)

    def index_id(self):
        return self.drive.id_of(provision.INDEX_NAME)

    def notes(self):
        return ai_notes.read_rows(self.fs, self.index_id())

    def make_ready(self, text):
        row = self.notes()[0]
        self.fs.write_cells(self.fs.books[self.index_id()][ai_notes.TAB], row["row"], 5, [[text, "ready"]])

    def test_the_sync_writes_one_row_in_the_index_sheet_and_sends_nothing(self):
        self.assertEqual(self.run_it("", smtp=None), f"row ready for {PERIOD}, nothing sent")
        rows = self.notes()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["Report"], row["Period"], row["Slot"], row["Status"], row["Language"]),
                         ("weekly", PERIOD, "weekly_brief", "waiting", "en"))
        self.assertIn("• Alpha: week 46: 120 clicks", row["Input"])
        self.assertIn("• Beta: Key pages set to noindex, open since 2026-11-12 https://t.test/b", row["Input"])
        self.assertIn("Never mention a tool", row["Input"])
        self.assertEqual(self.sent, [])

    def test_the_row_is_not_made_twice_and_its_facts_are_refreshed_while_it_waits(self):
        self.run_it("")
        self.items.append(wb.Item("Traffic", "Beta", "week 46: 9 clicks", "https://r.test/b"))
        self.run_it("")
        rows = self.notes()
        self.assertEqual(len(rows), 1)
        self.assertIn("week 46: 9 clicks", rows[0]["Input"])

    def test_the_first_check_sends_only_when_the_text_is_ready(self):
        self.run_it("")
        self.assertEqual(self.run_it("first"), "the text is not ready; the second check sends the facts as they are")
        self.assertEqual(self.sent, [])
        self.make_ready("A short opening.\n\nTraffic\n• Alpha: week 46: 120 clicks https://r.test/a\n")
        self.assertEqual(self.run_it("first"), f"sent for {PERIOD} (with the text)")
        self.assertEqual(len(self.sent), 1)
        message = self.sent[0]
        self.assertEqual((message["To"], message["Subject"]), (OWNER, "Your week · 16 Nov 2026"))
        self.assertIn("A short opening.", message.get_content())

    def test_the_second_check_sends_the_facts_as_they_are_when_there_is_no_text(self):
        self.run_it("")
        self.assertEqual(self.run_it("second"), f"sent for {PERIOD} (with the facts as they are)")
        body = self.sent[0].get_content()
        self.assertTrue(body.startswith("Here are the facts of the week."))
        self.assertIn("Traffic\n• Alpha: week 46: 120 clicks (+12% against the week before) https://r.test/a", body)
        self.assertIn("Drafts waiting for review\n• Alpha: best shoes https://d.test/1", body)
        self.assertNotIn("PulseO", body)

    def test_a_week_is_sent_once(self):
        self.run_it("second")
        self.assertEqual(self.run_it("second"), f"already sent for {PERIOD}")
        self.assertEqual(self.run_it("first"), f"already sent for {PERIOD}")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.run_it("second", today=date(2026, 11, 23)), f"sent for 2026-W48 (with the facts as they are)")
        self.assertEqual(len(self.sent), 2)

    def test_a_run_without_mail_sends_nothing_and_does_not_count_as_sent(self):
        self.assertEqual(self.run_it("second", smtp=None), "not sent (no mail in this run)")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.run_it("second"), f"sent for {PERIOD} (with the facts as they are)")

    def test_an_email_that_fails_is_tried_again_at_the_next_check(self):
        def broken(smtp, message):
            raise OSError("down")
        sources = canned(*self.items)
        self.assertEqual(wb.run_weekly_brief(self.runs, FakeSmtp(), TODAY, "second", self.logs.append, send=broken, sources=sources),
                         "email failed")
        self.assertIn("could not be sent", " ".join(self.logs))
        self.assertEqual(self.run_it("second"), f"sent for {PERIOD} (with the facts as they are)")

    def test_a_site_that_failed_in_the_run_is_left_out(self):
        self.runs[1].status = "failed"
        self.run_it("")
        self.assertNotIn("Beta", self.notes()[0]["Input"])

    def test_without_a_site_that_has_its_own_access_there_is_nothing_to_send(self):
        for r in self.runs:
            r.drive = None
        self.assertEqual(self.run_it("second"), "off (no site with its own access)")

    def test_an_example_of_the_email(self):
        """Printed in the test log: what the owner would read if no text were ready by the second check."""
        items = self.items + [
            wb.Item("Traffic", "Alpha", "biggest drop: Clicks fell 31.0% (100 to 69).", "https://r.test/a"),
            wb.Item("Briefs ready for writers", "Beta", "telescope for kids", "https://d.test/2"),
            wb.Item("Briefs ready for writers", "Beta", "binocular guide", "https://d.test/3", overdue=True),
            wb.Item("Monthly client reports ready to send", "Alpha", "the report for 2026-10 is ready", "https://r.test/a")]
        self.run_it("second", items=items)
        print("\n--- EXAMPLE (not sent) ---\nTo: " + self.sent[0]["To"] + "\nSubject: " + self.sent[0]["Subject"] + "\n\n"
              + self.sent[0].get_content() + "--- END ---")
        self.assertEqual(len(self.sent), 1)


class WeeklyDecksOff(unittest.TestCase):

    def test_the_weekly_deck_is_built_unless_the_run_says_off(self):
        cfg = sites.DeckConfig(enabled=True, weekly=True, monthly=True)
        self.assertTrue(cfg.weekly_deck_on({}))
        self.assertFalse(cfg.weekly_deck_on({"WEEKLY_DECKS": "off"}))
        self.assertFalse(cfg.weekly_deck_on({"WEEKLY_DECKS": " OFF "}))
        self.assertFalse(sites.DeckConfig(weekly=False).weekly_deck_on({}))

    def test_the_decks_run_builds_only_the_monthly_deck_when_the_weekly_one_is_off(self):
        kinds = []

        class Stop(Exception):
            pass

        def load_weekly(*a, **k):
            kinds.append("weekly")
            return None

        def load_monthly(*a, **k):
            kinds.append("monthly")
            return None
        fs = FakeSheets()
        fs.add_book("REP")
        site = make_sites(("site-a", "Alpha", None))[0]
        site.deck.enabled = True
        with mock.patch.object(deck_runner.dd, "load_weekly", load_weekly), mock.patch.object(deck_runner.dd, "load_monthly", load_monthly):
            deck_runner.run_decks(fs, "REP", site, FakeSmtp(), {"WEEKLY_DECKS": "off"}, log=lambda *a: None)
            self.assertEqual(kinds, ["monthly"])
            kinds.clear()
            deck_runner.run_decks(fs, "REP", site, FakeSmtp(), {}, log=lambda *a: None)
            self.assertEqual(kinds, ["monthly", "weekly"])


if __name__ == "__main__":
    unittest.main()
