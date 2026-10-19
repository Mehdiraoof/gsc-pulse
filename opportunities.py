"""
PulseO: the opportunity engine (v2.4)
=====================================
Turns the last 90 days of Search Console data (the Data sheet's Queries, Pages and Query×Page
tabs) and the latest month of the report tabs into a list of concrete things to do, each with an
estimated gain in clicks per month. The list is written to the `Opportunities` tab of the Reports
sheet and the client deck shows its top items.

Types, in the order they are listed
  quick_win     a query at position 4 to 20 with enough impressions (striking distance)
  ctr_fix       position 5 or better, but the click rate is under half of what that position gets
  update_page   a page at position 4 to 10 with lots of impressions and a low click rate
  decliner      a page down on last month AND on last year that still has impressions (not gone)
  question      a question query where we rank low or the click rate is low
  cannibal      one query that several own pages compete for
  check_page    a page whose impressions fell by 80% or more but did not disappear
  aio           a page that gained AI Overview impressions while its clicks fell
  seasonal      a page that grew strongly into next month last year

Gain is an estimate and always labelled so: impressions_90d / 3 x max(0, target CTR - CTR), where
the target is `target_ctr_top3` (default 8%, a conservative click rate for the top 3), rounded
to 5. The expected click rate by position is an industry-average estimate (EXPECTED_CTR).

Everything here is a pure function of plain data, so it is easy to test with invented numbers.
"""

import math
import re
from dataclasses import dataclass, field

import tabs

HEADER = ["type", "keyword", "page", "impressions_90d", "clicks_90d", "position", "ctr",
          "expected_ctr", "potential_gain_month", "diagnosis", "action", "segment"]
TYPES = ["quick_win", "ctr_fix", "update_page", "decliner", "question", "cannibal", "check_page",
         "aio", "seasonal"]
MAX_PER_TYPE = 300

# Average click rate by position (industry-average estimate; real results vary by SERP features).
EXPECTED_CTR = {1: .28, 2: .15, 3: .10, 4: .07, 5: .05, 6: .04, 7: .03, 8: .025, 9: .02, 10: .018}
EXPECTED_CTR_PAGE2 = .01            # positions 11 to 20
EXPECTED_CTR_BEYOND = .005

QUESTION_FA = re.compile(r"(چیست|چگونه|چطور|چرا|یعنی چه|کیست|کجاست)")
QUESTION_EN = re.compile(r"^(what|how|why|who|when|where|is|can|does)\b", re.IGNORECASE)

QUICK_WIN_FROM, QUICK_WIN_TO = 4, 20
CTR_FIX_SHARE = 0.5                 # CTR under 50% of expected
UPDATE_MIN_IMPRESSIONS = 1000
CANNIBAL_MIN_IMPRESSIONS = 30
SHARED_QUERY_MIN_IMPRESSIONS = 10
SEASONAL_GROWTH = 0.30
SEASONAL_MIN_CLICKS = 20

ACTIONS = {
    "quick_win": "Improve the page that ranks: a deeper answer, a better title, internal links.",
    "ctr_fix": "Rewrite title and meta description; check SERP features (images, AI Overview).",
    "question": "Write a short explainer page or add an FAQ answer.",
    "cannibal": "Merge into one page or make the pages target different intents.",
    "check_page": "Check this page (index status, recent changes).",
    "aio": "Check whether an AI Overview now answers this search; make the page the clear source.",
}


def expected_ctr(position: float) -> float:
    """The click rate a result at this position usually gets."""
    rank = int(position) if position >= 1 else 1
    if rank <= 10:
        return EXPECTED_CTR[rank]
    return EXPECTED_CTR_PAGE2 if rank <= 20 else EXPECTED_CTR_BEYOND


def gain(impressions_90d: float, ctr: float, target: float) -> int:
    """Estimated extra clicks a month if the result reached the target click rate, rounded to 5."""
    raw = impressions_90d / 3 * max(0.0, target - ctr)
    return int(math.floor(raw / 5 + 0.5) * 5)


