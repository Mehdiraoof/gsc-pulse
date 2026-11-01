"""
PulseO: the content dashboard, the monthly refresh (v2.7)
==========================================================
Once a month (after the monthly report), or on demand, this module

  1. finds or creates the site's content dashboard file (content_sheet.py, provision.py),
  2. reads the last 90 days of Search Console (pages, searches, query x page) and a few months of
     per-page history,
  3. lists the pages of the site: every address in Search Console's last 90 days, in the stored
     history, and in the sitemap(s), each in the tab whose rule it matches,
  4. adds rows for pages it has not seen before, updates the cells PulseO owns and leaves every
     cell of the owner alone (content.py has the rules),
  5. looks at every page once (site_check.py): a page that is gone (404, 410) or has moved
     (301, 302, 307, 308) is warned about in the Alerts and, if the next refresh finds the same,
     moves to `Removed Pages`. This is the only reason a page row ever leaves a tab,
  6. gives every empty Primary Keyword a value (searches of the page, then its title),
  7. sorts each tab by Score, and
  8. once a week, asks Google Autocomplete around the owner's seeds (autocomplete.py).

Nothing here prints an ID, a link, a URL or a keyword. The file never names the tool.
"""

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from googleapiclient.errors import HttpError

import aio_import
import autocomplete
import content
import content_import
import content_sheet as cs
import opportunities
import periods
import provision
import reports
import site_check
import tabs

MAX_IMPACT_CALLS = 60          # per-page before/after lookups in one refresh
PATCH_CHUNK = 400              # cells written one by one per call
TOP_URGENT = 5
PAGE_QUERY_DAYS = 480          # a page with no searches in 90 days is asked about for the stored 16 months
MAX_PAGE_QUERY_CALLS = 800     # Search Console calls for the searches of single pages, per refresh
PAGE_QUERY_BUDGET_S = 10 * 60
NOTE_FROM_TITLE = "From page title, not from searches"
NOTE_NO_KEYWORD = "No searches and no title found"
SOURCES = ("search", "history", "sitemap", "redirect")


class ContentError(Exception):
    """The content dashboard cannot be used. The message is safe to print."""


@dataclass
class ContentResult:
    created: bool = False
    refreshed: bool = False
    month: str = ""
    new_pages: int = 0
    new_topics: int = 0
    rows: dict = field(default_factory=dict)        # tab -> rows after the refresh
    urgent: list = field(default_factory=list)      # [(keyword, action, tab)]
    ideas: int = 0                                  # new rows in Keyword Ideas
    ideas_added: int = 0                            # ideas marked Add that became rows
    autocomplete: str = ""
    file_id: str = ""
    imports: list = field(default_factory=list)     # [(ImportSpec, ImportReport)] done in this run
    import_problems: list = field(default_factory=list)   # safe messages of imports that could not run
    repairs: list = field(default_factory=list)     # [(ImportSpec, RepairReport)] done in this run
    added: dict = field(default_factory=lambda: {s: 0 for s in SOURCES})   # new page rows by where the page came from
    sources: dict = field(default_factory=dict)     # "history" / "sitemap" -> addresses read (or "not readable")
    checked: dict = field(default_factory=dict)     # status check: ok, 404, 410, redirect, unknown -> pages
    warned: int = 0                                 # pages that got the first warning
    removed: dict = field(default_factory=dict)     # gone / redirect / merged -> rows moved to Removed Pages
    keywords: dict = field(default_factory=lambda: {"searches": 0, "title": 0, "empty": 0})

    def added_text(self) -> str:
        return ", ".join(f"{self.added[s]} from {s}" for s in SOURCES if self.added.get(s))

    def text(self) -> str:
        parts = []
        if self.created:
            parts.append("dashboard created")
        if self.refreshed:
            parts.append(f"refreshed for {self.month}: {self.new_pages} new pages"
                         + (f" ({self.added_text()})" if self.new_pages else "")
                         + f", {self.new_topics} new topics")
            if self.checked:
                parts.append("status check " + ", ".join(f"{k} {n}" for k, n in sorted(self.checked.items())))
            if self.warned or self.removed:
                parts.append(f"{self.warned} warned, removed "
                             + (", ".join(f"{n} {k}" for k, n in sorted(self.removed.items())) or "none"))
            if any(self.keywords.values()):
                parts.append("primary keywords filled from "
                             + ", ".join(f"{k} {n}" for k, n in self.keywords.items()))
        for _, report in self.imports:
            parts.append(f"imported {report.text()}")
        for _, report in self.repairs:
            parts.append(report.text())
        parts += [f"import not done: {p}" for p in self.import_problems]
        if self.ideas or self.autocomplete:
            parts.append(f"ideas +{self.ideas}" + (f" ({self.autocomplete})" if self.autocomplete else ""))
        return ", ".join(parts) or "up to date"


def last_complete_month(final: date) -> date:
    """First day of the newest month that ends on or before the last final day."""
    first = date(final.year, final.month, 1)
    return first if periods.month_end(first) <= final else periods.add_months(first, -1)


# --------------------------------------------------------------------------- #
# The state tab
# --------------------------------------------------------------------------- #

class State:
    """Rows of the hidden `_state` tab: [kind, key, value, updated]. Kind 'ac' is the autocomplete
    cache; the others are PulseO's own bookkeeping (last refresh, gap searches, dismissed ones, the
    first warning of a page that is gone, the priority it wrote, pages already removed ...)."""

    def __init__(self, values: list):
        self.rows, ac = [], []
        for r in values[1:] if values else []:
            r = [str(c) for c in list(r)[:4]] + [""] * (4 - len(r))
            if r[0] == "ac":
                ac.append(r)
            elif r[0]:
                self.rows.append(r)
        self.cache = autocomplete.Cache.from_rows(ac)

    def get(self, kind: str, key: str, default: str = "") -> str:
        for r in self.rows:
            if r[0] == kind and r[1] == key:
                return r[2]
        return default

    def when(self, kind: str, key: str) -> str:
        """The day (YYYY-MM-DD) the entry was last set, or ''."""
        for r in self.rows:
            if r[0] == kind and r[1] == key:
                return r[3]
        return ""

    def set(self, kind: str, key: str, value: str, today: date):
        for r in self.rows:
            if r[0] == kind and r[1] == key:
                r[2], r[3] = value, today.isoformat()
                return
        self.rows.append([kind, key, value, today.isoformat()])

    def delete(self, kind: str, key: str):
        self.rows = [r for r in self.rows if not (r[0] == kind and r[1] == key)]

    def keys(self, kinds) -> set:
        return {r[1] for r in self.rows if r[0] in kinds}

    def save(self, sheets, file_id: str):
        tabs.rewrite(sheets, file_id, cs.STATE, cs.STATE_HEADER, self.rows + self.cache.to_rows())


