"""
PulseO: the publishing run of one site
======================================
Reads the content dashboard of a site for the rows the owner has approved:

  To Do "To WordPress"   the draft document of the row (its newest Google Doc link in Attachments) becomes a WordPress DRAFT
                          (wp_publish.to_wordpress); the row shows Status "In WordPress", the link to the draft in Attachments
                          and a note with the date
  To Do "Apply update"   the update draft is copied into the original post (wp_publish.apply_update) or refused with the reason
                          in the row's note

Only those columns of a row are written: Status, To Do, Attachments (added to), Published/Updated Date (after an update) and
Notes/Checklist (added to). Every write to WordPress is also logged in the WordPress Log tab of the dashboard (date, site,
post ID, action). A site with two WordPress installs (a language each) sends a row to the install of the language the
document is written in. A DRY RUN (the default) reads and decides but writes nothing, anywhere. Nothing is ever published.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import content
import content_create
import content_sheet as cs
import gdocs
import history
import tabs
import wp_publish as pub

LOG_TAB = "WordPress Log"
LOG_HEADER = ["Date", "Site", "Post ID", "Action"]
ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")


@dataclass
class Report:
    seen: int = 0
    drafts: int = 0
    updates: int = 0
    refused: int = 0
    failed: int = 0
    would: int = 0                  # in a dry run: the rows that would have been sent
    lines: list = field(default_factory=list)

    def text(self) -> str:
        return (f"{self.drafts} drafts made, {self.updates} updates applied, {self.refused} refused, {self.failed} failed"
                + (f", {self.would} rows would be sent (dry run)" if self.would else ""))


def doc_language(source: str, default: str = "en") -> str:
    """The language a document is written in: Persian when most of its letters are in Arabic script."""
    text = re.sub(r"\[[^\]]*\]", " ", re.sub(r"<[^>]+>", " ", source or ""))          # not the slots in brackets: their words are Latin
    arabic, latin = len(ARABIC.findall(text)), len(LATIN.findall(text))
    if arabic + latin < 40:
        return default
    return "fa" if arabic > latin else "en"


def wanted_rows(rows: list) -> list:
    """[(index in the tab, row, kind)] of the rows whose To Do asks for the publishing flows."""
    out = []
    for i, row in enumerate(rows):
        todo = str(row.get(content.TODO, "")).strip()
        if todo == pub.TO_WORDPRESS:
            out.append((i, row, "draft"))
        elif todo == pub.APPLY_UPDATE:
            out.append((i, row, "apply"))
    return out


def _write_cells(sheets, file_id: str, tab: str, header: list, sheet_row: int, values: dict):
    updates = []
    for name, value in values.items():
        c = cs.col_of(header, name)
        if c is not None:
            updates.append((f"'{tab}'!{cs.letters(c)}{sheet_row}", [[value]]))
    tabs.patch_cells(sheets, file_id, updates)


def _log(sheets, file_id: str, rows: list):
    if not rows:
        return
    titles, _ = history.grid_info(sheets, file_id)
    tabs.ensure_tab(sheets, file_id, titles, LOG_TAB, LOG_HEADER)
    tabs.append(sheets, file_id, LOG_TAB, rows)


def publish_site(run, dash_id: str, targets, read_doc, today: date, write: bool = False, log=print) -> Report:
    """The approved rows of one site's dashboard. `targets(language) -> pub.Target or None` gives the install for a language
    (None: no login saved for it); `read_doc(doc_id) -> HTML`. Nothing is written unless `write` is true."""
    site, sheets = run.site, run.sheets
    report = Report()
    info = cs.sheet_info(sheets, dash_id)
    for tab in (t.name for t in site.content.tabs):
        if tab not in info:
            continue
        header = cs.read_header(sheets, dash_id, tab)
        _, rows = cs.table_of(cs.read_all(sheets, dash_id, tab))
        for index, row, kind in wanted_rows(rows):
            report.seen += 1
            sheet_row = index + 2
            notes = str(row.get("Notes/Checklist", ""))
            docs = gdocs.doc_ids_in(row.get("Attachments"))
            if kind == "draft" and not docs:
                _note(sheets, dash_id, tab, header, sheet_row, notes, f"Not sent to WordPress: no document in Attachments. {today.isoformat()}", write)
                report.failed += 1
                continue
            try:
                source = read_doc(docs[-1]) if kind == "draft" else ""
                language = doc_language(source, site.deck.language) if kind == "draft" else _language_of_update(notes, site)
                target = targets(language)
                if target is None:
                    report.failed += 1
                    report.lines.append(f"no WordPress login for language {language}")
                    continue
                if not write:                                    # a dry run decides nothing that is written
                    report.would += 1
                    continue
                slug = pub.slug_of(str(row.get(content.URL, "")).strip()) if kind == "draft" and row.get(content.URL) else ""
                outcome = (pub.to_wordpress(row, source, target, today, tab_hint=tab, live_slug=slug) if kind == "draft"
                           else pub.apply_update(row, target, today))
            except Exception as e:  # noqa: BLE001  one row never stops the others; the note says what, not where
                report.failed += 1
                log(f"  wordpress: a row could not be processed ({type(e).__name__})")
                continue
            values = {}
            if outcome.ok:
                report.drafts += kind == "draft"
                report.updates += kind == "apply"
                values = {k: v for k, v in outcome.changes.items() if not k.startswith("_")}
                if "_attach" in outcome.changes:
                    values["Attachments"] = content_create.appended(row.get("Attachments"), outcome.changes["_attach"])
                values["Notes/Checklist"] = content_create.appended(notes, outcome.note)
            else:
                report.refused += 1
                if outcome.note not in notes:
                    values["Notes/Checklist"] = content_create.appended(notes, outcome.note)
            _write_cells(sheets, dash_id, tab, header, sheet_row, values)
            _log(sheets, dash_id, [[today.isoformat(), site.name, pid, action] for action, pid in outcome.log])
    return report


def _note(sheets, dash_id, tab, header, sheet_row, notes, text, write):
    if write and text not in notes:
        _write_cells(sheets, dash_id, tab, header, sheet_row, {"Notes/Checklist": content_create.appended(notes, text)})


def _language_of_update(notes: str, site) -> str:
    """The language of an update is the one of the install its draft was made in, recorded in the row's note."""
    found = pub.TOKEN.findall(notes)
    return (found[-1][3] if found and found[-1][3] else site.deck.language)
