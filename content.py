"""
PulseO: the content dashboard, rules (v2.5)
============================================
A content database per site, one row per published page: the keywords it should target, how
it performs, what to do with it, plus rows for new topics the site has no page for.

This module holds only the rules, as pure functions of plain data (no Google calls), so every
rule is easy to test with invented numbers. content_sheet.py draws the file, content_run.py
reads the data and writes the file.

Product rule: the file is the owner's own work. No tool name appears in any cell, header, note
or guide text written here.

Who owns which cell (see the OWNED_* lists): PulseO writes the performance columns every month;
the planning columns (status, to do, keywords, notes, dates ...) are the owner's and are never
overwritten after the row exists. Two cells (Query Fan-outs, Recommendations) are filled only
while empty. Columns are always found by their header name, never by letter.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import unquote, urlsplit

import opportunities

# --------------------------------------------------------------------------- #
# The columns, A to AP
# --------------------------------------------------------------------------- #

HEADERS = [
    "Primary Keyword", "Published URL", "Status", "To Do", "Priority", "Score",
    "Clicks (90d)", "Impressions (90d)", "CTR", "Avg. Position", "Clicks MoM", "Clicks YoY",
    "AIO Impressions", "Primary KW Impr.", "Primary KW Pos.", "Ranking Queries",
    "Secondary Keywords", "Query Fan-outs", "Entities", "Competing Pages",
    "Intent", "Keyword Type", "Content Type", "SERP Features", "Meta Title",
    "One-Sentence Description", "Pain Point Solved", "Outlines", "Slug",
    "Words Count (Suggested)", "Attachments", "Related URLs", "PIC", "Briefed Date",
    "Published/Updated Date", "Impact Since Update", "Published Title", "Notes/Checklist",
    "Suggested Action", "Why", "Alerts", "Recommendations",
]
KEY, URL, STATUS, TODO, PRIORITY, SCORE = HEADERS[:6]
PUBLISHED_DATE, IMPACT = "Published/Updated Date", "Impact Since Update"
FANOUTS, RECOMMENDATIONS = "Query Fan-outs", "Recommendations"
SLUG = "Slug"

# written every month, whatever the cell holds
OWNED_MONTHLY = [PRIORITY, SCORE, "Clicks (90d)", "Impressions (90d)", "CTR", "Avg. Position",
                 "Clicks MoM", "Clicks YoY", "AIO Impressions", "Primary KW Impr.",
                 "Primary KW Pos.", "Ranking Queries", "Secondary Keywords", "Competing Pages",
                 IMPACT, "Suggested Action", "Why", "Alerts"]
# written only while the cell is empty
FILL_IF_EMPTY = [FANOUTS, RECOMMENDATIONS]
# written when the row is created, never again
ON_CREATE = [KEY, URL, STATUS, TODO, "Intent", "Keyword Type", "Content Type", SLUG]

STATUSES = ["Inboxed", "Briefed", "Ordered", "Published", "Later", "Gone"]
TODOS = ["Brief", "Write", "Update", "Rewrite", "Optimization", "Merge", "Redirect", "Keep", "Prune"]
PRIORITIES = ["Urgent", "High", "Moderate", "Low"]
INTENTS = ["Informational", "Commercial", "Navigational", "Transactional"]
KEYWORD_TYPES = ["General", "Seasonal", "Local", "Time-Sensitive"]
CONTENT_TYPES = ["Pillar", "Cornerstone", "Cluster"]

NEW_PAGE_MIN_IMPRESSIONS = 1       # a page with fewer impressions in 90 days does not get a row
SECONDARY_COUNT = 8
FANOUT_COUNT = 6
COMPETING_MIN_IMPRESSIONS = 20
COMPETING_SHOWN = 3
MAX_GAPS_PER_TAB = 20
GONE_MONTHS = 2
DECAY_MONTHS = 3
IMPACT_DAYS = 28
SEASON_LEAD_DAYS = 42
LARGE_PAGE = 2000                  # impressions from which a page is a cornerstone

# --------------------------------------------------------------------------- #
# Text: URLs and Persian
# --------------------------------------------------------------------------- #

ZWNJ = "‌"
_FA = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه",
                     "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
                     "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
                     "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
                     "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})
_STRIP = re.compile("[‌‍ـً-ٰٟ]")       # ZWNJ, ZWJ, tatweel, harakat
STOPWORDS = {"و", "در", "به", "از", "با", "برای", "که", "را", "این", "آن", "یک", "چیست", "است",
             "the", "a", "an", "of", "to", "in", "for", "and", "on", "is", "what", "how", "why"}

FA_QUESTIONS = ["چیست", "چه", "کیست", "چگونه", "چطور", "چرا", "کجا", "کجاست", "یعنی", "آیا",
                "چند", "چیه", "چی", "کی"]
EN_QUESTIONS = ["what", "how", "why", "who", "when", "where", "is", "can", "does"]
_QUESTION = re.compile(
    r"(?<![\w])(?:" + "|".join(map(re.escape, FA_QUESTIONS + EN_QUESTIONS)) + r")(?![\w])",
    re.IGNORECASE)


def norm(text: str) -> str:
    """Persian-safe form for comparing searches: ي→ی, ك→ک, no ZWNJ/tatweel, digits as 0-9,
    lower case, one space between words."""
    t = _STRIP.sub("", str(text).translate(_FA)).lower()
    return " ".join(t.split())


def words(text: str) -> set:
    return {w for w in re.split(r"[^\w]+", norm(text)) if len(w) >= 2 and w not in STOPWORDS}


def decode_url(url: str) -> str:
    """A page address as readable text: percent-decoded (UTF-8), without #fragment."""
    return unquote(re.sub(r"#.*$", "", str(url).strip()), errors="replace")