# --------------------------------------------------------------------------- #
# Search Console data
# --------------------------------------------------------------------------- #

def decoded_map(by_page: dict) -> dict:
    """{readable url: (clicks, impressions, position)}: variants that decode alike are added up."""
    acc = {}
    for url, (c, n, p) in by_page.items():
        a = acc.setdefault(content.url_key(url), [0, 0, 0.0])
        a[0] += c
        a[1] += n
        a[2] += p * n
    return {u: (c, n, (w / n if n else 0.0)) for u, (c, n, w) in acc.items()}


class Data:
    """The numbers a refresh needs, fetched when first asked for."""

    def __init__(self, fetch, final: date, brand_terms: list, clock=time.monotonic):
        self.fetch, self.final, self.brand = fetch, final, brand_terms
        self.first90 = final - timedelta(days=89)
        self._loaded = False
        self._months = {}
        self.impact_calls = 0
        self.clock = clock
        self.page_calls = 0
        self._page_started = None

    def load(self):
        if self._loaded:
            return
        self.pages = reports.by_page(self.fetch(["page"], self.first90, self.final))
        self.queries = reports.by_key(self.fetch(["query"], self.first90, self.final))
        self.pairs = [(r["keys"][0], r["keys"][1], r.get("clicks", 0), r.get("impressions", 0),
                       float(r.get("position", 0.0)))
                      for r in self.fetch(["query", "page"], self.first90, self.final)]
        self.stats = content.build_pages(self.pages, self.pairs)
        self.pairs_by_query = content.index_pairs(self.pairs)
        self.best = {}
        for query, url, _, n, p in self.pairs:
            if query not in self.best or n > self.best[query][1]:
                self.best[query] = (url, n, p)
        self.qnorm = {}
        for q, (c, n, p) in self.queries.items():
            a = self.qnorm.setdefault(content.norm(q), [0, 0, 0.0, q])
            a[0] += c
            a[1] += n
            a[2] = p if a[2] == 0 else min(a[2], p)
        self._loaded = True

    def month(self, first: date) -> dict:
        """{readable url: (clicks, impressions, position)} for one calendar month."""
        if first not in self._months:
            self._months[first] = decoded_map(
                reports.by_page(self.fetch(["page"], first, periods.month_end(first))))
        return self._months[first]

    def window(self, url: str, first: date, last: date):
        """(clicks, position) of one page over a range, or None when it had no impressions."""
        groups = [{"groupType": "and", "filters": [
            {"dimension": "page", "operator": "equals", "expression": url}]}]
        rows = self.fetch(["page"], first, last, groups)
        clicks = sum(r.get("clicks", 0) for r in rows)
        impr = sum(r.get("impressions", 0) for r in rows)
        if not impr:
            return None
        return clicks, sum(float(r.get("position", 0)) * r.get("impressions", 0) for r in rows) / impr

    def page_searches(self, url: str):
        """[(search, clicks, impressions, position)] of one page, most impressions first: Search
        Console asked with a page filter, the last 90 days first and, when that is empty, the stored
        16 months. None when the budget for such questions is used up (the answer is not "no
        searches", only unknown)."""
        if self._page_started is None:
            self._page_started = self.clock()
        forms = [site_check.ascii_url(url)]
        if url not in forms:
            forms.append(url)
        for first in (self.first90, self.final - timedelta(days=PAGE_QUERY_DAYS)):
            for expression in forms:
                if self.page_calls >= MAX_PAGE_QUERY_CALLS or self.clock() - self._page_started > PAGE_QUERY_BUDGET_S:
                    return None
                self.page_calls += 1
                groups = [{"groupType": "and", "filters": [
                    {"dimension": "page", "operator": "equals", "expression": expression}]}]
                try:
                    rows = self.fetch(["query"], first, self.final, groups)
                except HttpError:
                    continue
                if rows:
                    return sorted(((r["keys"][0], r.get("clicks", 0), r.get("impressions", 0),
                                    float(r.get("position", 0.0))) for r in rows),
                                  key=lambda q: (-q[2], q[0]))
        return []

    def lookup(self, key: str):
        """(impressions, best page path, position) of a search from the last 90 days, or None."""
        self.load()
        hit = self.qnorm.get(key)
        if not hit:
            return None
        best = self.best.get(hit[3])
        return int(hit[1]), content.path_of(best[0]) if best else "", round(hit[2], 1)


# --------------------------------------------------------------------------- #
# Values of one row
# --------------------------------------------------------------------------- #

@dataclass
class Context:
    site: object
    data: Data
    today: date
    cm: date                         # newest complete month
    brand: list
    aio: dict                        # readable url -> AIO impressions of the newest imported month
    seasonal_regex: str
    read_source: object = None       # spec -> (header, rows) of an old sheet to import, or None
    history_urls: list = field(default_factory=list)   # every page address in the stored history
    sitemap_urls: list = field(default_factory=list)   # every page address in the sitemap(s)
    probe: object = None             # url -> site_check.Probe, or None: no status check this run


def _num(v, digits=None):
    return round(v, digits) if digits is not None else int(round(v))


def _split(cell) -> list:
    return [x.strip() for x in str(cell or "").split(",") if x.strip()]


