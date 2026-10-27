"""
PulseO: sheets for sites that run with the owner's own access
=============================================================
A site with `"auth": "me"` needs no sheet made by hand. On its first run PulseO creates, in the
owner's Drive:

    PulseO /                      (folder, once)
        PulseO · Index            (spreadsheet, once: which sheets belong to which site)
        <label> /                 (folder per site, named after deck.site_label, else the site name)
            <label> · PulseO Data / History / Reports

and remembers the three IDs in the Index, tab `Sites`, so no ID has to appear in a log or in the
configuration. Later runs find the Index again with a Drive search for its exact name (the
`drive.file` permission only lets PulseO see files it created itself). IDs given in SITES_JSON win
over the Index. Once per new site the owner gets an email with the links.

Nothing here prints an ID or a link.
"""

from dataclasses import dataclass
from datetime import datetime

import history
import tabs

ROOT_FOLDER = "PulseO"
INDEX_NAME = "PulseO · Index"
INDEX_TAB = "Sites"
INDEX_HEADER = ["name", "data_id", "history_id", "reports_id", "created_at", "emailed_at", "content_id"]
INDEX_LAST = "G"                # the last column of the Index tab
FOLDER, SHEET = "application/vnd.google-apps.folder", "application/vnd.google-apps.spreadsheet"
KINDS = (("data", "PulseO Data"), ("history", "PulseO History"), ("reports", "PulseO Reports"))
RETRIES = 5


class ProvisionError(Exception):
    """A sheet could not be found or created. The message is safe to print."""


@dataclass
class Provisioned:
    created: int = 0            # spreadsheets created in this run
    emailed: bool = False
    text: str = ""


def _quote(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def find(drive, name: str, mime: str, parent: str = None):
    """ID of the first non-trashed file with exactly this name (and type, and folder), or None."""
    q = f"name = '{_quote(name)}' and mimeType = '{mime}' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    resp = drive.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10,
                              orderBy="createdTime").execute(num_retries=RETRIES)
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def create(drive, name: str, mime: str, parent: str = None) -> str:
    body = {"name": name, "mimeType": mime}
    if parent:
        body["parents"] = [parent]
    return drive.files().create(body=body, fields="id").execute(num_retries=RETRIES)["id"]


def ensure_folder(drive, name: str, parent: str = None) -> str:
    return find(drive, name, FOLDER, parent) or create(drive, name, FOLDER, parent)


def link(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def _open_index(drive, sheets) -> str:
    """The Index spreadsheet's ID; created (with its folder) when it does not exist yet."""
    index_id = find(drive, INDEX_NAME, SHEET)
    if index_id:
        return index_id
    index_id = create(drive, INDEX_NAME, SHEET, ensure_folder(drive, ROOT_FOLDER))
    titles, _ = history.grid_info(sheets, index_id)
    first = next(iter(titles))
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=index_id, body={"requests": [{
        "updateSheetProperties": {"properties": {"sheetId": titles[first], "title": INDEX_TAB,
                                                 "gridProperties": {"frozenRowCount": 1}},
                                  "fields": "title,gridProperties.frozenRowCount"}}]}))
    tabs.patch_cells(sheets, index_id, [(f"'{INDEX_TAB}'!A1:{INDEX_LAST}1", [INDEX_HEADER])])
    return index_id


def _row_of(rows: list, name: str):
    """(sheet row number, cells) of a site in the Index, or (None, [])."""
    for i, row in enumerate(rows, start=2):
        if row and row[0] == name:
            return i, list(row) + [""] * (len(INDEX_HEADER) - len(row))
    return None, [name] + [""] * (len(INDEX_HEADER) - 1)


def _save_row(sheets, index_id: str, row_no, row: list, rows_count: int) -> int:
    if row_no is None:
        row_no = rows_count + 2
    tabs.patch_cells(sheets, index_id, [(f"'{INDEX_TAB}'!A{row_no}:{INDEX_LAST}{row_no}", [row])])
    return row_no


