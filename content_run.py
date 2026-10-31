"""
PulseO: the content dashboard, the monthly refresh (v2.5)
==========================================================
Once a month (after the monthly report), or on demand, this module

  1. finds or creates the site's content dashboard file (content_sheet.py, provision.py),
  2. reads the last 90 days of Search Console (pages, searches, query x page) and a few months of
     per-page history,
  3. adds rows for new pages and new topics, updates the cells PulseO owns and leaves every
     cell of the owner alone (content.py has the rules),
  4. sorts each tab by Score, and
  5. once a week, asks Google Autocomplete around the owner's seeds (autocomplete.py).

Nothing here prints an ID, a link, a URL or a keyword. The file never names the tool.
"""

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from googleapiclient.errors import HttpError

import aio_import
import autocomplete
import content
import content_sheet as cs
import opportunities
import periods
import provision
import reports
import tabs

MAX_IMPACT_CALLS = 60          # per-page before/after lookups in one refresh
TOP_URGENT = 5


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

    def text(self) -> str:
        parts = []
        if self.created:
            parts.append("dashboard created")
        if self.refreshed:
            parts.append(f"refreshed for {self.month}: {self.new_pages} new pages, "
                         f"{self.new_topics} new topics")
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
    cache; the others are PulseO's own bookkeeping (last refresh, gap searches, dismissed ones)."""

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

    def set(self, kind: str, key: str, value: str, today: date):
        for r in self.rows:
            if r[0] == kind and r[1] == key:
                r[2], r[3] = value, today.isoformat()
                return
        self.rows.append([kind, key, value, today.isoformat()])

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

    def __init__(self, fetch, final: date, brand_terms: list):
        self.fetch, self.final, self.brand = fetch, final, brand_terms
        self.first90 = final - timedelta(days=89)
        self._loaded = False
        self._months = {}
        self.impact_calls = 0

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


def _num(v, digits=None):
    return round(v, digits) if digits is not None else int(round(v))


def _split(cell) -> list:
    return [x.strip() for x in str(cell or "").split(",") if x.strip()]


def page_values(ctx: Context, row: dict, stats, had_before: bool) -> dict:
    """The cells PulseO owns for a row that has a page. `stats` is None when Search Console shows
    nothing for the page: the row stays, with zeros and empty cells (never removed, status and the
    owner's cells untouched). Also returns "_advice" (the rule that fired) and "_keyword"."""
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
    kw = kw_cell or (content.primary_keyword(stats, ctx.brand) if stats else "")
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


def topic_values(ctx: Context, row: dict, group: list = None) -> dict:
    """The cells PulseO owns for a new-topic row (no page yet). `group` is the list of searches
    when known (new rows); existing rows use their primary and secondary keywords."""
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
    why = (f"{int(round(impressions)):,} impressions across {len(hits)} searches land on "
           f"{content.path_of(best[0])}, which doesn't cover this topic." if impressions and best
           else "No impressions for this topic yet.")
    return {
        "Priority": content.priority(sc), "Score": sc,
        "Clicks (90d)": _num(clicks), "Impressions (90d)": _num(impressions),
        "CTR": round(ctr, 4) if impressions else "",
        "Avg. Position": _num(position, 1) if position else "",
        "Clicks MoM": "", "Clicks YoY": "", "AIO Impressions": "",
        "Primary KW Impr.": _num(top[1]) if top else "",
        "Primary KW Pos.": _num(top[2], 1) if top and top[2] else "",
        "Ranking Queries": len(hits) or "", "Competing Pages": "", content.IMPACT: "",
        "Suggested Action": "New page", "Why": why, "Alerts": "No fitting page",
        "_advice": None, "_keyword": kw}


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


def _effective(rec: dict) -> dict:
    """The keyword cells of a record as they are (or will be) in the file."""
    vals = rec.get("vals") or {}
    kw = rec.get("fill_keyword") or rec["row"].get(content.KEY, "") or vals.get("_keyword", "")
    sec = vals.get("Secondary Keywords", rec["row"].get("Secondary Keywords", ""))
    return {content.KEY: kw, "Secondary Keywords": sec}


