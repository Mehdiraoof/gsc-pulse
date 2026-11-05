"""
PulseO: "Our work and its results", the glue between the sheets and work_items.py
================================================================================
  scan_site    (sync run) looks at the sitemap, the content dashboard and, once, the stored history, and records
               new and updated pages in the `_state` tab of the site's content dashboard.
  items_for    (any run) the work items of the last months, measured: automatic ones from that state, manual
               ones from the tab "Our Work" of the Reports sheet, the numbers from the stored history.
  prepare_notes (sync run) the rows of the tab "AI Notes" for the texts the reports will use.
The log gets counts only: no address, no keyword.
"""

import re
from datetime import date, timedelta

import ai_notes
import state as reports_state
import client_data as cd
import client_slides
import client_i18n
import content
import content_run
import content_sheet as cs
import deck_data as dd
import history
import i18n
import opportunities
import provision
import site_check
import work_items as wi
import work_view

HISTORY_DAYS = wi.LOOKBACK_CLIENT + 2 * wi.WINDOW_DAYS + 7      # how far back the page history is read


def load_content(sheets, file_id: str, site) -> tuple:
    """(State, dashboard rows) of a content dashboard (a page that is gone keeps its row, marked)."""
    state = content_run.State(cs.read_all(sheets, file_id, cs.STATE, 4))
    info = cs.sheet_info(sheets, file_id)
    rows = []
    for t in site.content.tabs:
        if t.name in info:
            rows += cs.table_of(cs.read_all(sheets, file_id, t.name))[1]
    return state, rows


def scan_site(drive, sheets, site, today: date, get=site_check.default_get, log=print) -> int:
    """Records new and updated pages. Returns how many events were found (0 when the site has no content
    dashboard yet)."""
    file_id = provision.existing_content_file(drive, sheets, site) if (site.content.enabled and drive is not None) else ""
    if not file_id:
        log("  work: no content dashboard yet")
        return 0
    state, rows = load_content(sheets, file_id, site)
    entries, ok = site_check.read_sitemap_entries(site_check.origin_of(site.site_url), get, site.content.extra_sitemaps)
    first_seen = None
    if not state.keys({"seen"}) and site.history_sheet_id:
        first_seen = history.read_first_seen(sheets, site.history_sheet_id)          # the first run: from the history
    found = wi.scan(state, today, entries, ok, rows, lambda u: site.content.tab_of(u) is not None, first_seen)
    state.save(sheets, file_id)
    log(f"  work: {found} new events, {len(state.keys({'seen'}))} pages followed"
        + ("" if ok else " (the sitemap could not be read)"))
    return found


def _final_day(sheets, site, table: wi.PageDays) -> date:
    try:
        last = history.read_state(sheets, site.history_sheet_id).get("H Pages", {}).get("last", "")
        if last:
            return date.fromisoformat(last)
    except Exception:                                     # noqa: BLE001  the table's own last day will do
        pass
    return table.last


def items_for(drive, sheets, site, today: date, queries=None, log=print) -> list:
    """All the work items of the last LOOKBACK_CLIENT days, measured (item.impact). Empty when there is nothing."""
    manual = wi.read_manual(sheets, site.reports_sheet_id) if site.reports_sheet_id else []
    file_id = provision.existing_content_file(drive, sheets, site) if (site.content.enabled and drive is not None) else ""
    auto, redirects = [], {}
    if file_id:
        state, rows = load_content(sheets, file_id, site)
        titles = {content.url_key(r.get(content.URL, "")): str(r.get(content.KEY, "")).strip()
                  for r in rows if str(r.get(content.URL, "")).strip() and str(r.get(content.KEY, "")).strip()}
        auto = wi.auto_items(state, today, wi.LOOKBACK_CLIENT, titles)
        redirects = wi.redirect_map(state, rows)
    items = [it for it in auto + manual if 0 <= (today - it.day).days <= wi.LOOKBACK_CLIENT]
    if not items or not site.history_sheet_id:
        return items
    table = wi.PageDays(history.read_pages_since(sheets, site.history_sheet_id,
                                                 today - timedelta(days=HISTORY_DAYS)))
    final = _final_day(sheets, site, table)
    if final is None:
        return items
    wi.evaluate(items, table, final, redirects, queries)
    counts = {}
    for i in items:
        key = f"{i.kind} {i.impact.status if i.impact else 'unmeasured'}"
        counts[key] = counts.get(key, 0) + 1
    log(f"  work: {len(items)} items ({', '.join(f'{n} {k}' for k, n in sorted(counts.items()))}), "
        f"{len(wi.for_client(items, today))} for the client deck, {len(wi.for_owner(items, today))} for the weekly deck")
    return items


def facts_text(items: list, T: dict, lang: str, short=lambda s: s) -> str:
    """The plain facts the writer of the intro sentence gets: what was done and what it achieved."""
    lines = []
    for it in items:
        r = work_view.client_row(it, T, lang, short)
        lines.append(f"- {r['what']} ({r['date']}): clicks {r['clicks']}; average position {r['position'] or 'n/a'}; {r['versus']}"
                     if r["clicks"] else f"- {r['what']}" + (f" ({r['date']})" if r["date"] else "") + " (no result to report yet)")
    return "\n".join(lines) or "- (no items)"