def page_values(ctx: Context, row: dict, stats, had_before: bool, hint: str = "") -> dict:
    """The cells PulseO owns for a row that has a page. `stats` is None when Search Console shows
    nothing for the page: the row stays, with zeros and empty cells (never removed, status and the
    owner's cells untouched). `hint` is the keyword found for an empty Primary Keyword cell when
    the page's own searches are not in the 90-day tables. Also returns "_advice" (the rule that
    fired) and "_keyword"."""
    d, cm = ctx.data, ctx.cm
    url = str(row.get(content.URL, "")).strip()
    key = content.url_key(url)
    prev, yoy = periods.add_months(cm, -1), periods.add_months(cm, -12)
    c_now = d.month(cm).get(key, (0, 0, 0))[0]
    out = {
        "Clicks MoM": content.change(c_now, d.month(prev).get(key, (0, 0, 0))[0]),
        "Clicks YoY": content.change(c_now, d.month(yoy).get(key, (0, 0, 0))[0]),
        "AIO Impressions": ctx.aio.get(key, ""),
    }
    kw_cell = str(row.get(content.KEY, "")).strip()
    kw = kw_cell or (content.primary_keyword(stats, ctx.brand) if stats else "") or hint
    seasonal = (str(row.get("Keyword Type", "")) == "Seasonal"
                or content.keyword_type_for(ctx.seasonal_regex, url, kw) == "Seasonal")
    peak = 0
    if seasonal:
        monthly = {m: d.month(m).get(key, (0, 0, 0))[0]
                   for m in (periods.add_months(cm, -i) for i in range(12))}
        peak = content.peak_month_of(monthly)
    series = [d.month(periods.add_months(cm, -i)).get(key, (0, 0, 0))[0] for i in range(3, -1, -1)]

    competing = []
    if stats is None or not stats.impressions:
        adv = content.NO_DATA if had_before else content.WAITING
        out.update({"Clicks (90d)": 0, "Impressions (90d)": 0, "CTR": "", "Avg. Position": "",
                    "Primary KW Impr.": "", "Primary KW Pos.": "", "Ranking Queries": "",
                    "Secondary Keywords": "", "Competing Pages": "", "Score": 0})
        sc, impressions, position = 0, 0, 0.0
        fanouts = []
    else:
        competing = content.competing_pages(d.pairs_by_query, kw, stats.url) if kw else []
        kw_impr, kw_pos = content.keyword_metrics(stats, kw) if kw else ("", "")
        adv = content.advice(stats.impressions, stats.position, stats.ctr)
        brand_n = sum(n for q, _, n, _ in stats.queries if content.skip_query(q, ctx.brand))
        brand_c = sum(c for q, c, _, _ in stats.queries if content.skip_query(q, ctx.brand))
        adj_n, adj_c = max(stats.impressions - brand_n, 0), max(stats.clicks - brand_c, 0)
        g = content.gain(adj_n, adj_c / adj_n if adj_n else 0.0, stats.position)
        sc = content.score(stats.impressions, g, stats.position, bool(competing), seasonal, False)
        impressions, position = stats.impressions, stats.position
        out.update({
            "Clicks (90d)": _num(stats.clicks), "Impressions (90d)": _num(stats.impressions),
            "CTR": round(stats.ctr, 4), "Avg. Position": _num(stats.position, 1),
            "Primary KW Impr.": kw_impr, "Primary KW Pos.": kw_pos,
            "Ranking Queries": len(stats.queries),
            "Secondary Keywords": ", ".join(content.secondary_keywords(stats, ctx.brand, kw)),
            "Competing Pages": content.competing_text(competing), "Score": sc})
        fanouts = content.fan_outs(stats, ctx.brand)
    out.update({
        "Priority": content.priority(sc),
        "Suggested Action": adv.action, "Why": adv.why,
        content.FANOUTS: ", ".join(fanouts),
        content.RECOMMENDATIONS: content.competing_recommendation(competing),
        "_advice": adv, "_keyword": kw, "_competing": competing, "_seasonal": seasonal,
        "_peak": peak, "_series": series, "_position": position, "_impressions": impressions})
    return out


def topic_values(ctx: Context, row: dict, group: list = None, possible: str = "") -> dict:
    """The cells PulseO owns for a new-topic row (no page yet). `group` is the list of searches
    when known (new rows); existing rows use their primary and secondary keywords. `possible`:
    addresses an old sheet's row might belong to (shown in the Alerts)."""
    d = ctx.data
    kw = str(row.get(content.KEY, "")).strip()
    group = group or [kw] + _split(row.get("Secondary Keywords"))
    hits = [d.qnorm[k] for k in dict.fromkeys(content.norm(q) for q in group) if k in d.qnorm]
    impressions = sum(h[1] for h in hits)
    clicks = sum(h[0] for h in hits)
    position = min((h[2] for h in hits if h[2]), default=0.0)
    top = d.qnorm.get(content.norm(kw))
    best = d.best.get(top[3]) if top else None
    ctr = clicks / impressions if impressions else 0.0
    g = content.gain(impressions, ctr, position) if impressions else 0.0
    sc = content.score(impressions, g, position, False, False, True)
    why = (content.why_text(int(round(impressions)), len(hits), content.path_of(best[0]))
           if impressions and best else "No impressions for this topic yet.")
    alerts = [content.NOT_FOUND_ALERT if str(row.get(content.STATUS, "")).strip() == "Published"
              else content.NO_PAGE_ALERT]
    if possible:
        alerts.append(content.POSSIBLE_ALERT + possible)
    return {
        "Priority": content.priority(sc), "Score": sc,
        "Clicks (90d)": _num(clicks), "Impressions (90d)": _num(impressions),
        "CTR": round(ctr, 4) if impressions else "",
        "Avg. Position": _num(position, 1) if position else "",
        "Clicks MoM": "", "Clicks YoY": "", "AIO Impressions": "",
        "Primary KW Impr.": _num(top[1]) if top else "",
        "Primary KW Pos.": _num(top[2], 1) if top and top[2] else "",
        "Ranking Queries": len(hits) or "", "Competing Pages": "", content.IMPACT: "",
        "Suggested Action": "New page", "Why": why,
        "Alerts": ", ".join(alerts),
        "_advice": None, "_keyword": kw}


def keep_owner_priority(state: State, row: dict, vals: dict, today: date):
    """The Priority is the tool's until the owner changes it: a value that is no longer the one the
    tool wrote last time is the owner's, and from then on it is left alone."""
    key = content.row_key(row)
    if not key:
        return
    current = str(row.get(content.PRIORITY, "")).strip()
    last = state.get("prio", key)
    if current and (last == "*" or (last and current != last)):
        vals["Priority"] = current
        state.set("prio", key, "*", today)
        return
    state.set("prio", key, vals["Priority"], today)


# --------------------------------------------------------------------------- #
# The live status of the pages
# --------------------------------------------------------------------------- #

def judge(state: State, key: str, kind: str, target: str, status: int, today: date) -> tuple:
    """(verdict, text) for one page after its status check. verdict is "none", "warn" (the first
    time a page is found gone or moved: text is the alert) or "remove" (an earlier refresh, on
    another day, found the same: text is the reason). A page that answers, and a status that says
    nothing (403, 429, 5xx, no answer), clear an earlier warning: only two answers in a row count."""
    if kind in (site_check.OK, site_check.UNKNOWN):
        state.delete("gone", key)
        return "none", ""
    gone = kind == site_check.GONE_KIND
    value = "gone" if gone else "redirect|" + content.url_key(target)
    first_seen = state.when("gone", key)
    if state.get("gone", key) == value and first_seen and first_seen < today.isoformat():
        return "remove", (f"Page returns {status}" if gone else f"Redirects to {target}")
    if state.get("gone", key) != value:
        state.set("gone", key, value, today)
    return "warn", (content.GONE_ALERT.format(code=status) if gone
                    else content.REDIRECT_ALERT.format(target=target))


