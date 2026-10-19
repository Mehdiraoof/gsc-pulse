"""
PulseO: what goes into a deck
=============================
Reads the Reports sheet tabs (R Monthly, R Weekly, R Keywords, R Pages, R AIO Pages,
R Alarms, R Weekly Movers, Notes) and turns them into plain Python structures: the
scorecard, action titles, alarms, tables. Search Console is never queried here; the
Reports sheet is the single source of truth.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from datetime import timedelta
from urllib.parse import unquote, urlsplit

from googleapiclient.errors import HttpError

import i18n
import opportunities
import periods
import resolved
import reports
import tabs

TOP_ROWS = 10
MAX_ALARMS = 8
WEEKLY_DROP_PCT = 20           # a week-on-week drop of this much is an alarm
WEEKLY_HIGH_PCT = 40


# --------------------------------------------------------------------------- #
# Reading numbers out of the sheet
# --------------------------------------------------------------------------- #

def num(value):
    """A sheet cell as a number, or None when it is empty or not a number."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def as_dicts(rows: list, header: list) -> list:
    out = []
    for r in rows:
        r = list(r) + [""] * (len(header) - len(r))
        out.append(dict(zip(header, r)))
    return out


def short_path(url: str, limit: int = 34) -> str:
    """A page URL as its decoded path, cut to `limit` characters with an ellipsis."""
    parts = urlsplit(url)
    path = unquote(parts.path or "/")
    if parts.query:
        path += "?" + unquote(parts.query)
    return path if len(path) <= limit else path[: limit - 1] + "…"


def shorten_urls(text: str, limit: int = 40) -> str:
    """Replaces every URL inside a sentence by its short decoded path."""
    return re.sub(r"https?://[^\s]+", lambda m: short_path(m.group(0).rstrip(".,;)"), limit), text)


# --------------------------------------------------------------------------- #
# The report data
# --------------------------------------------------------------------------- #

@dataclass
class Report:
    kind: str                                  # "monthly" | "weekly"
    period: str                                # '2026-09' or '2026-W41'
    prev_period: str
    year_period: str
    rows: dict = field(default_factory=dict)           # (period, segment) -> row dict
    labels: list = field(default_factory=list)         # periods that have an All row, ascending
    segments: list = field(default_factory=list)       # segment names in sheet order
    keywords: list = field(default_factory=list)       # R Keywords rows of the period
    pages: list = field(default_factory=list)          # R Pages rows of the period
    aio_pages: list = field(default_factory=list)      # R AIO Pages rows of the period
    alarms: list = field(default_factory=list)         # [{"severity", "area", "message", "impact"}]
    movers: list = field(default_factory=list)         # R Weekly Movers rows of the week
    notes: list = field(default_factory=list)          # [(topic, text)]
    page_types: list = field(default_factory=list)     # names only
    lost_new_floor: int = 10
    monday: object = None
    sunday: object = None
    gone: list = field(default_factory=list)           # R Gone Pages rows of the period
    opps: list = field(default_factory=list)           # Opportunities rows (monthly deck)
    resolved: object = None                            # resolved.Resolved (monthly deck)
    brand_terms: list = field(default_factory=list)    # lower-cased brand words (config + label + domain)
    deck_labels: dict = field(default_factory=dict)    # segment label -> the name the client deck uses

    def row(self, period=None, segment="All"):
        return self.rows.get((period or self.period, segment))


def _latest(rows: list):
    labels = sorted({r["Period"] for r in rows if r["Segment"] == "All" and r["Period"]})
    return labels


