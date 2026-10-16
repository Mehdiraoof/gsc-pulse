"""
GSC Pulse: alarms
=================
Deterministic rules that turn the report numbers into a short list of things
worth looking at. Messages are plain English; the first slide of a client deck
can be built from them.

Every alarm carries an *impact* (the clicks involved), which orders the list:
per period at most `alarm_max_per_period` rows are kept, high severity first and
then the biggest impact. Page losses are the noisiest rule, so only the biggest
few are listed and the rest become one summary line; the complete list of page
changes lives in `R Pages`.

All thresholds come from the site's configuration (sites.THRESHOLD_DEFAULTS).
"""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

HEADER = ["Period", "Severity", "Area", "Message", "Impact (clicks)"]
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
PAGE_LOSS = "Page loss"
IMPORT_PROBLEM_IMPACT = 10 ** 9          # a tab that could not be imported always comes first


@dataclass(frozen=True)
class Alarm:
    period: str
    severity: str
    area: str
    message: str
    impact: float = 0

    def row(self) -> list:
        return [self.period, self.severity, self.area, self.message, int(round(self.impact))]

    @property
    def key(self) -> tuple:
        return (self.period, self.area, self.message)

    def sort_key(self) -> tuple:
        return (SEVERITY_RANK.get(self.severity, 3), -self.impact, self.area, self.message)

    @property
    def is_summary(self) -> bool:
        return self.area == PAGE_LOSS and self.message.startswith("and ")


def from_row(row: list) -> Alarm:
    """An alarm read back from the sheet (cells arrive as text)."""
    cells = list(row) + [""] * (5 - len(row))
    try:
        impact = float(cells[4])
    except (TypeError, ValueError):
        impact = 0
    return Alarm(str(cells[0]), str(cells[1]), str(cells[2]), str(cells[3]), impact)


def _n(value) -> str:
    """12345 -> '12,345'"""
    return f"{int(round(value)):,}"


def _pct(change: float) -> str:
    return f"{abs(change):.1f}%"


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #

def import_problem(period: str, message: str) -> Alarm:
    return Alarm(period, "high", "AIO import", message, IMPORT_PROBLEM_IMPACT)


def segment_change(period: str, prev_period: str, segment: str, clicks: int, prev_clicks: int,
                   th: dict) -> list:
    """A segment's clicks moved by the configured percentage month on month."""
    if prev_clicks is None or prev_clicks <= 0 or prev_clicks < th["alarm_min_clicks"]:
        return []
    change = (clicks - prev_clicks) / prev_clicks * 100
    if abs(change) < th["alarm_clicks_change_pct"]:
        return []
    severity = "high" if abs(change) >= th["alarm_clicks_high_pct"] else "medium"
    direction = "rose" if change > 0 else "fell"
    return [Alarm(period, severity, "Segment clicks",
                  f"Clicks for {segment} {direction} by {_pct(change)} compared with {prev_period} "
                  f"({_n(prev_clicks)} to {_n(clicks)}).", abs(clicks - prev_clicks))]


def lost_keywords(period: str, prev_period: str, lost: list, th: dict) -> list:
    """lost: [(query, clicks_prev)] for keywords that dropped out of the data this month."""
    return [Alarm(period, "medium", "Lost keyword",
                  f"The keyword \"{query}\" brought {_n(prev_clicks)} clicks in "
                  f"{prev_period} and is not in Search Console's data in {period}.", prev_clicks)
            for query, prev_clicks in lost if prev_clicks >= th["alarm_lost_keyword_clicks"]]