def url_key(url: str) -> str:
    """What two rows must share to be the same page: decoded, no fragment."""
    return decode_url(url)


def dup_key(url: str) -> str:
    """Equal for URLs that differ only by case, a trailing ZWNJ or a trailing slash."""
    u = decode_url(url).replace(ZWNJ, "").lower()
    return u.rstrip("/")


def path_of(url: str) -> str:
    parts = urlsplit(decode_url(url))
    return (parts.path or "/") + (("?" + parts.query) if parts.query else "")


def slug_of(url: str) -> str:
    return path_of(url)


def is_question(query: str) -> bool:
    return bool(_QUESTION.search(norm(query)))


def is_brand(query: str, brand_terms: list) -> bool:
    q = norm(query)
    return any(norm(t) and norm(t) in q for t in brand_terms)


def skip_query(query: str, brand_terms: list) -> bool:
    """Brand searches and searches for an address are not topics."""
    return is_brand(query, brand_terms) or opportunities.looks_like_domain(query)


# --------------------------------------------------------------------------- #
# Rules: what to do with a page
# --------------------------------------------------------------------------- #

def expected_ctr(position: float) -> float:
    """The usual click rate at a position: 28%, 15%, 10%, 7%, then 5%."""
    rank = max(1, int(position))
    return {1: .28, 2: .15, 3: .10, 4: .07}.get(rank, .05)


@dataclass
class Advice:
    code: str
    todo: str
    action: str
    why: str


def advice(impressions: float, position: float, ctr: float) -> Advice:
    """The rule code for an existing page (last 90 days). ctr is a fraction (0.021 = 2.1%)."""
    imp = int(round(impressions))
    if imp < 20:
        return Advice("prune", "Keep", "Prune or merge candidate", "Fewer than 20 impressions in 90 days.")
    exp = expected_ctr(position)
    if position <= 5 and ctr < exp * 0.5 and imp >= 100:
        return Advice("ctr", "Optimization", "Optimize title & meta",
                      f"Position {position:.1f} but CTR {ctr * 100:.1f}%, well below the usual "
                      f"~{exp * 100:.0f}% at that position.")
    if position <= 10:
        if imp >= 300 and position > 3.5:          # a page already in the top 3 has nothing to reach
            return Advice("p1", "Update", "Update to reach the top 3",
                          f"On page 1 (position {position:.1f}) with {imp:,} impressions.")
        return KEEP
    if position <= 20:
        if imp >= 200:
            return Advice("p2", "Update", "Update to move from page 2 to page 1",
                          f"Page 2 (position {position:.1f}) with {imp:,} impressions.")
        return KEEP
    if imp >= 200:
        return Advice("deep", "Rewrite", "Rewrite around the queries it already gets",
                      f"Position {position:.1f} with {imp:,} impressions: Google shows it, but far down.")
    return KEEP


KEEP = Advice("keep", "Keep", "No action now", "Low impressions or already performing; review next month.")
WAITING = Advice("waiting", "", "Waiting for data", "No impressions yet.")
GONE = Advice("gone", "", "Check the redirect or removal",
              "No impressions for 2 full months: the page looks gone.")


