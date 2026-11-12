"""
PulseO: the daily health checks of a site (technical module, phase 1)
=====================================================================
Read only: this module only asks the site for pages, robots.txt and sitemaps (and Search Console for the sitemaps it
lists), and decides what is worth telling the owner.

  * checks      the home page, robots.txt, the sitemaps and a set of key pages (the top pages by clicks plus the pages
                named in the configuration): answer, noindex, canonical, server errors
  * confirm     a check that failed is made again after a wait (the configured minutes); only a problem that fails twice
                is a problem
  * decide      against the problems that are already open: a new problem is told once, a worse one is told again, a
                solved one is told once ("Fixed"); a big change of the key pages is one "Large change" alert
  * the text    of the email: what broke, since when, which pages, how many clicks they had, what to check first

Everything that touches the world (requests, clock, waiting, Search Console) is handed in, so all of it is testable
offline. Nothing here prints an address: counts only. No tool name is written anywhere a client or the owner reads.
"""

import html
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

import content
import site_check
import tabs

TIMEOUT_S = 20
MAX_BODY = 400_000
WORKERS = 4
KEY_PAGES = 20                     # the top pages by clicks that are watched
MIN_COMPARED = 5                   # a large change needs at least this many key pages seen in both runs
ROBOTS_TEXT_MAX = 45_000           # a sheet cell holds 50,000 characters
SERVER_ERROR = 500


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #

@dataclass
class Fetched:
    """What one request showed. status 0 means there was no answer."""
    status: int = 0
    headers: dict = field(default_factory=dict)      # lower-case names; a header sent twice is joined with a new line
    body: str = ""
    seconds: float = 0.0
    location: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _headers_of(msg) -> dict:
    out = {}
    if msg is None:
        return out
    for name in set(k.lower() for k in msg.keys()):
        out[name] = "\n".join(msg.get_all(name) or [])
    return out


