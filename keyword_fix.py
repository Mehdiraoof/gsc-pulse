"""
PulseO: the one-time fix of Primary Keywords that were chosen by impressions alone
================================================================================
A refresh never changes a Primary Keyword that is set, so the new rule (the search that fits the page's title and
slug best) cannot reach the rows that were filled by the old one. This command does, once per site, for the rows
that were certainly never chosen by a person. It is never part of a refresh.

A row may change only when ALL of these are true:
  * it has a Published URL and is not marked Removed or Redirected;
  * its Primary Keyword is still the page's highest-impression non-brand search (the old automatic pick);
  * its Notes/Checklist does not say "Keyword checked";
  * its Attachments hold no "Brief ·" and no "Draft ·" link;
  * the best match is strong and different from the current keyword.
On change: the new keyword goes into Primary Keyword, the old one into Secondary Keywords (no duplicates) and a line
into Notes/Checklist. A row that qualifies but has no strong match only gets the alert "Keyword needs review".

`plan()` writes nothing (the dry run). `apply()` writes the plan. Nothing here calls an AI.
"""

from dataclasses import dataclass
from datetime import date

import content
import content_import
import content_sheet as cs
import site_check
import tabs

ATTACHMENTS = "Attachments"
BRIEF_MARK, DRAFT_MARK = "Brief ·", "Draft ·"
CHANGE, REVIEW = "change", "review"
PLAN_HEADER = ["Tab", "Row", "Action", "Page", "Old keyword", "New keyword", "Score", "Score of the old keyword"]


@dataclass
class Change:
    tab: str
    row: int                 # sheet row number
    action: str              # change | review
    url: str
    old: str
    new: str
    score: float
    old_score: float


def qualifies(row: dict) -> bool:
    """The cheap conditions that need no data."""
    url, kw = str(row.get(content.URL, "")).strip(), str(row.get(content.KEY, "")).strip()
    if not url or not kw or content.is_settled(row):
        return False
    if content.checked(row.get(content_import.NOTES, "")):
        return False
    attachments = str(row.get(ATTACHMENTS, ""))
    return BRIEF_MARK not in attachments and DRAFT_MARK not in attachments


def plan(sheets, file_id: str, site, data, brand: list, probe=site_check.fetch_probe, log=print) -> list:
    """Every change this command would make, in tab and row order. Writes nothing."""
    cfg = site.content
    data.load()
    info = cs.sheet_info(sheets, file_id)
    found = []
    for t in cfg.tabs:
        if t.name not in info:
            continue
        _, rows = cs.table_of(cs.read_all(sheets, file_id, t.name))
        candidates = []
        for i, row in enumerate(rows):
            if not qualifies(row):
                continue
            url = str(row[content.URL]).strip()
            stats = data.stats.get(content.url_key(url))
            queries = content.nonbrand_queries(stats, brand) if stats else []
            if queries and content.norm(queries[0][0]) == content.norm(row[content.KEY]):
                candidates.append((i + 2, url, str(row[content.KEY]).strip(), stats))
        probes = site_check.check_all([c[1] for c in candidates], probe) if candidates else {}
        for number, url, old, stats in candidates:
            p = probes.get(url)
            title = p.title if p is not None and p.kind == site_check.OK else ""
            pick = content.pick_keyword(stats, brand, url, title, cfg.keyword_strong_match,
                                        cfg.keyword_min_impressions)
            old_score = content.match_score(old, content.page_wordsets(url, title, brand))
            if pick.strong and content.norm(pick.keyword) != content.norm(old):
                found.append(Change(t.name, number, CHANGE, url, old, pick.keyword, pick.score, old_score))
            elif not pick.strong and old_score < cfg.keyword_strong_match:
                # no search fits, and the keyword itself does not fit the page's title and slug either (a keyword
                # that fits is left alone: a page with few searches has nothing better to offer)
                found.append(Change(t.name, number, REVIEW, url, old, "", pick.score, old_score))
        log(f"  keyword fix: {t.name}: {len(candidates)} rows may change, "
            f"{sum(1 for c in found if c.tab == t.name and c.action == CHANGE)} to change, "
            f"{sum(1 for c in found if c.tab == t.name and c.action == REVIEW)} to flag")
    return found


def plan_rows(changes: list) -> list:
    return [PLAN_HEADER] + [[c.tab, c.row, c.action, c.url, c.old, c.new, c.score, c.old_score] for c in changes]


def add_secondary(existing, old: str, new: str) -> str:
    """The Secondary Keywords cell with the old keyword added (comma separated, once). Nothing is taken out."""
    have = content.split_keywords(existing)
    seen = {content.norm(x) for x in have} | {content.norm(new)}
    if content.norm(old) in seen:
        return str(existing or "")
    return ", ".join(have + [old]) if have else old


def apply(sheets, file_id: str, changes: list, today: date, log=print) -> dict:
    """Writes the plan. A row is read again first: if it no longer qualifies (someone worked in the sheet since the
    plan was made) it is left alone. Returns {"changed": n, "flagged": n, "skipped": n}."""
    done = {"changed": 0, "flagged": 0, "skipped": 0}
    by_tab = {}
    for c in changes:
        by_tab.setdefault(c.tab, []).append(c)
    for tab, items in by_tab.items():
        header, rows = cs.table_of(cs.read_all(sheets, file_id, tab))
        updates = []
        for c in items:
            row = rows[c.row - 2] if 0 < c.row - 1 <= len(rows) else {}
            if (not qualifies(row) or content.norm(row.get(content.URL, "")) != content.norm(c.url)
                    or content.norm(row.get(content.KEY, "")) != content.norm(c.old)):
                done["skipped"] += 1
                continue
            at = lambda name: f"'{tab}'!{cs.letters(cs.col_of(header, name))}{c.row}"      # noqa: E731
            if c.action == CHANGE:
                notes = str(row.get(content_import.NOTES, "")).strip()
                line = content.PLAN_NOTE.format(day=today.isoformat(), old=c.old)
                updates += [(at(content.KEY), [[c.new]]),
                            (at("Secondary Keywords"), [[add_secondary(row.get("Secondary Keywords", ""), c.old, c.new)]]),
                            (at(content_import.NOTES), [[(notes + "\n" + line) if notes else line]])]
                done["changed"] += 1
            else:
                alerts = str(row.get("Alerts", "")).strip()
                if content.REVIEW_ALERT not in alerts:
                    updates.append((at("Alerts"), [[(content.REVIEW_ALERT + ", " + alerts) if alerts
                                                     else content.REVIEW_ALERT]]))
                done["flagged"] += 1
        for i in range(0, len(updates), 200):
            tabs.patch_cells(sheets, file_id, updates[i:i + 200])
    log(f"  keyword fix: {done['changed']} keywords changed, {done['flagged']} rows flagged, "
        f"{done['skipped']} left alone (changed since the plan)")
    return done
