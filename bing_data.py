"""
Bing Webmaster data in the sheets
=================================
What Bing says about a site, next to what Google says and never mixed with it: its own tabs in the site's Reports
sheet and its own columns in the content dashboard. Nothing that exists is changed.

Reports sheet (new tabs only)
  Bing Weekly / Bing Monthly   clicks, impressions, CTR and change per complete week / month (Bing gives the traffic
                               per day; the position comes from its top queries, which it gives per week)
  Bing Keywords                growing, declining, new and lost queries of a month against the month before
  Bing Pages                   growing and declining pages, the same way
  Bing Crawl                   Bing's crawl statistics per day and its list of crawl issues, as Bing gives them
  Bing AI Citations            the monthly export of Bing's AI performance report (no API): pages and the questions
                               they were cited for, month over month

Content dashboard: `Bing Clicks 90d`, `Bing Impr. 90d` and `Bing AI Citations` are added after the last column and
filled by page address. Bing searches do not make idea rows.

The data code only asks an `Access` (bing.py) for a method and its parameters, so a provider that is added later
(OAuth) changes nothing here. Nothing here prints an address or the key.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import aio_import
import bing
import bing_ai
import content
import content_sheet as cs
import history
import periods
import provision
import reports
import sites
import tabs

WEEKLY, MONTHLY = "Bing Weekly", "Bing Monthly"
KEYWORDS, PAGES = "Bing Keywords", "Bing Pages"
CRAWL, CITATIONS = "Bing Crawl", "Bing AI Citations"
STATUS = "Bing Status"

COL_CLICKS, COL_IMPR, COL_AI = "Bing Clicks 90d", "Bing Impr. 90d", "Bing AI Citations"
DASHBOARD_COLUMNS = (COL_CLICKS, COL_IMPR, COL_AI)
DASHBOARD_DAYS = 90
CALL_PAUSE_S = 0.5                         # between two requests to Bing
READY_AFTER_DAYS = 14                      # a month is final for the weekly data after this, whatever the newest bucket is

POSITION = "Avg. Position (top queries)"


def _period_header(unit: str) -> list:
    return ["Period", "Clicks", "Impressions", "CTR (%)", POSITION,
            f"Clicks {unit}", f"Clicks {unit} %", f"Impr {unit}", f"Impr {unit} %"]


WEEKLY_HEADER, MONTHLY_HEADER = _period_header("WoW"), _period_header("MoM")
PERIOD_NOTE = "Period (month; Bing's weekly data)"
KEYWORDS_HEADER = [PERIOD_NOTE] + reports.KEYWORDS_HEADER[1:]
PAGES_HEADER = [PERIOD_NOTE] + reports.PAGES_HEADER[1:]
CITATIONS_HEADER = ["Period", "Kind", "Item", "Citations", "Citations prev", "Citations diff", "Path"]
STATUS_HEADER = ["Item", "Value"]

STAT_FIELDS = (("CrawledPages", "Crawled Pages"), ("CrawlErrors", "Crawl Errors"), ("InIndex", "In Index"),
               ("InLinks", "In Links"), ("Code2xx", "Code 2xx"), ("Code301", "Code 301"), ("Code302", "Code 302"),
               ("Code4xx", "Code 4xx"), ("Code5xx", "Code 5xx"), ("AllOtherCodes", "Other Codes"),
               ("BlockedByRobotsTxt", "Blocked by robots.txt"), ("ContainsMalware", "Malware"),
               ("DnsFailures", "DNS Failures"), ("ConnectionTimeout", "Timeouts"))
CRAWL_HEADER = (["Kind", "Date or Page"] + [label for _, label in STAT_FIELDS] + ["Http Code", "Issue", "Last seen"])
CRAWL_LAST = cs.letters(len(CRAWL_HEADER) - 1)

TABS = [(WEEKLY, WEEKLY_HEADER), (MONTHLY, MONTHLY_HEADER), (KEYWORDS, KEYWORDS_HEADER), (PAGES, PAGES_HEADER),
        (CRAWL, CRAWL_HEADER), (CITATIONS, CITATIONS_HEADER), (STATUS, STATUS_HEADER)]


@dataclass
class BingResult:
    weeks: int = 0
    months: int = 0
    keyword_periods: int = 0
    page_periods: int = 0
    queries: int = 0                       # different searches in what Bing gave
    pages: int = 0
    crawl_days: int = 0
    crawl_issues: int = 0
    ai_months: int = 0
    dashboard: int = 0                     # dashboard tabs that got the Bing columns
    problems: list = field(default_factory=list)      # calls that failed or tabs that could not be read
    last_month: tuple = None               # (label, clicks, impressions) of the newest complete month
    no_data: bool = False
    ai_problems: list = field(default_factory=list)   # export files that could not be imported (with the file name: for the Bing Status tab only)
    ai_notes: list = field(default_factory=list)      # other things to know about the export files
    ai_files: int = 0                      # files in the export folder

    def text(self) -> str:
        if self.no_data and not self.ai_months:
            return "no data from Bing yet" + (f" ({len(self.problems)} problems)" if self.problems else "")
        text = (f"+{self.months} months, +{self.weeks} weeks, +{self.keyword_periods} keyword periods, "
                f"+{self.page_periods} page periods, crawl {self.crawl_days} days / {self.crawl_issues} issues, "
                f"AI citations {self.ai_months} months")
        if self.dashboard:
            text += f", dashboard columns on {self.dashboard} tabs"
        if self.problems:
            text += f", {len(self.problems)} problems"
        return text


# --------------------------------------------------------------------------- #
# What Bing gives
# --------------------------------------------------------------------------- #

def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None            # Bing writes -1 where there is no position


@dataclass
class BingData:
    traffic: dict = field(default_factory=dict)      # date -> (clicks, impressions)
    queries: list = field(default_factory=list)      # (date, query, clicks, impressions, position)
    pages: list = field(default_factory=list)        # (date, address, clicks, impressions, position)
    crawl: list = field(default_factory=list)        # dicts as Bing gives them, with a "_date"
    issues: list = field(default_factory=list)       # dicts as Bing gives them
    problems: list = field(default_factory=list)
    got: set = field(default_factory=set)            # the datasets Bing answered (an empty answer counts)
    address: str = ""                                 # the address of the site that was read (as the Bing account writes it)
    verified: bool = False
    listed: list = field(default_factory=list)       # every address of this site in the account

    @property
    def empty(self) -> bool:
        return not (self.traffic or self.queries or self.pages or self.crawl or self.issues)


def traffic_of(rows: list) -> dict:
    out = {}
    for r in rows:
        d = bing.parse_date(r.get("Date"))
        if d is None:
            continue
        c, n = out.get(d, (0, 0))
        out[d] = (c + _int(r.get("Clicks")), n + _int(r.get("Impressions")))
    return out


def stats_of(rows: list) -> list:
    """(date, key, clicks, impressions, position) of the rows of a query or page answer."""
    out = []
    for r in rows:
        d = bing.parse_date(r.get("Date"))
        key = str(r.get("Query") or "").strip()
        if d is None or not key:
            continue
        out.append((d, key, _int(r.get("Clicks")), _int(r.get("Impressions")),
                    _float(r.get("AvgImpressionPosition"))))
    return out


def freshest(access, candidates: list, sleep=time.sleep) -> tuple:
    """Of several entries of one site (http / https, with / without www), the one whose traffic is newest (the newest day
    with clicks or impressions, then the most impressions): Bing keeps the data under the address it crawls. Returns the
    entry and the traffic rows already read for it. An entry whose traffic cannot be read is passed over."""
    best, best_key, best_rows = candidates[0], None, {}
    for entry in candidates:
        sleep(CALL_PAUSE_S)
        try:
            rows = bing.rows(access.get("GetRankAndTrafficStats", siteUrl=entry["Url"]))
        except bing.BingError:
            continue
        day = bing.last_day(rows, True) or date.min
        key = (day, sum(_int(r.get("Impressions")) for r in rows))
        if best_key is None or key > best_key:
            best, best_key, best_rows = entry, key, {"traffic": rows}
    return best, best_rows


def fetch(access, site, log=print, sleep=time.sleep) -> BingData:

    """Everything Bing says about one site. A call that fails is recorded and the others still run; the site not
    being in the account is a BingError (nothing to read)."""
    data = BingData()
    user_sites = bing.rows(access.get("GetUserSites"))
    wanted = site.bing.site_url or site.site_url
    candidates = bing.matching_sites(user_sites, wanted)
    if site.bing.site_url:                       # the owner named the address: exactly that one, as the account writes it
        candidates = [e for e in candidates if bing.same_address(e.get("Url"), site.bing.site_url)]
    if not candidates:
        raise bing.BingError("the site is not in the Bing account" if not site.bing.site_url
                             else "the address named in the configuration is not in the Bing account")
    data.listed = [str(e.get("Url", "")) for e in candidates]
    entry, rows_read = candidates[0], {}
    if len(candidates) > 1:                      # http and https, with and without www, are different entries in Bing
        entry, rows_read = freshest(access, candidates, sleep)
    url = entry["Url"]
    data.address, data.verified = url, bool(entry.get("IsVerified"))
    if not entry.get("IsVerified"):
        data.problems.append("the site is not verified in the Bing account")
    for name, method in (("traffic", "GetRankAndTrafficStats"), ("queries", "GetQueryStats"),
                         ("pages", "GetPageStats"), ("crawl", "GetCrawlStats"), ("issues", "GetCrawlIssues")):
        if name in rows_read:
            rows = rows_read[name]
        else:
            sleep(CALL_PAUSE_S)
            try:
                rows = bing.rows(access.get(method, siteUrl=url))
            except bing.BingError as e:
                data.problems.append(f"{name}: {e}")
                log(f"  bing: {name} could not be read ({e})")
                continue
        data.got.add(name)
        if name == "traffic":
            data.traffic = traffic_of(rows)
        elif name == "queries":
            data.queries = stats_of(rows)
        elif name == "pages":
            data.pages = stats_of(rows)
        elif name == "crawl":
            data.crawl = [dict(r, _date=bing.parse_date(r.get("Date"))) for r in rows]
        else:
            data.issues = rows
    return data


def newest(data: "BingData") -> tuple:
    """(newest day with clicks or impressions, newest day of any traffic row, newest week of the query data, of the page data);
    None where Bing gave nothing."""
    positive = [d for d, (c, n) in data.traffic.items() if c or n]
    return (max(positive) if positive else None, max(data.traffic) if data.traffic else None,
            max((d for d, *_ in data.queries), default=None), max((d for d, *_ in data.pages), default=None))


def write_status(sheets, reports_id: str, data: "BingData", today: date, result: "BingResult" = None, folder: str = ""):
    """The Bing Status tab: which address of the site was read, which others the account lists, and how fresh Bing's data is.
    The tab is rewritten whole every run; it is only for the owner to read."""
    day = lambda d: d.isoformat() if d else "none"
    positive, any_day, queries, pages = newest(data)
    rows = [["Address read", data.address or "none"],
            ["How it is written", bing.address_form(data.address) if data.address else ""],
            ["Verified in the Bing account", "yes" if data.verified else "no"],
            ["Addresses of this site in the account", " | ".join(data.listed)],
            ["Newest day with clicks or impressions", day(positive)],
            ["Newest day in the traffic data", day(any_day)],
            ["Newest week with query data", day(queries)],
            ["Newest week with page data", day(pages)],
            ["Checked", today.isoformat()]]
    if result is not None:
        rows.append(["Bing AI export folder", folder or "not set up in this run"])
        rows.append(["Files in that folder / months stored", f"{result.ai_files} files / {result.ai_months} months"])
        rows += [["AI export: not imported", t] for t in result.ai_problems] + [["AI export: note", t] for t in result.ai_notes]
    tabs.rewrite(sheets, reports_id, STATUS, STATUS_HEADER, rows)



# ---------------------------------------------------------------------------- #
# Numbers of a period
# ---------------------------------------------------------------------------- #

def sum_traffic(traffic: dict, first: date, last: date) -> tuple:
    clicks = impr = 0
    for i in range((last - first).days + 1):
        c, n = traffic.get(first + timedelta(days=i), (0, 0))
        clicks += c
        impr += n
    return clicks, impr


def position_of(queries: list, first: date, last: date):
    """Average position of the top queries of a period, weighted by their impressions; None when there is none."""
    weighted = total = 0
    for d, _, _, n, p in queries:
        if first <= d <= last and p is not None and n:
            weighted += p * n
            total += n
    return weighted / total if total else None


def aggregate(rows: list, first: date, last: date, fix=lambda k: k) -> dict:
    """{key: (clicks, impressions, position)} of the rows dated inside a period; the position is weighted by impressions."""
    acc = {}
    for d, key, c, n, p in rows:
        if first <= d <= last:
            a = acc.setdefault(fix(key), [0, 0, 0.0, 0])
            a[0] += c
            a[1] += n
            if p is not None:
                a[2] += p * n
                a[3] += n
    return {k: (c, n, (w / m if m else None)) for k, (c, n, w, m) in acc.items()}


def _page_key(url: str) -> str:
    return re.sub(r"#.*$", "", url)


def period_row(label: str, cur: tuple, prev, position) -> list:
    clicks, impr = cur
    change = reports._diff
    return [label, clicks, impr, round(clicks / impr * 100, 2) if impr else 0,
            "" if position is None else round(position, 1),
            *change(clicks, prev[0] if prev else None), *change(impr, prev[1] if prev else None)]


def period_rows(items: list, traffic: dict, queries: list, have: set) -> list:
    """The rows of the periods (label, first, last) that are not in the tab yet. A period has the period before it
    in the list for its change; the first one has none."""
    out = []
    for i, (label, first, last) in enumerate(items):
        if label in have:
            continue
        prev = sum_traffic(traffic, items[i - 1][1], items[i - 1][2]) if i else None
        out.append(period_row(label, sum_traffic(traffic, first, last), prev, position_of(queries, first, last)))
    return out


def month_ready(last: date, today: date, newest_bucket) -> bool:
    """The weekly figures of a month are complete when a bucket at or after its end has arrived, or when it is long ago."""
    return (newest_bucket is not None and newest_bucket >= last) or (today - last).days >= READY_AFTER_DAYS


# --------------------------------------------------------------------------- #
# The Reports sheet
# --------------------------------------------------------------------------- #

def write_periods(sheets, reports_id: str, data: BingData, site, today: date, result: BingResult, log=print):
    """Bing Weekly, Bing Monthly, Bing Keywords, Bing Pages. A closed period is written once and never again."""
    if not data.traffic:
        return
    days = sorted(data.traffic)
    final = min(days[-1], today - timedelta(days=3))
    first_data = next((d for d in days if data.traffic[d][1] > 0), days[0])
    earliest = days[0]
    months = periods.complete_months(earliest, final, first_data)
    weeks = periods.complete_weeks(earliest, final, first_data)
    if months:
        result.last_month = (months[-1][0], *sum_traffic(data.traffic, months[-1][1], months[-1][2]))

    for tab, header, items, key in ((WEEKLY, WEEKLY_HEADER, weeks, "weeks"), (MONTHLY, MONTHLY_HEADER, months, "months")):
        have = {r[0] for r in tabs.read_rows(sheets, reports_id, tab, "I") if r}
        rows = period_rows(items, data.traffic, data.queries, have)
        tabs.append(sheets, reports_id, tab, rows, log)
        setattr(result, key, len(rows))

    th = {k: site.threshold(k) for k in sites.THRESHOLD_DEFAULTS}
    segments = reports.build_segments(site.page_types, site.first_match_wins, site.other_label)
    buckets = [d for d, *_ in data.queries] + [d for d, *_ in data.pages]
    newest = max(buckets) if buckets else None
    done_kw = {r[0] for r in tabs.read_rows(sheets, reports_id, KEYWORDS, "L") if r}
    done_pg = {r[0] for r in tabs.read_rows(sheets, reports_id, PAGES, "N") if r}
    kw_rows, pg_rows = [], []
    for i, (label, first, last) in enumerate(months):
        if i == 0 or not month_ready(last, today, newest):
            continue
        pfirst, plast = months[i - 1][1], months[i - 1][2]
        if label not in done_kw and data.queries:
            kw_rows += reports.keyword_rows(label, aggregate(data.queries, first, last),
                                            aggregate(data.queries, pfirst, plast), th)[0]
            result.keyword_periods += 1
        if label not in done_pg and data.pages:
            pg_rows += reports.page_rows(label, aggregate(data.pages, first, last, _page_key),
                                         aggregate(data.pages, pfirst, plast, _page_key), None, segments, th)
            result.page_periods += 1
    tabs.append(sheets, reports_id, KEYWORDS, kw_rows, log)
    tabs.append(sheets, reports_id, PAGES, pg_rows, log)


def write_crawl(sheets, reports_id: str, data: BingData, today: date, result: BingResult):
    """Bing Crawl: a row per day of crawl statistics (kept, a later answer replaces the same day) and the issues Bing lists
    now (replaced every run). Written when Bing answered either; an answer that did not come leaves what is there."""
    if not ({"crawl", "issues"} & data.got):
        return
    old = [r + [""] * (len(CRAWL_HEADER) - len(r)) for r in tabs.read_rows(sheets, reports_id, CRAWL, CRAWL_LAST) if r]
    stats = {r[1]: r for r in old if r[0] == "Stats"}
    issues = [r for r in old if r[0] == "Issue"]
    if "crawl" in data.got:
        for r in data.crawl:
            if r["_date"] is None:
                continue
            stats[r["_date"].isoformat()] = (["Stats", r["_date"].isoformat()]
                                             + [_int(r.get(k)) for k, _ in STAT_FIELDS] + ["", "", ""])
    if "issues" in data.got:
        issues = [["Issue", str(r.get("Url", "")), "", "", "", _int(r.get("InLinks"))] + [""] * 10
                  + [r.get("HttpCode", ""), str(r.get("Issues", "")), today.isoformat()] for r in data.issues]
    rows = [stats[k] for k in sorted(stats)] + issues
    tabs.rewrite(sheets, reports_id, CRAWL, CRAWL_HEADER, rows)
    result.crawl_days, result.crawl_issues = len(stats), len(issues)


# --------------------------------------------------------------------------- #
# AI citations: the monthly export of Bing's AI performance report
# --------------------------------------------------------------------------- #

CIT_WORDS = ("citation", "cited", "count", "mention")
QUERY_WORDS = ("quer", "ground", "prompt", "search", "phrase", "keyword", "question")


def _count_column(header, data, taken):
    width = max(len(r) for r in data)
    if header:
        named = [i for i, c in enumerate(header) if aio_import._header_has(c, CIT_WORDS) and i not in taken
                 and aio_import._share(aio_import._column(data, i), lambda v: aio_import.parse_number(v) is not None) >= 0.5]
        if len(named) == 1:
            return named[0]
    numeric = [i for i in range(width) if i not in taken
               and aio_import._share(aio_import._column(data, i), lambda v: aio_import.parse_number(v) is not None) >= 0.8]
    if len(numeric) == 1:
        return numeric[0]
    raise aio_import.AioImportError("could not tell which column holds the citations "
                                    "(expected a column named like 'Citations')")


def _is_text(cell) -> bool:
    t = aio_import._clean(cell)
    return bool(t) and aio_import.parse_number(t) is None and aio_import.parse_date(t) is None and not aio_import._is_url(t)


def parse_citations(values: list, kind: str) -> tuple:
    """-> ({page address or search: citations}, rows skipped) from an export tab. Columns are found by their names,
    else by what the values look like."""
    rows = [[aio_import._clean(c) for c in r] for r in values if any(aio_import._clean(c) for c in r)]
    if not rows:
        raise aio_import.AioImportError("the tab is empty")
    header, data = None, rows
    if all(not aio_import._is_url(c) and aio_import.parse_number(c) is None for c in rows[0]):
        header, data = rows[0], rows[1:]
    if not data:
        raise aio_import.AioImportError("the tab has a header and no rows")
    if kind == "Pages":
        key = aio_import._pick(header, data, aio_import.URL_WORDS + ("cited",), aio_import._is_url, "cited pages")
    else:
        key = aio_import._pick(header, data, QUERY_WORDS, _is_text, "grounding queries")
    count = _count_column(header, data, {key})
    out, skipped = {}, 0
    for r in data:
        item = r[key] if key < len(r) else ""
        n = aio_import.parse_number(r[count] if count < len(r) else "")
        if not item or n is None or (kind == "Pages" and not aio_import._is_url(item)):
            skipped += 1
            continue
        if kind == "Pages":
            item = re.sub(r"#.*$", "", item)
        else:
            item = " ".join(item.split())
        out[item] = out.get(item, 0) + int(round(n))
    if not out:
        raise aio_import.AioImportError("no row with an item and a citations number was found")
    return out, skipped


def load_citations(sheets, reports_id: str) -> dict:
    """{(period, kind, item): citations} as stored in the Bing AI Citations tab."""
    out = {}
    for r in tabs.read_rows(sheets, reports_id, CITATIONS, "G"):
        if len(r) >= 4 and aio_import.parse_number(r[3]) is not None:
            out[(r[0], r[1], r[2])] = int(round(aio_import.parse_number(r[3])))
    return out


def citation_rows(stored: dict) -> list:
    """The tab's rows: oldest month first, then the page and search lists, most cited first; each with the month before."""
    rows = []
    for (period, kind, item), n in sorted(stored.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1], kv[0][2])):
        before = periods.month_label(periods.add_months(periods.parse_month(period), -1))
        prev = stored.get((before, kind, item))
        rows.append([period, kind, item, n, "" if prev is None else prev, "" if prev is None else n - prev,
                     tabs.path_of(item) if kind == "Page" else ""])
    return rows


