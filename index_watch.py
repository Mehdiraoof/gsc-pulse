"""
PulseO: the index watch (technical module, phase 2)
===================================================
What Google itself says about the pages of a site, asked through the Search Console URL Inspection API (read only):
is the page in the index, which address Google took as the main one, when it was last crawled, and what its rich results
say. Every run asks about a few pages, in this order, within the budget of the day:

  1. the key pages (the top pages by clicks and the configured ones), and the pages the health check cannot reach
     (the Checker Blocked tab): every run
  2. new or updated content of the content dashboards (a Published/Updated Date in the last 30 days)
  3. pages with Status Published that are not known to be in the index yet
  4. pages that lost more than half of their impressions against the month before
  5. the rest of the sitemap, the page that was looked at longest ago first, so that over the weeks every page is seen

The latest answer for each page is kept in the Index Status tab of the Technical sheet (one row per page, with the day it
was first seen and the day it last changed). From those rows come the findings: key pages that are not in the index,
that Google reads under another canonical, or whose rich results fail or went away (these can alert, after a second
look), and, for the owner to read only, new content that is still not indexed after 14 days, sitemap pages that are not
indexable, pages that dropped out of the index and rich result errors elsewhere. The content dashboards get one data
column, `Index Status`, after their other data columns.

The quota (2,000 requests per day and 600 per minute for one property) is never used above 75%: the daily maximum is in
the site's configuration (1,500 at most), what was used today is counted in the Technical sheet, requests are spaced, and
a quota answer or repeated errors stop the run for that site. Nothing here prints an address.
"""

import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import content
import content_sheet as cs
import tabs
from technical import Result

TAB = "Index Status"
HEADER = ["Address", "Verdict", "Coverage", "Indexing", "Robots.txt", "Page fetch", "Last crawl", "Google canonical",
          "Declared canonical", "Canonical differs", "Rich results", "Why checked", "First seen", "Last checked",
          "Last changed", "Clicks (28 days)"]
LAST_COL = "P"
(ADDRESS, VERDICT, COVERAGE, INDEXING, ROBOTS, FETCH, CRAWL, GOOGLE, DECLARED, DIFFERS, RICH, WHY, FIRST, CHECKED, CHANGED,
 CLICKS) = HEADER
COL_INDEX = "Index Status"                       # the content dashboard column

WHY_KEY, WHY_BLOCKED = "Key page", "Key page (blocked for the checker)"
WHY_NEW, WHY_PUBLISHED = "New or updated content", "Published, not indexed yet"
WHY_DROP, WHY_ROTATION = "Lost impressions", "Sitemap rotation"

QUOTA_PER_DAY, QUOTA_PER_MINUTE = 2000, 600
INTERVAL_S = 0.2                                 # 300 requests a minute at most: half of the per-minute quota
WORKERS = 4
MAX_ERRORS = 3                                   # this many answers in a row that are errors stop the run for the site
NEW_DAYS, UNINDEXED_AFTER_DAYS = 30, 14
DROP_SHARE, DROP_MIN_IMPRESSIONS = 0.5, 100
MAX_NEW, MAX_PUBLISHED, MAX_DROP = 300, 200, 100
SHOWN = 50                                       # pages kept in a finding (the count is the real one)
WRITE_CHUNK = 400                                # rows of the Index Status tab written in one call

NOT_INDEXABLE_FETCH = ("NOT_FOUND", "SOFT_404", "REDIRECT_ERROR")
BLOCKED_INDEXING = ("BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER")


# --------------------------------------------------------------------------- #
# The answer of the API
# --------------------------------------------------------------------------- #

@dataclass
class Inspection:
    url: str
    ok: bool = True                  # the API answered (False: an error, nothing is known)
    verdict: str = ""                # PASS (in the index) | NEUTRAL (excluded) | FAIL (error) | ""
    coverage: str = ""
    indexing: str = ""
    robots: str = ""
    fetch: str = ""
    last_crawl: str = ""
    google_canonical: str = ""
    user_canonical: str = ""
    rich: dict = field(default_factory=dict)         # rich result type -> (errors, warnings)
    error: str = ""


