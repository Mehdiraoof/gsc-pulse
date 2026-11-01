"""
PulseO: Google Docs made from HTML, through Drive
=================================================
A brief or a draft is written as HTML and uploaded to Drive as a Google Doc (Drive converts it); the
`drive.file` permission is enough for that and for reading back what PulseO itself made. Nothing here
prints an id or a name.

Google sometimes turns the font of an imported document into Arial, in a different document each
time. A Persian doc must be in Vazirmatn, so after a Persian doc is written it is read back
(exported) and, when the font was lost, written again (`write_doc`).
"""

import io
import re

from googleapiclient.http import MediaIoBaseUpload

import content_check as cc
import provision

DOC = "application/vnd.google-apps.document"
FONT_TRIES = 5
_LINK_ID = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)")


def doc_link(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def doc_ids_in(text: str) -> list:
    """The ids of the Google Docs linked in a cell, in order."""
    return list(dict.fromkeys(_LINK_ID.findall(str(text or ""))))


def _media(html: str):
    return MediaIoBaseUpload(io.BytesIO(html.encode("utf-8")), mimetype="text/html", resumable=False)


def create_doc(drive, name: str, html: str, parent: str) -> str:
    """A Google Doc made from HTML (Drive converts it)."""
    return drive.files().create(body={"name": name, "mimeType": DOC, "parents": [parent]},
                                media_body=_media(html), fields="id").execute(num_retries=provision.RETRIES)["id"]


def replace_doc(drive, doc_id: str, html: str):
    """Writes the HTML into a doc again (Drive converts it again)."""
    drive.files().update(fileId=doc_id, media_body=_media(html), fields="id").execute(num_retries=provision.RETRIES)


def find_doc(drive, name: str, parent: str):
    return provision.find(drive, name, DOC, parent)


def ensure_doc(drive, name: str, html: str, parent: str, replace: bool = False) -> str:
    """The doc of that name in the folder (made if missing). With `replace` an existing one gets the
    given content again."""
    found = find_doc(drive, name, parent)
    if not found:
        return create_doc(drive, name, html, parent)
    if replace:
        replace_doc(drive, found, html)
    return found


def export_html(drive, doc_id: str) -> str:
    data = drive.files().export(fileId=doc_id, mimeType="text/html").execute(num_retries=provision.RETRIES)
    return data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)


def docs_in(drive, folder_id: str) -> dict:
    """{doc id: name} of the documents in a folder."""
    resp = drive.files().list(q=f"'{folder_id}' in parents and trashed = false", spaces="drive",
                              fields="files(id,name,mimeType)", pageSize=200).execute(num_retries=provision.RETRIES)
    return {f["id"]: f["name"] for f in resp.get("files", []) if f.get("mimeType", DOC) == DOC}


def font_kept(html: str) -> bool:
    return "vazirmatn" in cc.Doc(html).style_text().lower()


def write_doc(drive, name: str, html: str, parent: str, language: str, tries: int = FONT_TRIES) -> tuple:
    """(doc id, times it was written again). The doc is made, or, when one of that name is already in
    the folder (an earlier run stopped half way), taken over. A Persian doc is read back and written
    again while Google has dropped its font."""
    doc_id = ensure_doc(drive, name, html, parent, replace=True)
    rewrites = 0
    if language == "fa":
        for _ in range(tries):
            if font_kept(export_html(drive, doc_id)):
                break
            replace_doc(drive, doc_id, html)
            rewrites += 1
    return doc_id, rewrites
