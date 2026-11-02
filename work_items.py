"""
PulseO: "Our work and its results"
=================================
What was done on a site, and what it achieved, for the monthly client deck (the best results) and for the
weekly deck of the owner (everything of the last 90 days, including what did not help).

Where the work items come from
  * Automatic, from data the tool already has, kept in the `_state` tab of the site's content dashboard
    (kind `seen`: the first day a page was seen and the last `lastmod` the sitemap gave; kind `work`: one
    row per event found). A page seen for the first time is a NEW PAGE; a page whose sitemap `lastmod`
    moved on, or whose row in the content dashboard has a Published/Updated Date, is an UPDATED PAGE; a
    row whose Status turned to Published is a new page. The first run only records what is there (the
    first-seen dates come from the stored Search Console history), it does not announce every page.
  * Manual, for work the data cannot show (technical fixes, redirects, schema, internal links): the tab
    "Our Work" of the site's Reports sheet: Date | Page or section | What was done | Show to client.
    The tool only reads it.

How the result is measured
  For an item dated D and a window of L days (28, or less when the change is fresh): the clicks,
  impressions, average position and ranking searches of its page(s) in the L days before D against the L
  days from D on, side by side with all the OTHER pages of the site over the same two windows. "Beat the
  site" means the page grew more than the rest of the site. A page that was redirected is measured
  together with the old address(es) that redirect to it, so a moved page is never taken for a new one.
  Fewer than 21 days of "after" data: early results (shown if positive); fewer than 7: no result yet.
  The stored history is per page, so the numbers include brand searches (only the ranking-search count
  leaves them out).

Nothing here calls an AI, and nothing here writes the Reports sheet.
"""

import fnmatch
import re
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import content
import history
import tabs

OUR_WORK_TAB = "Our Work"
OUR_WORK_HEADER = ["Date", "Page or section", "What was done", "Show to client"]
WHOLE_SITE = ("whole site", "کل سایت", "all pages", "sitewide", "site-wide")

WINDOW_DAYS = 28
EARLY_DAYS = 21              # fewer days of "after" data: early results
MIN_DAYS = 7                 # fewer: nothing to say yet
LOOKBACK_CLIENT = 120        # days back a change may be dated for the client deck
LOOKBACK_WEEKLY = 90
MAX_CLIENT_ITEMS = 8
MIN_GAIN_CLICKS = 5          # a result needs at least this many more clicks ...
MIN_GAIN_PCT = 0.10          # ... and at least this much growth (when there was something before)
MIN_NEW_CLICKS = 5           # a new page counts once it has this many clicks
POSITION_GAIN = 1.0          # or a better average position by this much, without losing clicks
MERGE_DAYS = 3               # events of one page this close are one piece of work
BASELINE_NEW_AFTER = 14      # at the first run: a page first seen this long after the history began is new

NEW, UPDATED, MANUAL = "new", "updated", "manual"
GOOD, EARLY, FLAT, NODATA = "good", "early", "flat", "nodata"


# --------------------------------------------------------------------------- #
# Items and measures
# --------------------------------------------------------------------------- #

@dataclass
class Totals:
    clicks: float = 0.0
    impressions: float = 0.0
    position: float = None            # impression-weighted average, None without impressions
    queries: int = None               # ranking searches (None when not counted)


@dataclass
class Impact:
    days: int                         # length of each window
    before: Totals
    after: Totals
    site_before: float                # clicks of all the other pages
    site_after: float
    status: str                       # good | early | flat | nodata
    positive: bool = False

    @property
    def gain(self) -> float:
        return self.after.clicks - self.before.clicks

    @property
    def change(self):
        return content.change(self.after.clicks, self.before.clicks) if self.before.clicks else None

    @property
    def site_change(self):
        return content.change(self.site_after, self.site_before) if self.site_before else None

    @property
    def beat_site(self):
        """True when the page grew more than the rest of the site; None when that cannot be said."""
        if self.site_change is None:
            return None
        mine = self.change if self.change is not None else (1.0 if self.after.clicks > 0 else None)
        return None if mine is None else mine > self.site_change


