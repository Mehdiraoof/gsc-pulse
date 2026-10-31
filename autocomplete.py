"""
PulseO: keyword ideas from Google Autocomplete (v2.5, part B)
==============================================================
The owner fills the `Keyword Seeds` tab of the content dashboard; once a week this module asks
Google's suggestion endpoint around every active seed and writes new ideas to `Keyword Ideas`,
where the owner decides (Add, Ignore, Later). An idea marked Add becomes a new-topic row at the
next monthly refresh.

The endpoint is unofficial. It is used gently and can never break a run:
  * one call per second plus a little random wait, at most 1,500 calls per run;
  * answers are cached for 30 days (in the file's hidden state tab);
  * on HTTP 429 or 403 the run stops softly: what was found so far is kept, nothing fails.

Pure functions plus one injected `http_get`, so everything is testable without the network.
"""

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import content

ENDPOINT = "https://suggestqueries.google.com/complete/search"
MAX_CALLS = 1500
CACHE_DAYS = 30
SEED_EVERY_DAYS = 7
THROTTLE_S = 1.0
JITTER_S = 0.5
MAX_FAILURES_IN_A_ROW = 5

FA_LETTERS = list("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")          # the 32 letters
EN_LETTERS = list("abcdefghijklmnopqrstuvwxyz")
FA_BEFORE = ["چرا", "چگونه", "چطور", "آیا", "چه", "کی", "کجا", "چند", "کدام", "بهترین"]
EN_BEFORE = ["what", "how", "why", "is", "can", "does", "best"]
FA_AFTER = ["چیست", "کیست", "یعنی چه"]
EN_AFTER = []
LANGUAGES = ("fa", "en")


class SoftStop(Exception):
    """Google asked us to slow down (429/403): stop this run, keep what was found."""


def queries_for(seed: str, lang: str) -> list:
    """[(query, pattern)]: the seed, the seed plus each letter, question words before it, and the
    seed plus the 'after' words. About 46 queries for Persian, 34 for English."""
    letters = FA_LETTERS if lang == "fa" else EN_LETTERS
    before = FA_BEFORE if lang == "fa" else EN_BEFORE
    after = FA_AFTER if lang == "fa" else EN_AFTER
    out = [(seed, "seed")]
    out += [(f"{seed} {l}", f"seed + {l}") for l in letters]
    out += [(f"{w} {seed}", f"{w} + seed") for w in before]
    out += [(f"{seed} {w}", f"seed + {w}") for w in after]
    return out


def url_for(query: str, lang: str, country: str = "") -> str:
    params = {"client": "firefox", "hl": lang, "q": query}
    if country:
        params["gl"] = country.lower()
    return ENDPOINT + "?" + urllib.parse.urlencode(params)