def refresh(sheets, file_id: str, site, ctx: Context, state: State, result: ContentResult,
            log=print):
    cfg = site.content
    ctx.data.load()
    info = cs.sheet_info(sheets, file_id)
    tables = {}                                  # tab -> (header, rows, sheetId, grid rows)
    for t in cfg.tabs:
        if t.name not in info:
            log("  content: a configured tab is missing in the file and was skipped")
            continue
        header, rows = cs.table_of(cs.read_all(sheets, file_id, t.name))
        tables[t.name] = (header, rows, info[t.name][0], info[t.name][1])

    existing_keys = {content.url_key(r.get(content.URL)) for _, rows, _, _ in tables.values()
                     for r in rows if str(r.get(content.URL, "")).strip()}
    work = {}                                    # tab -> list of records
    for t in cfg.tabs:
        if t.name not in tables:
            continue
        header, rows, _, _ = tables[t.name]
        recs = [{"row": r, "new": False, "topic": not str(r.get(content.URL, "")).strip()
                 and bool(str(r.get(content.KEY, "")).strip()),
                 "blank": not str(r.get(content.URL, "")).strip() and not str(r.get(content.KEY, "")).strip()}
                for r in rows]
        for key, s in sorted(ctx.data.stats.items()):
            if s.impressions < content.NEW_PAGE_MIN_IMPRESSIONS or key in existing_keys:
                continue
            if cfg.tab_of(s.url) is not t:
                continue
            existing_keys.add(key)
            recs.append({"row": {content.URL: key, content.STATUS: "Published"}, "new": True,
                         "topic": False, "blank": False, "stats": s})
            result.new_pages += 1
        work[t.name] = recs

    # ---- page rows -----------------------------------------------------------------
    all_urls = [str(rec["row"].get(content.URL, "")) for recs in work.values() for rec in recs]
    dups = content.duplicate_keys(all_urls)
    for t in cfg.tabs:
        if t.name not in work:
            continue
        for rec in work[t.name]:
            row = rec["row"]
            if rec["blank"]:
                continue
            if rec["topic"]:                             # a new topic that has no page yet
                rec["vals"] = topic_values(ctx, row)
                continue
            key = content.url_key(row.get(content.URL))
            stats = rec.get("stats") or ctx.data.stats.get(key)
            cm = ctx.cm
            # A row is never removed and its status is never changed. A page with no search data
            # only shows zeros and an alert; one that had impressions earlier is worth a look.
            had_before = any(ctx.data.month(periods.add_months(cm, -i)).get(key, (0, 0, 0))[1]
                             for i in (1, 2, 3, 4))
            no_data = bool(ctx.data.month(cm)) and not ctx.data.month(cm).get(key, (0, 0, 0))[1]
            vals = page_values(ctx, row, stats, had_before)
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
                gone=str(row.get(content.STATUS, "")).strip() == "Gone")
            rec["vals"] = vals
            if rec["new"]:
                vals["_create"] = {
                    content.KEY: vals["_keyword"],
                    content.URL: key, content.STATUS: "Published",
                    content.TODO: vals["_advice"].todo,
                    "Intent": content.intent_for(t.landing),
                    "Keyword Type": "Seasonal" if vals["_seasonal"] else "General",
                    "Content Type": content.content_type_for(t.landing, vals["_impressions"]),
                    content.SLUG: content.slug_of(key)}
    # a row whose own keyword cell is empty gets the computed one, whether the row is new or not
    for t in cfg.tabs:
        for rec in work.get(t.name, []):
            if rec.get("vals") and not rec["new"] and not str(rec["row"].get(content.KEY, "")).strip():
                rec["fill_keyword"] = rec["vals"]["_keyword"]

    # ---- ideas marked Add become topic rows, then content gaps --------------------
    present = _present([_effective(rec) for recs in work.values() for rec in recs])
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

    for t in cfg.tabs:
        if not t.gaps or t.name not in work:
            continue
        # a search is answered when its best page is any content page of the site (any tab)
        gaps = content.find_gaps(
            queries=ctx.data.queries, best=ctx.data.best, tab_matches=lambda u: cfg.tab_of(u) is not None,
            brand_terms=ctx.brand,
            minimum=cfg.min_gap_impressions_90d, known=present, already=already)
        for g in gaps:
            add_topic(t, g.keyword, g.queries, "gap")
            for q in g.queries:
                state.set("gap", content.norm(q), content.norm(g.keyword), ctx.today)
                present.add(content.norm(q))
            already.update(content.norm(q) for q in g.queries)

    # a gap row the owner deleted is not proposed again
    kept = _present([_effective(rec) for recs in work.values() for rec in recs])
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
        create = {name: [] for name in content.ON_CREATE}
        if extra > 0:
            for rec in recs[n_old:]:
                for name in create:
                    create[name].append(rec["vals"]["_create"].get(name, ""))
            cs.write_columns(sheets, file_id, t.name, header, 2 + n_old, create)
        updates = []
        for i, rec in enumerate(recs):
            if rec.get("fill_keyword") and cs.col_of(header, content.KEY) is not None:
                updates.append((f"'{t.name}'!{cs.letters(cs.col_of(header, content.KEY))}{2 + i}",
                                [[rec["fill_keyword"]]]))
        tabs.patch_cells(sheets, file_id, updates)
        cs.sort_by_score(sheets, file_id, sid, header, len(recs))
        result.rows[t.name] = len(recs)
        for rec in recs:
            vals = rec.get("vals")
            if vals and vals.get("Priority") == "Urgent":
                kw = rec.get("fill_keyword") or rec["row"].get(content.KEY) or vals.get("_keyword", "")
                urgent.append((vals["Score"], str(kw), vals["Suggested Action"], t.name))
    urgent.sort(key=lambda u: (-u[0], u[1]))
    result.urgent = [(k, a, t) for _, k, a, t in urgent[:TOP_URGENT]]
    result.refreshed = True


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
                                  today=today, lookup=data.lookup)
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