@dataclass
class WorkItem:
    kind: str                         # new | updated | manual
    day: date
    scope: str = ""                   # an address, a pattern like /article/*, "whole site"
    what: str = ""                    # manual items: the owner's words
    title: str = ""                   # automatic items: the page's keyword, or its path
    show: bool = True                 # "Show to client"
    impact: Impact = None
    pages: list = field(default_factory=list)       # the page keys the numbers cover (for the tests and the log)


# --------------------------------------------------------------------------- #
# Reading the manual tab
# --------------------------------------------------------------------------- #

def ensure_our_work_tab(sheets, reports_id: str):
    titles, _ = history.grid_info(sheets, reports_id)
    tabs.ensure_tab(sheets, reports_id, titles, OUR_WORK_TAB, OUR_WORK_HEADER)


def read_manual(sheets, reports_id: str) -> list:
    """The rows of "Our Work" as WorkItems (a row without a readable date or text is skipped)."""
    titles, _ = history.grid_info(sheets, reports_id)
    if OUR_WORK_TAB not in titles:
        return []
    out = []
    for raw in tabs.read_rows(sheets, reports_id, OUR_WORK_TAB, "D"):
        cells = [str(c).strip() if c is not None else "" for c in list(raw) + [""] * (4 - len(raw))]
        day = content.parse_date(cells[0])
        if day is None or not cells[2]:
            continue
        show = cells[3].lower() not in ("no", "n", "false", "0", "نه", "خیر")
        out.append(WorkItem(MANUAL, day, scope=cells[1], what=cells[2], show=show))
    return out


# --------------------------------------------------------------------------- #
# Finding the automatic items
# --------------------------------------------------------------------------- #

def _pack(first: str, lastmod: str, status: str, url: str) -> str:
    return "|".join((first, lastmod, status, url))


def _unpack(value: str) -> tuple:
    parts = (str(value).split("|", 3) + ["", "", "", ""])[:4]
    return tuple(parts)


def scan(state, today: date, entries: dict, sitemap_ok: bool, rows: list, tab_matches,
         first_seen: dict = None) -> int:
    """Looks for new and updated pages and records what it finds in `state` (the content dashboard's State).
    `entries` {address: lastmod} from the sitemap; `rows` the dashboard rows (dicts); `tab_matches(url)` is
    True for a content page; `first_seen` {address: 'YYYY-MM-DD'} from the stored history, used at the
    first run only. Returns the number of events found this time."""
    pages = {}                                              # url_key -> (url, lastmod, status, updated_cell)
    if sitemap_ok:
        for url, last in entries.items():
            if tab_matches(url):
                pages[content.url_key(url)] = [url, last, "", None]
    for r in rows:
        url = str(r.get(content.URL, "")).strip()
        if not url or not tab_matches(url):
            continue
        p = pages.setdefault(content.url_key(url), [url, "", "", None])
        p[2] = str(r.get(content.STATUS, "")).strip()
        p[3] = content.parse_date(r.get("Published/Updated Date"))
    baseline = not state.keys({"seen"})
    seen_in_history = {}
    for u, d in (first_seen or {}).items():
        k = content.url_key(u)
        if k not in seen_in_history or d < seen_in_history[k]:
            seen_in_history[k] = d
    history_start = min(seen_in_history.values()) if seen_in_history else ""
    found = 0

    def event(kind, key, day, url):
        nonlocal found
        row_key = f"{kind}|{key}|{day.isoformat()}"
        if not state.get("work", row_key):
            state.set("work", row_key, url, today)
            found += 1

    def near(first_day: str, day: date) -> bool:
        return bool(first_day) and abs((day - date.fromisoformat(first_day)).days) <= MERGE_DAYS

    for key, (url, last, status, upd) in pages.items():
        old = state.get("seen", key)
        d_last = content.parse_date(last)
        if not old:                                          # a page the state has never seen
            first = ""
            if baseline:                                     # only what the history shows as new, not every page
                seen = seen_in_history.get(key, "")
                if seen and history_start and seen > (date.fromisoformat(history_start)
                                                      + timedelta(days=BASELINE_NEW_AFTER)).isoformat():
                    first = seen
            else:
                first = (d_last if d_last and 0 <= (today - d_last).days <= 10 else today).isoformat()
            if first:
                event(NEW, key, date.fromisoformat(first), url)
            if baseline and d_last and 0 <= (today - d_last).days <= LOOKBACK_CLIENT and not near(first, d_last):
                event(UPDATED, key, d_last, url)
            first_day, prev_last, prev_status = first, last, status
        else:
            first_day, prev_last, prev_status, _ = _unpack(old)
            if d_last and last != prev_last and not near(first_day, d_last):
                event(UPDATED, key, d_last, url)
        if upd and 0 <= (today - upd).days <= LOOKBACK_CLIENT and not near(first_day, upd):
            event(UPDATED, key, upd, url)
        if old and status.lower() == "published" and prev_status and prev_status.lower() != "published":
            event(NEW, key, today, url)
        state.set("seen", key, _pack(first_day, last or prev_last, status, url), today)
    return found


