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
from dataclasses import dataclass, field

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
    lang: str = ""                      # the language of the page: the first part of <html lang> ("fa" for fa-IR)
    alternates: dict = field(default_factory=dict)      # the translations the page declares: {"fa": address} (hreflang)

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


def _decode(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    for charset in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def _title_of(raw: bytes, content_type: str) -> str:
    text = _decode(raw, content_type)
    t = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    return " ".join(html.unescape(t.group(1)).split()) if t else ""


def parse_page(text: str, base: str = "") -> tuple:
    """(title, language, translations) of a page: the language is the first part of <html lang> ("fa" for
    fa-IR), the translations are the hreflang tags of its head as {language: address} (x-default is not a language)."""
    t = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    title = " ".join(html.unescape(t.group(1)).split()) if t else ""
    m = re.search(r"<html\b[^>]*?\blang\s*=\s*[\"']?([A-Za-z]{2,3})", text[:5000], re.IGNORECASE)
    lang = m.group(1).lower() if m else ""
    alternates = {}
    for tag in re.findall(r"<link\b[^>]*>", text[:100_000], re.IGNORECASE):
        if not re.search(r"rel\s*=\s*[\"']?alternate", tag, re.IGNORECASE):
            continue
        hl = re.search(r"hreflang\s*=\s*[\"']([A-Za-z]{2,3})(?:-[A-Za-z0-9]+)?[\"']", tag, re.IGNORECASE)
        href = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        if hl and href:
            alternates.setdefault(hl.group(1).lower(), urllib.parse.urljoin(base, html.unescape(href.group(1))))
    return title, lang, alternates


def fetch_probe(url: str, timeout: float = TIMEOUT_S) -> Probe:
    """One GET without following redirects. Never raises."""
    req = urllib.request.Request(ascii_url(url), headers={
        "User-Agent": BROWSER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8,fa;q=0.7"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            title, lang, alternates = (
                parse_page(_decode(resp.read(MAX_BODY), resp.headers.get("Content-Type", "")), url)
                if resp.status == 200 else ("", "", {}))
            return Probe(resp.status, "", title, lang, alternates)
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
    for item in root:                  # only the <loc> of each <url> or <sitemap>: an <image:loc> inside it is a picture
        for el in item:
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


def read_sitemap_files(origin: str, get=default_get, extra=()) -> tuple:
    """({sitemap file address: [page addresses]}, ok). `ok` is False when no sitemap could be read at all: the caller
    then adds nothing for this run. One sitemap file that fails inside an index only costs its own pages."""
    if not origin:
        return {}, False
    files, seen, readable, total = {}, set(), 0, 0
    roots = sitemap_roots(origin, get) + [e for e in extra if e]       # the extra indexes of other languages
    default = f"{origin}/sitemap.xml"
    for round_ in (roots, [default]):             # the default address is the second try
        queue = [a for a in round_ if a not in seen]
        while queue and len(seen) < MAX_SITEMAPS and total < MAX_SITEMAP_URLS:
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
                files[address] = list(dict.fromkeys(locs))
                total += len(files[address])
        if readable:
            break
    return files, bool(readable)


def read_sitemaps(origin: str, get=default_get, extra=()) -> tuple:
    """(addresses, ok): every page address of the sitemap(s), once each. See read_sitemap_files."""
    files, ok = read_sitemap_files(origin, get, extra)
    urls = {}
    for locs in files.values():
        urls.update(dict.fromkeys(locs))
    return list(urls)[:MAX_SITEMAP_URLS], ok


def _entries(body: bytes) -> tuple:
    """(kind, [(address, lastmod)]) of one sitemap file. `lastmod` is 'YYYY-MM-DD' or '' when the file gives none."""
    try:
        root = ET.fromstring(_unpack(body))
    except ET.ParseError:
        return "", []
    tag = root.tag.rsplit("}", 1)[-1].lower()
    kind = {"sitemapindex": "index", "urlset": "urls"}.get(tag, "")
    out = []
    for item in root:
        loc, last = "", ""
        for el in item:
            name = el.tag.rsplit("}", 1)[-1].lower()
            if name == "loc" and el.text and el.text.strip():
                loc = el.text.strip()
            elif name == "lastmod" and el.text:
                m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", el.text)
                last = m.group(1) if m else ""
        if loc:
            out.append((loc, last))
    return kind, out


def read_sitemap_entries(origin: str, get=default_get, extra=()) -> tuple:
    """({address: lastmod}, ok): like read_sitemaps, with the date each page says it was last changed
    ('' when the sitemap does not say). `ok` is False when no sitemap could be read at all."""
    if not origin:
        return {}, False
    urls, seen, readable = {}, set(), 0
    for round_ in (sitemap_roots(origin, get) + [e for e in extra if e], [f"{origin}/sitemap.xml"]):
        queue = [a for a in round_ if a not in seen]
        while queue and len(seen) < MAX_SITEMAPS and len(urls) < MAX_SITEMAP_URLS:
            address = queue.pop(0)
            if address in seen:
                continue
            seen.add(address)
            status, body = get(address)
            kind, entries = _entries(body) if status == 200 else ("", [])
            if not kind:
                continue
            readable += 1
            if kind == "index":
                queue.extend(loc for loc, _ in entries)
            else:
                for loc, last in entries:
                    urls.setdefault(loc, last)
        if readable:
            break
    return dict(list(urls.items())[:MAX_SITEMAP_URLS]), bool(readable)