def run_content(drive, sheets, site, reports_id: str, fetch, final: date, today: date, notify=None,
                log=print, force: bool = False, http_get=autocomplete.default_http_get,
                sleep=time.sleep) -> ContentResult:
    """The content dashboard of one site: created if missing, refreshed once per complete month
    (or when `force`), ideas once a week."""
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
    if force or created or state.get("meta", "last_refresh") != month:
        _, aio_pages = aio_import.load_raw(sheets, reports_id)
        newest = max(aio_pages) if aio_pages else ""
        aio = {}
        for url, n in (aio_pages.get(newest) or {}).items():
            aio[content.url_key(url)] = aio.get(content.url_key(url), 0) + n
        ctx = Context(site=site, data=data, today=today, cm=cm, brand=brand, aio=aio,
                      seasonal_regex=cfg.seasonal_regex)
        refresh(sheets, file_id, site, ctx, state, result, log)
        state.set("meta", "last_refresh", month, today)
    update_ideas(sheets, file_id, site, data, state, today, result, http_get, sleep, log)
    state.save(sheets, file_id)
    return result


def owner_email(result: ContentResult, label: str, link: str) -> tuple:
    """(subject, body) of the owner-only note after a refresh: what changed and what to do first."""
    lines = [f"The content dashboard for {label} was refreshed for {result.month}.", "",
             f"New pages added: {result.new_pages}", f"New topics proposed: {result.new_topics}"]
    if result.urgent:
        lines += ["", "Top urgent rows:"]
        lines += [f"- {k or '(no keyword yet)'}: {a} [{t}]" for k, a, t in result.urgent]
    lines += ["", link, ""]
    return f"Content dashboard refreshed · {label} · {result.month}", "\n".join(lines)
