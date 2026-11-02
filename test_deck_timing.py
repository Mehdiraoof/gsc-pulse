"""The deck checks: nothing depends on the clock. A check sends a deck only if this period's sync has finished (the
first check also only when every text is ready); the second check and the Tuesday catch-up send with what is ready;
a deck goes out once. All data invented."""

import io
import os
import types
import unittest
from datetime import date
from unittest import mock

import yaml
from pptx import Presentation

import ai_notes
import deck_data as dd
import deck_runner
import gsc_pulse
import mailer
import our_work
import test_our_work as tow
import work_items as wi
from test_delivery import USER, deck, reset_smtp
from test_reports import RID, ReportsTestCase, site_with

DAY = date(2026, 11, 12)
SUMMARY = "- Clicks fell sharply.\n- Impressions fell too.\nNext steps:\n- Check indexing."
ROOT = os.path.dirname(os.path.abspath(__file__))


class Base(ReportsTestCase):

    def setUp(self):
        super().setUp()
        reset_smtp()
        self.site = site_with(deck=deck(ai_summary=True))
        self.run_reports(self.site)
        self.env = {"SMTP_USER": USER}
        self.smtp = mailer.SmtpSettings("smtp.test", 587, USER, "pw")
        self.sent = []

    # ---- what the sync, the owner's task and the checks do ---------------------------------
    def periods(self):
        return dd.load_monthly(self.sheets, RID, self.site).period, dd.load_weekly(self.sheets, RID, self.site).period

    def sync_writes_the_rows(self):
        monthly, weekly = self.periods()
        ai_notes.prepare(self.sheets, RID, [
            ai_notes.Slot("monthly", monthly, ai_notes.OUR_WORK_INTRO, "x", "en"),
            ai_notes.Slot("weekly", weekly, ai_notes.WEEKLY_SUMMARY, "y", "en")], DAY, lambda s: None)

    def sync_finishes(self):
        self.sync_writes_the_rows()
        deck_runner.mark_synced(self.sheets, RID, self.site)

    def task_writes(self, *slots):
        tab = self.sheets.books[RID][ai_notes.TAB]
        for row in tab.data[1:]:
            if row[ai_notes.COL["Slot"]] in slots:
                row[ai_notes.COL["Text"]] = {ai_notes.WEEKLY_SUMMARY: SUMMARY, ai_notes.OUR_WORK_INTRO: "A short intro."}[row[ai_notes.COL["Slot"]]]
                row[ai_notes.COL["Status"]] = "ready"

    def check(self, gate, **kw):
        self.logs.clear()

        def extras(kind, report):
            texts, waiting = ai_notes.texts_for(self.sheets, RID, kind, report.period)
            return {"texts": texts, "waiting": waiting, "work": [], "shown": []}

        return deck_runner.run_decks(self.sheets, RID, self.site, self.smtp, self.env, log=self.logs.append,
                                     send=lambda s, m: self.sent.append(m), fetch_logo=lambda url: None,
                                     extras=extras, gate=gate, **kw)

    def kinds_sent(self):
        return len(self.sent)

    def log_has(self, text):
        return any(text in line for line in self.logs)

    def decks_text(self):
        return ["\n".join(tow.deck_texts(Presentation(io.BytesIO(list(m.iter_attachments())[0].get_content()))))
                for m in self.sent]