def add_note(rec: dict, text: str):
    """Adds a line to the Notes/Checklist of a record (once)."""
    have = str(rec["row"].get(content_import.NOTES, "")).strip()
    if text in have:
        return
    new = have + "\n" + text if have else text
    rec["row"][content_import.NOTES] = new
    rec["import"] = {**rec.get("import", {}), content_import.NOTES: new}


def hand_over(rec: dict, target: str, cfg, work: dict, by_key: dict, ctx: Context, result: ContentResult):
    """A page that redirects hands its cells to the row of the page it redirects to (a row is made
    for that page when it has none): a cell only where the target's is empty (so the Primary
    Keyword only when the target has none), the notes added to. Nothing is overwritten."""
    key = content.url_key(target)
    dest = by_key.get(key)
    if dest is None:
        tab = cfg.tab_of(target)
        if tab is None or tab.name not in work:
            return                                # the target is in no tab: the row only goes to Removed Pages
        dest = {"row": {content.URL: content.display_url(target), content.STATUS: "Published"},
                "new": True, "topic": False, "blank": False, "stats": ctx.data.stats.get(key),
                "source": "redirect"}
        work[tab.name].append(dest)
        by_key[key] = dest
        result.added["redirect"] += 1
        result.new_pages += 1
    if dest is rec or dest.get("remove"):
        return
    src, vals = rec["row"], {}
    for col in content_import.TARGETS.values():
        if col in (content.URL, content_import.NOTES):
            continue
        if str(src.get(col, "")).strip() != "" and str(dest["row"].get(col, "")).strip() == "":
            vals[col] = src.get(col)
    note = f"Moved here from {content.display_url(str(src.get(content.URL, '')))} (that page redirects here)"
    old_notes = str(src.get(content_import.NOTES, "")).strip()
    if old_notes:
        note += ": " + old_notes
    have = str(dest["row"].get(content_import.NOTES, "")).strip()
    vals[content_import.NOTES] = have + "\n" + note if have else note
    dest["row"].update(vals)
    dest["import"] = {**dest.get("import", {}), **vals}
    rec["moved_to"] = content.display_url(target)


def fill_keywords(ctx: Context, work: dict, result: ContentResult):
    """A page row with an empty Primary Keyword: the top non-brand search of the page (the 90-day
    tables, else a page-filter question to Search Console), else the page title read by the status
    check (with a note), else nothing (with a note). Rows that have a keyword are never touched."""
    for recs in work.values():
        for rec in recs:
            row = rec["row"]
            url = str(row.get(content.URL, "")).strip()
            if not url or rec["blank"] or rec.get("remove") or str(row.get(content.KEY, "")).strip():
                continue
            stats = rec.get("stats") or ctx.data.stats.get(content.url_key(url))
            keyword = content.primary_keyword(stats, ctx.brand) if stats else ""
            if not keyword:
                found = ctx.data.page_searches(url)
                if found is None:                  # no more questions this run: try again next time
                    continue
                keyword = next((q for q, _, _, _ in found if not content.skip_query(q, ctx.brand)), "")
            if keyword:
                result.keywords["searches"] += 1
                rec["keyword_hint"] = keyword
                continue
            probe = rec.get("probe")
            title = probe.title if probe is not None and probe.kind == site_check.OK else ""
            if title:
                result.keywords["title"] += 1
                rec["keyword_hint"] = title
                add_note(rec, NOTE_FROM_TITLE)
            else:
                result.keywords["empty"] += 1
                add_note(rec, NOTE_NO_KEYWORD)


# --------------------------------------------------------------------------- #
# One refresh
# --------------------------------------------------------------------------- #

def _present(rows: list) -> set:
    """Normalized searches that a row already targets: its primary keyword and the secondary ones."""
    out = set()
    for r in rows:
        k = content.norm(r.get(content.KEY, ""))
        if k:
            out.add(k)
        out.update(content.norm(x) for x in _split(r.get("Secondary Keywords")))
    return out


def _targeted(recs: list) -> set:
    """Normalized searches the rows already target: the Primary Keyword of every row, and the
    Secondary Keywords of a topic row. A page row's secondary list is only what the page happens to
    rank for (the tool rewrites it every month), so it does not keep a search from being an idea."""
    out = set()
    for rec in recs:
        eff = _effective(rec)
        k = content.norm(eff[content.KEY])
        if k:
            out.add(k)
        if not str(rec["row"].get(content.URL, "")).strip():
            out.update(content.norm(x) for x in _split(eff["Secondary Keywords"]))
    return out


def _effective(rec: dict) -> dict:
    """The keyword cells of a record as they are (or will be) in the file."""
    vals = rec.get("vals") or {}
    kw = rec.get("fill_keyword") or rec["row"].get(content.KEY, "") or vals.get("_keyword", "")
    sec = vals.get("Secondary Keywords", rec["row"].get("Secondary Keywords", ""))
    return {content.KEY: kw, "Secondary Keywords": sec}


def removed_row(columns: list, row: dict, rec: dict, today: date) -> list:
    """One row of `Removed Pages`: everything the owner's row held, why it went and where it moved."""
    lead = {"Published URL": row.get(content.URL, ""), "Primary Keyword": row.get(content.KEY, ""),
            "Reason": rec["remove"], "Date": today.isoformat(), "Moved To": rec.get("moved_to", "")}
    out = []
    for c in columns:
        v = lead[c] if c in lead else row.get(c, "")
        if c in content_import.DATE_COLUMNS and isinstance(v, (int, float)) and not isinstance(v, bool):
            d = content.parse_date(v)
            v = d.isoformat() if d else v
        out.append(v)
    return out