def gain(impressions: float, ctr: float, position: float) -> float:
    """Clicks a month the page could gain: only between positions 3.5 and 20."""
    if not 3.5 <= position <= 20:
        return 0.0
    return impressions / 3 * max(0.0, 0.08 - ctr)


def score(impressions: float, gain_value: float, position: float, competing: bool,
          seasonal: bool, new_topic: bool) -> int:
    """0 to 100: potential gain (45), visibility (20), where it ranks (up to 20), plus bonuses."""
    s = min(gain_value / 150, 1) * 45
    s += min(math.log10(max(impressions, 1)) / math.log10(15000), 1) * 20
    if position:
        s += 5 if position < 3.5 else 20 if position <= 10 else 10 if position <= 20 else 0
    s += 5 if competing else 0
    s += 10 if seasonal else 0
    s += 10 if new_topic else 0
    return max(0, min(100, int(round(s))))


def priority(score_value: float) -> str:
    return ("Urgent" if score_value >= 80 else "High" if score_value >= 60
            else "Moderate" if score_value >= 40 else "Low")


# --------------------------------------------------------------------------- #
# Data of one page, built from the 90-day pages and query×page tables
# --------------------------------------------------------------------------- #

@dataclass
class PageStats:
    url: str                                   # as Search Console gives it
    clicks: float = 0.0
    impressions: float = 0.0
    position: float = 0.0
    queries: list = field(default_factory=list)     # [(query, clicks, impressions, position)] by impressions

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0


def build_pages(pages: dict, pairs: list) -> dict:
    """{decoded url: PageStats}. pages: {url: (clicks, impressions, position)} (reports.by_page);
    pairs: [(query, url, clicks, impressions, position)]. Variants that decode to the same
    address are added up."""
    out = {}
    for url, (c, n, p) in pages.items():
        key = url_key(url)
        s = out.setdefault(key, PageStats(url))
        total = s.impressions + n
        s.position = (s.position * s.impressions + p * n) / total if total else 0.0
        s.clicks += c
        s.impressions = total
    merged = {}                                  # (page, query) -> [clicks, impressions, position x impressions]
    for query, url, c, n, p in pairs:
        key = url_key(url)
        if key in out:
            m = merged.setdefault((key, query), [0.0, 0.0, 0.0])
            m[0] += c
            m[1] += n
            m[2] += p * n
    for (key, query), (c, n, w) in merged.items():
        out[key].queries.append((query, c, n, w / n if n else 0.0))
    for s in out.values():
        s.queries.sort(key=lambda q: (-q[2], q[0]))
    return out


def nonbrand_queries(stats: PageStats, brand_terms: list) -> list:
    return [q for q in stats.queries if not skip_query(q[0], brand_terms)]


def primary_keyword(stats: PageStats, brand_terms: list) -> str:
    """The top non-brand search of the page, by impressions in 90 days."""
    top = nonbrand_queries(stats, brand_terms)
    return top[0][0] if top else ""


def secondary_keywords(stats: PageStats, brand_terms: list, primary: str) -> list:
    out = []
    for q, _, _, _ in nonbrand_queries(stats, brand_terms):
        if norm(q) != norm(primary) and q not in out:
            out.append(q)
        if len(out) == SECONDARY_COUNT:
            break
    return out


def fan_outs(stats: PageStats, brand_terms: list) -> list:
    out = []
    for q, _, _, _ in nonbrand_queries(stats, brand_terms):
        if is_question(q) and q not in out:
            out.append(q)
        if len(out) == FANOUT_COUNT:
            break
    return out


def keyword_metrics(stats: PageStats, keyword: str) -> tuple:
    """(impressions, position) of one search on this page, or ("", "") when it has none."""
    for q, _, n, p in stats.queries:
        if norm(q) == norm(keyword):
            return int(round(n)), round(p, 1)
    return "", ""


def competing_pages(pairs_by_query: dict, keyword: str, own_url: str) -> list:
    """Other pages with 20+ impressions for the keyword, the top 3 by impressions, as paths."""
    rows = [(n, u) for u, n in pairs_by_query.get(norm(keyword), [])
            if url_key(u) != url_key(own_url) and n >= COMPETING_MIN_IMPRESSIONS]
    rows.sort(key=lambda r: (-r[0], r[1]))
    seen, out = set(), []
    for _, u in rows:
        p = path_of(u)
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) == COMPETING_SHOWN:
            break
    return out