def load_monthly(sheets, reports_id: str, site, period: str = None):
    """The monthly report for `period` (default: the latest month in R Monthly), or None."""
    monthly = as_dicts(tabs.read_rows(sheets, reports_id, reports.MONTHLY, "Q"),
                       reports.MONTHLY_HEADER)
    labels = _latest(monthly)
    if not labels:
        return None
    period = period or labels[-1]
    if period not in labels:
        return None
    first = periods.parse_month(period)
    rep = Report("monthly", period,
                 periods.month_label(periods.add_months(first, -1)),
                 periods.month_label(periods.add_months(first, -12)))
    rep.rows = {(r["Period"], r["Segment"]): r for r in monthly}
    rep.labels = [l for l in labels if l <= period]
    rep.segments = list(dict.fromkeys(r["Segment"] for r in monthly if r["Segment"]))
    rep.keywords = [r for r in as_dicts(tabs.read_rows(sheets, reports_id, reports.KEYWORDS, "L"),
                                        reports.KEYWORDS_HEADER) if r["Period"] == period]
    rep.pages = [r for r in as_dicts(tabs.read_rows(sheets, reports_id, reports.PAGES, "N"),
                                     reports.PAGES_HEADER) if r["Period"] == period]
    rep.aio_pages = [r for r in as_dicts(tabs.read_rows(sheets, reports_id, reports.AIO_PAGES, "G"),
                                         reports.AIO_PAGES_HEADER) if r["Period"] == period]
    rep.alarms = [{"severity": r[1], "area": r[2], "message": r[3], "impact": num(r[4]) or 0}
                  for r in (list(x) + [""] * 5 for x in
                            tabs.read_rows(sheets, reports_id, reports.ALARMS, "E"))
                  if r[0] == period]
    rep.notes = _notes(sheets, reports_id)
    rep.page_types = [t["name"] for t in site.page_types]
    rep.gone = _gone(sheets, reports_id, period)
    rep.brand_terms = opportunities.brand_terms_for(site)
    rep.deck_labels = {s: site.deck_label(s) for s in rep.segments if s != "All"}
    rep.opps = _opps(sheets, reports_id)
    rep.resolved = resolved.load(sheets, reports_id)
    rep.lost_new_floor = int(site.threshold("lost_new_min_impressions"))
    return rep


def load_weekly(sheets, reports_id: str, site, period: str = None):
    """The weekly report for `period` (default: the latest week in R Weekly), or None."""
    weekly = as_dicts(tabs.read_rows(sheets, reports_id, reports.WEEKLY, "Q"), reports.WEEKLY_HEADER)
    labels = _latest(weekly)
    if not labels:
        return None
    period = period or labels[-1]
    if period not in labels:
        return None
    year, week = int(period[:4]), int(period[6:])
    monday = date.fromisocalendar(year, week, 1)
    twin = periods.same_week_last_year(monday)
    rep = Report("weekly", period, periods.week_label(monday - timedelta(days=7)),
                 periods.week_label(twin) if twin else "")
    rep.monday, rep.sunday = monday, monday + timedelta(days=6)
    rep.rows = {(r["Period"], r["Segment"]): r for r in weekly}
    rep.labels = [l for l in labels if l <= period]
    rep.segments = list(dict.fromkeys(r["Segment"] for r in weekly if r["Segment"]))
    rep.movers = [r for r in as_dicts(tabs.read_rows(sheets, reports_id, reports.WEEKLY_MOVERS, "L"),
                                      reports.KEYWORDS_HEADER) if r["Period"] == period]
    rep.alarms = weekly_alarms(rep)
    rep.notes = _notes(sheets, reports_id)
    rep.page_types = [t["name"] for t in site.page_types]
    rep.gone = _gone(sheets, reports_id, period)
    rep.lost_new_floor = int(site.threshold("lost_new_min_impressions"))
    return rep


def _gone(sheets, reports_id: str, period: str) -> list:
    """The pages of R Gone Pages that disappeared in `period` (empty without the tab)."""
    try:
        rows = as_dicts(tabs.read_rows(sheets, reports_id, reports.GONE, "H"), reports.GONE_HEADER)
    except HttpError:
        return []
    return [r for r in rows if r["Period"] == period and r["Type"] == "Gone"]


def _opps(sheets, reports_id: str) -> list:
    """All rows of the Opportunities tab as dicts keyed by its header (empty without the tab)."""
    try:
        return as_dicts(tabs.read_rows(sheets, reports_id, reports.OPPORTUNITIES, "L"),
                        opportunities.HEADER)
    except HttpError:
        return []


def _notes(sheets, reports_id: str) -> list:
    return [(r[0], r[1] if len(r) > 1 else "")
            for r in tabs.read_rows(sheets, reports_id, reports.NOTES, "B") if r and r[0]]


def weekly_alarms(rep: Report) -> list:
    """Week-on-week drops of 20% or more in clicks or impressions, per segment."""
    out = []
    for seg in rep.segments:
        row = rep.row(segment=seg)
        if row is None:
            continue
        for pct_col, diff_col, key, metric in (
                ("Clicks WoW %", "Clicks WoW", "Clicks", "clicks"),
                ("Impr WoW %", "Impr WoW", "Impressions", "impressions")):
            pct, diff, cur = num(row.get(pct_col)), num(row.get(diff_col)), num(row.get(key))
            if pct is None or diff is None or cur is None or pct > -WEEKLY_DROP_PCT:
                continue
            out.append({"severity": "high" if pct <= -WEEKLY_HIGH_PCT else "medium",
                        "area": "Traffic", "weekly": True, "metric": metric, "segment": seg,
                        "pct": abs(pct), "a": cur - diff, "b": cur, "impact": abs(diff),
                        "message": ""})
    out.sort(key=lambda a: (0 if a["severity"] == "high" else 1, -a["impact"]))
    return out