def parse_inspection(url: str, response: dict) -> Inspection:
    """An Inspection from the answer of urlInspection.index.inspect. Only fields of the current API are read."""
    result = (response or {}).get("inspectionResult") or {}
    ix = result.get("indexStatusResult") or {}
    rich = {}
    for detected in (result.get("richResultsResult") or {}).get("detectedItems", []) or []:
        kind = str(detected.get("richResultType", "")).strip() or "Rich result"
        errors, warnings = rich.get(kind, (0, 0))
        for item in detected.get("items", []) or []:
            for issue in item.get("issues", []) or []:
                severity = issue.get("severity")
                errors += severity == "ERROR"
                warnings += severity == "WARNING"
        rich[kind] = (errors, warnings)
    return Inspection(url, True, str(ix.get("verdict", "")), str(ix.get("coverageState", "")),
                      str(ix.get("indexingState", "")), str(ix.get("robotsTxtState", "")), str(ix.get("pageFetchState", "")),
                      str(ix.get("lastCrawlTime", "")), str(ix.get("googleCanonical", "")), str(ix.get("userCanonical", "")), rich)


def error_reason(exc: Exception) -> str:
    """Why a request failed, without the address: "quota" when Google says the quota is used, else "HTTP 500" or the kind of error."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    text = str(getattr(exc, "content", b"") or b"").lower() if status else ""
    if status == 429 or (status == 403 and any(w in text for w in ("quota", "ratelimit", "dailylimit", "rate limit"))):
        return "quota"
    return f"HTTP {status}" if status else type(exc).__name__


class Inspector:
    """Asks the URL Inspection API about one property, spaced, counted, and stopped by a quota answer or repeated errors."""

    def __init__(self, service, site_url: str, sleep=time.sleep, clock=time.monotonic, interval: float = INTERVAL_S,
                 max_errors: int = MAX_ERRORS, factory=None):
        """`service` is one client, used by whoever asks. With `factory` (a function that makes a client) every thread gets
        a client of its own: the connection of a Google client is not safe to share between threads."""
        self._service, self._factory, self._site, self._sleep, self._clock = service, factory, site_url, sleep, clock
        self._local = threading.local()
        self._interval, self._max_errors = interval, max_errors
        self._lock = threading.Lock()
        self._next = 0.0
        self._errors = 0
        self.used = 0                # requests made in this run
        self.failed = 0
        self.stopped = ""            # "" | "quota reached" | "repeated errors"

    def _client(self):
        if self._factory is None:
            return self._service
        if getattr(self._local, "client", None) is None:
            self._local.client = self._factory()
        return self._local.client

    def inspect(self, url: str) -> Inspection:
        with self._lock:
            if self.stopped:
                return Inspection(url, ok=False, error=self.stopped)
            now = self._clock()
            if self._next > now:
                self._sleep(self._next - now)
            self._next = max(now, self._next) + self._interval
            self.used += 1
        try:
            response = self._client().urlInspection().index().inspect(
                body={"inspectionUrl": url, "siteUrl": self._site, "languageCode": "en-US"}).execute(num_retries=2)
        except Exception as e:  # noqa: BLE001  one page that cannot be inspected is not a reason to stop, until it repeats
            reason = error_reason(e)
            with self._lock:
                self.failed += 1
                if reason == "quota":
                    self.stopped = "quota reached"
                else:
                    self._errors += 1
                    if self._errors >= self._max_errors:
                        self.stopped = "repeated errors"
            return Inspection(url, ok=False, error=reason)
        with self._lock:
            self._errors = 0
        return parse_inspection(url, response)


# --------------------------------------------------------------------------- #
# The Index Status tab
# --------------------------------------------------------------------------- #

def key_of(url: str) -> str:
    return content.url_key(url)


def rich_text(rich: dict) -> str:
    """{"FAQ": (2, 1)} -> "FAQ: 2 errors, 1 warning"; a type without issues says "valid"."""
    parts = []
    for kind in sorted(rich):
        errors, warnings = rich[kind]
        bits = ([f"{errors} error{'s' if errors != 1 else ''}"] if errors else []) + \
               ([f"{warnings} warning{'s' if warnings != 1 else ''}"] if warnings else [])
        parts.append(f"{kind}: {', '.join(bits) if bits else 'valid'}")
    return "; ".join(parts)


def rich_of(text: str) -> dict:
    """The other way: the types and their error and warning counts out of the tab's text."""
    out = {}
    for part in [p for p in str(text or "").split("; ") if p.strip()]:
        kind, _, rest = part.partition(": ")
        errors = re.search(r"(\d+) error", rest)
        warnings = re.search(r"(\d+) warning", rest)
        out[kind.strip()] = (int(errors.group(1)) if errors else 0, int(warnings.group(1)) if warnings else 0)
    return out