def index_pairs(pairs: list) -> dict:
    """{norm(query): [(url, impressions)]} for the competing-pages rule."""
    by_query = {}
    for query, url, _, n, _ in pairs:
        by_query.setdefault(norm(query), []).append((url, n))
    return by_query


# --------------------------------------------------------------------------- #
# Dates, change and impact
# --------------------------------------------------------------------------- #

def change(cur, base):
    """Fractional change, or "" when the base is 0 or unknown."""
    if base in (None, "") or not base:
        return ""
    return round((cur - base) / base, 4)


def parse_date(cell):
    """A date from a sheet cell: a serial number (days since 1899-12-30), ISO text or d/m/y text."""
    if cell is None or cell == "":
        return None
    if isinstance(cell, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(cell).strip()):
        n = float(cell)
        return date(1899, 12, 30) + timedelta(days=int(n)) if 20000 <= n <= 80000 else None
    t = str(cell).strip().translate(_FA)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            d = datetime.strptime(t, fmt).date()
            if d.year >= 1990:
                return d
        except ValueError:
            continue
    return None


def impact_text(published: date, today: date, before: tuple, after: tuple) -> str:
    """"Clicks a → b (±x%), Pos p1 → p2" for the 28 days before vs after the update, or
    "Too early (N days)". before/after: (clicks, position) or None."""
    days = (today - published).days
    if days < IMPACT_DAYS:
        return f"Too early ({max(days, 0)} days)"
    if before is None or after is None:
        return "No data for these weeks"
    (c0, p0), (c1, p1) = before, after
    if c0:
        pct = f"{(c1 - c0) / c0 * 100:+.0f}%"
        return f"Clicks {int(c0)} → {int(c1)} ({pct}), Pos {p0:.1f} → {p1:.1f}"
    return f"Clicks {int(c0)} → {int(c1)}, Pos {p0:.1f} → {p1:.1f}"


def impact_windows(published: date) -> tuple:
    """((first, last) of the 28 days before, (first, last) of the 28 days after)."""
    before = (published - timedelta(days=IMPACT_DAYS), published - timedelta(days=1))
    after = (published, published + timedelta(days=IMPACT_DAYS - 1))
    return before, after


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #

ALERT_SEASON = "Seasonal: update 6 weeks before the peak"


def season_alert(peak_month: int, today: date) -> str:
    """"Seasonal: peaks in June, update by 2027-05-04": the next peak and 6 weeks before it."""
    if not peak_month:
        return ALERT_SEASON
    year = today.year if date(today.year, peak_month, 1) >= date(today.year, today.month, 1) else today.year + 1
    start = date(year, peak_month, 1)
    return f"Seasonal: peaks in {start.strftime('%B')}, update by {start - timedelta(days=SEASON_LEAD_DAYS)}"


def peak_month_of(monthly_clicks: dict) -> int:
    """The calendar month (1 to 12) with the most clicks in the last twelve months, or 0 when
    the numbers are too small to tell. monthly_clicks: {first day of month: clicks}."""
    if not monthly_clicks or max(monthly_clicks.values()) < 10:
        return 0
    return max(monthly_clicks.items(), key=lambda kv: (kv[1], kv[0]))[0].month


def decaying(clicks: list) -> bool:
    """clicks: oldest first, at least DECAY_MONTHS + 1 months. Down three months in a row."""
    if len(clicks) < DECAY_MONTHS + 1 or not clicks[-DECAY_MONTHS - 1]:
        return False
    tail = clicks[-DECAY_MONTHS - 1:]
    return all(tail[i + 1] < tail[i] for i in range(DECAY_MONTHS))


def alerts_for(*, advice_code: str, competing: list, seasonal: bool, peak_month: int, today: date,
               url: str, duplicate: bool, decay: bool, position: float, impressions: float,
               gone: bool) -> str:
    out = []
    if competing:
        out.append("Cannibalization")
    if seasonal:
        out.append(season_alert(peak_month, today))
    if advice_code == "prune":
        out.append("Prune candidate")
    if url and decode_url(url).rstrip("/").endswith("-old"):
        out.append("Old copy still indexed")
    if duplicate:
        out.append("Duplicate URL")
    if decay:
        out.append("Decay: clicks down 3 months in a row")
    if position > 20 and impressions > 0:
        out.append("Low visibility (pos > 20)")
    if gone:
        out.append("Gone")
    return ", ".join(out)


