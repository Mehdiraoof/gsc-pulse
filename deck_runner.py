"""
PulseO: deciding when decks are built, building them, and emailing them
=======================================================================
* Weekly deck: for the latest complete week in R Weekly, once per week.
* Monthly deck: when a new complete month exists that has not been sent yet.
* "Already sent" is tracked in the `State` tab of the site's Reports sheet, and moved
  forward right after each email went out, so a failure of one deck never loses the
  state of a deck that did send.
* `rebuild` builds and sends both decks now, whatever the state says.

The deck is built in memory and lives only in the email: nothing is uploaded, committed or
stored as a workflow artifact.
"""

import os
from dataclasses import dataclass, field

import client_deck
import deck_data as dd
import decks
import history
import i18n
import mailer
import summary
import tabs
from state import STATE_HEADER, STATE_TAB, read_state, write_state   # noqa: F401  (re-exported)

STATE_KEY = {"monthly": "monthly_deck_sent", "weekly": "weekly_deck_sent"}
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


@dataclass
class DeckResult:
    sent: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def text(self) -> str:
        parts = []
        if self.sent:
            parts.append("sent " + " + ".join(self.sent))
        if self.failed:
            parts.append("failed " + " + ".join(self.failed))
        if not parts:
            parts.append("nothing new" if self.skipped else "nothing to send")
        return ", ".join(parts)


# --------------------------------------------------------------------------- #
# The email
# --------------------------------------------------------------------------- #

def email_text(report: dd.Report, cfg, lang: str, report_title: str, period: str) -> tuple:
    """(subject, body): three headline numbers and the top alarm."""
    T = i18n.table(lang)
    cards = dd.scorecard(report, T)
    head = [c for c in cards if c.label_key in ("sc_clicks", "sc_impressions", "sc_position")]
    lines = []
    for c in head:
        changes = "; ".join(f"{T[l.label_key]} {l.text}" for l in c.lines if l.text != "—")
        lines.append(f"- {T[c.label_key]}: {c.value}" + (f" ({changes})" if changes else ""))
    top = (dd.alarm_text(report.alarms[0], lang) if report.alarms else T["mail_no_alarm"])
    report_lc = T["monthly_report_lc" if report.kind == "monthly" else "weekly_report_lc"]
    body = "\n".join([
        T["mail_greeting"], "",
        T["mail_intro"].format(report_lc=report_lc, label=cfg.site_label, period=period), "",
        T["mail_headlines"], *lines, "",
        *([] if report.kind == "monthly" else [f"{T['mail_top_alarm']}: {top}", ""]),   # the client deck has no alarms
        T["mail_attached"], "", T["mail_sign"], ""])
    subject = T["mail_subject"].format(report=report_title, label=cfg.site_label, period=period)
    return subject, body


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #

def run_decks(sheets, reports_id: str, site, smtp, env, rebuild: bool = False, log=print,
              scrub=lambda s: s, template_dir: str = TEMPLATE_DIR, send=mailer.send,
              post=summary._post, fetch_logo=decks.fetch_logo) -> DeckResult:
    cfg = site.deck
    result = DeckResult()
    titles, _ = history.grid_info(sheets, reports_id)
    tabs.ensure_tab(sheets, reports_id, titles, STATE_TAB, STATE_HEADER)
    state = read_state(sheets, reports_id)
    for kind in ("monthly", "weekly"):
        if not getattr(cfg, kind):
            continue
        # the owner (SMTP_USER) always gets the deck; other people are in To, the owner in Bcc
        to, bcc = mailer.split_recipients(
            mailer.resolve_recipients(cfg.recipients_for(kind), env), smtp.user)
        load = dd.load_monthly if kind == "monthly" else dd.load_weekly
        report = load(sheets, reports_id, site)
        if report is None:
            log(f"decks: {kind}: no report data yet")
            result.skipped.append(kind)
            continue
        if not rebuild and state.get(STATE_KEY[kind], (0, ""))[1] == report.period:
            log(f"decks: {kind} already sent for the latest period")
            result.skipped.append(kind)
            continue
        try:
            ai = None
            if cfg.ai_summary and kind == "weekly":            # the client deck has no summary slide
                ai = summary.request_summary(
                    dd.aggregated_text(report, cfg.site_label), cfg.language, env, post)
                if ai is None:
                    log("decks: AI summary not available, using the placeholder slide")
            logo = fetch_logo(cfg.logo_url) if cfg.logo_url else None
            payload = (client_deck.build_monthly(report, cfg, template_dir, logo) if kind == "monthly"
                       else decks.build_deck(report, cfg, template_dir, ai, logo))
            period = decks.period_title(report, cfg.language)
            T = i18n.table(cfg.language)
            subject, body = email_text(report, cfg, cfg.language,
                                       T["monthly_report" if kind == "monthly" else "weekly_report"],
                                       period)
            filename = mailer.safe_filename(cfg.site_label, report.period)
            message = mailer.build_message(smtp.user, to, subject, body, filename, payload, bcc=bcc)
            send(smtp, message)
        except Exception as e:                           # noqa: BLE001  one deck never blocks the other
            result.failed.append(kind)
            log(f"decks: {kind} failed ({scrub(f'{type(e).__name__}: {e}')})")
            continue
        write_state(sheets, reports_id, state, STATE_KEY[kind], report.period)
        result.sent.append(kind)
        count = len(to) + len(bcc)
        log(f"decks: {kind} deck sent ({count} recipient{'s' if count != 1 else ''})")
    return result