class CheckTests(Base):

    def test_the_first_check_sends_only_a_complete_deck(self):
        self.sync_finishes()
        self.check("first")
        self.assertEqual(self.kinds_sent(), 0)                               # the texts are still waiting
        self.assertTrue(self.log_has("first check: still waiting for our_work_intro"))
        self.assertTrue(self.log_has("first check: still waiting for weekly_summary"))
        self.task_writes(ai_notes.OUR_WORK_INTRO, ai_notes.WEEKLY_SUMMARY)
        result = self.check("first")
        self.assertEqual((result.sent, self.kinds_sent()), (["monthly", "weekly"], 2))
        monthly, weekly = self.decks_text()
        self.assertIn("Clicks fell sharply.", weekly)

    def test_the_sync_finishing_after_the_task_ran_the_deck_waits_for_the_second_check(self):
        self.check("first")                                                  # the task and the first check, before the sync
        self.assertEqual(self.kinds_sent(), 0)
        self.sync_finishes()                                                 # the sync ends; the retry of the task is next
        self.check("first")
        self.assertEqual(self.kinds_sent(), 0)
        self.task_writes(ai_notes.OUR_WORK_INTRO, ai_notes.WEEKLY_SUMMARY)
        self.check("second")
        self.assertEqual(self.kinds_sent(), 2)

    def test_the_sync_finishing_after_the_first_check_the_second_sends_it(self):
        self.check("first")
        self.assertTrue(self.log_has("has not finished"))
        self.assertEqual(self.kinds_sent(), 0)
        self.sync_finishes()
        self.task_writes(ai_notes.OUR_WORK_INTRO, ai_notes.WEEKLY_SUMMARY)
        self.check("second")
        self.assertEqual(self.kinds_sent(), 2)

    def test_the_second_check_builds_without_a_missing_text_and_shows_no_placeholder(self):
        self.sync_finishes()
        self.task_writes(ai_notes.OUR_WORK_INTRO)                            # the weekly summary never came
        result = self.check("second")
        self.assertEqual((result.sent, result.failed), (["monthly", "weekly"], []))
        self.assertTrue(self.log_has("no text yet for weekly_summary"))
        monthly, weekly = self.decks_text()
        for placeholder in ("Summary not generated automatically", "To get a written summary", "waiting", "Mehdi fills"):
            self.assertNotIn(placeholder, monthly + weekly)

    def test_the_sync_not_finished_at_the_second_check_nothing_is_sent_and_the_next_run_after_it_delivers(self):
        self.sync_writes_the_rows()                                          # rows exist but the sync has not ended
        self.check("second")
        self.assertEqual(self.kinds_sent(), 0)
        self.assertTrue(self.log_has("the sync for"))
        self.assertTrue(self.log_has("has not finished"))
        deck_runner.mark_synced(self.sheets, RID, self.site)                 # the sync ends after the second check
        self.check("second")                                                 # the Tuesday catch-up is a second-type check
        self.assertEqual(self.kinds_sent(), 2)

    def test_a_deck_is_sent_once_whichever_check_sends_it(self):
        self.sync_finishes()
        self.task_writes(ai_notes.OUR_WORK_INTRO, ai_notes.WEEKLY_SUMMARY)
        self.check("first")
        self.assertEqual(self.kinds_sent(), 2)
        for gate in ("second", "second", "first"):
            self.check(gate)
            self.assertTrue(self.log_has("already sent for the latest period"))
        self.assertEqual(self.kinds_sent(), 2)

    def test_what_was_shown_with_numbers_is_remembered_only_when_the_real_monthly_deck_goes_out(self):
        self.site.reports_sheet_id = RID
        self.sync_finishes()
        ids = ["updated|2026-10-12|https://example.test/article/updated"]

        def extras(kind, report):
            return {"texts": {}, "waiting": [], "work": [], "shown": ids if kind == "monthly" else []}

        def run(**kw):
            return deck_runner.run_decks(self.sheets, RID, self.site, self.smtp, self.env, log=lambda s: None,
                                         send=lambda s, m: self.sent.append(m), fetch_logo=lambda u: None,
                                         extras=extras, **kw)

        run(test=True, rebuild=True)                                          # a test copy remembers nothing
        self.assertEqual(our_work.shown_ids(self.sheets, self.site), set())
        run(gate="second")
        self.assertEqual(our_work.shown_ids(self.sheets, self.site), set(ids))

    def test_two_runs_that_overlap_send_it_once(self):
        self.sync_finishes()
        self.task_writes(ai_notes.OUR_WORK_INTRO, ai_notes.WEEKLY_SUMMARY)
        real = deck_runner.read_state
        calls = []

        def other_run_got_there_first(sheets, reports_id):
            state = real(sheets, reports_id)
            calls.append(1)
            if len(calls) > 1:                                               # the re-read right before sending
                monthly, weekly = self.periods()
                state[deck_runner.STATE_KEY["monthly"]] = (2, monthly)
                state[deck_runner.STATE_KEY["weekly"]] = (3, weekly)
            return state

        with mock.patch.object(deck_runner, "read_state", other_run_got_there_first):
            result = self.check("second")
        self.assertEqual((self.kinds_sent(), result.sent), (0, []))
        self.assertTrue(self.log_has("was sent by another run"))

    def test_a_run_started_by_hand_and_a_test_copy_are_not_held_by_the_checks(self):
        result = self.check("")                                              # no sync marker, no texts: a manual build sends
        self.assertEqual(self.kinds_sent(), 2)
        self.sent.clear()
        result = self.check("first", test=True, rebuild=True)                # a test copy, a rebuild: the gate does not apply
        self.assertEqual(len(result.sent), 2)

    def test_the_sync_marker_follows_the_period_of_the_latest_reports(self):
        monthly, weekly = self.periods()
        deck_runner.mark_synced(self.sheets, RID, self.site)
        state = deck_runner.read_state(self.sheets, RID)
        self.assertEqual(state[deck_runner.SYNC_KEY["monthly"]][1], monthly)
        self.assertEqual(state[deck_runner.SYNC_KEY["weekly"]][1], weekly)
        before = {k: v for k, v in state.items()}
        deck_runner.mark_synced(self.sheets, RID, self.site)                 # a second call changes nothing
        self.assertEqual(deck_runner.read_state(self.sheets, RID), before)