def take_out(sheets, file_id: str, tab: str, sid: int, removing: list, state: State, result: ContentResult,
             today: date, log) -> int:
    """Copies the rows that go to `Removed Pages`, then deletes them from the tab. `removing`:
    [(index of the row among the tab's data rows, record)]. A row is only taken out when the file
    still shows the same page (or topic) at that place. Returns how many went."""
    _, rows_now = cs.table_of(cs.read_all(sheets, file_id, tab))
    ok = []
    for i, rec in removing:
        now = rows_now[i] if i < len(rows_now) else None
        mine = rec["row"]
        url = str(mine.get(content.URL, "")).strip()
        same = now is not None and (str(now.get(content.URL, "")).strip() == url if url else
                                    str(now.get(content.KEY, "")).strip() == str(mine.get(content.KEY, "")).strip())
        if same:
            ok.append((i, rec, now))
    if len(ok) < len(removing):
        log("  content: some rows were not removed because the tab had changed; they are tried next time")
    if not ok:
        return 0
    columns = cs.removed_header(list(content_import.TARGETS.values()))
    cs.ensure_removed_pages(sheets, file_id, columns)
    tabs.append(sheets, file_id, cs.REMOVED, [removed_row(columns, now, rec, today) for _, rec, now in ok], log)
    cs.delete_rows(sheets, file_id, sid, [i + 1 for i, _, _ in ok])
    for _, rec, now in ok:
        kind = rec.get("remove_kind", "gone")
        result.removed[kind] = result.removed.get(kind, 0) + 1
        url = str(now.get(content.URL, "")).strip()
        if url:                                    # never added again by the history or the sitemap
            state.set("removed", content.url_key(url), kind, today)
            state.delete("gone", content.url_key(url))
    return len(ok)