# --------------------------------------------------------------------------- #
# Alarm wording
# --------------------------------------------------------------------------- #
# R Alarms keeps its sentences as they are (with period keys like 2026-08 and the segment name
# "All"). Everything a person reads (decks, emails) is rewritten here: month names ("vs
# August"), "vs the previous week", the segment's label, and nothing at all for the whole site.

_MONTH_KEY = re.compile(r"^(\d{4})-(\d{2})$")
_WEEK_KEY = re.compile(r"^(\d{4})-W(\d{2})$")
_URL = re.compile(r"(https?://[^\s]+)")
_EN_MONTHS = {name: i + 1 for i, name in enumerate(i18n.table("en")["months"])}


def human_period(key: str, lang: str) -> str:
    """'2026-08' -> 'August'; '2026-W41' -> 'week 41'; anything else unchanged."""
    m = _MONTH_KEY.match(key)
    if m and 1 <= int(m.group(2)) <= 12:
        return i18n.month_name(key, lang)
    english = _EN_MONTHS.get(key)                    # R Alarms sentences name the month in English
    if english:
        return i18n.month_name(f"2000-{english:02d}", lang)
    w = _WEEK_KEY.match(key)
    if w:
        text = i18n.table(lang)["week"].format(n=int(w.group(2)))
        return text.lower() if lang == "en" else text
    return key


def humanize_periods(text: str, lang: str) -> str:
    """Replaces raw period keys inside a sentence (URLs are left alone)."""
    pattern = re.compile(r"\b\d{4}-(?:W\d{2}|\d{2})\b")
    parts = _URL.split(text)
    return "".join(p if i % 2 else pattern.sub(lambda m: human_period(m.group(0), lang), p)
                   for i, p in enumerate(parts))


def segment_phrase(T: dict, metric: str, segment: str) -> str:
    """'Clicks' for the whole site, 'Clicks for English' for a segment."""
    if segment and segment != "All":
        return T[f"{metric}_seg"].format(seg=segment)
    return T[f"{metric}_all"]


def alarm_text(alarm: dict, lang: str) -> str:
    """The sentence a person reads for an alarm (URLs shortened, months by name, no 'All')."""
    T = i18n.table(lang)
    if alarm.get("weekly"):                                    # weekly alarms are built from numbers
        return T["weekly_fell"].format(
            metric=segment_phrase(T, alarm["metric"], alarm.get("segment", "All")),
            pct=f"{alarm['pct']:.1f}%", a=i18n.fmt_int(alarm["a"]), b=i18n.fmt_int(alarm["b"]))
    message = alarm["message"]
    if alarm.get("area") == "AIO import":                      # it quotes the tab names to use
        return shorten_urls(message)
    rendered = _render_message(message, lang, T)
    return shorten_urls(rendered if rendered else humanize_periods(message, lang))


# Two generations of sentences: the current ones (month names, "vs", the segment left out for
# the whole site, a page shown by its path) and the ones written before v2.2.
_PATTERNS = [
    ("msg_clicks", re.compile(r"Clicks(?: for (?P<seg>.+?))? (?P<dir>rose|fell) (?P<pct>[\d.]+%) vs "
                              r"(?P<prev>\S+) \((?P<a>[\d,]+) to (?P<b>[\d,]+)\)\.")),
    ("msg_page_loss", re.compile(r"The page (?P<page>.+) lost (?P<pct>[\d.]+%) of its clicks vs "
                                 r"(?P<prev>\S+) \((?P<a>[\d,]+) to (?P<b>[\d,]+)\)\.")),
    ("msg_aio_drop", re.compile(r"AI Overview impressions fell (?P<pct>[\d.]+%) vs "
                                r"(?P<prev>\S+) \((?P<a>[\d,]+) to (?P<b>[\d,]+)\)\.")),
    ("msg_clicks", re.compile(r"Clicks for (?P<seg>.+) (?P<dir>rose|fell) by (?P<pct>[\d.]+%) compared "
                              r"with (?P<prev>\S+) \((?P<a>[\d,]+) to (?P<b>[\d,]+)\)\.")),
    ("msg_lost_kw", re.compile(r"The keyword \"(?P<kw>.+)\" brought (?P<n>[\d,]+) clicks in (?P<prev>\S+) "
                               r"and is not in Search Console's data in (?P<period>\S+)\.")),
    ("msg_page_loss", re.compile(r"The page (?P<page>\S+) lost (?P<pct>[\d.]+%) of its clicks compared "
                                 r"with (?P<prev>\S+) \((?P<a>[\d,]+) to (?P<b>[\d,]+)\)\.")),
    ("msg_more", re.compile(r"and (?P<n>\d+) more pages? lost (?P<pct>\d+%\+) of (?:their|its) clicks")),
    ("msg_aio_drop", re.compile(r"AI Overview impressions fell by (?P<pct>[\d.]+%) compared with "
                                r"(?P<prev>\S+) \((?P<a>[\d,]+) to (?P<b>[\d,]+)\)\.")),
]


