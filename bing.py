"""
Bing Webmaster data: the access layer and a read-only check
===========================================================
The code that reads Bing data asks an `Access` for a method ("GetQueryStats", siteUrl=...) and gets the JSON back. How
the access was obtained (now an API key, later an OAuth token of a customer who connected Bing) is the access's own
business: a new provider is one more entry in PROVIDERS and the data code does not change.

The key travels in the request address, so an address is never logged or put in an error message: every error text is
made here from the method name, the status and the error code Bing answers with, and the key is scrubbed from it.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://ssl.bing.com/webmaster/api.svc/json/"
TIMEOUT_S = 40
RETRY_STATUSES = (0, 429, 500, 502, 503, 504)


class BingError(Exception):
    """A Bing request failed. The message is safe to print: no address, no key."""


class Access:
    """What the data code gets: `get(method, **params)` returns the answer's content (the `d` of the JSON)."""

    def get(self, method: str, **params):
        raise NotImplementedError


def default_opener(url: str) -> tuple:
    """(status, bytes); status 0 when there was no answer. Never raises, never shows the address."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read(30_000_000)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(100_000)
        except OSError:
            return e.code, b""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


class ApiKeyAccess(Access):
    """The account's API key: one key reads every site verified in the account."""

    def __init__(self, key: str, opener=default_opener, sleep=time.sleep, attempts: int = 4):
        self._key = key.strip()
        self._open = opener
        self._sleep = sleep
        self._attempts = attempts

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def get(self, method: str, **params):
        query = urllib.parse.urlencode({"apikey": self._key, **{k: v for k, v in params.items() if v is not None}})
        url = f"{BASE}{method}?{query}"
        status, body = 0, b""
        for attempt in range(self._attempts):
            status, body = self._open(url)
            if status not in RETRY_STATUSES:
                break
            if attempt + 1 < self._attempts:
                self._sleep(2 ** attempt * 2)                   # polite: 2, 4, 8 seconds
        if status != 200:
            raise BingError(self._scrub(f"{method}: {_reason(status, body)}"))
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            raise BingError(f"{method}: the answer was not JSON") from None
        return data.get("d", data) if isinstance(data, dict) else data


def _reason(status: int, body: bytes) -> str:
    if status == 0:
        return "no answer (network problem or timeout)"
    text = ""
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            text = f" ({data.get('Message') or ''} {data.get('ErrorCode', '')})".replace("  ", " ")
    except ValueError:
        pass
    return f"HTTP {status}{text.rstrip()}"[:200]


# The providers of an access: how a site's `bing.auth` becomes an Access. An OAuth provider is added here later.
PROVIDERS = {"api_key": lambda env: ApiKeyAccess(env["BING_API_KEY"])}


def access_for(auth: str, env) -> Access:
    """The access a site's `bing.auth` names, built from the environment; BingError when it cannot be had."""
    make = PROVIDERS.get(auth)
    if make is None:
        raise BingError(f"unknown way to reach Bing: {auth}")
    try:
        return make(env)
    except KeyError:
        raise BingError("the Bing credentials are not available in this run") from None


# --------------------------------------------------------------------------- #
# Sites
# --------------------------------------------------------------------------- #

def host_of(site_url: str) -> str:
    """The bare host of a site address: sc-domain:example.com, https://www.example.com/path/ -> example.com."""
    text = (site_url or "").strip()
    if text.startswith("sc-domain:"):
        host = text[len("sc-domain:"):]
    else:
        host = urllib.parse.urlsplit(text).netloc or text
    host = host.strip().strip("/").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def matching_sites(user_sites: list, site_url: str) -> list:
    """Every entry of the account's site list ({Url, IsVerified, ...}) that is this site by domain (http and https, with
    and without www are different entries in Bing), the most likely first: verified, then https, then the shortest."""
    host = host_of(site_url)
    found = [s for s in user_sites if isinstance(s, dict) and host_of(str(s.get("Url", ""))) == host]
    found.sort(key=lambda s: (not s.get("IsVerified"), not str(s.get("Url", "")).startswith("https"), len(str(s.get("Url", "")))))
    return found


