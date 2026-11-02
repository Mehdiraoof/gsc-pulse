"""Only the scheduled Monday run sends email by itself; a run started by hand sends nothing unless asked."""

import types
import unittest
from unittest import mock

import deck_runner
import gsc_pulse
import mail_policy
import test_history as th

SCHEDULED = {"GITHUB_EVENT_NAME": "schedule"}
MANUAL = {"GITHUB_EVENT_NAME": "workflow_dispatch"}


def policy(**env):
    return mail_policy.Policy(env)


class PolicyTests(unittest.TestCase):

    def test_a_scheduled_run_sends_everything_as_before(self):
        p = policy(**SCHEDULED)
        self.assertTrue(p.owner_mail())
        self.assertTrue(p.decks("site-1") and p.decks("site-2"))

    def test_a_run_that_does_not_know_how_it_started_behaves_as_scheduled(self):
        p = policy()
        self.assertTrue(p.owner_mail() and p.decks("site-1"))

    def test_a_manual_run_sends_nothing_by_default(self):
        p = policy(**MANUAL)
        self.assertFalse(p.owner_mail())
        self.assertFalse(p.decks("site-1") or p.decks("site-2"))
        self.assertIn("no email", p.banner())

    def test_every_input_that_is_not_an_email_request_changes_nothing(self):
        for name in ("REBUILD_CONTENT_SITE", "MARK_DECKS_SITE", "REBUILD_VIEWS", "REBUILD_ALARMS", "CLEANUP_GONE_SITE",
                     "TEST_DECK", "AI_HANDOFF", "WAIT_FOR_TEXTS_MINUTES", "DISCOVER_SITE"):
            p = policy(**MANUAL, **{name: "site-1"})
            self.assertFalse(p.owner_mail() or p.decks("site-1"), name)

    def test_send_emails_gives_a_manual_run_the_emails_of_a_scheduled_run(self):
        p = policy(**MANUAL, SEND_EMAILS="true")
        self.assertTrue(p.owner_mail() and p.decks("site-1") and p.decks("site-3"))
        self.assertFalse(policy(**MANUAL, SEND_EMAILS="false").owner_mail())
        self.assertFalse(policy(**MANUAL, SEND_EMAILS="").owner_mail())

    def test_asking_for_the_decks_of_one_site_sends_that_site_only_and_no_note(self):
        p = policy(**MANUAL, REBUILD_DECK_SITE="Site-2", TEST_DECK="true")
        self.assertTrue(p.decks("site-2"))
        self.assertFalse(p.decks("site-1") or p.decks("site-3"))
        self.assertFalse(p.owner_mail())

    def test_asking_for_all_decks_or_the_second_run_sends_decks_but_no_note(self):
        for env in ({"REBUILD_DECKS": "true"}, {"DECK_PHASE": "build"}):
            p = policy(**MANUAL, **env)
            self.assertTrue(p.decks("site-1") and p.decks("site-2"), env)
            self.assertFalse(p.owner_mail(), env)

    def test_a_manual_second_run_for_one_site_sends_that_site_only(self):
        p = policy(**MANUAL, DECK_PHASE="build", REBUILD_DECK_SITE="site-3")
        self.assertTrue(p.decks("site-3"))
        self.assertFalse(p.decks("site-1"))


class DecksAreNotBuiltOrMarkedTests(unittest.TestCase):
    """A run that may not send email builds nothing and marks nothing, so the next scheduled run sends the deck."""

    def test_sync_decks_with_mail_off_does_not_touch_the_decks(self):
        site = types.SimpleNamespace(deck=types.SimpleNamespace(enabled=True), reports_sheet_id="r", name="site-1")
        run = gsc_pulse.SiteRun(site)
        with mock.patch.object(deck_runner, "run_decks") as m, mock.patch.object(gsc_pulse, "site_header"):
            gsc_pulse.sync_decks(object(), run, object(), mail_off=mail_policy.NOT_SENT)
        m.assert_not_called()
        self.assertEqual(run.decks_text, mail_policy.NOT_SENT)


class WholeRunTests(unittest.TestCase):
    """gsc_pulse.main() as GitHub starts it: what each step is given to send with."""

    def run_it(self, env):
        self.w = th.MainWiringTests("test_history_off_when_secret_missing")
        self.w.setUp()
        calls = {"content": [], "decks": [], "sent": []}
        base = {**self.w.base, "SMTP_HOST": "smtp.test", "SMTP_PORT": "587", "SMTP_USER": "me@example.test",
                "SMTP_PASSWORD": "pw"}

        def content(gsc, run, smtp, **kw):
            calls["content"].append(smtp is not None)

        def decks(sheets, run, smtp, *a, **kw):
            calls["decks"].append((run.name, smtp is not None and not kw.get("mail_off")))

        with mock.patch.object(gsc_pulse, "sync_content", content), \
                mock.patch.object(gsc_pulse, "sync_decks", decks), \
                mock.patch("mailer.send", lambda *a, **k: calls["sent"].append(1)):
            out, err = self.w.run_main({**base, **env}, self.w.gsc, self.w.sheets)
        self.assertIsNone(err, out)
        return calls, out

    def test_the_scheduled_run_may_send(self):
        calls, _ = self.run_it(SCHEDULED)
        self.assertEqual(calls["content"], [True])
        self.assertTrue(all(ok for _, ok in calls["decks"]) and calls["decks"])

    def test_a_manual_run_may_not(self):
        calls, out = self.run_it(MANUAL)
        self.assertEqual(calls["content"], [False])
        self.assertFalse(any(ok for _, ok in calls["decks"]))
        self.assertEqual(calls["sent"], [])
        self.assertIn("no email is sent", out)

    def test_a_manual_content_refresh_does_not_email_the_owner(self):
        calls, _ = self.run_it({**MANUAL, "REBUILD_CONTENT_SITE": "default"})
        self.assertEqual(calls["content"], [False])

    def test_a_manual_run_that_asks_for_emails_gets_them(self):
        calls, out = self.run_it({**MANUAL, "SEND_EMAILS": "true"})
        self.assertEqual(calls["content"], [True])
        self.assertTrue(all(ok for _, ok in calls["decks"]))
        self.assertIn("send_emails", out)


if __name__ == "__main__":
    unittest.main()
