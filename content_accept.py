"""
PulseO: the "done" checklist for content creation, run on the fake test site
============================================================================
`snapshot` takes the dashboards of the fake world before a run; `verify` takes them again afterwards,
with what the run reported and the emails it sent, and tests every item of the checklist that a run
can show (1 to 11 and 13; item 12 is the test suite). It reads the documents back from Drive, as
Google exports them. Failures name an item, a rule and a count, never a text (content_check.py).
"""

import re
from datetime import date

import content
import content_check as cc
import content_create as ccr
import content_sheet as cs
import gdocs


def snapshot(sheets, world) -> dict:
    """{site name: {tab: Snapshot}} of the dashboards of the fake world."""
    out = {}
    for name, w in world.sites.items():
        out[name] = {tab: cc.Snapshot.of(cs.read_all(sheets, w.dashboard_id, tab)) for tab in ("Articles", "Landing Pages")}
    return out


def _rows(shots: dict):
    for tab, snap in shots.items():
        for row in snap.rows:
            yield tab, row


def _todo(row) -> str:
    return str(row.get(content.TODO, "")).strip().lower()


def _status(row) -> str:
    return str(row.get(content.STATUS, "")).strip().lower()


def _by_key(shots: dict) -> dict:
    out = {}
    for _, row in _rows(shots):
        out.setdefault(content.row_key(row), []).append(row)
    return out


def _links(row) -> list:
    return gdocs.doc_ids_in(row.get("Attachments"))


def _persian(doc: cc.Doc) -> bool:
    return bool(cc.PERSIAN_LETTER.search(doc.full_text))


def verify(drive, world, before: dict, after: dict, results: dict, emails: list, today: date, mode: str = "both") -> cc.Report:
    """The checklist for one run of every site. `results`: {site: CreateResult}; `emails`: [(site, subject, body)]."""
    report = cc.Report()
    expected_links = {}
    for name, w in world.sites.items():
        site, result = w.site, results[name]
        rows_before, rows_after = _by_key(before[name]), _by_key(after[name])
        briefs_before = gdocs.docs_in(drive, w.briefs_id)
        has_sop = bool(w.sop_id)
        links = [link for _, link in result.briefs + result.drafts]
        expected_links[name] = links if result.did_something else []

        # --- what should have happened, from the dashboard as it was
        todo_brief = [r for _, r in _rows(before[name]) if _todo(r) == "brief" and _status(r) != "briefed"
                      and str(r.get(content.KEY, "")).strip()]
        draft_rows = [r for _, r in _rows(before[name]) if _todo(r) == "draft" and str(r.get(content.KEY, "")).strip()]
        with_brief = [r for r in draft_rows if any(i in briefs_before for i in _links(r))]
        without = [r for r in draft_rows if r not in with_brief]
        if mode in ("briefs", "both") and not result.stopped:
            if len(result.briefs) != len(todo_brief) and len(todo_brief) <= 10:
                report.add([cc.Failure(1, "brief-count", name, f"{len(result.briefs)} of {len(todo_brief)}")])
        if mode in ("drafts", "both") and has_sop and not result.stopped:
            if len(result.drafts) != len(with_brief) and len(with_brief) <= 5:
                report.add([cc.Failure(2, "draft-count", name, f"{len(result.drafts)} of {len(with_brief)}")])

        processed = set(result.processed_keys)
        # item 9: nothing else changed
        for tab in before[name]:
            report.add([f for f in cc.diff_dashboard(before[name][tab], after[name][tab],
                                                     {k for k in processed if k in {content.row_key(r) for r in before[name][tab].rows}})])

        # items 1, 5, 6, 7, 8: the briefs
        for kw, link in result.briefs:
            rows = [r for k, rs in rows_after.items() for r in rs if str(r.get(content.KEY, "")).split() == kw.split()]
            if not rows:
                report.add([cc.Failure(1, "row-missing", name)])
                continue
            row = rows[0]
            doc_id = gdocs.doc_ids_in(link)[0]
            update = bool(str(row.get(content.URL, "")).strip())
            if (str(row.get(content.STATUS)) != "Briefed" or str(row.get("Briefed Date")) != today.isoformat()
                    or str(row.get(content.TODO)) != ("Update" if update else "Write") or doc_id not in _links(row)):
                report.add([cc.Failure(1, "row-not-updated", name)])
            if doc_id not in gdocs.docs_in(drive, w.briefs_id):
                report.add([cc.Failure(1, "wrong-folder", name)])
                continue
            html = gdocs.export_html(drive, doc_id)
            report.add(cc.check_brief(html, update=update))
            doc = cc.Doc(html)
            if (site.language == "fa") != _persian(doc):
                report.add([cc.Failure(1, "wrong-language", name)])
            if site.language == "fa":
                report.add(cc.check_persian(doc))

        # items 2, 5 to 8: the drafts
        for kw, link in result.drafts:
            rows = [r for k, rs in rows_after.items() for r in rs if str(r.get(content.KEY, "")).split() == kw.split()]
            row = rows[0] if rows else {}
            doc_id = gdocs.doc_ids_in(link)[0]
            if (str(row.get(content.STATUS)) != "Drafted" or str(row.get(content.TODO)) != "Review"
                    or doc_id not in _links(row) or ccr.NOTE_DRAFT_READY.split(".")[0] not in str(row.get("Notes/Checklist"))):
                report.add([cc.Failure(2, "row-not-updated", name)])
            if doc_id not in gdocs.docs_in(drive, w.drafts_id):
                report.add([cc.Failure(2, "wrong-folder", name)])
                continue
            html = gdocs.export_html(drive, doc_id)
            brief_ids = [i for i in _links(row) if i in gdocs.docs_in(drive, w.briefs_id)]
            comparison = ccr.tables_planned(ccr.read_text(drive, brief_ids[0])) if brief_ids else False
            report.add(cc.check_draft(html, language=site.language, domain=site.domain, has_comparison=comparison))
            if (site.language == "fa") != _persian(cc.Doc(html)):
                report.add([cc.Failure(2, "wrong-language", name)])

        # item 3: a Draft row without a brief is skipped with the note
        if has_sop and mode in ("drafts", "both") and not result.stopped:
            for r in without:
                after_rows = rows_after.get(content.row_key(r), [])
                noted = any(ccr.NOTE_NO_BRIEF in str(a.get("Notes/Checklist", "")) for a in after_rows)
                if not noted or any(_todo(a) != "draft" or _status(a) == "drafted" for a in after_rows):
                    report.add([cc.Failure(3, "not-skipped-with-a-note", name)])
        # item 4: no SOP, no drafts
        if not has_sop:
            if gdocs.docs_in(drive, w.drafts_id) or result.drafts or result.no_brief:
                report.add([cc.Failure(4, "draft-without-sop", name)])

    # item 10: one email per site with the right links (a site that stopped or had a note still gets its one email)
    report.add(cc.check_emails(emails, {n: l for n, l in expected_links.items() if l or not results[n].did_something}))
    for name, r in results.items():
        if r.did_something and not expected_links[name]:
            n = len([1 for site, _, _ in emails if site == name])
            if n != 1:
                report.add([cc.Failure(10, "email-count", name, f"{n} emails")])
    return report