def import_exports(sheets, reports_id: str, exports: list, today: date, result: BingResult, log=print):
    """Stores the export files of the site's Drive folder (bing_ai.Export) in the Bing AI Citations tab, month over month. The
    files stay where they are; reading them again changes nothing (a month and kind that is read again replaces what was
    stored for it; a later file of the same month and kind wins). A file that cannot be read is said in the Bing Status tab,
    by name, and in the log, without it. No file for a month is no problem."""
    parsed = {}
    result.ai_files = len(exports)
    for ex in sorted(exports, key=lambda e: e.modified):
        label, guessed = bing_ai.month_of(ex.name, ex.modified)
        kind = bing_ai.kind_of(ex.name, ex.values)
        try:
            data, skipped = parse_citations(ex.values, kind)
        except aio_import.AioImportError as e:
            result.ai_problems.append(f'"{ex.name}" was not imported: {e}')
            continue
        if (label, kind) in parsed:
            result.ai_notes.append(f'"{ex.name}" replaces an earlier file of the same month and kind ({label} {kind})')
        if guessed:
            result.ai_notes.append(f'"{ex.name}": its name has no month, so {label} was taken (the month before the file was changed)')
        parsed[(label, kind)] = data
    if exports:
        log(f"  bing AI: {len(exports)} files in the folder, {len(parsed)} lists read, {len(result.ai_problems)} not imported")
    if parsed:
        stored = load_citations(sheets, reports_id)
        replaced = {(label, "Page" if kind == "Pages" else "Query") for label, kind in parsed}
        stored = {k: v for k, v in stored.items() if (k[0], k[1]) not in replaced}
        for (label, kind), data in parsed.items():
            for item, n in data.items():
                stored[(label, "Page" if kind == "Pages" else "Query", item)] = n
        tabs.rewrite(sheets, reports_id, CITATIONS, CITATIONS_HEADER, citation_rows(stored))
    result.ai_months = len({k[0] for k in load_citations(sheets, reports_id)})