def duplicate_keys(urls: list) -> set:
    """The dup_key shared by two or more of the urls."""
    seen, dup = {}, set()
    for u in urls:
        if not u:
            continue
        k = dup_key(u)
        if k in seen and seen[k] != u:
            dup.add(k)
        seen.setdefault(k, u)
    return dup


# --------------------------------------------------------------------------- #
# New topics (content gaps)
# --------------------------------------------------------------------------- #

@dataclass
class Gap:
    keyword: str                    # the top search of the group
    queries: list                   # every search of the group, top first
    impressions: float
    clicks: float
    position: float                 # best position in the group
    best_page: str                  # where the top search lands today
    score: int = 0


def _same_group(a: dict, b: dict) -> bool:
    wa, wb = a["words"], b["words"]
    if not wa or not wb:
        return False
    inter = len(wa & wb)
    if not inter:
        return False
    if wa <= wb or wb <= wa:
        return True
    if a["page"] == b["page"]:
        return True
    return inter / len(wa | wb) >= 0.5


def group_queries(items: list) -> list:
    """items: [{"query", "impressions", "clicks", "position", "page", "words"}] -> groups of items
    that share their main words (or land on the same page and share a word)."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _same_group(items[i], items[j]):
                parent[find(j)] = find(i)
    groups = {}
    for i, item in enumerate(items):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())


def find_gaps(*, queries: dict, best: dict, tab_matches, brand_terms: list, minimum: float,
              known: set, already: set, limit: int = MAX_GAPS_PER_TAB) -> list:
    """
    New-topic candidates for one tab.
      queries  {query: (clicks, impressions, position)} over the last 90 days
      best     {query: (url, impressions on that page, position on that page)}: the page with the
               most impressions for the search
      tab_matches(url) -> bool: is the page one of this tab's pages?
      known    normalized searches that are already a primary or secondary keyword of some row
      already  normalized searches of gap rows that exist, were dismissed or were added from ideas
    """
    items = []
    for query, (c, n, p) in queries.items():
        if n < minimum or skip_query(query, brand_terms):
            continue
        page = best.get(query)
        if page is None:
            continue
        url, page_impr, _ = page
        covered = tab_matches(url)
        weak = covered and n < 0.05 * page_impr and p > 8
        if covered and not weak:
            continue
        items.append({"query": query, "impressions": n, "clicks": c, "position": p,
                      "page": url_key(url), "words": words(query)})
    items.sort(key=lambda i: (-i["impressions"], i["query"]))
    out = []
    for group in group_queries(items):
        group.sort(key=lambda i: (-i["impressions"], i["query"]))
        top = group[0]
        keys = {norm(i["query"]) for i in group}
        if norm(top["query"]) in known or keys & already:
            continue
        out.append(Gap(
            keyword=top["query"], queries=[i["query"] for i in group],
            impressions=sum(i["impressions"] for i in group),
            clicks=sum(i["clicks"] for i in group),
            position=min(i["position"] for i in group), best_page=top["page"]))
    for g in out:
        ctr = g.clicks / g.impressions if g.impressions else 0.0
        g.score = score(g.impressions, gain(g.impressions, ctr, g.position), g.position,
                        competing=False, seasonal=False, new_topic=True)
    out.sort(key=lambda g: (-g.score, -g.impressions, g.keyword))
    return out[:limit]


def gap_why(gap: Gap) -> str:
    return (f"{int(round(gap.impressions)):,} impressions across {len(gap.queries)} searches land on "
            f"{path_of(gap.best_page)}, which doesn't cover this topic.")


# --------------------------------------------------------------------------- #
# Classifying a row
# --------------------------------------------------------------------------- #

def intent_for(tab_landing: bool) -> str:
    return "Navigational" if tab_landing else "Informational"


def content_type_for(tab_landing: bool, impressions: float) -> str:
    return "Pillar" if tab_landing else "Cornerstone" if impressions >= LARGE_PAGE else "Cluster"


def keyword_type_for(seasonal_regex: str, url: str, keyword: str) -> str:
    if seasonal_regex and (re.search(seasonal_regex, decode_url(url), re.IGNORECASE)
                           or re.search(seasonal_regex, keyword or "", re.IGNORECASE)):
        return "Seasonal"
    return "General"


def competing_text(paths: list) -> str:
    return ", ".join(paths)


def competing_recommendation(paths: list) -> str:
    return (f"Overlaps with {', '.join(paths)}. Decide which page owns the keyword, then merge "
            f"or differentiate.") if paths else ""