def verify_repeat(drive, world, docs_before: dict, after_first: dict, after_second: dict, results: dict,
                  emails: list) -> cc.Report:
    """Items 11 and 10 for a second run right after the first: no doc added, no cell changed, no email."""
    report = cc.Report()
    report.add(cc.check_no_new_docs(docs_before, doc_sets(drive, world)))
    for name in world.sites:
        for tab in after_first[name]:
            report.add(cc.diff_dashboard(after_first[name][tab], after_second[name][tab], set()))
        r = results[name]
        if r.briefs or r.drafts:
            report.add([cc.Failure(11, "second-run-made-docs", name)])
    report.add(cc.check_emails(emails, {name: [] for name in world.sites}))
    return report


def doc_sets(drive, world) -> dict:
    """{folder id: set of doc ids} of every Briefs and Drafts folder of the world."""
    out = {}
    for w in world.sites.values():
        out[w.briefs_id] = set(gdocs.docs_in(drive, w.briefs_id))
        out[w.drafts_id] = set(gdocs.docs_in(drive, w.drafts_id))
    return out


def verify_limited(emails: list, results: dict, after: dict, before: dict) -> cc.Report:
    """Item 13: a run that hits a limit stops, says so, and leaves the rest as it was (item 9 checks the rest)."""
    report = cc.Report()
    stopped = [n for n, r in results.items() if r.stopped]
    if not stopped:
        report.add([cc.Failure(13, "did-not-stop")])
    for n in stopped:
        mails = [b for site, s, b in emails if site == n]
        if len(mails) != 1 or "stopped early" not in mails[0]:
            report.add([cc.Failure(13, "email-does-not-say-so", n)])
        if results[n].waiting <= 0:
            report.add([cc.Failure(13, "no-rows-left-waiting", n)])
    return report
