"""
GSC Pulse: report engine (v2.0)
===============================
Turns Search Console data into the monthly and weekly SEO reports, per site, in a
separate Reports spreadsheet. Long format, one row per period per item, so the tabs
can grow forever and be charted. Later versions (presentations) build on them.

Tabs
  R Monthly    totals per month and segment, with MoM / YoY changes and AI Overview columns
  R Weekly     the same per ISO week (segment All; page types with weekly_segments)
  R Keywords   Lost / New / Growing / Declining keywords, month vs previous month
  R Pages      Growing / Declining pages, month vs previous month and vs last year
  R AIO Pages  Growing / Declining pages by AI Overview impressions
  R Alarms     deterministic alarms in plain English
  Notes        fixed explanations of how to read the numbers
  AIO Raw      imported AI Overview data (see aio_import.py)

Rules
  * Only complete periods are reported (periods.py).
  * Closed periods are append-only. The one exception is the AI Overview columns of
    existing rows, which are filled in (or corrected) when an AIO import arrives
    after the month closed.
  * "All" totals come from a date-only query, segment totals from the same query with
    a page filter (`includingRegex`; `Other` excludes every rule), like the Search
    Console UI.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import aio_import
import alarms
import history
import sites
import periods
import tabs

MONTHLY, WEEKLY = "R Monthly", "R Weekly"
KEYWORDS, PAGES = "R Keywords", "R Pages"
AIO_PAGES, ALARMS, NOTES = "R AIO Pages", "R Alarms", "Notes"


def _change_header(unit: str) -> list:       # unit: "MoM" or "WoW"
    return [f"Clicks {unit}", f"Clicks {unit} %", f"Impr {unit}", f"Impr {unit} %",
            f"AIO {unit}", f"AIO {unit} %", "Clicks YoY", "Clicks YoY %", "Impr YoY", "Impr YoY %"]


def _period_header(unit: str) -> list:
    return (["Period", "Segment", "Clicks", "Impressions", "CTR (%)", "Avg. Position",
             "AIO Impressions"] + _change_header(unit))


MONTHLY_HEADER = _period_header("MoM")
WEEKLY_HEADER  = _period_header("WoW")
KEYWORDS_HEADER = ["Period", "Type", "Query", "Clicks", "Clicks prev", "Clicks diff",
                   "Impressions", "Impr prev", "Impr diff", "Position", "Position prev",
                   "Position diff"]
PAGES_HEADER = ["Period", "Type", "Page", "Page type(s)", "Clicks", "Clicks prev", "Clicks diff",
                "Impressions", "Impr prev", "Impr diff", "Position", "Position prev",
                "Position diff"]
AIO_PAGES_HEADER = ["Period", "Type", "Page", "AIO Impr", "AIO Impr prev", "AIO Impr diff"]

TABS = [(MONTHLY, MONTHLY_HEADER, "Q"), (WEEKLY, WEEKLY_HEADER, "Q"),
        (KEYWORDS, KEYWORDS_HEADER, "L"), (PAGES, PAGES_HEADER, "M"),
        (AIO_PAGES, AIO_PAGES_HEADER, "F"), (ALARMS, alarms.HEADER, "E"),
        (NOTES, ["Topic", "Explanation"], "B"), (aio_import.RAW_TAB, aio_import.RAW_HEADER, "D")]

AIO_COLS = (6, 11, 12)      # AIO Impressions, AIO change, AIO change % (0-based)


@dataclass
class ReportsResult:
    months: int = 0
    weeks: int = 0
    keyword_periods: int = 0
    page_periods: int = 0
    alarms: int = 0
    alarm_counts: dict = field(default_factory=dict)      # area -> rows in R Alarms
    aio_months: int = 0
    patched_cells: int = 0
    note: str = ""

    def summary(self) -> str:
        if self.note:
            return self.note
        return (f"+{self.months} months, +{self.weeks} weeks, +{self.keyword_periods} keyword "
                f"periods, +{self.page_periods} page periods, {self.alarms} new alarms, "
                f"AIO imported for {self.aio_months} months")


# --------------------------------------------------------------------------- #
# Segments (page types)
# --------------------------------------------------------------------------- #

@dataclass
class Segment:
    name: str
    kind: str                       # all | type | other
    regex: str = ""
    rules: list = field(default_factory=list)      # other: every rule it excludes

    def filter_groups(self):
        """dimensionFilterGroups for the Search Console API (None = no filter)."""
        if self.kind == "type":
            return [{"groupType": "and", "filters": [
                {"dimension": "page", "operator": "includingRegex", "expression": self.regex}]}]
        if self.kind == "other":
            return [{"groupType": "and", "filters": [
                {"dimension": "page", "operator": "excludingRegex", "expression": rx}
                for rx in self.rules]}]
        return None

    def matches(self, url: str) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "type":
            return re.search(self.regex, url) is not None
        return not any(re.search(rx, url) for rx in self.rules)


def build_segments(page_types: list) -> list:
    """All, each page type, and (only when page types exist) Other. Types may overlap."""
    segments = [Segment("All", "all")]
    segments += [Segment(t["name"], "type", t["regex"]) for t in page_types]
    if page_types:
        segments.append(Segment("Other", "other", rules=[t["regex"] for t in page_types]))
    return segments


def page_types_of(segments: list, url: str) -> str:
    return ", ".join(s.name for s in segments if s.kind != "all" and s.matches(url))


# --------------------------------------------------------------------------- #
# Numbers
# --------------------------------------------------------------------------- #

def daily_series(rows: list) -> dict:
    """{date: (clicks, impressions, position)} from a query with dimension 'date'."""
    out = {}
    for r in rows:
        out[date.fromisoformat(r["keys"][0])] = (int(r.get("clicks", 0)), int(r.get("impressions", 0)),
                                           float(r.get("position", 0)))
    return out



def totals(series: dict, first, last) -> tuple:
    """(clicks, impressions, average position) of a period. Position is weighted by
    impressions, which is how Search Console averages it over several days."""
    clicks = impr = 0
    weighted = 0.0
    for i in range((last - first).days + 1):
        c, n, p = series.get(first + timedelta(days=i), (0, 0, 0.0))
        clicks += c
        impr += n
        weighted += p * n
    return clicks, impr, (weighted / impr if impr else None)


def _diff(cur, prev):
    """(absolute change, percent change) as sheet cells; '' when there is no base."""
    if prev is None or cur is None:
        return "", ""
    d = cur - prev
    return d, (round(d / prev * 100, 1) if prev > 0 else "")


def aio_columns(aio, prev_aio) -> list:
    """[AIO Impressions, AIO change, AIO change %]"""
    change, pct = _diff(aio, prev_aio)
    return ["" if aio is None else aio, change, pct]


def build_row(label: str, segment: str, cur: tuple, prev, yoy, aio, prev_aio) -> list:
    clicks, impr, pos = cur
    row = [label, segment, clicks, impr, round(clicks / impr * 100, 2) if impr else 0,
           "" if pos is None else round(pos, 1)]
    row += [aio if aio is not None else ""]
    row += [*_diff(clicks, prev[0] if prev else None), *_diff(impr, prev[1] if prev else None)]
    row += aio_columns(aio, prev_aio)[1:]
    row += [*_diff(clicks, yoy[0] if yoy else None), *_diff(impr, yoy[1] if yoy else None)]
    return row


class AioData:
    """AI Overview numbers from AIO Raw."""

    def __init__(self, daily: dict, pages: dict):
        self.daily, self.pages = daily, pages

    def month(self, label: str, first, last, segment: Segment):
        if segment.kind == "all":
            days = [n for d, n in self.daily.items() if first <= d <= last]
            return sum(days) if days else None
        pages = self.pages.get(label)
        if not pages:
            return None
        return sum(n for url, n in pages.items() if segment.matches(url))

    def week(self, first, last, segment: Segment):
        if segment.kind != "all":
            return None
        days = [self.daily.get(first + timedelta(days=i)) for i in range(7)]
        return None if any(d is None for d in days) else sum(days)


# --------------------------------------------------------------------------- #
# Keywords and pages
# --------------------------------------------------------------------------- #

def by_key(rows: list) -> dict:
    """{query: (clicks, impressions, position)}"""
    return {r["keys"][0]: (int(r.get("clicks", 0)), int(r.get("impressions", 0)),
                           float(r.get("position", 0))) for r in rows}


def by_page(rows: list) -> dict:
    """{url: (clicks, impressions, position)} with #fragment variants added up."""
    acc = {}
    for r in rows:
        url = re.sub(r"#.*$", "", r["keys"][0])
        c, n, p = int(r.get("clicks", 0)), int(r.get("impressions", 0)), float(r.get("position", 0))
        a = acc.setdefault(url, [0, 0, 0.0])
        a[0] += c
        a[1] += n
        a[2] += p * n
    return {u: (c, n, (w / n if n else 0.0)) for u, (c, n, w) in acc.items()}


