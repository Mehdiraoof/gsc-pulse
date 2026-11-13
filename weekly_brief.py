"""
PulseO: the one weekly email for all sites
==========================================
It replaces the weekly deck of every site. Each Monday, after the sync, Content Monday and the technical check, the facts
of the week are collected from the sheets of all sites (no AI): one line each, with a link.

  * traffic     the clicks and impressions of the week against the week before, the worst drop, the biggest gain and loss
  * technical   the problems that are open in the Technical sheet of each site
  * drafts      the drafts waiting for review (Status Drafted, To Do Review)
  * briefs      the briefs ready for the writers (Status Briefed, To Do Write or Update)
  * reports     the monthly client reports that are ready and not yet sent
  * cases       new diagnosis cases, when a site has a Diagnosis tab
  * overdue     what has been waiting too long (a brief with no draft, a draft nobody reviewed, an old technical problem,
                a monthly report not sent)

The facts go into ONE row of an AI Notes tab (slot weekly_brief) in the Index sheet, the single central place, where the
owner's Claude plan finds it and writes the text. When the text is ready ONE email "Your week · [date]" is sent with it;
if it is not ready by the second deck check the facts are sent as they are. Nothing here calls an AI. A source that cannot
be read is named at the end of the email and never stops the rest.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import ai_notes
import content
import content_sheet as cs
import deck_data as dd
import history
import index_watch
import mailer
import periods
import provision
import reports
import state as st
import tabs
import technical

CASES_TAB = "Diagnosis"
OVERDUE_BRIEF_DAYS, OVERDUE_DRAFT_DAYS, OVERDUE_PROBLEM_DAYS, OVERDUE_REPORT_DAYS = 14, 21, 14, 7
PER_SITE = 5                         # lines per site and section; the rest is one line "and N more"
LINE_MAX = 220
SECTIONS = ("Traffic", "Technical problems", "Drafts waiting for review", "Briefs ready for writers",
            "Monthly client reports ready to send", "New diagnosis cases", "Overdue")
SENT_KEY = "weekly_brief_sent"
DOC_LINK = re.compile(r"https://docs\.google\.com/\S+")


@dataclass
class Item:
    section: str
    site: str                        # the label of the site
    text: str
    link: str = ""
    overdue: bool = False            # shown under Overdue instead of its own section

    def kind_line(self) -> str:
        """The line with what it is about in front, for the Overdue section."""
        return Item(self.section, self.site, f"{self.section.lower()}: {self.text}", self.link).line

    @property
    def line(self) -> str:
        text = f"{self.site}: {self.text}"
        text = text if len(text) <= LINE_MAX else text[:LINE_MAX - 1].rstrip() + "…"
        return text + (f" {self.link}" if self.link else "")


@dataclass
class Facts:
    day: date
    period: str                      # the ISO week of the Monday, "2026-W46"
    items: list = field(default_factory=list)
    missing: list = field(default_factory=list)      # what could not be read, for the end of the email

    def text(self) -> str:
        """The facts as the email shows them: a heading for each section that has something, one line each."""
        out = []
        for section in SECTIONS:
            wanted = [i for i in self.items if (i.overdue if section == "Overdue" else i.section == section and not i.overdue)]
            if not wanted:
                continue
            out.append(section)
            by_site = {}
            for item in wanted:
                by_site.setdefault(item.site, []).append(item)
            for lines in by_site.values():
                out += [f"• {i.kind_line() if section == 'Overdue' else i.line}" for i in lines[:PER_SITE]]
                if len(lines) > PER_SITE:
                    more = lines[PER_SITE:]
                    out.append(f"• {lines[0].site}: and {len(more)} more" + (f" {more[0].link}" if more[0].link else ""))
            out.append("")
        if not self.items:
            out += ["Nothing to report this week.", ""]
        if self.missing:
            out += ["Could not be read: " + ", ".join(self.missing), ""]
        return "\n".join(out).rstrip() + "\n"


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def period_of(day: date) -> str:
    year, week, _ = monday_of(day).isocalendar()
    return f"{year}-W{week:02d}"


def day_text(day: date) -> str:
    return f"{day.day} {day.strftime('%b %Y')}"


def signed(pct) -> str:
    return "no change" if pct is None or round(abs(pct)) < 1 else f"{'+' if pct > 0 else '-'}{abs(pct):.0f}%"


# --------------------------------------------------------------------------- #
# The sources
# --------------------------------------------------------------------------- #

def traffic_items(run, today: date) -> list:
    site = run.site
    label = site.deck.site_label or site.name
    rep = dd.load_weekly(run.sheets, site.reports_sheet_id, site)
    row = rep.row() if rep is not None else None
    if not row:
        return []
    link = provision.link(site.reports_sheet_id)
    clicks, impr = dd.num(row.get("Clicks")), dd.num(row.get("Impressions"))
    out = [Item("Traffic", label,
                f"week {rep.period[-2:]}: {int(clicks or 0):,} clicks ({signed(dd.num(row.get('Clicks WoW %')))} against the week "
                f"before), {int(impr or 0):,} impressions ({signed(dd.num(row.get('Impr WoW %')))})", link)]
    if rep.alarms:
        out.append(Item("Traffic", label, "biggest drop: " + dd.alarm_text(rep.alarms[0], "en"), link))
    moves = [(dd.num(r.get("Clicks diff")), r.get("Query", "")) for r in rep.movers if dd.num(r.get("Clicks diff"))]
    if moves:
        up, down = max(moves), min(moves)
        if up[0] and up[0] > 0:
            out.append(Item("Traffic", label, f"biggest gain: the search \"{up[1]}\" ({int(up[0]):+d} clicks)", link))
        if down[0] and down[0] < 0:
            out.append(Item("Traffic", label, f"biggest loss: the search \"{down[1]}\" ({int(down[0]):+d} clicks)", link))
    return out


def technical_items(run, today: date) -> list:
    site = run.site
    label = site.deck.site_label or site.name
    if run.drive is None:
        return []
    file_id = provision.existing_technical_file(run.drive, run.sheets, site)
    if not file_id:
        return []
    out = []
    for key, rec in technical.TechState(tabs.read_tab(run.sheets, file_id, "_state")).open().items():
        since = _day(rec.get("since", ""))
        late = bool(since) and (today - since).days >= OVERDUE_PROBLEM_DAYS
        out.append(Item("Technical problems", label, f"{rec.get('title', key)}" + (f", open since {since.isoformat()}" if since else ""),
                        provision.link(file_id), overdue=late))
    return out


def _day(value):
    if isinstance(value, date):
        return value
    return index_watch._to_date(value)


def dashboard_rows(run) -> tuple:
    """(dashboard file id, [row dicts of every page tab]); ('', []) when the site has no dashboard of its own."""
    site = run.site
    if run.drive is None or not site.content.enabled or site.auth != "me":
        return "", []
    file_id = provision.existing_content_file(run.drive, run.sheets, site)
    if not file_id:
        return "", []
    info = cs.sheet_info(run.sheets, file_id)
    rows = []
    for tab in (t.name for t in site.content.tabs):
        if tab in info:
            rows += cs.table_of(cs.read_all(run.sheets, file_id, tab))[1]
    return file_id, rows


def _name(row: dict) -> str:
    keyword = str(row.get(content.KEY, "")).strip()
    return keyword or tabs.path_of(str(row.get(content.URL, "")).strip()) or "a row without a name"


def _doc(row: dict, fallback: str) -> str:
    found = DOC_LINK.findall(str(row.get("Attachments", "")))
    return found[-1] if found else fallback


def work_items(run, today: date, rows: list = None, file_id: str = "") -> list:
    """Drafts waiting for review and briefs ready for writers, from the content dashboard."""
    site = run.site
    label = site.deck.site_label or site.name
    if rows is None:
        file_id, rows = dashboard_rows(run)
    fallback = provision.link(file_id) if file_id else ""
    out = []
    for row in rows:
        status, todo = str(row.get(content.STATUS, "")).strip().lower(), str(row.get(content.TODO, "")).strip().lower()
        briefed = _day(row.get("Briefed Date"))
        age = (today - briefed).days if briefed else None
        if status == "drafted" and todo == "review":
            out.append(Item("Drafts waiting for review", label, _name(row), _doc(row, fallback),
                            overdue=age is not None and age >= OVERDUE_DRAFT_DAYS))
        elif status == "briefed" and todo in ("write", "update"):
            out.append(Item("Briefs ready for writers", label, _name(row), _doc(row, fallback),
                            overdue=age is not None and age >= OVERDUE_BRIEF_DAYS))
    return out


def report_items(run, today: date) -> list:
    site = run.site
    cfg = site.deck
    if not cfg.enabled or not cfg.monthly or not site.reports_sheet_id:
        return []
    rid = site.reports_sheet_id
    monthly = dd.as_dicts(tabs.read_rows(run.sheets, rid, reports.MONTHLY, "Q"), reports.MONTHLY_HEADER)
    labels = dd._latest(monthly)
    if not labels:
        return []
    period = labels[-1]
    state = st.read_state(run.sheets, rid)
    if state.get("monthly_deck_sent", (0, ""))[1] == period or state.get("monthly_deck_synced", (0, ""))[1] != period:
        return []                                       # already sent, or this month\'s sync has not finished
    month_end = periods.month_end(periods.parse_month(period))
    return [Item("Monthly client reports ready to send", cfg.site_label or site.name,
                 f"the report for {period} is ready", provision.link(rid),
                 overdue=today >= month_end + timedelta(days=OVERDUE_REPORT_DAYS))]


def case_items(run, today: date) -> list:
    site = run.site
    if not site.reports_sheet_id:
        return []
    titles, _ = history.grid_info(run.sheets, site.reports_sheet_id)
    if CASES_TAB not in titles:
        return []
    values = tabs.read_tab(run.sheets, site.reports_sheet_id, CASES_TAB)
    if len(values) < 2:
        return []
    header = [str(h).strip().lower() for h in values[0]]
    status_at = header.index("status") if "status" in header else None
    out = []
    for r in values[1:]:
        status = str(r[status_at]).strip().lower() if status_at is not None and status_at < len(r) else ""
        if r and str(r[0]).strip() and status in ("", "new", "open"):
            out.append(Item("New diagnosis cases", site.deck.site_label or site.name, str(r[0]).strip(),
                            provision.link(site.reports_sheet_id)))
    return out


SOURCES = (("traffic", traffic_items), ("technical", technical_items), ("work", work_items), ("reports", report_items),
           ("cases", case_items))


def build_facts(runs: list, today: date, sources=SOURCES, log=print) -> Facts:
    """The facts of all sites. A source that cannot be read for a site is named in `missing` (the label of the site and
    the kind of fact), never the error."""
    facts = Facts(today, period_of(today))
    for run in runs:
        label = run.site.deck.site_label or run.site.name
        for name, source in sources:
            try:
                facts.items += source(run, today)
            except Exception as e:  # noqa: BLE001  one source never stops the rest
                facts.missing.append(f"{label} {name}")
                log(f"weekly brief: {run.name}: {name} could not be read ({type(e).__name__})")
    return facts


# --------------------------------------------------------------------------- #
# The row, the email
# --------------------------------------------------------------------------- #

def subject_of(day: date) -> str:
    return f"Your week · {day_text(monday_of(day))}"


def input_for(facts: Facts) -> str:
    return ai_notes.weekly_brief_input(facts.text(), day_text(monday_of(facts.day)))


def run_weekly_brief(runs: list, smtp, today: date, gate: str = "", log=print, send=mailer.send, sources=SOURCES) -> str:
    """The weekly email. gate "" (the sync): the row is written for the owner\'s plan, nothing is sent; "first" (the first deck
    check): sent only when the text is ready; "second" (the second check and the Tuesday catch-up): sent with the text if it
    is ready, else with the facts as they are. A week is sent once. Returns a short line for the run summary."""
    runs = [r for r in runs if r.status != "failed" and r.sheets is not None and r.drive is not None]
    if not runs:
        return "off (no site with its own access)"
    sheets, drive = runs[0].sheets, runs[0].drive
    index_id = provision._open_index(drive, sheets)
    titles, _ = history.grid_info(sheets, index_id)
    tabs.ensure_tab(sheets, index_id, titles, st.STATE_TAB, st.STATE_HEADER)
    sent = st.read_state(sheets, index_id)
    facts = build_facts(runs, today, sources, log)
    if sent.get(SENT_KEY, (0, ""))[1] == facts.period:
        return f"already sent for {facts.period}"
    ai_notes.prepare(sheets, index_id, [ai_notes.Slot("weekly", facts.period, ai_notes.WEEKLY_BRIEF, input_for(facts), "en")],
                     today, log)
    if not gate:
        return f"row ready for {facts.period}, nothing sent"
    ready, _ = ai_notes.texts_for(sheets, index_id, "weekly", facts.period)
    text = ready.get(ai_notes.WEEKLY_BRIEF, "")
    if not text and gate == "first":
        return "the text is not ready; the second check sends the facts as they are"
    body = (text.strip() if text else "Here are the facts of the week.\n\n" + facts.text()).rstrip() + "\n"
    if smtp is None:
        return "not sent (no mail in this run)"
    try:
        send(smtp, mailer.build_plain(smtp.user, [smtp.user], subject_of(today), body))
    except Exception as e:  # noqa: BLE001  not sent: the next check tries again
        log(f"weekly brief: the email could not be sent ({type(e).__name__})")
        return "email failed"
    st.write_state(sheets, index_id, sent, SENT_KEY, facts.period)
    return f"sent for {facts.period} ({'with the text' if text else 'with the facts as they are'})"
