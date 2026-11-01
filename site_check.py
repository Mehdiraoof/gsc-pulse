"""
PulseO: looking at the live site for the content dashboard (v2.7)
==================================================================
Two jobs, both read-only and both with an injected HTTP function so they are testable offline:

  * the sitemap(s) of a site: every page address in them (robots.txt first, then /sitemap.xml;
    sitemap indexes are followed). If the sitemap cannot be read, the caller adds nothing.
  * the status of a page address: one request without following redirects, a normal browser user
    agent, a timeout, a few requests at a time. A 404 or 410 is "gone", 301/302/307/308 is
    "redirect", 2xx is "ok" and anything else (403, 429, 5xx, no answer, blocked) is "unknown",
    which never changes anything.

The same request reads the page title (used for a row that has no searches at all).

Nothing here prints an address.
"""

import gzip
import html
import http.client
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import content

BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/124.0.0.0 Safari/537.36")
TIMEOUT_S = 15
WORKERS = 4                     # requests at the same time
BATCH = 40                      # addresses per round; the time budget is checked between rounds
BUDGET_S = 20 * 60              # after this the remaining addresses stay "unknown"
MAX_BODY = 200_000              # bytes of a page read for its title
MAX_SITEMAP_BYTES = 30_000_000
MAX_SITEMAPS = 60
MAX_SITEMAP_URLS = 50_000
REDIRECTS = (301, 302, 307, 308)
GONE = (404, 410)

OK, GONE_KIND, REDIRECT, UNKNOWN = "ok", "gone", "redirect", "unknown"


@dataclass
class Probe:
    """What one request showed. status 0 means there was no answer."""
    status: int = 0
    location: str = ""
    title: str = ""

    @property
    def kind(self) -> str:
        if 200 <= self.status < 300:
            return OK
        if self.status in GONE:
            return GONE_KIND
        if self.status in REDIRECTS and self.location:
            return REDIRECT
        return UNKNOWN


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #

def origin_of(site_url: str) -> str:
    """The home address of a Search Console property: sc-domain:example.com -> https://example.com,
    https://www.example.com/path/ -> https://www.example.com. Empty when it cannot be told."""
    text = (site_url or "").strip()
    if text.startswith("sc-domain:"):
        host = text[len("sc-domain:"):].strip().strip("/")
        return f"https://{host}" if host else ""
    parts = urllib.parse.urlsplit(text)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def ascii_url(url: str) -> str:
    """An address a request can be made with: the host as punycode, the rest percent-encoded."""
    parts = urllib.parse.urlsplit(str(url).strip())
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if ":" in host:                                   # an IPv6 address
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host + (f":{port}" if port else "")
    safe = "/%:@!$&'()*+,;=-._~"
    return urllib.parse.urlunsplit((parts.scheme, netloc, urllib.parse.quote(parts.path, safe=safe),
                                    urllib.parse.quote(parts.query, safe=safe + "?"), ""))


# --------------------------------------------------------------------------- #
# The requests
# --------------------------------------------------------------------------- #

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _title_of(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    for charset in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            text = raw.decode(charset, errors="replace")
            break
        except LookupError:
            continue
    t = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    return " ".join(html.unescape(t.group(1)).split()) if t else ""


def fetch_probe(url: str, timeout: float = TIMEOUT_S) -> Probe:
    """One GET without following redirects. Never raises."""
    req = urllib.request.Request(ascii_url(url), headers={
        "User-Agent": BROWSER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8,fa;q=0.7"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            title = _title_of(resp.read(MAX_BODY), resp.headers.get("Content-Type", "")) \
                if resp.status == 200 else ""
            return Probe(resp.status, "", title)
    except urllib.error.HTTPError as e:
        return Probe(e.code, e.headers.get("Location", "") if e.headers else "", "")
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException, socket.timeout):
        return Probe(0)


def default_get(url: str) -> tuple:
    """(status, bytes) for a sitemap or robots.txt; redirects are followed. A network problem
    gives (0, b"")."""
    req = urllib.request.Request(ascii_url(url), headers={"User-Agent": BROWSER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read(MAX_SITEMAP_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException, socket.timeout):
        return 0, b""


def classify(probe: Probe, url: str) -> tuple:
    """(kind, target). A redirect to the same address (a slash, http to https, www) is not a move:
    it is unknown, so nothing changes."""
    kind = probe.kind
    if kind != REDIRECT:
        return kind, ""
    target = urllib.parse.urljoin(url, probe.location)
    if content.url_key(target) == content.url_key(url):
        return UNKNOWN, ""
    return REDIRECT, content.display_url(target)


def check_all(urls: list, probe=fetch_probe, workers: int = WORKERS, budget_s: float = BUDGET_S,
              clock=time.monotonic) -> dict:
    """{url: Probe} for every address. A few at a time; when the time budget is used up the rest
    get Probe(0), which is "unknown"."""
    out, started = {}, clock()
    todo = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            if clock() - started >= budget_s:
                out.update({u: Probe(0) for u in todo[i:]})
                break
            out.update(zip(batch, pool.map(probe, batch)))
    return out


# --------------------------------------------------------------------------- #
# Sitemaps
# --------------------------------------------------------------------------- #

def _unpack(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except (OSError, EOFError):
            return b""
    return body


def _locs(body: bytes) -> tuple:
    """(kind, [addresses]) of one sitemap file: kind is "index", "urls" or "" when it is not one."""
    try:
        root = ET.fromstring(_unpack(body))
    except ET.ParseError:
        return "", []
    tag = root.tag.rsplit("}", 1)[-1].lower()
    kind = {"sitemapindex": "index", "urlset": "urls"}.get(tag, "")
    out = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() == "loc" and el.text and el.text.strip():
            out.append(el.text.strip())
    return kind, out


def sitemap_roots(origin: str, get) -> list:
    """The sitemap addresses robots.txt names, or /sitemap.xml when it names none."""
    status, body = get(f"{origin}/robots.txt")
    found = []
    if status == 200:
        for line in body.decode("utf-8", errors="replace").splitlines():
            m = re.match(r"\s*sitemap\s*:\s*(\S+)", line, re.IGNORECASE)
            if m and m.group(1) not in found:
                found.append(m.group(1))
    return found or [f"{origin}/sitemap.xml"]


def read_sitemaps(origin: str, get=default_get) -> tuple:
    """(addresses, ok). `ok` is False when no sitemap could be read at all: the caller then adds
    nothing for this run. One sitemap file that fails inside an index only costs its own pages."""
    if not origin:
        return [], False
    urls, seen, readable = {}, set(), 0
    roots = sitemap_roots(origin, get)
    default = f"{origin}/sitemap.xml"
    for round_ in (roots, [default]):             # the default address is the second try
        queue = [a for a in round_ if a not in seen]
        while queue and len(seen) < MAX_SITEMAPS and len(urls) < MAX_SITEMAP_URLS:
            address = queue.pop(0)
            if address in seen:
                continue
            seen.add(address)
            status, body = get(address)
            kind, locs = _locs(body) if status == 200 else ("", [])
            if not kind:
                continue
            readable += 1
            if kind == "index":
                queue.extend(locs)
            else:
                urls.update(dict.fromkeys(locs))
        if readable:
            break
    return list(urls)[:MAX_SITEMAP_URLS], bool(readable)