def _num(v):
    return "" if v is None else round(v, 1)


def _pos_diff(cur, prev):
    return "" if cur is None or prev is None else round(cur - prev, 1)


def compare_rows(cur: dict, prev: dict) -> dict:
    """{key: (clicks, clicks_prev, impr, impr_prev, pos, pos_prev)} over both periods."""
    out = {}
    for key in set(cur) | set(prev):
        c, n, p = cur.get(key, (0, 0, None))
        pc, pn, pp = prev.get(key, (0, 0, None))
        out[key] = (c, pc, n, pn, p if n else None, pp if pn else None)
    return out


def _line(period, kind, key, v, extra=()) -> list:
    c, pc, n, pn, p, pp = v
    return [period, kind, key, *extra, c, pc, c - pc, n, pn, n - pn,
            _num(p), _num(pp), _pos_diff(p, pp)]


def movers(period: str, cmp: dict, top_n: int, growing: str, declining: str, extra) -> list:
    """Top-N risers and fallers by click difference (ties: impression difference)."""
    up = [(k, v) for k, v in cmp.items() if v[0] - v[1] > 0]
    down = [(k, v) for k, v in cmp.items() if v[0] - v[1] < 0]
    up.sort(key=lambda kv: (-(kv[1][0] - kv[1][1]), -(kv[1][2] - kv[1][3]), kv[0]))
    down.sort(key=lambda kv: (kv[1][0] - kv[1][1], kv[1][2] - kv[1][3], kv[0]))
    return ([_line(period, growing, k, v, extra(k)) for k, v in up[:top_n]]
            + [_line(period, declining, k, v, extra(k)) for k, v in down[:top_n]])


