"""
PulseO: rehearsal of content creation on a COPY of a real site's dashboard
==========================================================================
Before a real site is switched to content creation, the whole thing is tried on a copy:

  prepare  copies the rows of the site's content dashboard into a new dashboard inside the "PulseO Test"
           folder (the rows marked Brief in full; every other row with an address only as far as a
           keyword, an address and a status, so the draft has real pages to link to). The real file is
           only read. The SOP text is put into the sheet "Rehearsal · Inputs" (tab Inputs, rows
           "sop" | one line of text) by the owner, because this tool can open only the files it made itself.
  run      makes the SOP document from those lines, writes the briefs, marks the briefed rows Draft, writes
           the drafts, tests every document with the checker, and sends the owner one email.

Nothing here prints a keyword, an address, an id or a text: counts and rule names only.
"""

from datetime import date

import content
import content_check as cc
import content_create as ccr
import content_docs as cd
import content_sheet as cs
import fake_site
import gdocs
import history
import provision
import site_check
import sites
import tabs

FOLDER = "Rehearsal copy"
DASH_NAME = "[Rehearsal] Content Dashboard"
INPUTS_NAME = "Rehearsal · Inputs"
INPUTS_TAB = "Inputs"
SOP_NAME = "Rehearsal · SOP"


class RehearsalError(Exception):
    pass


def _norm(value) -> str:
    return str(value or "").strip().lower()


def source_dashboard_id(drive, sheets, site) -> str:
    """The id of the site's content dashboard, found without making anything (configuration, else the Index)."""
    if site.content.sheet_id:
        return site.content.sheet_id
    index_id = provision._open_index(drive, sheets)
    rows = tabs.read_rows(sheets, index_id, provision.INDEX_TAB, provision.INDEX_LAST)
    _, row = provision._row_of(rows, site.name)
    if not row[6]:
        raise RehearsalError("the site has no content dashboard yet")
    return row[6]


def _folders(drive) -> dict:
    root = provision.ensure_folder(drive, fake_site.TEST_ROOT)
    folder = provision.ensure_folder(drive, FOLDER, root)
    return {"folder": folder, "briefs": provision.ensure_folder(drive, "Briefs", folder),
            "drafts": provision.ensure_folder(drive, "Drafts", folder)}


def copy_rows(header: list, rows: list) -> tuple:
    """(rows to write, how many are marked Brief). A row marked Brief is copied whole; any other row that has an
    address keeps only its keyword, address and status (it is only a page to link to)."""
    out, marked = [], 0
    for row in rows:
        todo, status = _norm(row.get(content.TODO)), _norm(row.get(content.STATUS))
        if todo == "brief" and status != "briefed" and str(row.get(content.KEY, "")).strip():
            out.append({h: row.get(h, "") for h in content.HEADERS})
            marked += 1
        elif str(row.get(content.URL, "")).strip():
            out.append({h: (row.get(h, "") if h in (content.KEY, content.URL, content.STATUS) else "")
                        for h in content.HEADERS})
    return out, marked


def clear_generated(drive, ids: dict) -> int:
    n = 0
    for folder in (ids["briefs"], ids["drafts"]):
        for doc_id in gdocs.docs_in(drive, folder):
            drive.files().update(fileId=doc_id, body={"trashed": True}).execute(num_retries=provision.RETRIES)
            n += 1
    return n


def prepare(drive, sheets, site, log=print) -> dict:
    """Makes (or finds) the rehearsal files and copies the rows of the site's dashboard into them. Safe to repeat:
    the copy is put back to the rows of the real dashboard and the earlier rehearsal documents are trashed."""
    source = source_dashboard_id(drive, sheets, site)
    ids = _folders(drive)
    tab_list = [t for t in site.content.tabs]
    ids["dashboard"] = provision.find(drive, DASH_NAME, provision.SHEET, ids["folder"])
    if not ids["dashboard"]:
        ids["dashboard"] = provision.create(drive, DASH_NAME, provision.SHEET, ids["folder"])
        cs.initial_setup(sheets, ids["dashboard"], "Rehearsal", [sites.ContentTab(t.name, ".*") for t in tab_list])
    last = cs.letters(len(content.HEADERS) - 1)
    total = marked = 0
    info = cs.sheet_info(sheets, source)
    for t in tab_list:
        if t.name not in info:
            continue
        header, rows = cs.table_of(cs.read_all(sheets, source, t.name))
        copied, n = copy_rows(header, rows)
        history._write(sheets.spreadsheets().values().clear(spreadsheetId=ids["dashboard"], range=f"'{t.name}'!A2:{last}", body={}))
        if copied:
            tabs.patch_cells(sheets, ids["dashboard"], [(f"'{t.name}'!A2:{last}{len(copied) + 1}",
                                                          [[r.get(h, "") for h in content.HEADERS] for r in copied])])
        total += len(copied)
        marked += n
    ids["inputs"] = provision.find(drive, INPUTS_NAME, provision.SHEET, ids["folder"])
    if not ids["inputs"]:
        ids["inputs"] = provision.create(drive, INPUTS_NAME, provision.SHEET, ids["folder"])
    titles, _ = history.grid_info(sheets, ids["inputs"])
    if INPUTS_TAB not in titles:
        first = next(iter(titles))
        history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=ids["inputs"], body={"requests": [{
            "updateSheetProperties": {"properties": {"sheetId": titles[first], "title": INPUTS_TAB},
                                      "fields": "title"}}]}))
    settings = ccr.config_from_site(site)
    old = [r for r in tabs.read_rows(sheets, ids["inputs"], INPUTS_TAB, "B") if r and str(r[0]).strip() == "sop"]
    rows = [["key", "value"], ["label", settings.label], ["language", settings.language], ["domain", settings.domain],
            ["extra_rules", settings.extra_rules], ["tabs", ", ".join(settings.tabs)]] + old
    history._write(sheets.spreadsheets().values().clear(spreadsheetId=ids["inputs"], range=f"'{INPUTS_TAB}'!A1:B5000", body={}))
    tabs.patch_cells(sheets, ids["inputs"], [(f"'{INPUTS_TAB}'!A1:B{len(rows)}", rows)])
    trashed = clear_generated(drive, ids)
    log(f"  rehearsal copy ready: {total} rows copied, {marked} marked Brief, {trashed} earlier documents trashed, "
        f"{len(old)} SOP lines waiting in Inputs")
    return ids


