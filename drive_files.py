"""
PulseO: putting a file into a Drive folder
==========================================
One small helper for files the tool makes itself (a picture, a text): created in the folder given, never replacing anything,
never shared. The tool's Google permission only covers files it made, so this is also how it keeps its own copies.
"""

import io

from googleapiclient.http import MediaIoBaseUpload

RETRIES = 5


def upload_file(drive, folder_id: str, name: str, data: bytes, mime: str = "image/png") -> tuple:
    """(file id, link to open it) of a new file `name` in the folder. A file of the same name is not touched: Drive allows two."""
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    made = drive.files().create(body={"name": name, "parents": [folder_id]}, media_body=media,
                                fields="id").execute(num_retries=RETRIES)
    return made["id"], f"https://drive.google.com/file/d/{made['id']}/view"


def files_named(drive, folder_id: str, name: str) -> list:
    """The ids of the files of this exact name in the folder."""
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    resp = drive.files().list(q=f"name = '{safe}' and '{folder_id}' in parents and trashed = false", spaces="drive",
                              fields="files(id)", pageSize=10).execute(num_retries=RETRIES)
    return [f["id"] for f in resp.get("files", [])]