def _render_message(message: str, lang: str, T: dict):
    """A known alarm sentence in the deck language, or None."""
    for key, pattern in _PATTERNS:
        m = pattern.fullmatch(message.strip())
        if not m:
            continue
        g = m.groupdict()
        if key == "msg_more" and lang == "en":
            return message.strip()                       # already plain English (singular or plural)
        for field_name in ("prev", "period"):
            if field_name in g:
                g[field_name] = human_period(g[field_name], lang)
        if key == "msg_clicks":
            direction = g.pop("dir")
            g["metric"] = segment_phrase(T, "clicks", g.pop("seg") or "All")
            return T["msg_clicks_up" if direction == "rose" else "msg_clicks_down"].format(**g)
        if key == "msg_page_loss":
            g["page"] = short_path(g["page"], 40)
        return T[key].format(**g)
    return None


def period_title(rep: Report, lang: str) -> str:
    """'September 2026' or 'Week 41 · 5 Oct – 11 Oct 2026'."""
    T = i18n.table(lang)
    if rep.kind == "monthly":
        return i18n.month_title(rep.period, lang)
    return f"{T['week'].format(n=int(rep.period[6:]))} · {i18n.week_dates(rep.monday, rep.sunday, lang)}"


# --------------------------------------------------------------------------- #
# Scorecard and titles
# --------------------------------------------------------------------------- #

@dataclass
class Line:
    label_key: str          # "mom" | "wow" | "yoy"
    text: str
    good: object = None     # True green, False red, None neutral


@dataclass
class Card:
    label_key: str
    value: str
    lines: list = field(default_factory=list)
    note_key: str = ""      # shown instead of lines (e.g. "not_imported")


def _pct_line(label_key: str, pct) -> Line:
    if pct is None:
        return Line(label_key, "—")
    return Line(label_key, i18n.fmt_signed(pct, 1, pct=True), pct > 0 if pct else None)


def scorecard(rep: Report, T: dict) -> list:
    """The five cards of the scorecard slide, with colours decided here (good / bad)."""
    unit = "mom" if rep.kind == "monthly" else "wow"
    pre = "MoM" if rep.kind == "monthly" else "WoW"
    cur = rep.row()
    prev, year = rep.row(rep.prev_period), rep.row(rep.year_period) if rep.year_period else None
    if cur is None:
        return []
    clicks, impr = num(cur["Clicks"]), num(cur["Impressions"])
    cards = [
        Card("sc_clicks", i18n.fmt_int(clicks or 0),
             [_pct_line(unit, num(cur[f"Clicks {pre} %"])), _pct_line("yoy", num(cur["Clicks YoY %"]))]),
        Card("sc_impressions", i18n.fmt_int(impr or 0),
             [_pct_line(unit, num(cur[f"Impr {pre} %"])), _pct_line("yoy", num(cur["Impr YoY %"]))]),
    ]

    ctr = num(cur["CTR (%)"])
    ctr_lines = []
    for key, other in ((unit, prev), ("yoy", year)):
        o = num(other["CTR (%)"]) if other else None
        if ctr is None or o is None:
            ctr_lines.append(Line(key, "—"))
        else:
            d = round(ctr - o, 2)
            ctr_lines.append(Line(key, f"{i18n.fmt_signed(d, 2)} {T['pt']}", d > 0 if d else None))
    cards.append(Card("sc_ctr", f"{ctr:.2f}%" if ctr is not None else "—", ctr_lines))

    pos = num(cur["Avg. Position"])
    pos_lines = []
    for key, other in ((unit, prev), ("yoy", year)):
        o = num(other["Avg. Position"]) if other else None
        if pos is None or o is None:
            pos_lines.append(Line(key, "—"))
        else:
            d = round(pos - o, 1)
            word = T["worse"] if d > 0 else (T["better"] if d < 0 else "")
            pos_lines.append(Line(key, f"{i18n.fmt_signed(d, 1)} {word}".strip(),
                                  d < 0 if d else None))          # lower is better
    cards.append(Card("sc_position", f"{pos:.1f}" if pos is not None else "—", pos_lines))

    aio = num(cur["AIO Impressions"])
    if aio is None:
        cards.append(Card("sc_aio", "—", note_key="not_imported"))
    else:
        cards.append(Card("sc_aio", i18n.fmt_int(aio), [_pct_line(unit, num(cur[f"AIO {pre} %"]))]))
    return cards