def read_inputs(drive, sheets) -> tuple:
    """(ids, settings dict, SOP text) of the rehearsal copy that prepare made."""
    ids = _folders(drive)
    ids["dashboard"] = provision.find(drive, DASH_NAME, provision.SHEET, ids["folder"])
    ids["inputs"] = provision.find(drive, INPUTS_NAME, provision.SHEET, ids["folder"])
    if not ids["dashboard"] or not ids["inputs"]:
        raise RehearsalError("the rehearsal copy is not made yet (run prepare first)")
    settings, sop = {}, []
    for r in tabs.read_rows(sheets, ids["inputs"], INPUTS_TAB, "B"):
        key = str(r[0]).strip() if r else ""
        value = str(r[1]) if len(r) > 1 else ""
        if key == "sop":
            sop.append(value)
        elif key and key != "key":
            settings[key] = value
    return ids, settings, "\n".join(sop).strip()


def _snapshots(sheets, dashboard_id: str, tab_names: list) -> dict:
    return {t: cc.Snapshot.of(cs.read_all(sheets, dashboard_id, t)) for t in tab_names}


def _mark_draft(sheets, dashboard_id: str, tab_names: list, keys: set) -> int:
    """The step that stands for the owner: rows that were just briefed are set to Draft."""
    n = 0
    for item in ccr.load_items(sheets, dashboard_id, tab_names):
        if item.key in keys:
            ccr.write_row(sheets, dashboard_id, item, {content.TODO: "Draft"})
            n += 1
    return n


def check_documents(drive, language: str, domain: str, items: list, results: ccr.CreateResult, comparison_of) -> cc.Report:
    """The checker on every brief and draft of a result. `items`: the rows (to know update or new page)."""
    report = cc.Report()
    by_kw = {it.keyword: it for it in items}
    for kw, link in results.briefs:
        html = gdocs.export_html(drive, gdocs.doc_ids_in(link)[0])
        report.add(cc.check_brief(html, update=bool(by_kw[kw].url)))
        if language == "fa":
            report.add(cc.check_persian(cc.Doc(html)))
    for kw, link in results.drafts:
        html = gdocs.export_html(drive, gdocs.doc_ids_in(link)[0])
        report.add(cc.check_draft(html, language=language, domain=domain, has_comparison=comparison_of(by_kw[kw])))
    return report


def run(drive, sheets, ai, today: date, mode: str = "both", http_get=site_check.default_get, log=print) -> tuple:
    """(CreateResult of everything, cc.Report). mode: briefs, drafts or both."""
    ids, settings, sop_text = read_inputs(drive, sheets)
    if not sop_text:
        raise RehearsalError("the SOP text is not in Inputs yet (rows with the key sop)")
    language = settings.get("language") or "fa"
    sop_html = cd.wrap(language, cd.md_to_html(sop_text))
    sop_id = gdocs.ensure_doc(drive, SOP_NAME, sop_html, ids["folder"], replace=True)
    tab_names = [t.strip() for t in settings.get("tabs", "").split(",") if t.strip()]
    cfg = ccr.CreateConfig(label=settings.get("label", ""), language=language, domain=settings.get("domain", ""),
                           tabs=tab_names, sop_doc_id=sop_id, briefs_folder_id=ids["briefs"],
                           drafts_folder_id=ids["drafts"], extra_rules=settings.get("extra_rules", ""),
                           max_briefs=20, max_drafts=20, max_requests=40)
    total = ccr.CreateResult()
    report = cc.Report()
    snap = _snapshots(sheets, ids["dashboard"], tab_names)
    for phase in [p for p in ("briefs", "drafts") if mode in (p, "both")]:
        result = ccr.run(drive, sheets, ids["dashboard"], cfg, ai, phase, today, http_get=http_get, log=log)
        items = ccr.load_items(sheets, ids["dashboard"], tab_names)
        after = _snapshots(sheets, ids["dashboard"], tab_names)
        for t in tab_names:
            report.add(cc.diff_dashboard(snap[t], after[t], result.processed_keys))

        def comparison_of(item, items=items):
            for link in gdocs.doc_ids_in(item.get("Attachments")):
                if link in gdocs.docs_in(drive, ids["briefs"]):
                    return ccr.tables_planned(ccr.read_text(drive, link))
            return False
        report.add(check_documents(drive, language, cfg.domain, items, result, comparison_of).failures)
        total.briefs += result.briefs
        total.drafts += result.drafts
        total.no_brief += result.no_brief
        total.problems += result.problems
        total.waiting += result.waiting
        total.stopped = total.stopped or result.stopped
        total.requests += result.requests
        total.font_rewrites += result.font_rewrites
        total.processed_keys |= result.processed_keys
        if phase == "briefs" and mode == "both":
            log(f"  rehearsal: {_mark_draft(sheets, ids['dashboard'], tab_names, result.processed_keys)} briefed rows set to Draft")
        snap = _snapshots(sheets, ids["dashboard"], tab_names)
    return total, report