def match_site(user_sites: list, site_url: str) -> dict:
    """The entry that is this site, by domain; {} when none (the first of matching_sites)."""
    found = matching_sites(user_sites, site_url)
    return found[0] if found else {}


def address_form(url: str) -> str:
    """How an address is written, without the address: "https, without www"."""
    text = (url or "").strip().lower()
    scheme = "https" if text.startswith("https://") else "http" if text.startswith("http://") else "no scheme"
    host = urllib.parse.urlsplit(text).netloc or text.split("/")[0]
    return f"{scheme}, {'with' if host.startswith('www.') else 'without'} www"


def same_address(a: str, b: str) -> bool:
    """The same address, exactly as Bing writes it: scheme, www and host must match; a slash at the end does not matter."""
    norm = lambda u: (str(u or "").strip().lower().rstrip("/"))
    return norm(a) == norm(b)


# --------------------------------------------------------------------------- #
# Dates and rows
# --------------------------------------------------------------------------- #

_MS_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def parse_date(value):
    """A day (date) from Bing's "/Date(1316156400000-0700)/" or an ISO text; None when it is neither."""
    text = str(value or "")
    m = _MS_DATE.search(text)
    if m:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(m.group(1)))).date()
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def rows(answer) -> list:
    """The list of dict rows of an answer (an answer that is not a list is no rows)."""
    return [r for r in answer if isinstance(r, dict)] if isinstance(answer, list) else []


# --------------------------------------------------------------------------- #
# The read-only check
# --------------------------------------------------------------------------- #

CHECK_CALLS = (("traffic", "GetRankAndTrafficStats"), ("queries", "GetQueryStats"), ("pages", "GetPageStats"),
               ("crawl stats", "GetCrawlStats"), ("crawl issues", "GetCrawlIssues"))


def _num(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def last_day(answer_rows: list, positive: bool = False):
    """The newest date in the rows of an answer; with `positive` only a day that has clicks or impressions. None when none."""
    days = [parse_date(r.get("Date")) for r in answer_rows
            if not positive or _num(r.get("Clicks")) > 0 or _num(r.get("Impressions")) > 0]
    days = [d for d in days if d]
    return max(days) if days else None


def check_lines(access: Access, site_list: list, today=None) -> list:
    """For every site, every address of it that the Bing account lists (http and https, with and without www are
    different entries): how it is written, whether it is verified, and what the key can read from it (rows and the newest
    day of the traffic, query and page data). Counts, forms and dates only: no site address, no query, no page. Nothing
    is changed."""
    today = today or datetime.now(timezone.utc).date()
    try:
        user_sites = rows(access.get("GetUserSites"))
    except BingError as e:
        return [f"the site list could not be read ({e})"]
    lines = [f"the account lists {len(user_sites)} sites, {sum(1 for s in user_sites if s.get('IsVerified'))} verified"]
    for site in site_list:
        entries = matching_sites(user_sites, site.site_url)
        if not entries:
            lines.append(f"{site.name}: not in the Bing account")
            continue
        lines.append(f"{site.name}: {len(entries)} address{'es' if len(entries) != 1 else ''} of this site in the account")
        for i, entry in enumerate(entries, start=1):
            parts = [f"{address_form(entry['Url'])}, {'verified' if entry.get('IsVerified') else 'NOT verified'}"]
            for label, method in CHECK_CALLS:
                try:
                    got = rows(access.get(method, siteUrl=entry["Url"]))
                except BingError as e:
                    parts.append(f"{label}: failed ({e})")
                    continue
                text = f"{label}: {len(got)} rows"
                if method in ("GetRankAndTrafficStats", "GetQueryStats", "GetPageStats") and last_day(got):
                    text += f", newest {last_day(got)}"
                if method == "GetRankAndTrafficStats" and got:
                    last = [r for r in got if (parse_date(r.get("Date")) or today) >= today - timedelta(days=30)]
                    text += (f", newest day with clicks or impressions {last_day(got, True)}, last 30 days: "
                             f"{sum(_num(r.get('Clicks')) for r in last)} clicks, {sum(_num(r.get('Impressions')) for r in last)} impressions")
                parts.append(text)
            lines.append(f"{site.name} address {i}: " + "; ".join(parts))
    return lines