def title_for_clicks(rep: Report, T: dict) -> str:
    """'Clicks down 32% this month, 91% vs last year' (rules, not prose)."""
    cur = rep.row()
    when = T["this_month"] if rep.kind == "monthly" else T["this_week"]
    pre = "MoM" if rep.kind == "monthly" else "WoW"
    m, y = (num(cur[f"Clicks {pre} %"]) if cur else None), (num(cur["Clicks YoY %"]) if cur else None)
    if m is None:
        return T["summary_placeholder"] if cur is None else f"{T['sc_clicks']}: {i18n.fmt_int(num(cur['Clicks']) or 0)}"
    d = lambda v: T["dir_up"] if v > 0 else T["dir_down"]                       # noqa: E731
    mt = f"{abs(m):.0f}%"
    if round(abs(m)) == 0 and y is None:
        return T["title_flat"].format(when=when)
    if y is None or round(abs(y)) == 0:
        return T["title_mom_only"].format(dir=d(m), m=mt, when=when)
    yt = f"{abs(y):.0f}%"
    if (m > 0) == (y > 0):
        return T["title_same"].format(dir=d(m), m=mt, when=when, y=yt)
    return T["title_mixed"].format(dir=d(m), dir2=d(y), m=mt, when=when, y=yt)


def what_it_means(rep: Report, T: dict) -> str:
    """One rule-based sentence for the scorecard callout, or '' when no rule applies."""
    cur = rep.row()
    if cur is None or rep.kind != "monthly":
        return ""
    year = rep.row(rep.year_period)
    clicks_yoy, clicks_mom = num(cur["Clicks YoY %"]), num(cur["Clicks MoM %"])
    impr_mom = num(cur["Impr MoM %"])
    pos, pos_year = num(cur["Avg. Position"]), (num(year["Avg. Position"]) if year else None)
    if clicks_yoy is not None and clicks_yoy <= -20 and pos is not None and pos_year is not None \
            and pos < pos_year:
        return T["means_position_up_clicks_down"].format(a=f"{pos_year:.1f}", b=f"{pos:.1f}")
    if clicks_mom is not None and impr_mom is not None and clicks_mom <= -20 and impr_mom <= -20:
        return T["means_both_down"]
    if clicks_mom is not None and impr_mom is not None and clicks_mom >= 20 and impr_mom >= 20:
        return T["means_both_up"]
    return ""


def alarms_title(alarms: list, T: dict, weekly: bool = False) -> str:
    when = T["this_week"] if weekly else T["this_month"]
    shown = alarms
    if not shown:
        return T["alarms_none_title"].format(when=when)
    sev = {s: sum(1 for a in shown if a["severity"] == s) for s in ("high", "medium", "low")}
    if len(shown) == 1:
        mix = T["alarms_all"].format(sev=T["sev_word_" + shown[0]["severity"]])
        return T["alarms_title_one"].format(when=when, mix=mix)
    kinds = [s for s, n in sev.items() if n]
    if len(kinds) == 1:
        mix = T["alarms_all"].format(sev=T["sev_word_" + kinds[0]])
    else:
        mix = T["alarms_mix"].format(high=sev["high"], medium=sev["medium"] + sev["low"])
    return T["alarms_title"].format(n=len(shown), when=when, mix=mix)


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

def of_type(rows: list, kind: str) -> list:
    return [r for r in rows if r.get("Type") == kind]