def _day(stamp: str) -> str:
    return str(stamp or "")[:10]


def signature(rec: dict) -> tuple:
    return tuple(rec.get(k, "") for k in (VERDICT, COVERAGE, INDEXING, ROBOTS, FETCH, GOOGLE, RICH))


def is_indexed(rec: dict) -> bool:
    return rec.get(VERDICT) == "PASS"


def is_known(rec: dict) -> bool:
    return bool(rec.get(VERDICT))


def canonical_differs(url: str, google: str) -> bool:
    return bool(google) and key_of(google) != key_of(url)


class Table:
    """The Index Status tab in memory: one record per page (by page key), what changed is written back row by row."""

    def __init__(self, values: list = None):
        self.recs, self.rows, self.dirty, self.new = {}, {}, set(), []
        for i, r in enumerate(values or [], start=2):
            cells = [str(c) for c in list(r)[:len(HEADER)]] + [""] * (len(HEADER) - len(r))
            if not cells[0].strip():
                continue
            k = key_of(cells[0])
            self.recs[k], self.rows[k] = dict(zip(HEADER, cells)), i
        self.count = len(values or [])

    def get(self, url: str) -> dict:
        return self.recs.get(key_of(url))

    def put(self, url: str, rec: dict):
        k = key_of(url)
        if k in self.rows:
            self.dirty.add(k)
        elif k not in self.new:
            self.new.append(k)
        self.recs[k] = rec

    def write(self, sheets, file_id: str):
        """Writes the rows that changed in place and appends the new ones."""
        updates = [(f"'{TAB}'!A{self.rows[k]}:{LAST_COL}{self.rows[k]}", [[self.recs[k].get(h, "") for h in HEADER]])
                   for k in sorted(self.dirty, key=lambda k: self.rows[k])]
        for i in range(0, len(updates), WRITE_CHUNK):
            tabs.patch_cells(sheets, file_id, updates[i:i + WRITE_CHUNK])
        tabs.append(sheets, file_id, TAB, [[self.recs[k].get(h, "") for h in HEADER] for k in self.new])
        for offset, k in enumerate(self.new, start=self.count + 2):
            self.rows[k] = offset
        self.count += len(self.new)
        self.dirty, self.new = set(), []


def new_record(url: str, ins: Inspection, prev: dict, why: str, clicks: int, today: date) -> dict:
    rec = {ADDRESS: content.display_url(url), VERDICT: ins.verdict, COVERAGE: ins.coverage, INDEXING: ins.indexing,
           ROBOTS: ins.robots, FETCH: ins.fetch, CRAWL: _day(ins.last_crawl), GOOGLE: content.display_url(ins.google_canonical),
           DECLARED: content.display_url(ins.user_canonical),
           DIFFERS: "yes" if canonical_differs(url, ins.google_canonical) else ("no" if ins.google_canonical else ""),
           RICH: rich_text(ins.rich), WHY: why, FIRST: (prev or {}).get(FIRST) or today.isoformat(),
           CHECKED: today.isoformat(), CLICKS: clicks or ""}
    changed = prev is None or signature(prev) != signature(rec)
    rec[CHANGED] = today.isoformat() if changed else (prev.get(CHANGED) or today.isoformat())
    return rec


# --------------------------------------------------------------------------- #
# What to look at today
# --------------------------------------------------------------------------- #

