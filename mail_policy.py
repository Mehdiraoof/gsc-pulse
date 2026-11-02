"""
PulseO: who may send email in this run
======================================
Only the scheduled Monday run sends email by itself. A run started by hand (a test, a check, a re-run) sends
nothing unless the person who started it asked for email in that run:

  * `send_emails` asks for the normal emails of a scheduled run (decks that are due, the notes to the owner);
  * `rebuild_decks`, `rebuild_deck_site` (that one site) and `deck_phase = build` ask for decks, and only decks;
  * `email_test_summary` asks for the one summary email of a content test (handled where that test ends).

A deck that is not sent is not marked as sent either, so the next scheduled run sends it. A run that does not
know how it was started (no GITHUB_EVENT_NAME: a local run) behaves like a scheduled run, as before.
"""

TRUE = ("1", "true", "yes", "on")
MANUAL_EVENT = "workflow_dispatch"
NOT_SENT = "not sent (a run started by hand sends no email unless asked)"


def _on(env, name: str) -> bool:
    return str(env.get(name, "") or "").strip().lower() in TRUE


class Policy:
    def __init__(self, env):
        self.manual = str(env.get("GITHUB_EVENT_NAME", "") or "").strip() == MANUAL_EVENT
        self.send_all = _on(env, "SEND_EMAILS")
        self.rebuild_all = _on(env, "REBUILD_DECKS")
        self.deck_site = str(env.get("REBUILD_DECK_SITE", "") or "").strip().lower()
        self.build_phase = str(env.get("DECK_PHASE", "") or "").strip().lower() == "build"

    def owner_mail(self) -> bool:
        """Notes to the owner (sheets ready, a content refresh, lost access): scheduled runs, or asked for."""
        return not self.manual or self.send_all

    def decks(self, site_name: str) -> bool:
        """May the decks of this site be emailed in this run?"""
        if self.owner_mail() or self.rebuild_all:
            return True
        if self.deck_site:                               # one site was named: no other site's deck goes out
            return self.deck_site == str(site_name).strip().lower()
        return self.build_phase

    def banner(self) -> str:
        if not self.manual:
            return ""
        if self.send_all:
            return "mail: started by hand with send_emails: emails go out as in a scheduled run"
        asked = [n for n, on in (("all decks", self.rebuild_all), ("the second run's decks", self.build_phase),
                                 ("the decks of one site", bool(self.deck_site))) if on]
        return "mail: started by hand: " + (("only " + " and ".join(asked) + " (asked for)") if asked
                                            else "no email is sent")