def change_cell(diff) -> tuple:
    """('+11', True) / ('−14', False) / ('0', None)"""
    n = num(diff)
    if n is None:
        return "—", None
    return i18n.fmt_signed(n), (n > 0 if n else None)


def trend_series(rep: Report, count: int) -> tuple:
    """(labels, clicks, impressions) for the last `count` periods ending at the report's period."""
    labels = rep.labels[-count:]
    clicks = [num(rep.row(l)["Clicks"]) or 0 for l in labels]
    impr = [num(rep.row(l)["Impressions"]) or 0 for l in labels]
    return labels, clicks, impr


def aio_series(rep: Report, count: int = 13) -> tuple:
    labels = [l for l in rep.labels[-count:] if num(rep.row(l)["AIO Impressions"]) is not None]
    return labels, [num(rep.row(l)["AIO Impressions"]) for l in labels]


def first_sentence(text: str, limit: int = 150) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# The aggregated text (AI summary input, and the notes of the placeholder slide)
# --------------------------------------------------------------------------- #

def aggregated_text(rep: Report, label: str) -> str:
    """Aggregated numbers only: scorecard, segments, alarms, top movers, AIO. No IDs or raw tables."""
    T = i18n.table("en")
    pre = "MoM" if rep.kind == "monthly" else "WoW"
    lines = [f"Site: {label}", f"Report: {'Monthly' if rep.kind == 'monthly' else 'Weekly'} SEO report, "
                              f"period {period_title(rep, 'en')}", ""]
    for card in scorecard(rep, T):
        parts = [f"{T[l.label_key] if l.label_key in T else l.label_key.upper()} {l.text}" for l in card.lines]
        lines.append(f"{T[card.label_key]}: {card.value}" + (f" ({'; '.join(parts)})" if parts
                                                              else (f" ({T[card.note_key]})" if card.note_key else "")))
    segs = [s for s in rep.segments if s != "All"]
    if segs:
        lines += ["", "Segments (clicks, change):"]
        for s in segs:
            r = rep.row(segment=s)
            if r:
                lines.append(f"- {s}: {r['Clicks']} clicks, {pre} {r.get(f'Clicks {pre} %', '') or 'n/a'}%, "
                             f"YoY {r['Clicks YoY %'] or 'n/a'}%")
    lines += ["", "Alarms:"]
    for a in rep.alarms[:MAX_ALARMS]:
        lines.append(f"- [{a['severity']}] {alarm_text(a, 'en')}")
    if not rep.alarms:
        lines.append("- none")

    def block(title, rows, name_key, cols):
        lines.extend(["", title])
        for r in rows[:TOP_ROWS]:
            name = short_path(r[name_key]) if name_key == "Page" else r[name_key]
            lines.append("- " + name + ": " + ", ".join(f"{c} {r[c]}" for c in cols))
        if not rows:
            lines.append("- none")

    if rep.kind == "monthly":
        block("Lost keywords (impressions last month):", of_type(rep.keywords, "Lost"), "Query", ["Impr prev"])
        block("New keywords (impressions):", of_type(rep.keywords, "New"), "Query", ["Impressions"])
        block("Growing keywords (clicks, change):", of_type(rep.keywords, "Growing"), "Query", ["Clicks", "Clicks diff"])
        block("Declining keywords (clicks, change):", of_type(rep.keywords, "Declining"), "Query", ["Clicks", "Clicks diff"])
        block("Growing pages (clicks, change):", of_type(rep.pages, "Growing"), "Page", ["Clicks", "Clicks diff"])
        block("Declining pages (clicks, change):", of_type(rep.pages, "Declining"), "Page", ["Clicks", "Clicks diff"])
        block("Pages growing vs last year:", of_type(rep.pages, "YoY Growing"), "Page", ["Clicks", "Clicks diff"])
        block("Pages declining vs last year:", of_type(rep.pages, "YoY Declining"), "Page", ["Clicks", "Clicks diff"])
        block("AI search, growing pages:", of_type(rep.aio_pages, "Growing")[:5], "Page", ["AIO Impr", "AIO Impr diff"])
        block("AI search, declining pages:", of_type(rep.aio_pages, "Declining")[:5], "Page", ["AIO Impr", "AIO Impr diff"])
    else:
        block("Growing keywords vs previous week:", of_type(rep.movers, "Growing"), "Query", ["Clicks", "Clicks diff"])
        block("Declining keywords vs previous week:", of_type(rep.movers, "Declining"), "Query", ["Clicks", "Clicks diff"])
    return "\n".join(lines)