def latest_page_citations(stored: dict) -> dict:
    """{page key: citations} of the newest month that has a page list."""
    months = sorted({k[0] for k in stored if k[1] == "Page"})
    if not months:
        return {}
    out = {}
    for (period, kind, item), n in stored.items():
        if period == months[-1] and kind == "Page":
            out[content.url_key(item)] = out.get(content.url_key(item), 0) + n
    return out


# --------------------------------------------------------------------------- #
# The content dashboard
# --------------------------------------------------------------------------- #

def page_totals(pages: list, today: date) -> dict:
    """{page key: (clicks, impressions)} of the last 90 days, pages that decode alike added up."""
    first = today - timedelta(days=DASHBOARD_DAYS - 1)
    out = {}
    for d, url, c, n, _ in pages:
        if first <= d <= today:
            a = out.setdefault(content.url_key(url), [0, 0])
            a[0] += c
            a[1] += n
    return {k: tuple(v) for k, v in out.items()}


def _basic_filter(sheets, file_id: str, sid: int):
    resp = history._read(sheets.spreadsheets().get(
        spreadsheetId=file_id, fields="sheets(properties(sheetId),basicFilter)"))
    for s in resp.get("sheets", []):
        if s["properties"].get("sheetId") == sid:
            return s.get("basicFilter")
    return None