def keyword_rows(period: str, cur: dict, prev: dict, th: dict) -> tuple:
    """-> (rows for R Keywords, [(query, clicks_prev)] of the Lost keywords)"""
    cmp = compare_rows(cur, prev)
    floor = th["lost_new_min_impressions"]
    lost = sorted(((k, v) for k, v in cmp.items() if v[3] >= floor and v[2] == 0),
                  key=lambda kv: (-kv[1][3], kv[0]))
    new = sorted(((k, v) for k, v in cmp.items() if v[3] == 0 and v[2] >= floor),
                 key=lambda kv: (-kv[1][2], kv[0]))
    rows = [_line(period, "Lost", k, v) for k, v in lost]
    rows += [_line(period, "New", k, v) for k, v in new]
    rows += movers(period, cmp, int(th["movers_top_n"]), "Growing", "Declining", lambda k: ())
    return rows, [(k, v[1]) for k, v in lost]


def page_rows(period: str, cur: dict, prev: dict, yoy, segments: list, th: dict) -> list:
    n = int(th["movers_top_n"])
    extra = lambda url: (page_types_of(segments, url),)        # noqa: E731
    rows = movers(period, compare_rows(cur, prev), n, "Growing", "Declining", extra)
    if yoy is not None:
        rows += movers(period, compare_rows(cur, yoy), n, "YoY Growing", "YoY Declining", extra)
    return rows