def default_http_get(url: str) -> tuple:
    """(status, text). A network problem raises OSError."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def parse_suggestions(body: str) -> list:
    """The Firefox-style answer is ["query", ["suggestion", ...], ...]."""
    try:
        data = json.loads(body)
        return [str(s) for s in data[1]] if isinstance(data, list) and len(data) > 1 else []
    except (ValueError, TypeError):
        return []


@dataclass
class Cache:
    """{key: (date, [suggestions])}; keys are 'lang|country|query'."""
    entries: dict = field(default_factory=dict)

    @staticmethod
    def key(lang: str, country: str, query: str) -> str:
        return f"{lang}|{country.lower()}|{query}"

    def get(self, key: str, today: date):
        hit = self.entries.get(key)
        if hit and (today - hit[0]).days < CACHE_DAYS:
            return hit[1]
        return None

    def put(self, key: str, today: date, suggestions: list):
        self.entries[key] = (today, suggestions)

    def prune(self, today: date):
        self.entries = {k: v for k, v in self.entries.items() if (today - v[0]).days < CACHE_DAYS}

    def to_rows(self) -> list:
        return [["ac", k, json.dumps(v[1], ensure_ascii=False), v[0].isoformat()]
                for k, v in sorted(self.entries.items())]

    @classmethod
    def from_rows(cls, rows: list):
        cache = cls()
        for r in rows:
            if len(r) >= 4 and r[0] == "ac":
                try:
                    cache.entries[str(r[1])] = (date.fromisoformat(str(r[3])), json.loads(r[2]))
                except (ValueError, TypeError):
                    continue
        return cache


@dataclass
class SeedRun:
    row: int                       # 1-based row in the Keyword Seeds tab
    seed: str
    lang: str
    country: str


def due_seeds(rows: list, today: date) -> list:
    """rows: dicts {Seed, Language, Country, Active, Last run} -> [SeedRun] for active seeds that
    have not run in the last 7 days."""
    out = []
    for i, r in enumerate(rows, start=2):
        seed = str(r.get("Seed", "")).strip()
        if not seed or str(r.get("Active", "")).strip().lower() not in ("yes", "y", "true", "1"):
            continue
        last = content.parse_date(r.get("Last run", ""))
        if last is not None and (today - last).days < SEED_EVERY_DAYS:
            continue
        lang = str(r.get("Language", "")).strip().lower()
        if lang not in LANGUAGES:
            lang = "en" if seed.isascii() else "fa"
        out.append(SeedRun(i, seed, lang, str(r.get("Country", "")).strip()))
    return out


@dataclass
class Outcome:
    ideas: list = field(default_factory=list)       # rows for the Keyword Ideas tab
    ran: list = field(default_factory=list)         # SeedRun that were finished
    calls: int = 0
    cached: int = 0
    stopped: str = ""                               # why the run stopped early, if it did


def collect(seeds: list, *, cache: Cache, today: date, http_get=default_http_get, sleep=time.sleep,
            rng=random.random, max_calls: int = MAX_CALLS) -> tuple:
    """-> ({norm(keyword): (keyword, seed run, pattern)}, Outcome without rows). Seeds are worked
    one after another; a seed whose queries were not all asked is not marked as run."""
    found, outcome = {}, Outcome()
    failures = 0
    for seed in seeds:
        finished = True
        for query, pattern in queries_for(seed.seed, seed.lang):
            key = Cache.key(seed.lang, seed.country, query)
            suggestions = cache.get(key, today)
            if suggestions is not None:
                outcome.cached += 1
            else:
                if outcome.calls >= max_calls:
                    outcome.stopped = "call limit for this run reached"
                    finished = False
                    break
                if outcome.calls:
                    sleep(THROTTLE_S + rng() * JITTER_S)
                outcome.calls += 1
                try:
                    status, body = http_get(url_for(query, seed.lang, seed.country))
                except OSError:
                    status, body = 0, ""
                if status in (429, 403):
                    outcome.stopped = f"Google answered HTTP {status}; will continue next run"
                    finished = False
                    break
                if status != 200:
                    failures += 1
                    if failures >= MAX_FAILURES_IN_A_ROW:
                        outcome.stopped = "too many failed calls; will continue next run"
                        finished = False
                        break
                    continue
                failures = 0
                suggestions = parse_suggestions(body)
                cache.put(key, today, suggestions)
            for s in suggestions:
                k = content.norm(s)
                if k and k != content.norm(seed.seed) and k not in found:
                    found[k] = (s, seed, pattern)
        if finished:
            outcome.ran.append(seed)
        else:
            break
    return found, outcome


def new_ideas(found: dict, *, brand_terms: list, existing: set, known: set, today: date,
              lookup=lambda k: None) -> list:
    """Keyword Ideas rows for what is new: not already an idea (written before, kept or deleted)
    and not already a primary or secondary keyword. Nothing is filtered by topic, length or
    wording: the owner decides. `lookup(norm keyword)` gives (impressions, best page, position)
    from Search Console, or None."""
    rows = []
    for k, (keyword, seed, pattern) in found.items():
        if k in existing or k in known:
            continue
        hit = lookup(k)
        impr, page, pos = hit if hit else ("", "", "")
        rows.append([keyword, seed.seed, pattern, seed.lang, impr, page, pos, today.isoformat(), ""])
    return rows