def _column_count(sheets, file_id: str, sid: int) -> int:
    resp = history._read(sheets.spreadsheets().get(
        spreadsheetId=file_id, fields="sheets(properties(sheetId,gridProperties(columnCount)))"))
    for s in resp.get("sheets", []):
        if s["properties"].get("sheetId") == sid:
            return s["properties"].get("gridProperties", {}).get("columnCount", 0)
    return 0


ANCHOR = "Ranking Queries"                   # the last of Google's performance columns: the Bing columns follow it


def _move(sid: int, source: int, destination: int) -> dict:
    """Moves one column so that it ends at index `destination`."""
    return {"moveDimension": {"source": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": source, "endIndex": source + 1},
                              "destinationIndex": destination + 1 if source < destination else destination}}


def add_columns(sheets, file_id: str, tab: str, sid: int, header: list, names: list, anchors: tuple = None) -> list:
    """Puts the Bing columns, in their order, right after the last of Google's performance columns (at the end when that
    column is not in the tab). A column that is somewhere else (an earlier version put them at the end) is moved; one that
    is in place is left. Inserting and moving keep every column's format, rules and filter with it. Styles what it adds
    like the other columns the tool writes. Returns the new header."""
    header = list(header)
    before = len(header)
    present = [header.index(a) + 1 for a in (anchors or (ANCHOR,)) if a in header]       # after the last of the anchor columns found
    start = max(present) if present else before
    grid = _column_count(sheets, file_id, sid)
    requests, written, at_end = [], [], False
    for i, name in enumerate(names):
        target = start + i
        if name in header:
            at = header.index(name)
            if at != target:
                requests.append(_move(sid, at, target))
                header.insert(target, header.pop(at))
            continue
        if target < len(header):                # in the middle: the grid grows by the column
            requests.append({"insertDimension": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": target,
                                                           "endIndex": target + 1}, "inheritFromBefore": False}})
            grid += 1
        else:                                    # after the last column
            at_end = True
            if grid <= target:
                requests.append({"appendDimension": {"sheetId": sid, "dimension": "COLUMNS", "length": 1}})
                grid += 1
        header.insert(target, name)
        written.append((target, name))
        requests.append(cs.header_cell(sid, target, target + 1, cs.GREY, cs.NAVY))
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": target, "endIndex": target + 1},
            "properties": {"pixelSize": 120}, "fields": "pixelSize"}})
        requests.append(cs._fmt(sid, "backgroundColor", {"backgroundColor": cs.rgb(cs.TINT_GREY)},
                                r1=1, c1=target, c2=target + 1))
    if not requests:
        return header
    if at_end:                                   # the owner's filter, with its conditions, takes the new last columns in
        flt = _basic_filter(sheets, file_id, sid)
        if flt and flt.get("range", {}).get("endColumnIndex") == before:
            requests.append({"setBasicFilter": {"filter": dict(flt, range=dict(flt["range"], endColumnIndex=len(header)))}})
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": requests}))
    if written:
        tabs.patch_cells(sheets, file_id, [(f"'{tab}'!{cs.letters(c)}1", [[n]]) for c, n in written])
    return header