@dataclass
class ContentPage:
    url: str
    status: str = ""
    day: date = None                 # the Published/Updated Date of the dashboard row


def plan(key_urls: list, blocked_urls: list, content_pages: list, drop_urls: list, sitemap_urls: list, table: Table,
         today: date, budget: int) -> list:
    """[(address, why)] in the order of the list at the top of this module, each page once, at most `budget` of them."""
    chosen, seen = [], set()

    def take(urls, why, limit=None):
        n = 0
        for url in urls:
            k = key_of(url)
            if k in seen or len(chosen) >= budget or (limit is not None and n >= limit):
                continue
            seen.add(k)
            chosen.append((url, why))
            n += 1

    blocked_keys = {key_of(u) for u in blocked_urls}
    take([u for u in key_urls if key_of(u) in blocked_keys], WHY_BLOCKED)
    take([u for u in key_urls if key_of(u) not in blocked_keys], WHY_KEY)
    recent = [p for p in content_pages if p.url and p.day and today - timedelta(days=NEW_DAYS) <= p.day <= today]
    take([p.url for p in sorted(recent, key=lambda p: p.day, reverse=True)], WHY_NEW, MAX_NEW)

    def checked_at(url):
        rec = table.get(url)
        return (rec or {}).get(CHECKED, "")

    waiting = [p.url for p in content_pages if p.url and p.status.strip().lower() == "published"
               and not is_indexed(table.get(p.url) or {})]
    take(sorted(waiting, key=checked_at), WHY_PUBLISHED, MAX_PUBLISHED)
    take(drop_urls, WHY_DROP, MAX_DROP)
    take(sorted(dict.fromkeys(sitemap_urls), key=checked_at), WHY_ROTATION)        # never looked at first, then the longest ago
    return chosen


def lost_impressions(current: list, before: list) -> list:
    """Page addresses that had at least DROP_MIN_IMPRESSIONS impressions in the earlier window and lost more than half of
    them in the current one (Search Console rows of ["page"]), the biggest loss first."""
    now, was = {}, {}
    for rows, acc in ((current, now), (before, was)):
        for r in rows:
            if "?" in r["keys"][0]:
                continue
            a = acc.setdefault(key_of(r["keys"][0]), [r["keys"][0], 0])
            a[1] += int(r.get("impressions", 0))
    lost = [(was[k][1] - now.get(k, [0, 0])[1], was[k][0]) for k in was
            if was[k][1] >= DROP_MIN_IMPRESSIONS and now.get(k, [0, 0])[1] < was[k][1] * (1 - DROP_SHARE)]
    return [url for _, url in sorted(lost, reverse=True)]


# --------------------------------------------------------------------------- #
# One round
# --------------------------------------------------------------------------- #

@dataclass
class Round:
    asked: int = 0
    answered: int = 0
    failed: int = 0
    stopped: str = ""
    dropped: set = field(default_factory=set)        # page keys that were in the index and are not now
    rich_gone: set = field(default_factory=set)      # page keys whose rich results were there and are not now
    reasons: Counter = field(default_factory=Counter)


def inspect_pages(inspector, planned: list, table: Table, clicks_of, today: date, workers: int = None) -> Round:
    """Asks about the planned pages and writes the answers into the table. A page that could not be asked about keeps what
    the table had."""
    rnd = Round(asked=len(planned))
    if not planned:
        return rnd
    with ThreadPoolExecutor(max_workers=workers or WORKERS) as pool:
        answers = list(pool.map(lambda item: inspector.inspect(item[0]), planned))
    for (url, why), ins in zip(planned, answers):
        rnd.reasons[why] += 1
        if not ins.ok:
            rnd.failed += 1
            continue
        rnd.answered += 1
        prev = table.get(url)
        rec = new_record(url, ins, prev, why, clicks_of([url]), today)
        k = key_of(url)
        if prev and is_indexed(prev) and is_known(rec) and not is_indexed(rec):
            rnd.dropped.add(k)
        if prev and rich_of(prev.get(RICH)) and not rec[RICH]:
            rnd.rich_gone.add(k)
        table.put(url, rec)
    rnd.stopped = inspector.stopped
    return rnd