class SyncRunTests(Base):

    def run_sync_work(self, prepare):
        run = gsc_pulse.SiteRun(self.site)
        run.sheets, run.drive, run.gsc = self.sheets, None, None
        self.site.reports_sheet_id = RID
        with mock.patch.object(our_work, "scan_site"), mock.patch.object(our_work, "gsc_queries", return_value=None), \
                mock.patch.object(our_work, "prepare_notes", prepare), mock.patch.object(gsc_pulse, "site_header"), \
                mock.patch.dict(os.environ, {"AI_HANDOFF": "on"}):
            gsc_pulse.sync_work(run, DAY)

    def test_the_sync_marks_itself_finished_only_after_the_notes_are_written(self):
        order = []
        self.run_sync_work(lambda *a, **k: order.append("notes") or {"added": 0, "refreshed": 0})
        self.assertEqual(order, ["notes"])
        self.assertIn(deck_runner.SYNC_KEY["monthly"], deck_runner.read_state(self.sheets, RID))

    def test_a_sync_that_could_not_write_the_notes_is_not_marked_so_no_deck_goes_out_half_ready(self):
        def broken(*a, **k):
            raise RuntimeError("notes failed")

        with self.assertRaises(RuntimeError):
            self.run_sync_work(broken)
        self.assertNotIn(deck_runner.SYNC_KEY["monthly"], deck_runner.read_state(self.sheets, RID))
        self.check_gate_holds()

    def check_gate_holds(self):
        extras = lambda kind, report: {"texts": {}, "waiting": [], "work": []}      # noqa: E731
        result = deck_runner.run_decks(self.sheets, RID, self.site, self.smtp, self.env, log=lambda s: None,
                                       send=lambda s, m: self.sent.append(m), fetch_logo=lambda u: None,
                                       extras=extras, gate="second")
        self.assertEqual((result.sent, self.sent), ([], []))

    def test_sync_decks_hands_the_round_to_the_deck_runner(self):
        run = gsc_pulse.SiteRun(self.site)
        self.site.reports_sheet_id = RID
        with mock.patch.object(deck_runner, "run_decks", return_value=deck_runner.DeckResult()) as m, \
                mock.patch.object(our_work, "extras_for", return_value=lambda *a: {}), \
                mock.patch.object(gsc_pulse, "site_header"), mock.patch.dict(os.environ, {"AI_HANDOFF": "on"}):
            gsc_pulse.sync_decks(self.sheets, run, self.smtp, build=True, gate="first")
        self.assertEqual(m.call_args.kwargs["gate"], "first")


class WorkflowTests(unittest.TestCase):
    """The schedule is in the workflow file: the sync, two checks and the Tuesday catch-up, none waiting for the clock."""

    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "gsc_pulse.yml"), encoding="utf-8") as f:
            self.data = yaml.safe_load(f)
        self.text = open(os.path.join(ROOT, ".github", "workflows", "gsc_pulse.yml"), encoding="utf-8").read()

    def test_the_four_scheduled_runs_are_off_the_hour(self):
        crons = [c["cron"] for c in self.data[True]["schedule"]]
        self.assertEqual(crons, ["17 3 * * 1", "13 14 * * 1", "13 17 * * 1", "13 14 * * 2"])
        self.assertTrue(all(not c.startswith("0 ") for c in crons))

    def test_the_sync_runs_only_for_its_own_cron_and_the_checks_only_for_the_others(self):
        jobs = self.data["jobs"]
        self.assertIn("github.event.schedule == '17 3 * * 1'", jobs["sync"]["if"])
        self.assertIn("github.event.schedule != '17 3 * * 1'", jobs["build"]["if"])
        self.assertIn("AI_HANDOFF", jobs["build"]["if"])
        self.assertNotIn("needs", jobs["build"])                             # a check does not belong to a sync run

    def test_the_first_check_is_told_apart_and_nothing_waits_four_hours(self):
        env = self.data["jobs"]["build"]["steps"][-1]["env"]
        self.assertIn("'13 14 * * 1' && 'first'", env["DECK_CHECK"])
        self.assertNotIn("240", str(env.get("WAIT_FOR_TEXTS_MINUTES", "")))
        self.assertLessEqual(self.data["jobs"]["build"]["timeout-minutes"], 60)


if __name__ == "__main__":
    unittest.main()