def refresh(sheets, file_id: str, site, ctx: Context, state: State, result: ContentResult,
            log=print):
    cfg = site.content
    today = ctx.today
    ctx.data.load()
    info = cs.sheet_info(sheets, file_id)
    tables = {}                                  # tab -> (header, rows, sheetId, grid rows)
    for t in cfg.tabs:
        if t.name not in info:
            log("  content: a configured tab is missing in the file and was skipped")
            continue
        header, rows = cs.table_of(cs.read_all(sheets, file_id, t.name))
        tables[t.name] = (header, rows, info[t.name][0], info[t.name][1])

    # ---- the pages of the site: Search Console, the stored history, the sitemap -----------
    existing_keys = {content.url_key(r.get(content.URL)) for _, rows, _, _ in tables.values()
                     for r in rows if str(r.get(content.URL, "")).strip()}
    removed_keys = state.keys({"removed"})
    candidates = [("search", s.url, s) for _, s in sorted(ctx.data.stats.items())
                  if s.impressions >= content.NEW_PAGE_MIN_IMPRESSIONS]
    candidates += [("history", u, None) for u in ctx.history_urls]
    candidates += [("sitemap", u, None) for u in ctx.sitemap_urls]
    fresh = {}                                   # tab -> [(source, url, stats)]
    for source, url, s in candidates:
        key = content.url_key(url)
        if key in existing_keys or key in removed_keys:
            continue
        tab = cfg.tab_of(url)
        if tab is None or tab.name not in tables:
            continue
        existing_keys.add(key)
        fresh.setdefault(tab.name, []).append((source, url, s))
    work = {}                                    # tab -> list of records
    for t in cfg.tabs:
        if t.name not in tables:
            continue
        header, rows, _, _ = tables[t.name]
        recs = [{"row": r, "new": False, "topic": not str(r.get(content.URL, "")).strip()
                 and bool(str(r.get(content.KEY, "")).strip()),
                 "blank": not str(r.get(content.URL, "")).strip() and not str(r.get(content.KEY, "")).strip()}
                for r in rows]
        for source, url, s in fresh.get(t.name, []):
            recs.append({"row": {content.URL: content.display_url(url), content.STATUS: "Published"},
                         "new": True, "topic": False, "blank": False,
                         "stats": s or ctx.data.stats.get(content.url_key(url)), "source": source})
            result.added[source] += 1
            result.new_pages += 1
        work[t.name] = recs

    # ---- old sheets to import, once each (content_import.py has the rules) -------------
    done_imports = []
    for spec in cfg.imports:
        key = content_import.spec_key(spec)
        if state.get("import", key) or spec.target_tab not in work:
            continue
        try:
            if ctx.read_source is None:
                raise content_import.ImportFailed("there is no way to read the old sheet in this run")
            header, raw = ctx.read_source(spec)
        except content_import.ImportFailed as e:
            result.import_problems.append(str(e))
            log(f"  content: import skipped ({e})")
            continue
        report = content_import.apply(content_import.parse_rows(header, raw), work[spec.target_tab],
                                      ctx, spec.target_tab)
        done_imports.append((spec, key, report))
        result.imports.append((spec, report))
        log(f"  content: import {report.text()}")
        for rowkey, text in report.possible.items():
            state.set("possible", rowkey, text, today)

    # ---- once: rows an earlier import added without a page are matched again ---------------
    repairs = []
    if cfg.repair_imports and not state.get("repair", "v1") and not result.import_problems:
        if cfg.imports and not done_imports:      # an import done in this run already used today's rules
            entries = []
            if cs.IMPORT_LOG in info:
                _, entries = cs.table_of(cs.read_all(sheets, file_id, cs.IMPORT_LOG))
            for spec in cfg.imports:
                if spec.target_tab not in work:
                    continue
                mine = [e for e in entries if str(e.get("Source tab", "")) == spec.source_tab]
                report = content_import.repair(mine, work[spec.target_tab], ctx, spec.target_tab)
                repairs.append((spec, report))
                result.repairs.append((spec, report))
                log(f"  content: {report.text()}")
                for rowkey, text in report.possible.items():
                    state.set("possible", rowkey, text, today)
        state.set("repair", "v1", today.isoformat(), today)

    # ---- the live status of every page row -------------------------------------------------
    by_key = {}
    for recs in work.values():
        for rec in recs:
            url = str(rec["row"].get(content.URL, "")).strip()
            if url:
                by_key.setdefault(content.url_key(url), rec)
    if ctx.probe is not None:
        addresses = [str(rec["row"].get(content.URL, "")).strip() for recs in work.values() for rec in recs
                     if str(rec["row"].get(content.URL, "")).strip()]
        probes = site_check.check_all(addresses, ctx.probe)
        moves = []
        for recs in work.values():
            for rec in recs:
                url = str(rec["row"].get(content.URL, "")).strip()
                if not url:
                    continue
                probe = probes.get(url, site_check.Probe(0))
                kind, target = site_check.classify(probe, url)
                label = str(probe.status) if kind == site_check.GONE_KIND else kind
                result.checked[label] = result.checked.get(label, 0) + 1
                rec["probe"] = probe
                verdict, text = judge(state, content.url_key(url), kind, target, probe.status, today)
                if verdict == "remove" and rec["new"]:
                    verdict, text = "warn", (content.GONE_ALERT.format(code=probe.status)
                                             if kind == site_check.GONE_KIND
                                             else content.REDIRECT_ALERT.format(target=target))
                if verdict == "warn":
                    rec["check"] = text
                    result.warned += 1
                elif verdict == "remove":
                    rec["remove"] = text
                    rec["remove_kind"] = "gone" if kind == site_check.GONE_KIND else "redirect"
                    if kind == site_check.REDIRECT:
                        moves.append((rec, target))
        for rec, target in moves:
            hand_over(rec, target, cfg, work, by_key, ctx, result)
        log(f"  content: status check {sorted(result.checked.items())}")

    fill_keywords(ctx, work, result)

    # ---- page rows -----------------------------------------------------------------
    all_urls = [str(rec["row"].get(content.URL, "")) for recs in work.values() for rec in recs]
    dups = content.duplicate_keys(all_urls)
    for t in cfg.tabs:
        if t.name not in work:
            continue
        for rec in work[t.name]:
            row = rec["row"]
            if rec["blank"] or (rec.get("remove") and rec["topic"]):
                continue
            if rec["topic"]:                             # a new topic that has no page yet
                rec["vals"] = topic_values(ctx, row, possible=state.get("possible", content.row_key(row)))
                keep_owner_priority(state, row, rec["vals"], today)
                if rec["new"]:                           # a topic that came from an old sheet
                    kw = str(row.get(content.KEY, ""))
                    rec["vals"]["_create"] = {
                        content.KEY: kw, content.URL: "", content.STATUS: "Inboxed", content.TODO: "Write",
                        "Intent": content.intent_for(t.landing),
                        "Keyword Type": content.keyword_type_for(cfg.seasonal_regex, "", kw),
                        "Content Type": content.content_type_for(t.landing, rec["vals"]["Impressions (90d)"] or 0),
                        content.SLUG: "", **rec.get("import", {})}
                continue
            key = content.url_key(row.get(content.URL))
            stats = rec.get("stats") or ctx.data.stats.get(key)
            cm = ctx.cm
            # A row is never removed because of its data and its status is never changed. A page with
            # no search data only shows zeros and an alert; one that had impressions earlier is worth a look.
            had_before = any(ctx.data.month(periods.add_months(cm, -i)).get(key, (0, 0, 0))[1]
                             for i in (1, 2, 3, 4))
            no_data = bool(ctx.data.month(cm)) and not ctx.data.month(cm).get(key, (0, 0, 0))[1]
            vals = page_values(ctx, row, stats, had_before, rec.get("keyword_hint", ""))
            published = content.parse_date(row.get(content.PUBLISHED_DATE))
            impact = row.get(content.IMPACT, "")
            if published and stats:
                if (ctx.today - published).days < content.IMPACT_DAYS:
                    impact = content.impact_text(published, ctx.today, None, None)
                elif ctx.data.impact_calls < MAX_IMPACT_CALLS:
                    b, a = content.impact_windows(published)
                    ctx.data.impact_calls += 2
                    impact = content.impact_text(published, ctx.today,
                                                 ctx.data.window(stats.url, *b), ctx.data.window(stats.url, *a))
            elif not published:
                impact = ""
            vals[content.IMPACT] = impact
            vals["Alerts"] = content.alerts_for(
                advice_code=vals["_advice"].code, competing=vals["_competing"], seasonal=vals["_seasonal"],
                peak_month=vals["_peak"], today=ctx.today, url=str(row.get(content.URL, "")),
                duplicate=content.dup_key(str(row.get(content.URL, ""))) in dups,
                decay=content.decaying(vals["_series"]), position=vals["_position"],
                impressions=vals["_impressions"], no_data=no_data,
                gone=str(row.get(content.STATUS, "")).strip() == "Gone",
                check=rec.get("check", ""), possible=state.get("possible", content.row_key(row)))
            keep_owner_priority(state, row, vals, today)
            rec["vals"] = vals
            if rec["new"]:
                address = str(row.get(content.URL, ""))
                vals["_create"] = {
                    content.KEY: vals["_keyword"],
                    content.URL: address, content.STATUS: "Published",
                    content.TODO: vals["_advice"].todo,
                    "Intent": content.intent_for(t.landing),
                    "Keyword Type": "Seasonal" if vals["_seasonal"] else "General",
                    "Content Type": content.content_type_for(t.landing, vals["_impressions"]),
                    content.SLUG: content.slug_of(address), **rec.get("import", {})}
    # a row whose own keyword cell is empty gets the computed one, whether the row is new or not
    for t in cfg.tabs:
        for rec in work.get(t.name, []):
            if rec.get("vals") and not rec["new"] and not str(rec["row"].get(content.KEY, "")).strip():
                rec["fill_keyword"] = rec["vals"]["_keyword"]

    # ---- ideas marked Add become topic rows, then content gaps --------------------
    live = [rec for recs in work.values() for rec in recs if not rec.get("remove")]
    present = _targeted(live)
    gap_tab = next((t for t in cfg.tabs if t.gaps and t.name in work), None)
    already = state.keys({"gap", "dismissed", "idea"})

    def add_topic(t, keyword, group, source):
        vals = topic_values(ctx, {content.KEY: keyword}, group)
        vals["Secondary Keywords"] = ", ".join(group[1:content.SECONDARY_COUNT + 1])
        vals["_create"] = {
            content.KEY: keyword, content.URL: "", content.STATUS: "Inboxed", content.TODO: "Write",
            "Intent": content.intent_for(t.landing),
            "Keyword Type": content.keyword_type_for(cfg.seasonal_regex, "", keyword),
            "Content Type": content.content_type_for(t.landing, vals["Impressions (90d)"] or 0),
            content.SLUG: ""}
        work[t.name].append({"row": {content.KEY: keyword}, "new": True, "topic": True,
                             "blank": False, "vals": vals})
        state.set("prio", content.row_key({content.KEY: keyword}), vals["Priority"], today)
        result.new_topics += 1
        return vals

    if cs.IDEAS in info and gap_tab is not None:
        _, ideas = cs.table_of(cs.read_all(sheets, file_id, cs.IDEAS))
        for idea in ideas:
            kw = str(idea.get("Keyword", "")).strip()
            k = content.norm(kw)
            if (str(idea.get("Decision", "")).strip().lower() == "add" and k
                    and k not in already and k not in present):
                add_topic(gap_tab, kw, [kw], "idea")
                state.set("idea", k, ctx.today.isoformat(), ctx.today)
                present.add(k)
                already.add(k)
                result.ideas_added += 1

    def page_words(url: str) -> set:
        """What a content page is about: its slug, its row's keyword and its top searches."""
        key = content.url_key(url)
        words = set(content_import.slug_words(url))
        rec = by_key.get(key)
        if rec is not None:
            words |= content.topic_words(rec["row"].get(content.KEY, ""))
        stats = ctx.data.stats.get(key)
        if stats:                                  # a search far down the page does not say what the page is about
            for q in [q for q in content.nonbrand_queries(stats, ctx.brand) if q[3] <= 20][:3]:
                words |= content.topic_words(q[0])
        return words

    for t in cfg.tabs:
        if not t.gaps or t.name not in work:
            continue
        # a search is answered when its best page is any content page of the site (any tab)
        gaps = content.find_gaps(
            queries=ctx.data.queries, best=ctx.data.best, tab_matches=lambda u: cfg.tab_of(u) is not None,
            brand_terms=ctx.brand,
            minimum=cfg.min_gap_impressions_90d, known=present, already=already,
            exclude_terms=cfg.gap_exclude_terms, page_words=page_words)
        for g in gaps:
            add_topic(t, g.keyword, g.queries, "gap")
            for q in g.queries:
                state.set("gap", content.norm(q), content.norm(g.keyword), ctx.today)
                present.add(content.norm(q))
            already.update(content.norm(q) for q in g.queries)

    # an idea row the owner deleted is not proposed again
    kept = _targeted([rec for recs in work.values() for rec in recs if not rec.get("remove")])
    for r in list(state.rows):
        if r[0] == "gap" and r[1] == r[2] and r[1] not in kept:
            for other in state.rows:
                if other[0] == "gap" and other[2] == r[2]:
                    other[0] = "dismissed"

    # ---- write ----------------------------------------------------------------------
    urgent = []
    for t in cfg.tabs:
        if t.name not in work:
            continue
        header, rows, sid, grid_rows = tables[t.name]
        recs = work[t.name]
        n_old = len(rows)
        extra = len(recs) - n_old
        cs.extend_rows(sheets, file_id, sid, grid_rows, len(recs) + 2)
        columns = {name: [] for name in content.OWNED_MONTHLY + content.FILL_IF_EMPTY}
        for rec in recs:
            vals = rec.get("vals")
            row = rec["row"]
            for name in columns:
                current = row.get(name, "")
                if vals is None or (name == content.IMPACT and rec["topic"]):
                    columns[name].append(current)
                elif name in content.FILL_IF_EMPTY:
                    columns[name].append(current if str(current) != "" else vals.get(name, ""))
                elif name in vals:
                    columns[name].append(vals[name])
                else:
                    columns[name].append(current)
        # new rows have no old values: the lists above hold their computed values already
        cs.write_columns(sheets, file_id, t.name, header, 2, columns)
        if extra > 0:
            names = list(content.ON_CREATE)               # an imported row brings more columns
            for rec in recs[n_old:]:
                names += [n for n in rec["vals"]["_create"] if n not in names]
            create = {name: [rec["vals"]["_create"].get(name, "") for rec in recs[n_old:]] for name in names}
            cs.write_columns(sheets, file_id, t.name, header, 2 + n_old, create)
        updates = []
        for i, rec in enumerate(recs[:n_old]):            # what an old sheet (or a moved page) says about an existing row
            for name, value in (rec.get("import") or {}).items():
                c = cs.col_of(header, name)
                if c is not None:
                    updates.append((f"'{t.name}'!{cs.letters(c)}{2 + i}", [[value]]))
        for i, rec in enumerate(recs):
            if rec.get("fill_keyword") and cs.col_of(header, content.KEY) is not None:
                updates.append((f"'{t.name}'!{cs.letters(cs.col_of(header, content.KEY))}{2 + i}",
                                [[rec["fill_keyword"]]]))
        for i in range(0, len(updates), PATCH_CHUNK):
            tabs.patch_cells(sheets, file_id, updates[i:i + PATCH_CHUNK])
        # the only rows that ever leave a tab: a page that is gone or moved (found twice in a row),
        # and an old row merged into its page by the repair; they are copied to Removed Pages first
        removing = [(i, rec) for i, rec in enumerate(recs[:n_old]) if rec.get("remove")]
        gone = take_out(sheets, file_id, t.name, sid, removing, state, result, today, log) if removing else 0
        cs.sort_by_score(sheets, file_id, sid, header, len(recs) - gone)
        result.rows[t.name] = len(recs) - gone
        for rec in recs:
            vals = rec.get("vals")
            if vals and not rec.get("remove") and vals.get("Priority") == "Urgent":
                kw = rec.get("fill_keyword") or rec["row"].get(content.KEY) or vals.get("_keyword", "")
                urgent.append((vals["Score"], str(kw), vals["Suggested Action"], t.name))
    urgent.sort(key=lambda u: (-u[0], u[1]))
    result.urgent = [(k, a, t) for _, k, a, t in urgent[:TOP_URGENT]]
    result.refreshed = True

    # ---- an import or a repair is done: a log for the owner, and never again ---------
    for spec, key, report in done_imports:
        cs.ensure_import_log(sheets, file_id)
        tabs.append(sheets, file_id, cs.IMPORT_LOG, content_import.log_rows(spec, report), log)
        state.set("import", key, ctx.today.isoformat(), ctx.today)
    for spec, report in repairs:
        cs.ensure_import_log(sheets, file_id)
        tabs.append(sheets, file_id, cs.IMPORT_LOG, content_import.log_rows(spec, report), log)