def fill_dashboard(sheets, file_id: str, tab_names: list, page_stats: dict, citations: dict, have_pages: bool = False,
                   log=print) -> int:
    """The three Bing columns of every page-group tab, by page address. The Clicks and Impressions columns are added when
    Bing gave any page at all (a page it does not list in the last 90 days gets 0); the citations column when an export came. A column is only added when there is something
    to put in it. A row without a page address, and a row that is settled (page gone), keeps what its cells hold.
    Returns the number of tabs written."""
    info = cs.sheet_info(sheets, file_id)
    wanted = [n for n, have in ((COL_CLICKS, have_pages), (COL_IMPR, have_pages), (COL_AI, bool(citations)))
              if have]
    if not wanted:
        return 0
    done = 0
    for tab in tab_names:
        if tab not in info:
            continue
        sid = info[tab][0]
        header = cs.read_header(sheets, file_id, tab)
        if content.URL not in header:
            continue
        header = add_columns(sheets, file_id, tab, sid, header, wanted)
        _, rows = cs.table_of(cs.read_all(sheets, file_id, tab))
        columns = {name: [] for name in wanted}
        for row in rows:
            url = str(row.get(content.URL, "")).strip()
            key = content.url_key(url) if url else ""
            skip = not key or content.is_settled(row)
            for name in wanted:
                current = row.get(name, "")
                if skip:
                    columns[name].append(current)
                elif name == COL_CLICKS:
                    columns[name].append(page_stats.get(key, (0, 0))[0])
                elif name == COL_IMPR:
                    columns[name].append(page_stats.get(key, (0, 0))[1])
                else:
                    columns[name].append(citations.get(key, 0))
        cs.write_columns(sheets, file_id, tab, header, 2, columns)
        done += 1
    return done