def split_text(text: str) -> tuple:
    """'Diagnosis. Action.' -> ('Diagnosis.', 'Action.') at the first sentence end."""
    head, sep, tail = text.partition(". ")
    return (head + ".", tail) if sep else (text, "")


# --------------------------------------------------------------------------- #
# The data
# --------------------------------------------------------------------------- #

@dataclass
class QueryRow:
    query: str
    clicks: float
    impressions: float
    position: float

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0


@dataclass
class PageRow:
    url: str
    clicks: float
    impressions: float
    position: float

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0


@dataclass
class Pair:
    query: str
    url: str
    clicks: float
    impressions: float
    position: float


def _f(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _strip_fragment(url: str) -> str:
    return re.sub(r"#.*$", "", url)


def parse_queries(rows: list) -> list:
    """The Queries tab: [header, [query, clicks, impressions, ctr %, position], ...]."""
    out = []
    for r in rows[1:]:
        if len(r) >= 5 and r[0]:
            out.append(QueryRow(r[0], _f(r[1]), _f(r[2]), _f(r[4])))
    return out


def _merge(items: list, key, make):
    """Rows that differ only by a #fragment are added up (position weighted by impressions)."""
    acc = {}
    for k, clicks, impr, pos in items:
        a = acc.setdefault(k, [0.0, 0.0, 0.0])
        a[0] += clicks
        a[1] += impr
        a[2] += pos * impr
    return [make(k, c, n, (w / n if n else 0.0)) for k, (c, n, w) in acc.items()]


def parse_pages(rows: list) -> list:
    """The Pages tab: [header, [page, clicks, impressions, ctr %, position], ...]."""
    items = [(_strip_fragment(r[0]), _f(r[1]), _f(r[2]), _f(r[4]))
             for r in rows[1:] if len(r) >= 5 and r[0]]
    return _merge(items, None, lambda url, c, n, p: PageRow(url, c, n, p))


def parse_pairs(rows: list) -> list:
    """The Query×Page tab: [header, [query, page, clicks, impressions, ctr %, position], ...]."""
    items = [((r[0], _strip_fragment(r[1])), _f(r[2]), _f(r[3]), _f(r[5]))
             for r in rows[1:] if len(r) >= 6 and r[0] and r[1]]
    return _merge(items, None, lambda k, c, n, p: Pair(k[0], k[1], c, n, p))


@dataclass
class Inputs:
    queries: list = field(default_factory=list)          # QueryRow
    pages: list = field(default_factory=list)            # PageRow
    pairs: list = field(default_factory=list)            # Pair
    month_pages: list = field(default_factory=list)      # R Pages rows (dicts) of the latest month
    aio_pages: list = field(default_factory=list)        # R AIO Pages rows (dicts) of the latest month
    gone: set = field(default_factory=set)               # URLs that are gone this month
    seasonal: dict = field(default_factory=dict)         # url -> (clicks this month last year, next month)
    next_month: str = "next month"
    brand_terms: list = field(default_factory=list)
    min_impressions: float = 300
    target_ctr: float = 0.08
    segment_of: object = lambda url: ""                  # url -> segment name
    resolved: object = None                              # resolved.Resolved or None


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #

DOMAIN_LIKE = re.compile(r"\bwww\b|https?:|\.(?:ir|com|net|org|io|app|info|co|me)(?:\b|/)", re.IGNORECASE)
LANGUAGE_ROOT = re.compile(r"[a-z]{2}(?:-[a-z]{2})?", re.IGNORECASE)


def looks_like_domain(query: str) -> bool:
    """A search for a site or an address ("app.example.ir", "www example"), not a topic."""
    return bool(DOMAIN_LIKE.search(query))


def is_homepage(url: str) -> bool:
    """The site's front page or a language root (/, /en, /fa/): those pages are reached by name,
    not by a topic, so they are never an opportunity."""
    path = tabs.path_of(url).strip("/")
    return not path or bool(LANGUAGE_ROOT.fullmatch(path))


def brand_terms_for(site) -> list:
    """The brand terms of a site: those in its configuration plus, always, the site label and the
    bare domain name (kalameh from sc-domain:kalameh.com), lower-cased and without duplicates."""
    configured = [t.strip().lower() for t in site.opportunity_setting("brand_terms") if t.strip()]
    label = getattr(getattr(site, "deck", None), "site_label", "") or ""
    host = re.sub(r"^(?:sc-domain:|https?://)", "", (site.site_url or "").strip().lower()).split("/")[0]
    labels = [p for p in host.split(".") if p and p != "www"]
    stem = labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")
    if len(labels) >= 3 and stem in ("co", "com", "org", "net", "gov", "ac"):      # example.co.uk
        stem = labels[-3]
    defaults = [t.strip().lower() for t in (label, stem) if len(t.strip()) >= 3]   # too short would match anything
    out = []
    for t in configured + defaults:
        if t not in out:
            out.append(t)
    return out


def _is_brand(query: str, terms: list) -> bool:
    q = query.lower()
    return any(t.lower() in q for t in terms)


def _skip_query(query: str, terms: list) -> bool:
    """Brand searches and searches for an address are not opportunities."""
    return _is_brand(query, terms) or looks_like_domain(query)


def _homepage(url: str) -> bool:
    return is_homepage(url)


def _row(kind, keyword, page, impressions, clicks, position, ctr, gain_value, diagnosis, action,
         segment) -> list:
    return [kind, keyword, page, int(round(impressions)), int(round(clicks)),
            round(position, 1) if position else "", round(ctr, 4),
            round(expected_ctr(position), 4) if position else "", int(gain_value),
            diagnosis, action, segment]


def _best_pages(pairs: list) -> dict:
    """query -> its pair with the most impressions."""
    best = {}
    for p in pairs:
        if p.query not in best or p.impressions > best[p.query].impressions:
            best[p.query] = p
    return best


def _top_queries(pairs: list) -> dict:
    """url -> the query with the most impressions for that page."""
    top = {}
    for p in pairs:
        if p.url not in top or p.impressions > top[p.url].impressions:
            top[p.url] = p
    return top


def build(inp: Inputs) -> list:
    """All opportunities as sheet rows (see HEADER), sorted by type, then gain, biggest first."""
    rows = []
    best, top = _best_pages(inp.pairs), _top_queries(inp.pairs)
    by_query = {}
    for p in inp.pairs:
        by_query.setdefault(p.query, []).append(p)
    page_by_url = {p.url: p for p in inp.pages}
    page_by_url_month = {}
    for r in inp.month_pages:
        page_by_url_month.setdefault(r["Page"], {})[r["Type"]] = r
    resolved = inp.resolved

    def seg(url):
        return inp.segment_of(url) if url else ""

    def resolved_page(url):
        return bool(resolved) and resolved.page(url)

    # 1 quick_win, 2 ctr_fix: query based
    for q in inp.queries:
        if q.impressions < inp.min_impressions or _skip_query(q.query, inp.brand_terms):
            continue
        page = best[q.query].url if q.query in best else ""
        if page and _homepage(page):                  # reached by name, never an opportunity
            continue
        if QUICK_WIN_FROM <= q.position <= QUICK_WIN_TO:
            g = gain(q.impressions, q.ctr, inp.target_ctr)
            if g > 0:
                where = "page 1" if q.position <= 10 else "page 2"
                rows.append(_row("quick_win", q.query, page, q.impressions, q.clicks, q.position,
                                 q.ctr, g, f"Ranks on {where} but below the top 3.",
                                 ACTIONS["quick_win"], seg(page)))
        if q.position <= 5 and q.ctr < CTR_FIX_SHARE * expected_ctr(q.position):
            g = gain(q.impressions, q.ctr, inp.target_ctr)
            if g > 0:
                rows.append(_row("ctr_fix", q.query, page, q.impressions, q.clicks, q.position,
                                 q.ctr, g,
                                 f"Click rate {q.ctr:.1%} where about {expected_ctr(q.position):.1%} "
                                 f"is usual at this position.", ACTIONS["ctr_fix"], seg(page)))

    # 3 update_page
    for p in inp.pages:
        if not (4 <= p.position <= 10) or p.impressions < UPDATE_MIN_IMPRESSIONS or _homepage(p.url):
            continue
        exp = expected_ctr(p.position)
        if p.ctr >= exp:
            continue
        g = gain(p.impressions, p.ctr, inp.target_ctr)
        if g <= 0:
            continue
        tq = top.get(p.url)
        keyword = tq.query if tq else ""
        text = _page_diagnosis(p, tq, exp, by_query, inp)
        diagnosis, action = split_text(text)
        rows.append(_row("update_page", keyword, p.url, p.impressions, p.clicks, p.position, p.ctr,
                         g, diagnosis, action, seg(p.url)))

    # 4 decliner (down on last month AND on last year, still visible, not gone, not resolved)
    for url, kinds in page_by_url_month.items():
        if "Declining" not in kinds or "YoY Declining" not in kinds:
            continue
        if url in inp.gone or resolved_page(url):
            continue
        m = kinds["Declining"]
        impr = _f(m["Impressions"])
        if impr <= 0:
            continue
        text = _decline_diagnosis(m)
        diagnosis, action = split_text(text)
        base = page_by_url.get(url)
        tq = top.get(url)
        lost = max(0.0, -_f(m["Clicks diff"]))
        rows.append(_row("decliner", tq.query if tq else "", url,
                         base.impressions if base else impr, base.clicks if base else _f(m["Clicks"]),
                         base.position if base else _f(m["Position"]),
                         base.ctr if base else (_f(m["Clicks"]) / impr), lost, diagnosis, action,
                         seg(url)))

    # 5 question
    for q in inp.queries:
        if q.impressions < inp.min_impressions / 3 or _skip_query(q.query, inp.brand_terms):
            continue
        if not (QUESTION_FA.search(q.query) or QUESTION_EN.search(q.query)):
            continue
        if q.position > 5 or q.ctr < expected_ctr(q.position):
            page = best[q.query].url if q.query in best else ""
            if page and _homepage(page):
                continue
            rows.append(_row("question", q.query, page, q.impressions, q.clicks, q.position, q.ctr,
                             gain(q.impressions, q.ctr, inp.target_ctr),
                             f"Question search ranking at position {q.position:.1f}.",
                             ACTIONS["question"], seg(page)))

    # 6 cannibal
    for q in inp.queries:
        if _skip_query(q.query, inp.brand_terms):
            continue
        if q.query in best and _homepage(best[q.query].url):       # a search that lands on the front page
            continue
        competing = [p for p in by_query.get(q.query, [])
                     if p.impressions >= CANNIBAL_MIN_IMPRESSIONS and not _homepage(p.url)]
        if len(competing) < 2:
            continue
        top_page = max(competing, key=lambda p: p.impressions)
        rows.append(_row("cannibal", q.query, top_page.url, q.impressions, q.clicks, q.position,
                         q.ctr, gain(q.impressions, q.ctr, inp.target_ctr),
                         f"{len(competing)} pages compete for this search.", ACTIONS["cannibal"],
                         seg(top_page.url)))

    # 7 check_page: impressions fell 80% or more but the page did not disappear
    for url, kinds in page_by_url_month.items():
        m = kinds.get("Declining")
        if not m or url in inp.gone:
            continue
        impr, prev = _f(m["Impressions"]), _f(m["Impr prev"])
        if impr <= 0 or prev <= 0 or (prev - impr) / prev < 0.8:
            continue
        base = page_by_url.get(url)
        rows.append(_row("check_page", "", url, base.impressions if base else impr,
                         base.clicks if base else _f(m["Clicks"]),
                         base.position if base else _f(m["Position"]),
                         base.ctr if base else _f(m["Clicks"]) / impr, max(0.0, -_f(m["Clicks diff"])),
                         f"Impressions down {(prev - impr) / prev:.0%} on last month.",
                         ACTIONS["check_page"], seg(url)))

    # 8 aio: AI Overview impressions up, clicks down
    for a in inp.aio_pages:
        if a["Type"] != "Growing" or _f(a["AIO Impr diff"]) <= 0:
            continue
        m = page_by_url_month.get(a["Page"], {}).get("Declining")
        if not m or a["Page"] in inp.gone:
            continue
        base = page_by_url.get(a["Page"])
        rows.append(_row("aio", "", a["Page"], base.impressions if base else _f(m["Impressions"]),
                         base.clicks if base else _f(m["Clicks"]),
                         base.position if base else _f(m["Position"]), base.ctr if base else 0.0, 0,
                         f"AI Overview impressions +{_f(a['AIO Impr diff']):.0f}, clicks "
                         f"{_f(m['Clicks diff']):+.0f}.", ACTIONS["aio"], seg(a["Page"])))

    # 9 seasonal: the page grew strongly into next month last year
    for url, (this_month, next_month) in inp.seasonal.items():
        if this_month < SEASONAL_MIN_CLICKS or next_month < this_month * (1 + SEASONAL_GROWTH):
            continue
        if url in inp.gone or resolved_page(url):
            continue
        base = page_by_url.get(url)
        tq = top.get(url)
        pct = (next_month - this_month) / this_month
        rows.append(_row("seasonal", tq.query if tq else "", url, base.impressions if base else 0,
                         base.clicks if base else 0, base.position if base else 0,
                         base.ctr if base else 0.0, next_month - this_month,
                         f"Grew {pct:+.0%} into {inp.next_month} last year.",
                         f"Update before {inp.next_month}.", seg(url)))

    return _order(rows)


def _page_diagnosis(page: PageRow, top_pair, expected: float, by_query: dict, inp: Inputs) -> str:
    if top_pair:
        others = {p.url for p in by_query.get(top_pair.query, [])
                  if p.url != page.url and p.impressions >= SHARED_QUERY_MIN_IMPRESSIONS}
        if len(others) >= 2:
            return (f"Split with {len(others)} other pages for the same keyword. "
                    f"Merge into one strong page.")
    if page.ctr < CTR_FIX_SHARE * expected:
        return "Ranks well, title doesn't win the click. Rewrite title + meta, add a quick summary."
    if page.position >= 8:
        return "Bottom of page 1. Refresh content, add internal links from related pages."
    season = inp.seasonal.get(page.url)
    if season and season[0] >= SEASONAL_MIN_CLICKS and season[1] >= season[0] * (1 + SEASONAL_GROWTH):
        return f"Seasonal. Update before {inp.next_month}."
    return "Close to the top. Answer the main question in the first lines, add FAQ."


def _decline_diagnosis(m: dict) -> str:
    pos_diff = _f(m["Position diff"])
    impr, impr_prev = _f(m["Impressions"]), _f(m["Impr prev"])
    clicks, clicks_prev = _f(m["Clicks"]), _f(m["Clicks prev"])
    if pos_diff >= 1:
        return "Ranking fell. Update content."
    stable = abs(pos_diff) < 1
    if stable and impr_prev > 0 and (impr_prev - impr) / impr_prev >= 0.20:
        return "Fewer searches (demand or seasonal). Keep the page current; no urgent fix."
    ctr, ctr_prev = (clicks / impr if impr else 0), (clicks_prev / impr_prev if impr_prev else 0)
    if stable and ctr_prev > 0 and (ctr_prev - ctr) / ctr_prev >= 0.25:
        return "Search results changed (click rate fell). Rewrite title and meta, check SERP features."
    return "Down on last month and last year. Review the page and its internal links."


def _order(rows: list) -> list:
    """By type, then potential gain (biggest first); at most MAX_PER_TYPE rows per type."""
    out = []
    for kind in TYPES:
        of_kind = sorted((r for r in rows if r[0] == kind),
                         key=lambda r: (-r[8], r[1], r[2]))
        out += of_kind[:MAX_PER_TYPE]
    return out


def counts(rows: list) -> dict:
    """type -> number of rows."""
    out = {}
    for r in rows:
        out[r[0]] = out.get(r[0], 0) + 1
    return out