def auto_items(state, today: date, lookback: int, titles: dict = None) -> list:
    """The events recorded in `state` that are at most `lookback` days old, one item per page and piece of
    work (events of one page within MERGE_DAYS become one; a new page wins over an update)."""
    titles = titles or {}
    raw = []
    for r in state.rows:
        if r[0] != "work":
            continue
        kind, key, day = (r[1].split("|", 2) + ["", "", ""])[:3]
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        if 0 <= (today - d).days <= lookback:
            raw.append((key, d, kind, r[2]))
    raw.sort(key=lambda e: (e[0], e[1], 0 if e[2] == NEW else 1))
    out = []
    for key, d, kind, url in raw:
        if out and out[-1][0] == key and (d - out[-1][1]).days <= MERGE_DAYS:
            continue
        out.append((key, d, kind, url))
    return [WorkItem(kind, d, scope=url, title=titles.get(key) or content.path_of(url))
            for key, d, kind, url in out]


# --------------------------------------------------------------------------- #
# The numbers
# --------------------------------------------------------------------------- #

class PageDays:
    """The stored per-page days as a table: rows (day, page key, clicks, impressions, position)."""

    def __init__(self, rows: list):
        self.rows = [(d, content.url_key(p), c, n, pos) for d, p, c, n, pos in rows]
        self.keys = {r[1] for r in self.rows}
        self.last = max((r[0] for r in self.rows), default=None)

    def totals(self, first: date, last: date, keys: set = None, others_of: set = None) -> Totals:
        """Clicks, impressions and weighted position of the pages in `keys` (all pages when None), or of all
        pages except `others_of`, over first..last."""
        clicks = impressions = weighted = 0.0
        for d, k, c, n, pos in self.rows:
            if d < first or d > last:
                continue
            if keys is not None and k not in keys:
                continue
            if others_of is not None and k in others_of:
                continue
            clicks += c
            impressions += n
            weighted += pos * n
        return Totals(clicks, impressions, round(weighted / impressions, 2) if impressions else None)


def redirect_map(state, rows: list = None) -> dict:
    """{target page key: {keys of the pages that redirect to it}} from the content state (pages found
    redirecting) and from the dashboard rows with Status Redirected (the new address is in Alerts)."""
    out = {}
    for r in state.rows:
        if r[0] == "gone" and str(r[2]).startswith("redirect|"):
            out.setdefault(content.url_key(r[2].split("|", 1)[1]), set()).add(content.url_key(r[1]))
    for r in rows or []:
        if str(r.get(content.STATUS, "")).strip().lower() != "redirected":
            continue
        src = str(r.get(content.URL, "")).strip()
        found = re.search(r"https?://\S+", str(r.get("Alerts", "")))
        if src and found:
            out.setdefault(content.url_key(found.group(0).rstrip(".,;)")), set()).add(content.url_key(src))
    return out