def aio_page_rows(period: str, cur: dict, prev: dict, top_n: int) -> list:
    pairs = {u: (cur.get(u, 0), prev.get(u, 0)) for u in set(cur) | set(prev)}
    up = sorted(((u, v) for u, v in pairs.items() if v[0] - v[1] > 0),
                key=lambda kv: (-(kv[1][0] - kv[1][1]), kv[0]))
    down = sorted(((u, v) for u, v in pairs.items() if v[0] - v[1] < 0),
                  key=lambda kv: (kv[1][0] - kv[1][1], kv[0]))
    return ([[period, "Growing", u, a, b, a - b] for u, (a, b) in up[:top_n]]
            + [[period, "Declining", u, a, b, a - b] for u, (a, b) in down[:top_n]])


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #

def notes_rows(segments: list, th: dict) -> list:
    types = [s.name for s in segments if s.kind == "type"]
    defined = (", ".join(types) + (", Other (pages that match none of these)" if types else "")
               if types else "None. Only the All segment is reported.")
    return [
        ["Data lag", "Search Console data is final about two to three days after a day ends. "
                     "The last final date is used, never a later one."],
        ["Complete periods only", "Months are calendar months and weeks run Monday to Sunday. "
                                  "A period is reported only after its last day has final data. "
                                  "A partial month or week is never shown as if it were complete."],
        ["Closed periods", "Once a month or week is written it is not rewritten. The only "
                           "exception is the AIO columns, which are filled in when the AI "
                           "Overview export for that month is imported."],
        ["All and segments", "All totals come from a date-only query, like the Search Console "
                             "page with no filter. Segment totals use a page filter. Page types "
                             "can overlap, so segment rows do not add up to All."],
        ["Anonymized queries", "Query-level data leaves out anonymized queries. A keyword that is "
                               "'lost' or 'new' is lost from, or new to, the data Search Console "
                               "returns, not necessarily from Google."],
        ["Changes", "MoM compares with the previous month, WoW with the previous week and YoY "
                    "with the same month (or ISO week) a year earlier. Percentages are empty when "
                    "the comparison base is 0 or when Search Console no longer holds that period "
                    "(it keeps about 16 months)."],
        ["Alarms", f"R Alarms keeps at most {int(th['alarm_max_per_period'])} rows per period: high "
                   f"severity first, then the biggest impact (the clicks involved, in the last "
                   f"column). Only the {int(th['alarm_page_loss_shown'])} page losses with the "
                   f"most clicks lost are listed, followed by one line that counts the others. "
                   f"The complete list of page changes is in R Pages."],
        ["AI Overviews","Search Console has no API for the Generative AI report. Each month its "
                         "export is copied into this sheet as 'AIO YYYY-MM Pages' and 'AIO "
                         "YYYY-MM Dates' tabs. They are read on the next run, stored in AIO Raw "
                         "and removed. Months without an import have empty AIO columns."],
        ["Page types", defined],
    ]


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #

def _pad(row, n):
    return list(row) + [""] * (n - len(row))