# --------------------------------------------------------------------------- #
# The findings
# --------------------------------------------------------------------------- #

def _top(counter: Counter, n: int = 3) -> str:
    return ", ".join(f"{name or 'no reason given'} x{count}" for name, count in counter.most_common(n))


def key_results(table: Table, key_urls: list, rnd: Round, clicks_of) -> list:
    """The three findings of the key pages that can alert (the pages are listed by their address)."""
    unindexed, canonical, rich = [], [], []
    reasons, kinds = Counter(), Counter()
    seen = set()
    for url in key_urls:
        k = key_of(url)
        rec = table.get(url)
        if k in seen or not rec or not is_known(rec):
            continue
        seen.add(k)
        if canonical_differs(url, rec.get(GOOGLE, "")):
            canonical.append(url)
        elif not is_indexed(rec) and "redirect" not in rec.get(COVERAGE, "").lower():
            unindexed.append(url)
            reasons[rec.get(COVERAGE, "")] += 1
        types = rich_of(rec.get(RICH, ""))
        if any(e for e, _ in types.values()) or k in rnd.rich_gone:
            rich.append(url)
            kinds.update([t for t, (e, _) in types.items() if e] or ["a rich result that went away"])
    total = len(seen)
    dropped = sum(1 for u in unindexed if key_of(u) in rnd.dropped)

    def make(key, bad, detail):
        return Result(key, not bad, pages=list(bad), clicks=clicks_of(bad), count=len(bad),
                      detail=detail if bad else "all inspected key pages are fine")

    return [
        make("idx_key_index", unindexed,
             f"{len(unindexed)} of {total} inspected key pages are not in Google's index ({_top(reasons)})"
             + (f"; {dropped} of them were in the index before" if dropped else "")),
        make("idx_key_canonical", canonical,
             f"{len(canonical)} of {total} inspected key pages: Google took another address as the main one"),
        make("idx_key_rich", rich, f"{len(rich)} of {total} inspected key pages have a rich result problem ({_top(kinds)})"),
    ]


def finding(key: str, pages: list, clicks_of, detail: str, none: str) -> Result:
    return Result(key, not pages, report_only=True, pages=list(pages)[:SHOWN], clicks=clicks_of(pages), count=len(pages),
                  detail=detail if pages else none)


def report_results(table: Table, key_urls: list, content_pages: list, sitemap_urls: list, rnd: Round, today: date,
                   clicks_of) -> list:
    """The findings that are only for the owner to read."""
    key_keys = {key_of(u) for u in key_urls}
    # new or updated content still not indexed after 14 days
    late = [p.url for p in content_pages if p.url and p.day
            and today - timedelta(days=NEW_DAYS) <= p.day <= today - timedelta(days=UNINDEXED_AFTER_DAYS)
            and (table.get(p.url) or {}) and is_known(table.get(p.url)) and not is_indexed(table.get(p.url))]
    # sitemap pages that are not indexable
    not_indexable, why = [], Counter()
    for url in dict.fromkeys(sitemap_urls):
        rec = table.get(url)
        if not rec or not is_known(rec):
            continue
        reason = ("noindex" if rec.get(INDEXING) in BLOCKED_INDEXING else
                  "not found or soft 404" if rec.get(FETCH) in ("NOT_FOUND", "SOFT_404") else
                  "redirect" if rec.get(FETCH) == "REDIRECT_ERROR" or "redirect" in rec.get(COVERAGE, "").lower() else
                  "canonical elsewhere" if rec.get(DIFFERS) == "yes" else "")
        if reason:
            not_indexable.append(url)
            why[reason] += 1
    dropped = [table.recs[k][ADDRESS] for k in rnd.dropped if k not in key_keys and k in table.recs]
    rich_errors, kinds = [], Counter()
    for k, rec in table.recs.items():
        if k in key_keys:
            continue
        errs = {t: e for t, (e, _) in rich_of(rec.get(RICH, "")).items() if e}
        if errs:
            rich_errors.append(rec[ADDRESS])
            kinds.update(errs.keys())
    return [
        finding("idx_new_content", late, clicks_of,
                f"{len(late)} pages with new or updated content are still not indexed after {UNINDEXED_AFTER_DAYS} days",
                "no new or updated content is waiting for the index"),
        finding("idx_sitemap_not_indexable", not_indexable, clicks_of,
                f"{len(not_indexable)} inspected sitemap pages are not indexable ({_top(why)})",
                "the inspected sitemap pages are all indexable"),
        finding("idx_dropped", dropped, clicks_of, f"{len(dropped)} pages that were in the index are not any more",
                "no page dropped out of the index"),
        finding("idx_rich_errors", rich_errors, clicks_of, f"{len(rich_errors)} pages have rich result errors ({_top(kinds)})",
                "no rich result errors on the other pages"),
    ]