def scope_keys(scope: str, table: PageDays, redirects: dict) -> tuple:
    """(page keys, whole_site) an item covers. An address (and the pages that redirect to it), a pattern with *
    over the path, or the whole site. Anything else covers nothing."""
    text = str(scope or "").strip()
    if text.lower() in WHOLE_SITE:
        return set(table.keys), True
    if text.startswith("http"):
        key = content.url_key(text)
        return {key} | redirects.get(key, set()), False
    if text.startswith("/"):
        if "*" in text:
            hit = {k for k in table.keys if fnmatch.fnmatchcase(content.path_of(k), text)}
        else:
            hit = {k for k in table.keys if content.path_of(k) == content.path_of(text)}
        for k in list(hit):
            hit |= redirects.get(k, set())
        return hit, False
    return set(), False


def measure(item: WorkItem, table: PageDays, final: date, redirects: dict, queries=None) -> Impact:
    """Fills item.impact (and item.pages). `queries(item, first, last) -> int` counts the ranking searches of an
    set of pages in a window (optional)."""
    keys, whole = scope_keys(item.scope, table, redirects)
    item.pages = sorted(keys)
    days = min(WINDOW_DAYS, (final - item.day).days + 1)
    if not keys or days < MIN_DAYS:
        item.impact = Impact(max(days, 0), Totals(), Totals(), 0.0, 0.0, NODATA)
        return item.impact
    b1, b2 = item.day - timedelta(days=days), item.day - timedelta(days=1)
    a1, a2 = item.day, item.day + timedelta(days=days - 1)
    before, after = table.totals(b1, b2, keys=keys), table.totals(a1, a2, keys=keys)
    if queries:
        before.queries, after.queries = queries(item, b1, b2), queries(item, a1, a2)
    if whole:
        site_b = site_a = 0.0
    else:
        site_b = table.totals(b1, b2, others_of=keys).clicks
        site_a = table.totals(a1, a2, others_of=keys).clicks
    status, positive = classify(item.kind, before, after, days)
    item.impact = Impact(days, before, after, site_b, site_a, status, positive)
    return item.impact


def classify(kind: str, before: Totals, after: Totals, days: int) -> tuple:
    """(status, positive): good (a clear gain), early (fresh: fewer than EARLY_DAYS of data), flat (no clear gain)."""
    gain = after.clicks - before.clicks
    if before.clicks:
        grew = gain >= MIN_GAIN_CLICKS and gain / before.clicks >= MIN_GAIN_PCT
    else:
        grew = after.clicks >= MIN_NEW_CLICKS
    better_position = (before.position is not None and after.position is not None
                       and before.position - after.position >= POSITION_GAIN and gain >= 0)
    clear = grew or better_position
    if days < EARLY_DAYS:
        return EARLY, gain > 0
    return (GOOD if clear else FLAT), clear


def evaluate(items: list, table: PageDays, final: date, redirects: dict, queries=None) -> list:
    for it in items:
        measure(it, table, final, redirects, queries)
    return items


# --------------------------------------------------------------------------- #
# Who sees what
# --------------------------------------------------------------------------- #

def for_client(items: list, today: date) -> list:
    """The items of the client deck: shown to the client, dated within LOOKBACK_CLIENT days, with a clear gain
    (or fresh and positive), best result first, at most MAX_CLIENT_ITEMS. What did not help is left out."""
    shown = [it for it in items
             if it.show and it.impact and 0 <= (today - it.day).days <= LOOKBACK_CLIENT
             and ((it.impact.status == GOOD) or (it.impact.status == EARLY and it.impact.positive))]
    shown.sort(key=lambda it: (-it.impact.gain, it.day, it.scope))
    return shown[:MAX_CLIENT_ITEMS]


def for_owner(items: list, today: date) -> list:
    """Every item of the last LOOKBACK_WEEKLY days that has a number to show, whatever it achieved (the ones
    that did not help too), newest first."""
    shown = [it for it in items if it.impact and it.impact.status != NODATA
             and 0 <= (today - it.day).days <= LOOKBACK_WEEKLY]
    shown.sort(key=lambda it: (it.day, it.scope), reverse=True)
    return shown