def run_reports(sheets, reports_id: str, site, gsc_fetch, final, today, log=print,
                rebuild_alarms: bool = False) -> ReportsResult:
    """
    sheets      authorised Sheets service           reports_id  the site's Reports spreadsheet
    site        sites.Site (page_types, thresholds, weekly_segments)
    gsc_fetch   callable(dimensions, first_date, last_date, filter_groups=None) -> list of rows
    final       last final date in Search Console   today       date of the run
    rebuild_alarms  recompute R Alarms for every past period with the current rules
    """
    th = {k: site.threshold(k) for k in sites.THRESHOLD_DEFAULTS}
    segments = build_segments(site.page_types)
    result = ReportsResult()

    titles, _ = history.grid_info(sheets, reports_id)
    for name, header, _ in TABS:
        tabs.ensure_tab(sheets, reports_id, titles, name, header)

    # ---- AI Overview imports first, so every table below can use them -----
    imp = aio_import.run_import(sheets, reports_id, today, log)
    result.aio_months = len(imp.changed)
    aio_daily, aio_pages = aio_import.load_raw(sheets, reports_id)
    aio = AioData(aio_daily, aio_pages)

    new_alarms = [alarms.import_problem(p, msg) for p, msg in imp.problems]

    # R Alarms written by older rules has no Impact column: rebuild it for all past periods
    old_header = tabs.read_rows(sheets, reports_id, ALARMS, "E", first_row=1)[:1]
    migrate = bool(old_header) and old_header[0] != alarms.HEADER
    rebuild = rebuild_alarms or migrate
    if migrate and len(old_header[0]) < len(alarms.HEADER) and titles.get(ALARMS) is not None:
        tabs.add_columns(sheets, reports_id, titles[ALARMS], len(alarms.HEADER) - len(old_header[0]))
    cap = int(th["alarm_max_per_period"])

    # ---- Periods ------------------------------------------------------------
    cache = {}

    def fetch(dims, first, last, groups=None):
        key = (tuple(dims), first, last, json.dumps(groups, sort_keys=True))
        if key not in cache:
            cache[key] = gsc_fetch(dims, first, last, groups)
        return cache[key]

    earliest = today - timedelta(days=history.BACKFILL_DAYS)
    all_series = daily_series(fetch(["date"], earliest, final))
    active = sorted(d for d, (_, n, _) in all_series.items() if n > 0)
    if not active:
        log("  reports: no Search Console data in range yet")
        result.note = "no data yet"
        _finish(sheets, reports_id, segments, new_alarms, rebuild, cap, th, result, log)
        return result
    first_data = active[0]

    months = periods.complete_months(earliest, final, first_data)
    weeks = periods.complete_weeks(earliest, final, first_data)
    month_by_label = {m[0]: m for m in months}
    week_by_label = {w[0]: w for w in weeks}

    existing = {name: tabs.read_rows(sheets, reports_id, name, last)
                for name, _, last in TABS if name in (MONTHLY, WEEKLY, KEYWORDS, PAGES, AIO_PAGES)}
    series = {"All": all_series}

    def segment_series(seg):
        if seg.name not in series:
            series[seg.name] = daily_series(fetch(["date"], earliest, final, seg.filter_groups()))
        return series[seg.name]

    # ---- R Monthly / R Weekly: totals ---------------------------------------
    def build_table(tab, items, segs, kind):
        have = {(r[0], r[1]) for r in existing[tab] if len(r) > 1}
        out = []
        for label, first, last in items:
            for seg in segs:
                if (label, seg.name) in have:
                    continue
                s = segment_series(seg)
                if kind == "month":
                    prev_first = periods.add_months(first, -1)
                    prev = (totals(s, prev_first, periods.month_end(prev_first))
                            if periods.month_label(prev_first) in month_by_label else None)
                    yfirst = periods.add_months(first, -12)
                    yoy = (totals(s, yfirst, periods.month_end(yfirst))
                           if periods.month_label(yfirst) in month_by_label else None)
                    a = aio.month(label, first, last, seg)
                    pa = aio.month(periods.month_label(prev_first), prev_first,
                                   periods.month_end(prev_first), seg)
                else:
                    pfirst = first - timedelta(days=7)
                    prev = (totals(s, pfirst, pfirst + timedelta(days=6))
                            if periods.week_label(pfirst) in week_by_label else None)
                    yfirst = periods.same_week_last_year(first)
                    yoy = (totals(s, yfirst, yfirst + timedelta(days=6))
                           if yfirst and periods.week_label(yfirst) in week_by_label else None)
                    a = aio.week(first, last, seg)
                    pa = aio.week(pfirst, pfirst + timedelta(days=6), seg)
                out.append(build_row(label, seg.name, totals(s, first, last), prev, yoy, a, pa))
        return out

    monthly_new = build_table(MONTHLY, months, segments, "month")
    weekly_segs = segments if site.weekly_segments else segments[:1]
    weekly_new = build_table(WEEKLY, weeks, weekly_segs, "week")
    tabs.append(sheets, reports_id, MONTHLY, monthly_new, log)
    tabs.append(sheets, reports_id, WEEKLY, weekly_new, log)
    result.months = len({r[0] for r in monthly_new})
    result.weeks = len({r[0] for r in weekly_new})

    # ---- AIO columns of rows that already exist -----------------------------
    seg_by_name = {s.name: s for s in segments}
    updates = []
    for tab, rows, kind in ((MONTHLY, existing[MONTHLY], "month"), (WEEKLY, existing[WEEKLY], "week")):
        for i, row in enumerate(rows):
            row = _pad(row, len(MONTHLY_HEADER))
            seg = seg_by_name.get(row[1])
            if seg is None:
                continue
            if kind == "month" and row[0] in month_by_label:
                _, first, last = month_by_label[row[0]]
                pfirst = periods.add_months(first, -1)
                want = aio_columns(aio.month(row[0], first, last, seg),
                                   aio.month(periods.month_label(pfirst), pfirst,
                                             periods.month_end(pfirst), seg))
            elif kind == "week" and row[0] in week_by_label:
                _, first, last = week_by_label[row[0]]
                pfirst = first - timedelta(days=7)
                want = aio_columns(aio.week(first, last, seg),
                                   aio.week(pfirst, pfirst + timedelta(days=6), seg))
            else:
                continue
            have = [row[c] for c in AIO_COLS]
            if all(tabs.same(h, w) for h, w in zip(have, want)):
                continue
            r = i + 2
            updates += [(f"'{tab}'!G{r}", [[want[0]]]), (f"'{tab}'!L{r}:M{r}", [want[1:]])]
            result.patched_cells += 3
    tabs.patch_cells(sheets, reports_id, updates)

    # ---- R Keywords / R Pages ----------------------------------------------
    kw_done = {r[0] for r in existing[KEYWORDS] if r}
    pg_done = {r[0] for r in existing[PAGES] if r}
    for label, first, last in months:
        prev_label = periods.month_label(periods.add_months(first, -1))
        if prev_label not in month_by_label:
            continue
        pfirst, plast = month_by_label[prev_label][1:]

        # Alarms are worked out for the periods written in this run, or for every period
        # when R Alarms is being rebuilt (that needs the data again, but writes no data rows).
        if label not in kw_done or rebuild:
            cur = by_key(fetch(["query"], first, last))
            prev = by_key(fetch(["query"], pfirst, plast))
            rows, lost = keyword_rows(label, cur, prev, th)
            if label not in kw_done:
                tabs.append(sheets, reports_id, KEYWORDS, rows, log)
                result.keyword_periods += 1
            new_alarms += alarms.lost_keywords(label, prev_label, lost, th)

        if label not in pg_done or rebuild:
            cur = by_page(fetch(["page"], first, last))
            prev = by_page(fetch(["page"], pfirst, plast))
            if label not in pg_done:
                ylabel = periods.month_label(periods.add_months(first, -12))
                yoy = None
                if ylabel in month_by_label:
                    yoy = by_page(fetch(["page"], *month_by_label[ylabel][1:]))
                tabs.append(sheets, reports_id, PAGES,
                            page_rows(label, cur, prev, yoy, segments, th), log)
                result.page_periods += 1
            new_alarms += alarms.page_losses(
                label, prev_label,
                [(u, cur.get(u, (0,))[0], v[0]) for u, v in sorted(prev.items())], th)
            if site.page_types:
                other = next(s for s in segments if s.kind == "other")
                new_alarms += alarms.new_url_patterns(
                    label, [u for u in cur if other.matches(u)],
                    [u for u in prev if other.matches(u)], th)

    # ---- Alarms from the totals of the months in question ------------------
    segment_months = sorted(month_by_label) if rebuild else sorted({r[0] for r in monthly_new})
    for label in segment_months:
        prev_label = periods.month_label(periods.add_months(month_by_label[label][1], -1))
        if prev_label not in month_by_label:
            continue
        for seg in segments:
            s = segment_series(seg)
            first, last = month_by_label[label][1:]
            pfirst, plast = month_by_label[prev_label][1:]
            new_alarms += alarms.segment_change(
                label, prev_label, seg.name,
                totals(s, first, last)[0], totals(s, pfirst, plast)[0], th)

    # ---- R AIO Pages and the AIO alarm -------------------------------------
    _aio_pages(sheets, reports_id, aio, existing[AIO_PAGES], int(th["movers_top_n"]), log)
    all_seg = segments[0]
    aio_months = ({periods.month_label(d) for d in aio.daily} | set(aio.pages)) if rebuild \
        else imp.changed
    for label in sorted(aio_months | {periods.month_label(periods.add_months(
            periods.parse_month(m), 1)) for m in aio_months}):
        first = periods.parse_month(label)
        pfirst = periods.add_months(first, -1)
        new_alarms += alarms.aio_drop(
            label, periods.month_label(pfirst),
            aio.month(label, first, periods.month_end(first), all_seg),
            aio.month(periods.month_label(pfirst), pfirst, periods.month_end(pfirst), all_seg), th)

    _finish(sheets, reports_id, segments, new_alarms, rebuild, cap, th, result, log)
    return result