def _decode(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def http_fetch(url: str, follow: bool = False, timeout: float = TIMEOUT_S, clock=time.monotonic) -> Fetched:
    """One GET with a normal browser user agent. Redirects are followed only when asked. Never raises."""
    req = urllib.request.Request(site_check.ascii_url(url), headers={
        "User-Agent": site_check.BROWSER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8"})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    start = clock()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY)
            return Fetched(resp.status, _headers_of(resp.headers), _decode(raw, resp.headers.get("Content-Type", "")),
                           clock() - start)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(20_000)
        except OSError:
            raw = b""
        headers = _headers_of(e.headers)
        return Fetched(e.code, headers, _decode(raw, headers.get("content-type", "")), clock() - start,
                       headers.get("location", ""))
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException, socket.timeout):
        return Fetched(0, {}, "", clock() - start)


# --------------------------------------------------------------------------- #
# Reading robots.txt and a page
# --------------------------------------------------------------------------- #

def _robots_lines(text: str) -> list:
    out = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" in line:
            name, _, value = line.partition(":")
            out.append((name.strip().lower(), value.strip()))
    return out


def robots_groups(text: str) -> list:
    """[(set of agents, [(rule, path)])]: consecutive user-agent lines share the rules that follow them."""
    groups, agents, rules, last_was_agent = [], set(), [], False
    for name, value in _robots_lines(text):
        if name == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = set(), []
            agents.add(value.lower())
            last_was_agent = True
        elif name in ("disallow", "allow"):
            rules.append((name, value))
            last_was_agent = False
        else:
            last_was_agent = False
    if agents or rules:
        groups.append((agents, rules))
    return groups


def robots_blocks_all(text: str) -> bool:
    """Does robots.txt keep Googlebot away from the whole site? The rules for Googlebot win over those for every agent."""
    groups = robots_groups(text)
    own = [g for g in groups if "googlebot" in g[0]] or [g for g in groups if "*" in g[0]]
    for _, rules in own:
        blocks = any(name == "disallow" and value in ("/", "/*") for name, value in rules)
        allows = any(name == "allow" and value in ("/", "/*") for name, value in rules)
        if blocks and not allows:
            return True
    return False


def robots_sitemaps(text: str) -> list:
    out = []
    for name, value in _robots_lines(text):
        if name == "sitemap" and value and value not in out:
            out.append(value)
    return out


def _normal_robots(text: str) -> list:
    return [re.sub(r"\s+", " ", l).strip() for l in (text or "").replace("\r", "").split("\n")
            if l.strip() and not l.strip().startswith("#")]


def robots_diff(old: str, new: str) -> tuple:
    """(added lines, removed lines) of the rules, comments and blank lines ignored."""
    a, b = _normal_robots(old), _normal_robots(new)
    return [l for l in b if l not in a], [l for l in a if l not in b]


def _attr(tag: str, name: str) -> str:
    m = re.search(r"""\b%s\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""" % name, tag, re.I)
    return html.unescape(next((g for g in m.groups() if g is not None), "")) if m else ""


def _noindex_text(value: str) -> bool:
    return bool(re.search(r"(?<![\w-])(noindex|none)(?![\w-])", value or "", re.I))


def header_noindex(value: str) -> bool:
    """X-Robots-Tag: a line for every agent, or for Googlebot, that says noindex. Lines for other agents do not count."""
    for line in (value or "").split("\n"):
        m = re.match(r"\s*([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if m and m.group(1).lower() != "unavailable_after":
            if m.group(1).lower() == "googlebot" and _noindex_text(m.group(2)):
                return True
        elif _noindex_text(line):
            return True
    return False


@dataclass
class PageFacts:
    noindex: bool = False
    canonical: str = ""


def page_facts(f: Fetched, url: str) -> PageFacts:
    """Does the page say noindex (meta robots / googlebot or the X-Robots-Tag header), and what is its canonical?"""
    facts = PageFacts(noindex=header_noindex(f.headers.get("x-robots-tag", "")))
    for tag in re.findall(r"<meta\b[^>]*>", f.body, re.I):
        if _attr(tag, "name").lower() in ("robots", "googlebot") and _noindex_text(_attr(tag, "content")):
            facts.noindex = True
    for tag in re.findall(r"<link\b[^>]*>", f.body, re.I):
        if "canonical" in _attr(tag, "rel").lower().split() and _attr(tag, "href"):
            facts.canonical = urllib.parse.urljoin(url, _attr(tag, "href").strip())
            break
    return facts


# --------------------------------------------------------------------------- #
# What a check finds
# --------------------------------------------------------------------------- #

@dataclass
class Result:
    key: str                                   # home | robots | robots_block | sitemap:<address> | sitemap_drop:<address> |
    ok: bool = True                            # key_status | key_noindex | key_canonical | server_errors | sitemaps_api
    skipped: bool = False                      # the check could not be made: nothing is concluded from it
    detail: str = ""
    pages: list = field(default_factory=list)  # addresses of the pages it touches
    clicks: int = 0                            # clicks of those pages in the last 28 days
    count: int = 0                             # how much is wrong (pages, errors): more than before is "worse"
    note: str = ""                             # "changed" for robots.txt; "not confirmed" after a failed second check

    @property
    def family(self) -> str:
        return self.key.split(":", 1)[0]


# family -> (what the check is called, the problem as the subject says it, the first thing to check)
FAMILIES = {
    "home": ("Home page", "Home page not answering",
             "Open the home page in a browser and check the hosting and the server."),
    "robots": ("robots.txt", "robots.txt not available",
               "Open /robots.txt in a browser: it should answer with the file. Check the hosting and any recent change to it."),
    "robots_changed": ("robots.txt changes", "robots.txt changed", ""),
    "robots_block": ("robots.txt blocking", "robots.txt blocks the whole site",
                     "Open /robots.txt and remove the line that blocks everything (Disallow: /) if it is not meant to be there."),
    "sitemap": ("Sitemap", "Sitemap not readable",
                "Open the sitemap address in a browser: the file must exist and be valid. Check the plugin or script that makes it."),
    "sitemap_drop": ("Sitemap size", "Sitemap lost many pages",
                     "Compare the sitemap with the last one you know: find what removed the pages (a plugin, a filter, a failed build)."),
    "key_status": ("Key pages answer", "Key pages not answering 200",
                   "Open these pages: check for deleted pages, redirects or errors after a recent change."),
    "key_blocked": ("Key pages blocked for checker", "Key pages blocked for the checker only",
                     "These pages are live but refuse the checker (403); look at them with the index inspection instead."),
    "key_noindex": ("Key pages noindex", "Key pages set to noindex",
                    "Check the robots meta tag and the X-Robots-Tag header of these pages: a noindex keeps them out of Google."),
    "key_canonical": ("Key pages canonical", "Key pages point to another canonical",
                      "Check the canonical tag of these pages: it should point to the page itself unless another page is meant."),
    "server_errors": ("Server errors", "Server errors on key pages",
                      "Check the server and the hosting logs: many key pages are failing at once."),
    "sitemaps_api": ("Sitemaps in Search Console", "Sitemap error in Search Console",
                     "Open Search Console, Sitemaps, and read the error shown for the sitemap."),
    "large_change": ("Key pages changed", "Large change detected",
                     "Check what was published or changed on the site today (a migration, a redirect rule, a new build)."),
}


def name_of(key: str) -> str:
    return FAMILIES.get(key.split(":", 1)[0], (key, key, ""))[0]


def title_of(key: str) -> str:
    return FAMILIES.get(key.split(":", 1)[0], (key, key, ""))[1]


def check_first(key: str) -> str:
    return FAMILIES.get(key.split(":", 1)[0], (key, key, ""))[2]


def _status_text(f: Fetched) -> str:
    if f.status == 0:
        return "no answer (timeout or connection problem)"
    return f"answered {f.status}"


@dataclass
class Context:
    """What the checks of one site work with."""
    site: object                                   # sites.Site
    origin: str
    key_urls: list                                 # the key pages
    clicks: dict                                   # {content.url_key(page): clicks in the last 28 days}
    fetch: object = http_fetch                     # fetch(url, follow=False) -> Fetched
    get: object = site_check.default_get           # get(url) -> (status, bytes): robots.txt and sitemaps, redirects followed
    sitemaps_api: object = None                    # () -> [{"path", "errors", "warnings"}] or raises
    prev_robots: str = None                        # the last version of robots.txt that was read; None: never
    prev_counts: dict = field(default_factory=dict)  # {sitemap address: number of pages} of the last good run
    extra_sitemaps: list = field(default_factory=list)
    daily: bool = False                            # the small daily check: the home page and robots.txt only
    blocked: set = field(default_factory=set)      # content.url_key of the key pages that are known to refuse the checker only
    # filled while the checks run, saved afterwards
    robots_text: str = None
    sitemap_counts: dict = field(default_factory=dict)
    key_status: dict = field(default_factory=dict)  # {page: status code} of the key pages seen in this round

    def clicks_of(self, urls) -> int:
        return sum(self.clicks.get(content.url_key(u), 0) for u in urls)

    @property
    def cfg(self):
        return self.site.technical


def check_home(ctx: Context) -> list:
    if not ctx.origin:
        return [Result("home", skipped=True, detail="the site address is not known")]
    home = ctx.origin + "/"
    f = ctx.fetch(home, True)
    slow = f.status == 200 and f.seconds > ctx.cfg.slow_seconds
    if f.status == 200 and not slow:
        detail = f"answered 200 in {f.seconds:.1f}s"
    elif slow:
        detail = f"answered 200 but took {f.seconds:.1f}s (the limit is {ctx.cfg.slow_seconds:g}s)"
    else:
        detail = _status_text(f)
    return [Result("home", f.status == 200 and not slow, detail=detail, pages=[home],
                   clicks=ctx.clicks_of([home]), count=1)]


def check_robots(ctx: Context) -> list:
    if not ctx.origin:
        return [Result("robots", skipped=True), Result("robots_block", skipped=True)]
    status, body = ctx.get(ctx.origin + "/robots.txt")
    if status != 200:
        note = " (Google treats a missing file as no rules)" if status in (404, 410) else ""
        text = "no answer (timeout or connection problem)" if status == 0 else f"answered {status}{note}"
        return [Result("robots", False, detail=text, pages=[ctx.origin + "/robots.txt"], count=1),
                Result("robots_block", skipped=True, detail="robots.txt could not be read")]
    text = body.decode("utf-8", errors="replace")
    out = [Result("robots", True, detail="answered 200")]
    blocked = robots_blocks_all(text)
    out.append(Result("robots_block", not blocked, count=1,
                      detail="the rules keep Googlebot away from the whole site (Disallow: /)" if blocked
                      else "no rule blocks the whole site"))
    if ctx.prev_robots is not None and _normal_robots(ctx.prev_robots) != _normal_robots(text):
        added, removed = robots_diff(ctx.prev_robots, text)
        parts = [f"+ {l[:80]}" for l in added[:4]] + [f"- {l[:80]}" for l in removed[:4]]
        out.append(Result("robots_changed", True, note="changed",
                          detail=f"{len(added)} lines added, {len(removed)} removed: " + "; ".join(parts)))
    ctx.robots_text = text
    return out


def sitemap_roots(ctx: Context, robots_text) -> list:
    roots = robots_sitemaps(robots_text) if robots_text is not None else list(ctx.prev_counts)
    for e in ctx.extra_sitemaps:
        if e and e not in roots:
            roots.append(e)
    return roots or [ctx.origin + "/sitemap.xml"]


def check_sitemaps(ctx: Context, robots_text) -> list:
    if not ctx.origin:
        return []
    out, seen = [], set()
    for root in sitemap_roots(ctx, robots_text):
        total, child_failed, queue = 0, False, [root]
        while queue and len(seen) < site_check.MAX_SITEMAPS:
            address = queue.pop(0)
            if address in seen:
                continue
            seen.add(address)
            status, body = ctx.get(address)
            kind, locs = site_check._locs(body) if status == 200 else ("", [])
            if not kind:
                why = ("no answer (timeout or connection problem)" if status == 0 else
                       f"answered {status}" if status != 200 else "the file is not a valid sitemap")
                out.append(Result(f"sitemap:{address}", False, detail=why, pages=[address], count=1))
                child_failed = child_failed or address != root
                continue
            if kind == "index":
                queue.extend(locs)
            else:
                total += len(set(locs))
            out.append(Result(f"sitemap:{address}", True, detail=f"{len(set(locs))} {'sitemaps' if kind == 'index' else 'addresses'}"))
        failed_root = any(r.key == f"sitemap:{root}" and not r.ok for r in out)
        if failed_root or child_failed:
            continue                                                   # a size cannot be told from a partial read
        ctx.sitemap_counts[root] = total
        before = ctx.prev_counts.get(root)
        share = ctx.cfg.sitemap_drop_alert
        dropped = before is not None and before - total >= 2 and total < before * (1 - share)
        out.append(Result(f"sitemap_drop:{root}", not dropped, pages=[root], count=1,
                          detail=(f"{total} addresses, it had {before}" if dropped else f"{total} addresses")))
    return out


REDIRECTS = (301, 302, 303, 307, 308)


def fetch_page(ctx: Context, url: str) -> Fetched:
    """One key page. A redirect that only adds or removes a slash, or moves to https or www, leads to the same page: the
    page it leads to is the one that is checked. A redirect to another page stays what it is: a change."""
    f = ctx.fetch(url, False)
    for _ in range(2):
        target = urllib.parse.urljoin(url, f.location or f.headers.get("location", "")) if f.status in REDIRECTS else ""
        if not target or target == url or content.url_key(target) != content.url_key(url):
            break
        url, f = target, ctx.fetch(target, False)
    return f


def check_keypages(ctx: Context, only=None) -> list:
    """The key pages (or, in the second round, only those that failed): answer, noindex, canonical, server errors."""
    blocked = [u for u in ctx.key_urls if content.url_key(u) in ctx.blocked]       # listed by the owner: live, but refuse the checker
    urls = [u for u in ctx.key_urls if u not in blocked and (only is None or u in only)]
    if not ctx.key_urls:
        return [Result(k, skipped=True, detail="no key pages known") for k in ("key_status", "key_noindex", "key_canonical", "server_errors")]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fetched = dict(zip(urls, pool.map(lambda u: fetch_page(ctx, u), urls)))
    status_bad, noindex, canon, server = [], [], [], []
    codes = {}
    for url, f in fetched.items():
        ctx.key_status[url] = f.status
        if f.status != 200:
            status_bad.append(url)
            codes[f.status] = codes.get(f.status, 0) + 1
            if f.status == 0 or f.status >= SERVER_ERROR:
                server.append(url)
            continue
        facts = page_facts(f, url)
        if facts.noindex:
            noindex.append(url)
        if facts.canonical and content.url_key(facts.canonical) != content.url_key(url):
            canon.append(url)
    total = len(ctx.key_urls) - len(blocked)

    def make(key, bad, detail, count=None):
        return Result(key, not bad, pages=list(bad), clicks=ctx.clicks_of(bad), count=len(bad) if count is None else count,
                      detail=detail if bad else "all key pages are fine")

    shown = ", ".join(f"{'no answer' if c == 0 else c} x{n}" for c, n in sorted(codes.items()))
    share = len(server) / total if total else 0
    out = [
        make("key_status", status_bad, f"{len(status_bad)} of {total} key pages did not answer 200 ({shown})"),
        make("key_noindex", noindex, f"{len(noindex)} of {total} key pages tell search engines not to index them"),
        make("key_canonical", canon, f"{len(canon)} of {total} key pages have a canonical that points to another page"),
        make("server_errors", server if share > ctx.cfg.server_error_share else [],
             f"{len(server)} of {total} key pages failed with a server error or no answer"),
    ]
    if blocked:
        out.append(Result("key_blocked", True, note="blocked", pages=blocked, clicks=ctx.clicks_of(blocked), count=len(blocked),
                          detail=f"{len(blocked)} key pages are listed as blocked for the checker only: not checked here"))
    return out


def check_sitemaps_api(ctx: Context) -> list:
    if ctx.sitemaps_api is None:
        return [Result("sitemaps_api", skipped=True, detail="not available")]
    try:
        items = ctx.sitemaps_api()
    except Exception:                                  # noqa: BLE001  the Search Console answer is not a reason for an alert
        return [Result("sitemaps_api", skipped=True, detail="the list could not be read")]
    bad = []
    for item in items:
        try:
            errors = int(item.get("errors") or 0)
        except (TypeError, ValueError):
            errors = 0
        if errors > 0:
            bad.append((str(item.get("path", "")), errors))
    return [Result("sitemaps_api", not bad, pages=[p for p, _ in bad], count=sum(e for _, e in bad),
                   detail=(f"{len(bad)} submitted sitemaps show errors ({sum(e for _, e in bad)} in all)" if bad
                           else f"{len(items)} submitted sitemaps, no errors"))]


@dataclass
class Round:
    results: list
    key_status: dict
    robots_text: str
    sitemap_counts: dict


def check_all(ctx: Context, only: dict = None) -> Round:
    """One round of every check. `only` ({result key: its pages}) makes it the second round: only what failed is asked again."""
    ctx.key_status, ctx.sitemap_counts, ctx.robots_text = {}, {}, None
    results = []
    failing = only or {}

    def wanted(*keys):
        return only is None or any(k in failing or any(f.startswith(k + ":") for f in failing) for k in keys)

    if wanted("home"):
        results += check_home(ctx)
    robots_text = None
    if wanted("robots", "robots_block", "sitemap", "sitemap_drop"):
        r = check_robots(ctx)
        results += [x for x in r if only is None or x.key in failing]
        robots_text = ctx.robots_text
    if ctx.daily:                                  # the small daily check: only the home page and robots.txt
        return Round(results, {}, ctx.robots_text, {})
    if wanted("sitemap", "sitemap_drop"):
        results += check_sitemaps(ctx, robots_text)
    key_keys = ("key_status", "key_noindex", "key_canonical", "server_errors")
    if wanted(*key_keys):
        if only is None:
            results += check_keypages(ctx)
        else:
            pages = {u for k in key_keys for u in failing.get(k, [])}
            results += check_keypages(ctx, pages)
    if wanted("sitemaps_api"):
        results += check_sitemaps_api(ctx)
    return Round(results, dict(ctx.key_status), ctx.robots_text, dict(ctx.sitemap_counts))


def failing_of(rnd: Round) -> dict:
    """{key: pages} of what failed in a round: what the second round asks again."""
    return {r.key: list(r.pages) for r in rnd.results if not r.ok and not r.skipped}


@dataclass
class LogLine:
    key: str
    result: str          # ok | failed (first check) | confirmed problem | not confirmed | skipped | changed
    detail: str = ""
    pages: list = field(default_factory=list)
    clicks: int = 0


def confirm(first: Round, second: Round = None, ctx: Context = None) -> tuple:
    """(final results, log lines). A problem of the first round is confirmed when the second round fails it too (for a set of
    pages: on the pages that failed twice). Without a second round nothing failing is confirmed."""
    again = {r.key: r for r in (second.results if second else [])}
    final, log = [], []
    for r in first.results:
        if r.skipped:
            log.append(LogLine(r.key, "skipped", r.detail))
            final.append(r)
        elif r.note == "changed":
            log.append(LogLine(r.key, "changed", r.detail))
            final.append(r)
        elif r.note == "blocked":
            log.append(LogLine(r.key, "blocked for checker only", r.detail, r.pages, r.clicks))
            final.append(r)
        elif r.ok:
            log.append(LogLine(r.key, "ok", r.detail))
            final.append(r)
        else:
            log.append(LogLine(r.key, "failed (first check)", r.detail, r.pages, r.clicks))
            r2 = again.get(r.key)
            if r2 is not None and not r2.ok and not r2.skipped:
                pages = [p for p in r.pages if p in set(r2.pages)] if r.pages and r2.pages else list(r2.pages)
                if pages or not r.pages:
                    clicks = ctx.clicks_of(pages) if ctx and pages else r2.clicks
                    keep = Result(r.key, False, detail=r2.detail, pages=pages, clicks=clicks,
                                  count=len(pages) if r.key.startswith(("key_", "server_")) else max(r2.count, 1))
                    if r.key == "sitemaps_api":
                        keep.count = r2.count
                    final.append(keep)
                    log.append(LogLine(r.key, "confirmed problem", keep.detail, pages, clicks))
                    continue
            if r2 is not None and r2.skipped:
                final.append(Result(r.key, skipped=True, detail="the second check could not be made"))
                log.append(LogLine(r.key, "skipped", "the second check could not be made"))
                continue
            final.append(Result(r.key, True, detail=r2.detail if r2 else "passed the second check", note="not confirmed"))
            log.append(LogLine(r.key, "not confirmed", "the second check passed"))
    return final, log


# --------------------------------------------------------------------------- #
# What is remembered between runs
# --------------------------------------------------------------------------- #

class TechState:
    """Rows of the hidden `_state` tab of the Technical sheet: [kind, key, value, updated]."""

    def __init__(self, values: list = None):
        self.rows = []
        for r in (values or [])[1:]:
            r = [str(c) for c in list(r)[:4]] + [""] * (4 - len(r))
            if r[0]:
                self.rows.append(r)

    def get(self, kind: str, key: str, default=None):
        for r in self.rows:
            if r[0] == kind and r[1] == key:
                return r[2]
        return default

    def set(self, kind: str, key: str, value: str, today: date):
        for r in self.rows:
            if r[0] == kind and r[1] == key:
                r[2], r[3] = value, today.isoformat()
                return
        self.rows.append([kind, key, value, today.isoformat()])

    def delete(self, kind: str, key: str):
        self.rows = [r for r in self.rows if not (r[0] == kind and r[1] == key)]

    def of(self, kind: str) -> dict:
        return {r[1]: r[2] for r in self.rows if r[0] == kind}

    def clear(self, kind: str):
        self.rows = [r for r in self.rows if r[0] != kind]

    def table(self) -> list:
        return self.rows

    # the open problems: {key: {"since", "title", "count", "alerted", "alerted_count"}}
    def open(self) -> dict:
        out = {}
        for key, value in self.of("open").items():
            try:
                out[key] = json.loads(value)
            except ValueError:
                continue
        return out

    def save_open(self, opened: dict, today: date):
        self.clear("open")
        for key, rec in opened.items():
            self.rows.append(["open", key, json.dumps(rec, ensure_ascii=False), today.isoformat()])


# --------------------------------------------------------------------------- #
# What is worth saying
# --------------------------------------------------------------------------- #

@dataclass
class Alert:
    key: str
    title: str
    detail: str
    since: str
    pages: list
    clicks: int
    kind: str = "new"          # new | worse
    was: int = 0               # for "worse": how much was wrong when it was last told


@dataclass
class Decision:
    alerts: list = field(default_factory=list)
    fixed: list = field(default_factory=list)       # [(title, since)]
    large: dict = None                              # {"changed": n, "total": n, "pages": [...], "clicks": n}
    opened: dict = field(default_factory=dict)      # the open problems as they stand after this run
    told: list = field(default_factory=list)        # keys whose open record is marked as told when the email goes out


KEY_FAMILIES = ("key_status", "key_noindex", "key_canonical", "server_errors")


def large_change(prev: dict, now: dict, share: float):
    """{"changed", "total"} when more than `share` of the key pages seen in both runs changed their status, else None."""
    both = [u for u in now if u in prev and prev[u] is not None]
    if len(both) < MIN_COMPARED:
        return None
    changed = [u for u in both if prev[u] != now[u]]
    if len(changed) / len(both) > share:
        return {"changed": len(changed), "total": len(both), "pages": changed}
    return None


def decide(results: list, opened: dict, today: date, large: dict = None, clicks_of=lambda urls: 0) -> Decision:
    """Against the open problems: a new problem is told, a worse one is told again, one that is gone is told as fixed.
    A skipped check changes nothing. During a large change the key-page problems are not told one by one."""
    d = Decision(opened={k: dict(v) for k, v in opened.items()})
    folded = set()
    unsure = {r.key for r in results if r.note == "not confirmed"}          # failed once, passed the second check: unknown
    checked = {r.key for r in results if not r.skipped and r.key not in unsure}
    skipped_families = {r.family for r in results if r.skipped}
    families_run = {r.family for r in results}
    if large:
        folded = set(KEY_FAMILIES)
        d.large = dict(large, clicks=clicks_of(large["pages"]))
    for r in results:
        if r.skipped or r.note == "changed" or r.ok:
            continue
        rec = d.opened.get(r.key)
        title = title_of(r.key)
        if rec is None:
            rec = {"since": today.isoformat(), "title": title, "count": r.count, "alerted": "", "alerted_count": 0}
            d.opened[r.key] = rec
            new = True
        else:
            rec["count"], new = r.count, not rec.get("alerted")
        worse = bool(rec.get("alerted")) and r.count > int(rec.get("alerted_count", 0))
        if new or worse:
            d.told.append(r.key)
            if r.family not in folded:                       # during a large change one alert says it all
                d.alerts.append(Alert(r.key, title, r.detail, rec["since"], list(r.pages), r.clicks,
                                      "new" if new else "worse", int(rec.get("alerted_count", 0)) if worse else 0))
    for key in list(d.opened):
        rec = d.opened[key]
        family = key.split(":", 1)[0]
        passed = key in checked and not any(r.key == key and not r.ok for r in results)
        gone = key not in checked and key not in unsure and family in families_run and family not in skipped_families
        if passed or gone:
            if rec.get("alerted") and family not in folded:
                d.fixed.append((rec.get("title") or title_of(key), rec.get("since", "")))
            del d.opened[key]
    return d


def mark_told(opened: dict, keys: list, today: date) -> dict:
    """The open problems whose email went out are marked as told, with how much was wrong at that time."""
    for key in keys:
        if key in opened:
            opened[key]["alerted"] = today.isoformat()
            opened[key]["alerted_count"] = opened[key].get("count", 0)
    return opened


# --------------------------------------------------------------------------- #
# The email
# --------------------------------------------------------------------------- #

def readable(url: str) -> str:
    path = tabs.path_of(url)
    return path if path != "/" or not url else "/ (home page)"


def pages_line(pages: list, limit: int = 8) -> str:
    shown = ", ".join(readable(p) for p in pages[:limit])
    return shown + (f" (and {len(pages) - limit} more)" if len(pages) > limit else "")


def _day(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso


def alert_block(site_label: str, a: Alert) -> list:
    lines = [f"{site_label} · {a.title}", f"What broke: {a.detail}"]
    if a.kind == "worse":
        lines.append(f"This got worse since it was last reported (before: {a.was}).")
    lines.append(f"Since: {_day(a.since)}")
    if a.pages:
        lines.append(f"Pages: {pages_line(a.pages)}")
        lines.append(f"Clicks on those pages in the last 28 days: {a.clicks:,}")
    lines.append(f"First thing to check: {check_first(a.key)}")
    return lines


def large_block(site_label: str, large: dict) -> list:
    return [f"{site_label} · Large change detected",
            f"What happened: {large['changed']} of {large['total']} key pages changed their answer since the last check. "
            "The single problems are not listed one by one.",
            f"Pages: {pages_line(large['pages'])}",
            f"Clicks on those pages in the last 28 days: {large.get('clicks', 0):,}",
            f"First thing to check: {check_first('large_change')}"]


def build_email(entries: list):
    """entries: [(site label, Decision)]. (subject, body), or None when there is nothing to say."""
    problems = [(label, a) for label, d in entries for a in d.alerts]
    larges = [(label, d.large) for label, d in entries if d.large]
    fixed = [(label, t, s) for label, d in entries for t, s in d.fixed]
    n = len(problems) + len(larges)
    if not n and not fixed:
        return None
    if n == 1:
        label, what = (problems[0][0], problems[0][1].title) if problems else (larges[0][0], "Large change detected")
        subject = f"Site alert · {label} · {what}"
    elif n:
        subject = f"Site alert · {n} problems"
    elif len(fixed) == 1:
        subject = f"Fixed · {fixed[0][0]} · {fixed[0][1]}"
    else:
        subject = f"Fixed · {len(fixed)} problems"
    parts = []
    for label, large in larges:
        parts += large_block(label, large) + [""]
    for label, a in problems:
        parts += alert_block(label, a) + [""]
    if fixed:
        parts.append("Fixed")
        parts += [f"{label} · {title} (open since {_day(since)})" for label, title, since in fixed] + [""]
    return subject, "\n".join(parts).rstrip() + "\n"