def provision_site(drive, sheets, site, notify=None, log=print) -> Provisioned:
    """
    Makes sure the site has its three sheets and sets site.sheet_id / history_sheet_id /
    reports_sheet_id. `notify(subject, body)` sends the owner the "sheets ready" email.
    """
    result = Provisioned()
    configured = {"data": site.sheet_id, "history": site.history_sheet_id,
                  "reports": site.reports_sheet_id}
    if all(configured.values()):
        result.text = "sheets from the configuration"
        return result

    index_id = _open_index(drive, sheets)
    rows = tabs.read_rows(sheets, index_id, INDEX_TAB, INDEX_LAST)
    row_no, row = _row_of(rows, site.name)
    ids = {kind: configured[kind] or row[1 + i] for i, (kind, _) in enumerate(KINDS)}
    label = site.deck.site_label or site.name

    count = len(rows)
    folder = None
    for i, (kind, title) in enumerate(KINDS):
        if ids[kind]:
            continue
        if folder is None:
            folder = ensure_folder(drive, label, ensure_folder(drive, ROOT_FOLDER))
        ids[kind] = create(drive, f"{label} · {title}", SHEET, folder)
        result.created += 1
        row[1 + i] = ids[kind]
        row[4] = row[4] or datetime.now().strftime("%Y-%m-%d %H:%M")
        was_new = row_no is None
        row_no = _save_row(sheets, index_id, row_no, row, count)           # remembered at once
        count += was_new
    row[1:4] = [ids["data"], ids["history"], ids["reports"]]

    site.sheet_id, site.history_sheet_id, site.reports_sheet_id = (
        ids["data"], ids["history"], ids["reports"])
    result.text = f"{result.created} sheets created" if result.created else "sheets from the Index"

    if notify and not row[5] and row_no is not None:
        body = "\n".join([
            f"PulseO created the Google Sheets for {label}.", "",
            f"Data:    {link(ids['data'])}", f"History: {link(ids['history'])}",
            f"Reports: {link(ids['reports'])}", "",
            f"Index of all PulseO sheets: {link(index_id)}", "",
            "They are in your Drive, in the folder PulseO.", "", "PulseO", ""])
        try:
            notify(f"PulseO · sheets ready · {label}", body)
        except Exception as e:  # noqa: BLE001  the sheets exist; the Index still lists them
            log(f"  provision: could not send the 'sheets ready' email ({type(e).__name__})")
        else:
            row[5] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _save_row(sheets, index_id, row_no, row, count)
            result.emailed = True
    return result


# --------------------------------------------------------------------------- #
# The content dashboard file (v2.5): one more sheet of a site, remembered in the Index
# --------------------------------------------------------------------------- #

def _site_folder(drive, site, label: str) -> str:
    """The folder of the site's other sheets (wherever the owner has moved it), else the one
    PulseO makes itself."""
    if site.sheet_id:
        try:
            parents = drive.files().get(fileId=site.sheet_id, fields="parents").execute(
                num_retries=RETRIES).get("parents", [])
            if parents:
                return parents[0]
        except Exception:  # noqa: BLE001  fall back to the folder PulseO keeps
            pass
    return ensure_folder(drive, label, ensure_folder(drive, ROOT_FOLDER))


def content_file(drive, sheets, site, label: str, notify=None, log=print):
    """
    The content dashboard of a site: (spreadsheet id, created in this call). An id in the
    configuration wins; else the one in the Index; else a new, empty spreadsheet named
    "[label] Content Dashboard" is made next to the site's other sheets and remembered in the
    Index. The owner gets one email with the link when it is made. Nothing here prints an ID.
    """
    if site.content.sheet_id:
        return site.content.sheet_id, False
    index_id = _open_index(drive, sheets)
    rows = tabs.read_rows(sheets, index_id, INDEX_TAB, INDEX_LAST)
    row_no, row = _row_of(rows, site.name)
    if row[6]:
        return row[6], False
    file_id = create(drive, f"[{label}] Content Dashboard", SHEET, _site_folder(drive, site, label))
    row[6] = file_id
    tabs.patch_cells(sheets, index_id, [(f"'{INDEX_TAB}'!G1", [[INDEX_HEADER[6]]])])
    _save_row(sheets, index_id, row_no, row, len(rows))                      # remembered at once
    if notify:
        try:
            notify(f"Content dashboard ready · {label}",
                   f"The content dashboard for {label} is ready:\n\n{link(file_id)}\n\n"
                   f"It is in your Drive, next to the other sheets of this site. Move it wherever "
                   f"you like.\n")
        except Exception as e:  # noqa: BLE001  the file exists; the Index lists it
            log(f"  content: could not send the 'dashboard ready' email ({type(e).__name__})")
    return file_id, True