# --------------------------------------------------------------------------- #
# Ideas (part B)
# --------------------------------------------------------------------------- #

def update_ideas(sheets, file_id: str, site, data: Data, state: State, today: date, result: ContentResult,
                 http_get=autocomplete.default_http_get, sleep=time.sleep, log=print):
    info = cs.sheet_info(sheets, file_id)
    if cs.SEEDS not in info or cs.IDEAS not in info:
        return
    seed_header, seed_rows = cs.table_of(cs.read_all(sheets, file_id, cs.SEEDS))
    due = autocomplete.due_seeds(seed_rows, today)
    if not due:
        return
    ideas_header, ideas = cs.table_of(cs.read_all(sheets, file_id, cs.IDEAS))
    # ideas on the sheet, and ideas that were written before and then deleted: not added again
    existing = {content.norm(i.get("Keyword", "")) for i in ideas} | state.keys({"idea_seen"})
    known = set()
    for t in site.content.tabs:
        if t.name in info:
            known |= _present(cs.table_of(cs.read_all(sheets, file_id, t.name))[1])
    brand = opportunities.brand_terms_for(site)
    found, outcome = autocomplete.collect(due, cache=state.cache, today=today, http_get=http_get, sleep=sleep)
    rows = autocomplete.new_ideas(found, brand_terms=brand, existing=existing, known=known,
                                  today=today, lookup=data.lookup,
                                  exclude_terms=site.content.gap_exclude_terms)
    if rows:
        tabs.append(sheets, file_id, cs.IDEAS, rows)
        for r in rows:
            state.set("idea_seen", content.norm(r[0]), today.isoformat(), today)
    c = cs.col_of(seed_header, "Last run")
    if c is not None:
        tabs.patch_cells(sheets, file_id, [(f"'{cs.SEEDS}'!{cs.letters(c)}{s.row}", [[today.isoformat()]])
                                           for s in outcome.ran])
    state.cache.prune(today)
    result.ideas = len(rows)
    result.autocomplete = (f"{outcome.calls} calls, {outcome.cached} cached" +
                           (f"; stopped: {outcome.stopped}" if outcome.stopped else ""))
    if outcome.stopped:
        log(f"  content: ideas stopped early ({outcome.stopped})")


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #

def load_pages(ctx: Context, result: ContentResult, history_pages, sitemap, log):
    """The page addresses of the stored history and of the sitemap(s), when the caller can read
    them. A source that cannot be read is skipped for this run and changes nothing."""
    if history_pages is not None:
        try:
            ctx.history_urls = list(history_pages() or [])
            result.sources["history"] = len(ctx.history_urls)
        except Exception as e:
            result.sources["history"] = "not readable"
            log(f"  content: the stored history could not be read ({type(e).__name__}); skipped")
    if sitemap is not None:
        try:
            urls, ok = sitemap()
        except Exception as e:
            urls, ok = [], False
            log(f"  content: the sitemap could not be read ({type(e).__name__}); skipped")
        if ok:
            ctx.sitemap_urls = list(urls)
            result.sources["sitemap"] = len(ctx.sitemap_urls)
        else:
            result.sources["sitemap"] = "not readable"
            log("  content: no sitemap could be read; skipped for this run")
    log(f"  content: addresses read {sorted(result.sources.items())}")


def run_content(drive, sheets, site, reports_id: str, fetch, final: date, today: date, notify=None,
                log=print, force: bool = False, http_get=autocomplete.default_http_get,
                sleep=time.sleep, read_source=None, history_pages=None, sitemap=None,
                probe=None) -> ContentResult:
    """The content dashboard of one site: created if missing, refreshed once per complete month
    (or when `force`, or when an old sheet is waiting to be imported or repaired), ideas once a
    week. `read_source(spec) -> (header, rows)` reads an old sheet named in `content.import_from`;
    `history_pages() -> [address]`, `sitemap() -> ([address], ok)` and `probe(address) -> Probe`
    look at the stored history, the sitemap(s) and the live pages."""
    cfg = site.content
    result = ContentResult()
    label = site.deck.site_label or site.name
    file_id, created = provision.content_file(drive, sheets, site, label, notify, log)
    result.file_id, result.created = file_id, created
    try:
        info = cs.sheet_info(sheets, file_id)
    except HttpError as e:
        if e.resp.status in (403, 404):
            raise ContentError("the content file cannot be opened: it must be a file created by "
                               "this tool in your Drive (empty content.sheet_id to create one)") from None
        raise
    if created or cfg.tabs[0].name not in info:
        cs.initial_setup(sheets, file_id, label, cfg.tabs)
    else:
        cs.ensure_extra_tabs(sheets, file_id)
    state = State(cs.read_all(sheets, file_id, cs.STATE, 4))

    cm = last_complete_month(final)
    month = periods.month_label(cm)
    result.month = month
    brand = opportunities.brand_terms_for(site)
    data = Data(fetch, final, brand)
    waiting = (read_source is not None and any(
        not state.get("import", content_import.spec_key(s)) for s in cfg.imports)) \
        or (cfg.repair_imports and not state.get("repair", "v1"))
    if force or created or waiting or state.get("meta", "last_refresh") != month:
        _, aio_pages = aio_import.load_raw(sheets, reports_id)
        newest = max(aio_pages) if aio_pages else ""
        aio = {}
        for url, n in (aio_pages.get(newest) or {}).items():
            aio[content.url_key(url)] = aio.get(content.url_key(url), 0) + n
        ctx = Context(site=site, data=data, today=today, cm=cm, brand=brand, aio=aio,
                      seasonal_regex=cfg.seasonal_regex, read_source=read_source, probe=probe)
        load_pages(ctx, result, history_pages, sitemap, log)
        refresh(sheets, file_id, site, ctx, state, result, log)
        state.set("meta", "last_refresh", month, today)
        state.save(sheets, file_id)                   # an import is recorded as done as soon as it is
    update_ideas(sheets, file_id, site, data, state, today, result, http_get, sleep, log)
    state.save(sheets, file_id)
    return result


def owner_email(result: ContentResult, label: str, link: str) -> tuple:
    """(subject, body) of the owner-only note after a refresh: what changed and what to do first."""
    lines = [f"The content dashboard for {label} was refreshed for {result.month}.", "",
             f"New pages added: {result.new_pages}" + (f" ({result.added_text()})" if result.new_pages else ""),
             f"New topics proposed: {result.new_topics}"]
    if result.checked:
        lines.append("Page status check: " + ", ".join(f"{k} {n}" for k, n in sorted(result.checked.items())))
    if result.warned or result.removed:
        lines.append(f"Pages warned (gone or moved, removed next time if still so): {result.warned}")
        lines += [f"Rows moved to Removed Pages ({k}): {n}" for k, n in sorted(result.removed.items())]
    if any(result.keywords.values()):
        lines.append("Empty Primary Keywords filled from " + ", ".join(
            f"{k}: {n}" for k, n in result.keywords.items()))
    for spec, report in result.imports:
        lines += ["", f"Old sheet imported into {spec.target_tab}: {report.text()}.",
                  "Where each old row went is listed in the Import Log tab."]
    for spec, report in result.repairs:
        lines += ["", f"{report.text()}.", "The rows are listed in the Import Log tab."]
    lines += [f"Not imported: {p}" for p in result.import_problems]
    if result.urgent:
        lines += ["", "Top urgent rows:"]
        lines += [f"- {k or '(no keyword yet)'}: {a} [{t}]" for k, a, t in result.urgent]
    lines += ["", link, ""]
    return f"Content dashboard refreshed · {label} · {result.month}", "\n".join(lines)