def run_result(rnd: Round, used_before: int, budget: int, total: int, daily_max: int) -> Result:
    """One information row: what was asked today and what is left of the day's quota."""
    detail = (f"{rnd.answered} of {rnd.asked} planned pages inspected ({rnd.failed} failed), {used_before + rnd.asked} of the "
              f"{daily_max} allowed today used, {total} pages in the Index Status tab")
    if rnd.stopped:
        detail += f"; stopped: {rnd.stopped}"
    return Result("idx_run", not rnd.stopped, report_only=True, detail=detail, count=rnd.asked)


# --------------------------------------------------------------------------- #
# The content dashboards
# --------------------------------------------------------------------------- #

def _to_date(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return date(1899, 12, 30) + timedelta(days=int(value)) if value > 1 else None
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4,6}", text):                    # a date as the number of days of the sheet, read back as text
        return date(1899, 12, 30) + timedelta(days=int(text))
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_content_pages(sheets, file_id: str, tab_names: list, log=print) -> list:
    """The pages of the content dashboard: address, Status and Published/Updated Date of every row that has an address. A
    tab that cannot be read is left out."""
    out = []
    info = cs.sheet_info(sheets, file_id)
    for tab in tab_names:
        if tab not in info:
            continue
        try:
            _, rows = cs.table_of(cs.read_all(sheets, file_id, tab))
        except Exception as e:  # noqa: BLE001
            log(f"  index watch: a dashboard tab could not be read ({type(e).__name__})")
            continue
        for row in rows:
            url = str(row.get(content.URL, "")).strip()
            if url and not content.is_settled(row):
                out.append(ContentPage(url, str(row.get(content.STATUS, "")), _to_date(row.get(content.PUBLISHED_DATE))))
    return out


def status_text(rec) -> str:
    """What the dashboard column says: Indexed, Not indexed: <the reason Google gives>, or Not checked yet."""
    if not rec or not is_known(rec):
        return "Not checked yet"
    return "Indexed" if is_indexed(rec) else f"Not indexed: {rec.get(COVERAGE) or rec.get(VERDICT)}"


def fill_dashboard(sheets, file_id: str, tab_names: list, table: Table, log=print) -> int:
    """The Index Status column of every page tab, filled by page address after the other data columns (the Bing columns
    when they are there). A row without an address, and a row that is settled (page gone), keeps what its cell holds; the
    owner's columns are never touched. Returns the number of tabs written."""
    import bing_data                                          # the column is placed with the same care as the Bing ones
    info = cs.sheet_info(sheets, file_id)
    done = 0
    for tab in tab_names:
        if tab not in info:
            continue
        header = cs.read_header(sheets, file_id, tab)
        if content.URL not in header:
            continue
        header = bing_data.add_columns(sheets, file_id, tab, info[tab][0], header, [COL_INDEX],
                                       anchors=(bing_data.ANCHOR, bing_data.COL_CLICKS, bing_data.COL_IMPR, bing_data.COL_AI))
        _, rows = cs.table_of(cs.read_all(sheets, file_id, tab))
        values = []
        for row in rows:
            url = str(row.get(content.URL, "")).strip()
            if not url or content.is_settled(row):
                values.append(row.get(COL_INDEX, ""))
            else:
                values.append(status_text(table.get(url)))
        cs.write_columns(sheets, file_id, tab, header, 2, {COL_INDEX: values})
        done += 1
    return done