def page_losses(period: str, prev_period: str, pages: list, th: dict) -> list:
    """
    pages: [(url, clicks, clicks_prev)]. The pages that lost most of their clicks: the
    biggest `alarm_page_loss_shown` by clicks lost are listed, the rest become one line.
    """
    found = []
    for url, clicks, prev_clicks in pages:
        if prev_clicks < th["alarm_page_loss_min_clicks"] or prev_clicks <= 0:
            continue
        loss = (prev_clicks - clicks) / prev_clicks * 100
        if loss >= th["alarm_page_loss_pct"]:
            found.append((prev_clicks - clicks, url, clicks, prev_clicks, loss))
    found.sort(key=lambda f: (-f[0], f[1]))
    shown = max(int(th["alarm_page_loss_shown"]), 0)
    out = [Alarm(period, "medium", PAGE_LOSS,
                 f"The page {unquote(url)} lost {_pct(loss)} of its clicks compared with "
                 f"{prev_period} ({_n(prev)} to {_n(clicks)}).", lost)
           for lost, url, clicks, prev, loss in found[:shown]]
    more = len(found) - shown
    if more > 0:
        pct = f"{th['alarm_page_loss_pct']:g}"
        what = "page lost" if more == 1 else "pages lost"
        its = "its" if more == 1 else "their"
        # sorts straight after the pages it summarises
        impact = (found[shown - 1][0] - 0.01) if shown else found[0][0]
        out.append(Alarm(period, "medium", PAGE_LOSS,
                         f"and {more} more {what} {pct}%+ of {its} clicks", impact))
    return out


def aio_drop(period: str, prev_period: str, aio: int, prev_aio: int, th: dict) -> list:
    """AI Overview impressions fell by the configured percentage month on month."""
    if aio is None or prev_aio is None or prev_aio <= 0:
        return []
    drop = (prev_aio - aio) / prev_aio * 100
    if drop < th["alarm_aio_drop_pct"]:
        return []
    return [Alarm(period, "medium", "AI Overviews",
                  f"AI Overview impressions fell by {_pct(drop)} compared with {prev_period} "
                  f"({_n(prev_aio)} to {_n(aio)}).", 0)]


def _groups(urls: list) -> dict:
    """Groups URLs by host and by host + first path segment."""
    groups = {}
    for url in urls:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}/"
        groups.setdefault(("host", host), []).append(url)
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2:                # a lone slug is a page, not a section
            groups.setdefault(("folder", f"{host}{segments[0]}/"), []).append(url)
    return groups


def new_url_patterns(period: str, other_urls: list, prev_other_urls: list, th: dict) -> list:
    """
    Pages that match no page type but share a host or a first path segment: probably a
    section that deserves its own page type. Only reported when the group is new, i.e.
    it was below the threshold in the previous month. The site's main host is not a
    'new pattern'.
    """
    need = th["alarm_new_pattern_pages"]
    now, before = _groups(other_urls), _groups(prev_other_urls)
    hosts = {}
    for (kind, key), urls in _groups(other_urls + prev_other_urls).items():
        if kind == "host":
            hosts[key] = len(urls)
    main_host = max(hosts, key=hosts.get) if hosts else None
    out = []
    for (kind, key), urls in sorted(now.items()):
        if len(urls) < need or len(before.get((kind, key), [])) >= need:
            continue
        if kind == "host" and key == main_host:
            continue
        what = "subdomain" if kind == "host" else "first folder"
        out.append(Alarm(period, "low", "New URL pattern",
                         f"{len(urls)} pages that match no page type share the same {what} "
                         f"({unquote(key)}). This may be a new section: consider a page-type "
                         f"rule.", 0))
    return out


# --------------------------------------------------------------------------- #
# Keeping the list short
# --------------------------------------------------------------------------- #

def cap_period(alarms: list, cap: int) -> list:
    """
    The alarms of one period, de-duplicated, ordered (high severity first, then the biggest
    impact) and cut to `cap` rows. If the cut would keep page losses but drop the line that
    says how many more there are, that line is kept and the lowest-ranked other row goes.
    """
    unique = {}
    for a in alarms:
        unique.setdefault(a.key, a)
    ordered = sorted(unique.values(), key=Alarm.sort_key)
    cap = max(int(cap), 1)
    if len(ordered) <= cap:
        return ordered
    kept = ordered[:cap]
    summary = next((a for a in ordered if a.is_summary), None)
    if summary and summary not in kept and any(a.area == PAGE_LOSS for a in kept):
        drop = next((i for i in range(len(kept) - 1, -1, -1) if kept[i].area != PAGE_LOSS),
                    len(kept) - 1)
        del kept[drop]
        kept.append(summary)
        kept.sort(key=Alarm.sort_key)
    return kept
