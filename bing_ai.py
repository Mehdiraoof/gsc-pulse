"""
Bing AI Performance export: a Drive folder per site
===================================================
Bing has no API for its AI Performance report (as far as is known), so once a month the owner exports it from Bing
Webmaster Tools and drops the files into the folder of the site:

    PulseO / <site label> / Bing AI export        (made next to the site's other sheets)

The folder is made by the tool in the owner's Drive. The tool's own Google permission only sees files it made itself, so
the files the owner drops are read with the service account, and the tool shares the (empty) folder with it, read only,
the first time. Nothing is ever written to the folder or changed in the files.

A file is a Google Sheet, a .csv (comma, semicolon or tab; UTF-8 or UTF-16) or an .xlsx. Its month comes from its name
("2026-10 ...", "Oct 2026 ..."); a name without a month means the month before the file was last changed (said in the
Bing Status tab). Whether it lists cited pages or grounding queries is told from its name or, failing that, from its
content. The columns are found by their names (bing_data.parse_citations). A month with no file is skipped quietly.
Nothing here prints a file name or an address: counts only.
"""

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime

import aio_import
import provision

FOLDER = "Bing AI export"
READ_SCOPES = ["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/spreadsheets.readonly"]
MAX_BYTES = 20_000_000
MAX_FILES = 60
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


@dataclass
class Export:
    """One file of the folder: its name, the day it was last changed and its rows."""
    name: str
    modified: date
    values: list


# --------------------------------------------------------------------------- #
# Month and kind
# --------------------------------------------------------------------------- #

def previous_month(day: date) -> str:
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return f"{year:04d}-{month:02d}"


def month_of(name: str, modified: date) -> tuple:
    """("2026-10", guessed): the month a file is about, from its name; the month before it was last changed when the name
    does not say (guessed is then True)."""
    text = name.lower()
    m = re.search(r"(?<!\d)(20\d{2})[-_. /](0?[1-9]|1[0-2])(?!\d)", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}", False
    names = "|".join(MONTHS)
    m = re.search(rf"\b({names})[a-z]*[-_. ,]*(20\d{{2}})", text) or re.search(rf"(20\d{{2}})[-_. ,]*({names})", text)
    if m:
        year, mon = (m.group(2), m.group(1)) if m.group(1) in MONTHS else (m.group(1), m.group(2))
        return f"{year}-{MONTHS[mon]:02d}", False
    return previous_month(modified), True


def kind_of(name: str, values: list) -> str:
    """"Pages" (a list of cited pages) or "Queries" (a list of grounding queries): from the file's name, else from whether its
    first data rows hold page addresses."""
    text = name.lower()
    if re.search(r"quer|ground|prompt|phrase", text):
        return "Queries"
    if re.search(r"page|url|cited", text):
        return "Pages"
    for row in values[1:8] or values[:8]:
        if any(aio_import._is_url(aio_import._clean(c)) for c in row):
            return "Pages"
    return "Queries"


# --------------------------------------------------------------------------- #
# File formats
# --------------------------------------------------------------------------- #

def csv_rows(raw: bytes) -> list:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
    first = text.lstrip().split("\n", 1)[0]
    delimiter = max(",;\t", key=lambda d: (first.count(d), d == ","))        # the one the header line has most of; a comma on a tie
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _column(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(raw: bytes) -> list:
    """The rows of the first sheet of an .xlsx (values only: text, numbers, shared and inline strings)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        strings = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
                strings.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t") or t.tag == "t"))
        sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            return []
        rows = []
        for row in ET.fromstring(z.read(sheets[0])).iter():
            if not row.tag.endswith("}row") and row.tag != "row":
                continue
            cells = {}
            for c in row:
                ref = c.get("r") or ""
                if not ref:
                    continue
                kind, v = c.get("t"), None
                for child in c:
                    if child.tag.endswith("}v") or child.tag == "v":
                        v = child.text or ""
                    elif child.tag.endswith("}is") or child.tag == "is":
                        v = "".join(t.text or "" for t in child.iter() if t.tag.endswith("}t") or t.tag == "t")
                if v is None:
                    continue
                if kind == "s":
                    v = strings[int(v)] if v.isdigit() and int(v) < len(strings) else ""
                cells[_column(ref)] = v
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        return rows


# --------------------------------------------------------------------------- #
# The folder
# --------------------------------------------------------------------------- #

def folder_for(drive, site, label: str, create: bool = True):
    """(folder id, created in this call); ('', False) when there is none and `create` is off. The folder sits next to the
    site's other sheets (PulseO / <label> / Bing AI export, or the site's own project folder)."""
    parent = provision._site_folder(drive, site, label)
    found = provision.find(drive, FOLDER, provision.FOLDER, parent)
    if found or not create:
        return found or "", False
    return provision.create(drive, FOLDER, provision.FOLDER, parent), True


def share_with(drive, folder_id: str, email: str) -> bool:
    """Gives `email` (the service account) read access to the folder, once. False when that could not be done."""
    if not (folder_id and email):
        return False
    try:
        listed = drive.permissions().list(fileId=folder_id, fields="permissions(emailAddress)").execute(num_retries=3)
        if any((p.get("emailAddress") or "").lower() == email.lower() for p in listed.get("permissions", [])):
            return True
        drive.permissions().create(fileId=folder_id, body={"type": "user", "role": "reader", "emailAddress": email},
                                   sendNotificationEmail=False, fields="id").execute(num_retries=3)
        return True
    except Exception:  # noqa: BLE001  the folder exists; the owner can share it by hand
        return False


def read_folder(reader_drive, reader_sheets, folder_id: str, log=print) -> list:
    """The files in the folder as `Export`s, read as the service account. A file that cannot be read is left out and counted
    in the log (never named)."""
    if not folder_id:
        return []
    listed = reader_drive.files().list(
        q=f"'{folder_id}' in parents and trashed = false", fields="files(id,name,mimeType,modifiedTime,size)",
        pageSize=MAX_FILES, orderBy="modifiedTime").execute(num_retries=3).get("files", [])
    out, unread = [], 0
    for f in listed:
        try:
            when = datetime.fromisoformat(str(f.get("modifiedTime", ""))[:10]).date()
        except ValueError:
            when = date.today()
        name, mime = f.get("name", ""), f.get("mimeType", "")
        try:
            if mime == GOOGLE_SHEET:
                meta = reader_sheets.spreadsheets().get(spreadsheetId=f["id"], fields="sheets.properties.title").execute(num_retries=3)
                tab = meta["sheets"][0]["properties"]["title"]
                values = reader_sheets.spreadsheets().values().get(spreadsheetId=f["id"], range=f"'{tab}'").execute(
                    num_retries=3).get("values", [])
            elif int(f.get("size") or 0) > MAX_BYTES:
                raise ValueError("too big")
            else:
                raw = reader_drive.files().get_media(fileId=f["id"]).execute(num_retries=3)
                if name.lower().endswith(".xlsx") or raw[:2] == b"PK":
                    values = xlsx_rows(raw)
                elif name.lower().endswith((".csv", ".tsv", ".txt")) or mime.startswith("text/"):
                    values = csv_rows(raw)
                else:
                    raise ValueError("not a table")
        except Exception:  # noqa: BLE001
            unread += 1
            continue
        out.append(Export(name, when, values))
    if unread:
        log(f"  bing AI: {unread} of {len(listed)} files in the folder could not be read as a table")
    return out