def gsc_queries(gsc, site):
    """A `queries(item, first, last)` counting the distinct non-brand searches of an item's page(s) from Search
    Console; None when the item names no page or the request fails."""
    brand = opportunities.brand_terms_for(site)

    def count(item, first: date, last: date):
        scope = str(item.scope or "").strip()
        body = {"startDate": first.isoformat(), "endDate": last.isoformat(), "dimensions": ["query"], "rowLimit": 5000}
        if scope.startswith("http"):
            flt = {"dimension": "page", "operator": "equals", "expression": scope}
        elif scope.startswith("/"):
            flt = {"dimension": "page", "operator": "includingRegex",
                   "expression": ".*" + re.escape(scope).replace(r"\*", ".*") + ".*"}
        elif scope.lower() in wi.WHOLE_SITE:
            flt = None
        else:
            return None
        if flt:
            body["dimensionFilterGroups"] = [{"filters": [flt]}]
        try:
            resp = gsc.searchanalytics().query(siteUrl=site.site_url, body=body).execute(num_retries=3)
        except Exception:                                  # noqa: BLE001  a count is nice to have, never needed
            return None
        return len({content.norm(row["keys"][0]) for row in resp.get("rows", [])
                    if not content.skip_query(row["keys"][0], brand)})
    return count


def prepare_notes(drive, sheets, site, today: date, queries=None, log=print) -> dict:
    """Writes the AI Notes rows the next reports of this site need (nothing is emailed, no AI is called).
    Returns {"added": n, "refreshed": n}."""
    cfg = site.deck
    wanted = []
    label = cfg.site_label
    lang = cfg.language
    if cfg.weekly and cfg.ai_summary:
        rep = dd.load_weekly(sheets, site.reports_sheet_id, site)
        if rep is not None:
            wanted.append(ai_notes.Slot("weekly", rep.period, ai_notes.WEEKLY_SUMMARY,
                                        ai_notes.weekly_summary_input(dd.aggregated_text(rep, label), label,
                                                                      dd.period_title(rep, lang), lang), lang))
    if cfg.monthly:
        rep = dd.load_monthly(sheets, site.reports_sheet_id, site)
        if rep is not None:
            items = wi.for_client(items_for(drive, sheets, site, today, queries, log), today, rep.period,
                                  shown_ids(sheets, site))
            if items:
                T = {**i18n.table(lang), **client_i18n.table(lang)}
                wanted.append(ai_notes.Slot("monthly", rep.period, ai_notes.OUR_WORK_INTRO,
                                            ai_notes.our_work_intro_input(label, dd.period_title(rep, lang), lang,
                                                                          facts_text(items, T, lang)), lang))
            # the slides the client deck could have: the owner's plan chooses which of them go in
            slides = cd.ClientReport(rep, lang, label, items, "").slides()
            wanted.append(ai_notes.Slot("monthly", rep.period, ai_notes.CLIENT_SLIDES,
                                        client_slides.input_text(label, dd.period_title(rep, lang), lang, slides), lang))
    result = ai_notes.prepare(sheets, site.reports_sheet_id, wanted, today, log) if wanted else {"added": 0, "refreshed": 0}
    wi.ensure_our_work_tab(sheets, site.reports_sheet_id)
    log(f"  AI notes: {result['added']} rows added, {result['refreshed']} refreshed")
    return result


def extras_for(drive, sheets, site, today: date, gsc=None, log=print):
    """The `extras(kind, report)` the deck runner calls when the texts come from the AI Notes tab: the texts that
    are ready, the slots still waiting, and the work items for that deck (the client's best results for the monthly
    deck, everything of the last 90 days for the weekly one). The items are measured once per run."""
    queries = gsc_queries(gsc, site) if gsc is not None else None
    cache = {}

    def extras(kind: str, report) -> dict:
        if "items" not in cache:
            cache["items"] = items_for(drive, sheets, site, today, queries, log)
        texts, waiting = ai_notes.texts_for(sheets, site.reports_sheet_id, kind, report.period)
        work = (wi.for_client(cache["items"], today, report.period, shown_ids(sheets, site)) if kind == "monthly"
                else wi.for_owner(cache["items"], today))
        return {"texts": texts, "waiting": waiting, "work": work,
                "shown": [wi.item_id(it) for it in work if kind == "monthly" and it.numbers and it.kind != wi.GROUP]}
    return extras





def shown_ids(sheets, site) -> set:
    """The ids of the work items already shown to the client with their numbers (kept in the State tab of the
    Reports sheet, written when the monthly deck went out), so a later result is shown once and never again."""
    if not site.reports_sheet_id:
        return set()
    return {k[len(wi.SHOWN_PREFIX):] for k in reports_state.read_state(sheets, site.reports_sheet_id)
            if k.startswith(wi.SHOWN_PREFIX)}