def _aio_pages(sheets, reports_id, aio: AioData, existing: list, top_n: int, log):
    """Rebuilds the R AIO Pages rows of every month that has AIO page data for itself and
    the month before; rewrites the tab only when something changed."""
    wanted = {}
    for label in sorted(aio.pages):
        prev = periods.month_label(periods.add_months(periods.parse_month(label), -1))
        if prev in aio.pages:
            wanted[label] = aio_page_rows(label, aio.pages[label], aio.pages[prev], top_n)
    if not wanted:
        return
    have = {}
    for r in existing:
        r = _pad(r, 6)
        have.setdefault(r[0], []).append(r[:3] + [tabs.number(c) for c in r[3:6]])
    changed = any(
        len(have.get(p, [])) != len(rows) or any(
            not all(tabs.same(a, b) for a, b in zip(x, y)) for x, y in zip(have.get(p, []), rows))
        for p, rows in wanted.items())
    if not changed:
        return
    merged = {p: rows for p, rows in have.items() if p not in wanted}
    merged.update(wanted)
    table = [row for p in sorted(merged) for row in merged[p]]
    tabs.rewrite(sheets, reports_id, AIO_PAGES, AIO_PAGES_HEADER, table)


def _save_alarms(sheets, reports_id, candidates: list, rebuild: bool, cap: int,
                 result: ReportsResult):
    """
    Merges the new alarms into R Alarms, period by period: a period that gets new alarms is
    re-sorted (high severity first, then the biggest impact) and cut to `cap` rows. Periods
    without new alarms stay as they are. With `rebuild` the tab is made from the candidates
    alone (they cover every past period). The tab is rewritten only when something changed.
    """
    old = [] if rebuild else [alarms.from_row(r)
                              for r in tabs.read_rows(sheets, reports_id, ALARMS, "E") if len(r) >= 4]
    by_old, by_new = {}, {}
    for a in old:
        by_old.setdefault(a.period, []).append(a)
    for a in candidates:
        by_new.setdefault(a.period, []).append(a)

    final = {}
    for period in set(by_old) | set(by_new):
        if period in by_new:
            final[period] = alarms.cap_period(by_old.get(period, []) + by_new[period], cap)
        else:
            final[period] = by_old[period]

    ordered = [a for p in sorted(final) for a in final[p]]
    old_rows = [a.row() for p in sorted(by_old) for a in by_old[p]]
    new_rows = [a.row() for a in ordered]
    same_rows = len(old_rows) == len(new_rows) and all(
        all(tabs.same(x, y) for x, y in zip(r1, r2)) for r1, r2 in zip(old_rows, new_rows))
    if rebuild or not same_rows:
        tabs.rewrite(sheets, reports_id, ALARMS, alarms.HEADER, new_rows)
    known = {a.key for a in old}
    result.alarms = sum(1 for a in ordered if a.key not in known)
    for a in ordered:
        result.alarm_counts[a.area] = result.alarm_counts.get(a.area, 0) + 1


def _finish(sheets, reports_id, segments, new_alarms: list, rebuild: bool, cap: int, th: dict,
            result: ReportsResult, log):
    """Saves the alarms and refreshes the Notes tab."""
    _save_alarms(sheets, reports_id, new_alarms, rebuild, cap, result)
    tabs.rewrite(sheets, reports_id, NOTES, ["Topic", "Explanation"], notes_rows(segments, th))
    log(f"  reports: {result.summary()}")
    if rebuild:
        log("  R Alarms rebuilt for all periods: " + ", ".join(
            f"{area} {n}" for area, n in sorted(result.alarm_counts.items())))