# --------------------------------------------------------------------------- #
# One site
# --------------------------------------------------------------------------- #

def run_bing(sheets, site, access, today: date, log=print, drive=None, sleep=time.sleep, exports=None, folder: str = "") -> BingResult:
    """The Bing tabs of the site's Reports sheet and the Bing columns of its content dashboard. `access` is a bing.Access
    (the data code does not care how it was obtained). Raises only what is wrong with the sheets; a Bing error is
    recorded in the result and everything that does not need Bing still runs."""
    result = BingResult()
    rid = site.reports_sheet_id
    if not rid:
        result.no_data = True
        result.problems.append("no reports sheet")
        return result
    try:
        data = fetch(access, site, log, sleep)
    except bing.BingError as e:
        log(f"  bing: {e}")
        data = BingData(problems=[str(e)])
    result.problems += data.problems
    result.no_data = data.empty
    result.queries = len({q for _, q, *_ in data.queries})
    result.pages = len({_page_key(u) for _, u, *_ in data.pages})

    titles, _ = history.grid_info(sheets, rid)
    for name, header in TABS:
        tabs.ensure_tab(sheets, rid, titles, name, header)
    if exports is not None:                      # the files of the site's Bing AI export folder (a callable; it may raise)
        try:
            import_exports(sheets, rid, exports(), today, result, log)
        except Exception as e:  # noqa: BLE001  an export that cannot be read never stops the Bing data
            result.ai_problems.append(f"the export folder could not be read ({type(e).__name__})")
            log(f"  bing AI: the export folder could not be read ({type(e).__name__})")
    result.ai_months = len({k[0] for k in load_citations(sheets, rid)})
    write_periods(sheets, rid, data, site, today, result, log)
    write_crawl(sheets, rid, data, today, result)
    if data.address:
        write_status(sheets, rid, data, today, result, folder)
        positive, _, queries, pages = newest(data)
        log(f"  bing: address read: {bing.address_form(data.address)} ({len(data.listed)} listed in the account); "
            f"newest day with clicks or impressions {positive or 'none'}, newest week of queries {queries or 'none'}, of pages {pages or 'none'}")

    if drive is not None and site.content.enabled and site.auth == "me":
        file_id = provision.existing_content_file(drive, sheets, site)
        if file_id:
            result.dashboard = fill_dashboard(
                sheets, file_id, [t.name for t in site.content.tabs], page_totals(data.pages, today),
                latest_page_citations(load_citations(sheets, rid)), have_pages=bool(data.pages), log=log)
    return result
