"""
PulseO: small Google Sheets helpers for report tabs
======================================================
Thin wrappers around the throttled read / write calls of history.py, so the
report modules never talk to the Sheets API directly and always stay inside
the per-minute quotas.
"""

import history


def ensure_tab(sheets, spreadsheet_id: str, titles: dict, name: str, header: list):
    """Creates the tab (bold, frozen header row) when it does not exist yet.
    `titles` is the {title: sheetId} dict of the spreadsheet and is kept up to date."""
    if name not in titles:
        history.create_tab(sheets, spreadsheet_id, name, header)
        titles[name] = None


def read_rows(sheets, spreadsheet_id: str, tab: str, last_col: str, first_row: int = 2) -> list:
    """All rows of a tab from `first_row` on, as lists of strings (trailing empty cells cut)."""
    resp = history._read(sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'!A{first_row}:{last_col}"))
    return resp.get("values", [])


def read_tab(sheets, spreadsheet_id: str, tab: str) -> list:
    """Everything in a tab (used for the small AIO import tabs)."""
    resp = history._read(sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'"))
    return resp.get("values", [])


def append(sheets, spreadsheet_id: str, tab: str, rows: list, log=print):
    if rows:
        history.append_rows(sheets, spreadsheet_id, tab, rows, log)


def rewrite(sheets, spreadsheet_id: str, tab: str, header: list, rows: list):
    """Replaces the whole content of a (small) tab: header first, then the rows."""
    history._write(sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'", body={}))
    history._write(sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1",
        valueInputOption="RAW", body={"values": [header] + rows}))


def patch_cells(sheets, spreadsheet_id: str, updates: list):
    """updates: [(A1 range, [[values]])]. One call, whatever the number of cells."""
    if not updates:
        return
    history._write(sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW",
              "data": [{"range": rng, "values": values} for rng, values in updates]}))


def delete_tabs(sheets, spreadsheet_id: str, sheet_ids: list):
    if sheet_ids:
        history._write(sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"deleteSheet": {"sheetId": sid}} for sid in sheet_ids]}))


def same(a, b) -> bool:
    """Do two cell values mean the same? Sheets hands numbers back as text."""
    def norm(v):
        if v is None or v == "":
            return ""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return str(int(f)) if f == int(f) else repr(round(f, 6))
    return norm(a) == norm(b)


def number(cell):
    """Sheets hands numbers back as text; write them back as numbers, not as text."""
    try:
        f = float(cell)
    except (TypeError, ValueError):
        return cell
    return int(f) if f == int(f) else f


def add_columns(sheets, spreadsheet_id: str, sheet_id: int, count: int):
    """Widens a tab's grid by `count` columns."""
    history._write(sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"appendDimension": {"sheetId": sheet_id, "dimension": "COLUMNS",
                                                 "length": count}}]}))
