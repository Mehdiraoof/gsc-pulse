"""
PulseO: the content dashboard, the file (v2.5)
===============================================
Creates and formats the Google Sheet of a site's content dashboard, reads its tabs by header
name and writes the cells PulseO owns. The rules are in content.py.

The file is the owner's own work: no tool name appears in any title, header, note or guide text.

Layout: one tab per configured page group, then `Guide`, `Keyword Seeds`, `Keyword Ideas`, and a
hidden `_state` tab (bookkeeping: gap rows already proposed, dismissed ones, autocomplete cache).
"""

import content
import history
import tabs

GUIDE, SEEDS, IDEAS, STATE = "Guide", "Keyword Seeds", "Keyword Ideas", "_state"
IMPORT_LOG = "Import Log"
REMOVED = "Removed Pages"
# what a removed row keeps: where it was, why it went, where its notes moved, and every column of the
# owner's, so that nothing is lost
REMOVED_LEAD = ["Published URL", "Primary Keyword", "Reason", "Date", "Moved To"]
SEEDS_HEADER = ["Seed", "Language", "Country", "Active", "Last run"]
IDEAS_HEADER = ["Keyword", "Seed", "Pattern", "Language", "Impr (90d)", "Best Page", "Position",
                "First Found", "Decision"]
STATE_HEADER = ["Kind", "Key", "Value", "Updated"]

FONT, FONT_SIZE = "Vazirmatn", 10
HEADER_HEIGHT, ROW_HEIGHT = 44, 28
GRID_ROWS = 2000
NAVY, GREY, AMBER = "#1B1E3C", "#D9DBE5", "#F2A541"
TINT_GREY, TINT_AMBER = "#F3F4F8", "#FDF8EF"
WHITE = "#FFFFFF"

# header colour by column index: navy A:F, grey G:T, navy U:AL (AJ grey), amber AM:AP
GREY_COLS = set(range(6, 20)) | {35}
AMBER_COLS = set(range(38, 42))
WIDTHS = {0: 230, 1: 360, 2: 90, 3: 110, 4: 90, 5: 70, 16: 300, 17: 300, 18: 200, 19: 260,
          24: 240, 25: 260, 26: 240, 27: 280, 36: 200, 37: 280, 38: 190, 39: 380, 40: 260, 41: 380}
DEFAULT_WIDTH = 105

DROPDOWNS = {content.STATUS: content.STATUSES, content.TODO: content.TODOS,
             content.PRIORITY: content.PRIORITIES, "Intent": content.INTENTS,
             "Keyword Type": content.KEYWORD_TYPES, "Content Type": content.CONTENT_TYPES}


def rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def letters(n: int) -> str:
    """0 -> A, 26 -> AA"""
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_of(header: list, name: str):
    """0-based column of a header name, or None. Columns are always found by name."""
    return header.index(name) if name in header else None


# --------------------------------------------------------------------------- #
# Formatting requests
# --------------------------------------------------------------------------- #

def _range(sid, r1=None, r2=None, c1=None, c2=None) -> dict:
    out = {"sheetId": sid}
    for key, v in (("startRowIndex", r1), ("endRowIndex", r2), ("startColumnIndex", c1), ("endColumnIndex", c2)):
        if v is not None:
            out[key] = v
    return out


def _fmt(sid, fields, fmt, **rng) -> dict:
    return {"repeatCell": {"range": _range(sid, **rng), "cell": {"userEnteredFormat": fmt},
                           "fields": "userEnteredFormat(" + fields + ")"}}


def _text(bold=False, color=None, strike=False) -> dict:
    t = {"fontFamily": FONT, "fontSize": FONT_SIZE, "bold": bold, "strikethrough": strike}
    if color:
        t["foregroundColor"] = rgb(color)
    return t


def base_requests(sid: int, rows: int = GRID_ROWS, cols: int = 42, frozen_cols: int = 0) -> list:
    """Font, clipping, row heights and the frozen header, for any tab."""
    grid = {"rowCount": rows, "columnCount": cols, "frozenRowCount": 1}
    fields = "gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount"
    if frozen_cols:
        grid["frozenColumnCount"] = frozen_cols
        fields += ",gridProperties.frozenColumnCount"
    return [
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": grid}, "fields": fields}},
        _fmt(sid, "textFormat,wrapStrategy,verticalAlignment",
             {"textFormat": _text(), "wrapStrategy": "CLIP", "verticalAlignment": "MIDDLE"}),
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
                                                 "startIndex": 0, "endIndex": 1},
                                       "properties": {"pixelSize": HEADER_HEIGHT}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
                                                 "startIndex": 1, "endIndex": rows},
                                       "properties": {"pixelSize": ROW_HEIGHT}, "fields": "pixelSize"}},
    ]


def header_cell(sid: int, c1: int, c2: int, background: str, text_color: str) -> dict:
    return _fmt(sid, "backgroundColor,textFormat,horizontalAlignment",
                {"backgroundColor": rgb(background), "textFormat": _text(True, text_color),
                 "horizontalAlignment": "LEFT"}, r1=0, r2=1, c1=c1, c2=c2)


def tab_requests(sid: int) -> list:
    """Everything a page-group tab needs: colours, formats, dropdowns, rules, filter, widths."""
    header = content.HEADERS
    n = len(header)
    reqs = base_requests(sid, GRID_ROWS, n, frozen_cols=2)
    for c in range(n):
        if c in AMBER_COLS:
            reqs.append(header_cell(sid, c, c + 1, AMBER, NAVY))
        elif c in GREY_COLS:
            reqs.append(header_cell(sid, c, c + 1, GREY, NAVY))
        else:
            reqs.append(header_cell(sid, c, c + 1, NAVY, WHITE))
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": WIDTHS.get(c, DEFAULT_WIDTH)}, "fields": "pixelSize"}})
    for c in sorted(GREY_COLS):
        reqs.append(_fmt(sid, "backgroundColor", {"backgroundColor": rgb(TINT_GREY)}, r1=1, c1=c, c2=c + 1))
    for c in sorted(AMBER_COLS):
        reqs.append(_fmt(sid, "backgroundColor", {"backgroundColor": rgb(TINT_AMBER)}, r1=1, c1=c, c2=c + 1))
    for name, pattern in (("Impressions (90d)", "#,##0"), ("Clicks (90d)", "#,##0"), ("CTR", "0.00%"),
                          ("Clicks MoM", "+0%;-0%;0%"), ("Clicks YoY", "+0%;-0%;0%")):
        c = header.index(name)
        reqs.append(_fmt(sid, "numberFormat", {"numberFormat": {"type": "NUMBER", "pattern": pattern}},
                         r1=1, c1=c, c2=c + 1))
    for name, values in DROPDOWNS.items():
        c = header.index(name)
        reqs.append({"setDataValidation": {
            "range": _range(sid, r1=1, c1=c, c2=c + 1),
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": v} for v in values]},
                     "strict": False, "showCustomUi": True}}})
    reqs.append({"setBasicFilter": {"filter": {"range": _range(sid, r1=0, c1=0, c2=n)}}})
    reqs += conditional_rules(sid)
    return reqs


def conditional_rules(sid: int) -> list:
    """Priority colours; A:D green for Write, amber for Brief, struck through when Gone."""
    header = content.HEADERS
    pc = header.index(content.PRIORITY)
    sc, tc = letters(header.index(content.STATUS)), letters(header.index(content.TODO))
    rules = []

    def add(rng, condition, fmt):
        rules.append({"addConditionalFormatRule": {"index": len(rules), "rule": {
            "ranges": [rng], "booleanRule": {"condition": condition, "format": fmt}}}})

    a_to_d = _range(sid, r1=1, c1=0, c2=4)
    add(a_to_d, {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=${sc}2="Gone"'}]},
        {"textFormat": {"strikethrough": True, "foregroundColor": rgb("#8A8FA3")}})
    add(a_to_d, {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=${tc}2="Brief"'}]},
        {"backgroundColor": rgb("#FDF1DE"), "textFormat": {"bold": True}})
    add(a_to_d, {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=${tc}2="Write"'}]},
        {"backgroundColor": rgb("#E3F4EC")})
    for name, back, fore in (("Urgent", "#FCE4E4", "#B03030"), ("High", "#FDF1DE", "#9A5B00"),
                             ("Moderate", "#ECEEFB", "#3A46A8"), ("Low", "#EFEFF2", "#6B7085")):
        add(_range(sid, r1=1, c1=pc, c2=pc + 1),
            {"type": "TEXT_EQ", "values": [{"userEnteredValue": name}]},
            {"backgroundColor": rgb(back), "textFormat": {"bold": True, "foregroundColor": rgb(fore)}})
    return rules


def small_tab_requests(sid: int, header: list, dropdowns: dict = None, filter_on: bool = False) -> list:
    n = len(header)
    reqs = base_requests(sid, 1000, max(n, 6))
    reqs.append(header_cell(sid, 0, n, NAVY, WHITE))
    for c in range(n):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": 150 if c else 280}, "fields": "pixelSize"}})
    for name, values in (dropdowns or {}).items():
        c = header.index(name)
        reqs.append({"setDataValidation": {
            "range": _range(sid, r1=1, c1=c, c2=c + 1),
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": v} for v in values]},
                     "strict": False, "showCustomUi": True}}})
    if filter_on:
        reqs.append({"setBasicFilter": {"filter": {"range": _range(sid, r1=0, c1=0, c2=n)}}})
    return reqs


def guide_requests(sid: int, lines: list) -> list:
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
            "rowCount": max(len(lines) + 10, 60), "columnCount": 3, "hideGridlines": True}},
            "fields": "gridProperties.rowCount,gridProperties.columnCount,gridProperties.hideGridlines"}},
        _fmt(sid, "textFormat,wrapStrategy,verticalAlignment",
             {"textFormat": _text(), "wrapStrategy": "WRAP", "verticalAlignment": "TOP"}),
    ]
    for c, w in enumerate((230, 20, 760)):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    for i, row in enumerate(lines):
        if len(row) == 1 and row[0]:                  # title, intro and section lines run across the page
            reqs.append({"mergeCells": {"range": _range(sid, r1=i, r2=i + 1, c1=0, c2=3),
                                        "mergeType": "MERGE_ALL"}})
        if row and row[0] and not any(row[1:]) and (i == 0 or row[0] in GUIDE_SECTIONS):
            size = 14 if i == 0 else FONT_SIZE
            reqs.append(_fmt(sid, "textFormat", {"textFormat": dict(_text(True, NAVY), fontSize=size)},
                             r1=i, r2=i + 1, c1=0, c2=3))
        elif row and row[0] == "Column" and len(row) > 2 and row[2] == "Meaning":
            reqs.append(_fmt(sid, "textFormat", {"textFormat": _text(True, NAVY)}, r1=i, r2=i + 1, c1=0, c2=3))
        elif row and row[0] and len(row) > 2 and row[2]:
            reqs.append(_fmt(sid, "textFormat", {"textFormat": _text(True)}, r1=i, r2=i + 1, c1=0, c2=1))
    return reqs


GUIDE_SECTIONS = ("Tabs", "How to work with it", "Colors")


def guide_lines(label: str, tab_list: list) -> list:
    """The Guide tab. Wording is the owner's; it names no tool."""
    lines = [
        [f"{label} Content Dashboard · How to use this sheet"],
        ["This file lists every published page by type, plus new topics to write. Each row is one "
         "page. Rows are sorted by Score, so the most valuable work is at the top."],
        [],
        ["Tabs"],
    ]
    for t in tab_list:
        lines.append([t.name, "", t.description or "Published pages of this type, plus new topics to write."])
    lines += [
        [SEEDS, "", "Searches you want ideas around. Add a seed, its language (fa or en), an optional "
                    "country and set Active to Yes."],
        [IDEAS, "", "Search suggestions found around your seeds. Decide each one: Add (it becomes a new "
                    "topic), Ignore or Later."],
        [],
        ["How to work with it"],
        ["1. Start at the top", "", "Filter Priority to Urgent and High. These are the pages with the most to gain."],
        ["2. Read the row", "", "The grey columns show how the page performs. The orange columns at the far "
                                "right explain what to do and why."],
        ["3. Decide", "", "Set To Do and Status. Choose 'Brief' in To Do when a page needs a content brief "
                          "for a writer."],
        ["4. Assign", "", "Fill PIC and Briefed Date. Briefs are linked in Attachments."],
        ["5. After publishing", "", "Fill Published/Updated Date. Impact Since Update then shows the result "
                                    "(4 weeks before vs after)."],
        [],
        ["Column", "", "Meaning"],
        ["Primary Keyword", "", "The main search query this page should rank for."],
        ["Published URL", "", "The live page. Empty = a new topic that has no page yet."],
        ["Status", "", "Inboxed → Briefed → Ordered → Published. Later = parked. Gone = redirected or removed."],
        ["To Do", "", "Brief = prepare a brief · Write = new page · Update = improve content · Rewrite = start "
                      "over around the right keyword · Optimization = title, meta and intro · Merge = combine "
                      "with a competing page · Redirect = 301 to another page · Keep = no action · Prune = "
                      "remove or noindex."],
        ["Priority / Score", "", "Score from 0 to 100 based on the clicks the page could gain, its visibility, "
                                 "how close it is to the top 3, plus seasonal timing and competing pages. 80+ "
                                 "Urgent, 60+ High, 40+ Moderate, below 40 Low."],
        ["Clicks, Impressions, CTR, Avg. Position", "", "Google Search results for the last 90 days."],
        ["Clicks MoM / YoY", "", "Change in clicks vs last month and vs the same month last year."],
        ["AIO Impressions", "", "How often the page appeared in Google's AI answers (AI Overviews, AI Mode)."],
        ["Primary KW Impr. / Pos.", "", "How the primary keyword itself performs on this page."],
        ["Ranking Queries", "", "How many different searches show this page."],
        ["Secondary Keywords", "", "Other searches the page already appears for. Cover them in the content."],
        ["Query Fan-outs", "", "The follow-up questions people (and Google's AI) ask around the topic. Answer "
                               "each in a short section."],
        ["Entities", "", "People, places, books and concepts the page should name clearly."],
        ["Competing Pages", "", "Other pages on the site that rank for the same primary keyword."],
        ["Intent … Notes/Checklist", "", "Planning columns for the brief and the writer: intent, keyword type, "
                                         "content type (Pillar, Cornerstone, Cluster), SERP features, meta "
                                         "title, description, outline, slug, word count, links, owner and dates."],
        ["Impact Since Update", "", "Clicks and position for the 4 weeks before vs after the Published/Updated Date."],
        ["Suggested Action / Why", "", "The recommended next step and the data behind it."],
        ["Alerts", "", "Things to watch: seasonal deadlines, competing pages, duplicate URLs, low visibility."],
        ["Recommendations", "", "Details for the decision: internal links to add (from page, to page, anchor "
                                "text), what to merge or redirect, quick content fixes."],
        [],
        ["Colors", "", "Green row = new topic to write. Light orange row = brief requested. Struck-through = "
                       "page gone."],
    ]
    return lines


# --------------------------------------------------------------------------- #
# Creating the file
# --------------------------------------------------------------------------- #

def initial_setup(sheets, file_id: str, label: str, tab_list: list):
    """Names the tabs, formats them and writes headers and the Guide into a new, empty spreadsheet."""
    titles, _ = history.grid_info(sheets, file_id)
    first = next(iter(titles))
    names = [t.name for t in tab_list]
    ids = {names[0]: titles[first] if titles[first] is not None else 0}
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": ids[names[0]], "title": names[0]},
                                       "fields": "title"}}]
    extra = names[1:] + [GUIDE, SEEDS, IDEAS, STATE]
    for name in extra:
        props = {"title": name}
        if name == STATE:
            props["hidden"] = True
        reqs.append({"addSheet": {"properties": props}})
    made = history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": reqs}))
    for name, reply in zip(extra, made["replies"][1:]):
        ids[name] = reply["addSheet"]["properties"]["sheetId"]
    reqs = []
    for t in tab_list:
        reqs += tab_requests(ids[t.name])
    reqs += small_tab_requests(ids[SEEDS], SEEDS_HEADER, {"Language": ["fa", "en"], "Active": ["Yes", "No"]})
    reqs += small_tab_requests(ids[IDEAS], IDEAS_HEADER, {"Decision": ["Add", "Ignore", "Later"]}, True)
    reqs += small_tab_requests(ids[STATE], STATE_HEADER)
    lines = guide_lines(label, tab_list)
    reqs += guide_requests(ids[GUIDE], lines)
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": reqs}))
    updates = [(f"'{t.name}'!A1:{letters(len(content.HEADERS) - 1)}1", [content.HEADERS]) for t in tab_list]
    updates += [(f"'{SEEDS}'!A1:{letters(len(SEEDS_HEADER) - 1)}1", [SEEDS_HEADER]),
                (f"'{IDEAS}'!A1:{letters(len(IDEAS_HEADER) - 1)}1", [IDEAS_HEADER]),
                (f"'{STATE}'!A1:D1", [STATE_HEADER]),
                (f"'{GUIDE}'!A1:C{len(lines)}", [r + [""] * (3 - len(r)) for r in lines])]
    tabs.patch_cells(sheets, file_id, updates)


def ensure_extra_tabs(sheets, file_id: str):
    """Seeds, ideas and state tabs for a file that was made without them (for example by hand)."""
    titles, _ = history.grid_info(sheets, file_id)
    wanted = [(n, h, d, f) for n, h, d, f in (
        (SEEDS, SEEDS_HEADER, {"Language": ["fa", "en"], "Active": ["Yes", "No"]}, False),
        (IDEAS, IDEAS_HEADER, {"Decision": ["Add", "Ignore", "Later"]}, True),
        (STATE, STATE_HEADER, None, False)) if n not in titles]
    if not wanted:
        return
    reqs = []
    for name, _, _, _ in wanted:
        props = {"title": name}
        if name == STATE:
            props["hidden"] = True
        reqs.append({"addSheet": {"properties": props}})
    made = history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": reqs}))
    reqs = []
    for (name, header, dropdowns, flt), reply in zip(wanted, made["replies"]):
        reqs += small_tab_requests(reply["addSheet"]["properties"]["sheetId"], header, dropdowns, flt)
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": reqs}))
    tabs.patch_cells(sheets, file_id, [(f"'{n}'!A1:{letters(len(h) - 1)}1", [h]) for n, h, _, _ in wanted])


def ensure_import_log(sheets, file_id: str):
    """The `Import Log` tab: where each row of an old sheet went. Made when the first import runs."""
    import content_import
    titles, _ = history.grid_info(sheets, file_id)
    if IMPORT_LOG in titles:
        return
    made = history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
        {"addSheet": {"properties": {"title": IMPORT_LOG}}}]}))
    sid = made["replies"][0]["addSheet"]["properties"]["sheetId"]
    header = content_import.LOG_HEADER
    history._write(sheets.spreadsheets().batchUpdate(
        spreadsheetId=file_id, body={"requests": small_tab_requests(sid, header, None, True)}))
    tabs.patch_cells(sheets, file_id, [(f"'{IMPORT_LOG}'!A1:{letters(len(header) - 1)}1", [header])])


def removed_header(import_columns: list) -> list:
    """Header of the `Removed Pages` tab: the lead columns, then the owner's columns."""
    return REMOVED_LEAD + [c for c in import_columns if c not in REMOVED_LEAD]


def ensure_removed_pages(sheets, file_id: str, header: list):
    """The `Removed Pages` tab: rows taken out of a tab (the page is gone or redirected, or an old
    row was merged into its page). Made when the first row goes."""
    titles, _ = history.grid_info(sheets, file_id)
    if REMOVED in titles:
        return
    made = history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
        {"addSheet": {"properties": {"title": REMOVED}}}]}))
    sid = made["replies"][0]["addSheet"]["properties"]["sheetId"]
    history._write(sheets.spreadsheets().batchUpdate(
        spreadsheetId=file_id, body={"requests": small_tab_requests(sid, header, None, True)}))
    tabs.patch_cells(sheets, file_id, [(f"'{REMOVED}'!A1:{letters(len(header) - 1)}1", [header])])


def delete_rows(sheets, file_id: str, sid: int, indexes: list):
    """Deletes data rows of a tab by 0-based row index (the header is row 0). The one place a
    row of a tab is taken out; the caller has written it to `Removed Pages` first."""
    wanted = sorted(set(indexes), reverse=True)          # from the bottom up: the others keep their place
    for i in range(0, len(wanted), 100):
        history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
            {"deleteDimension": {"range": {"sheetId": sid, "dimension": "ROWS",
                                           "startIndex": r, "endIndex": r + 1}}}
            for r in wanted[i:i + 100]]}))


def sheet_info(sheets, file_id: str) -> dict:
    """{title: (sheetId, rowCount)}"""
    resp = history._read(sheets.spreadsheets().get(
        spreadsheetId=file_id, fields="sheets(properties(sheetId,title,gridProperties(rowCount)))"))
    return {s["properties"]["title"]: (s["properties"]["sheetId"],
                                       s["properties"].get("gridProperties", {}).get("rowCount", 0))
            for s in resp.get("sheets", [])}


def extend_rows(sheets, file_id: str, sid: int, current: int, needed: int):
    """Grows a tab to `needed` rows with the same row height."""
    if needed <= current:
        return
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
        {"appendDimension": {"sheetId": sid, "dimension": "ROWS", "length": needed - current}},
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
                                                 "startIndex": current, "endIndex": needed},
                                       "properties": {"pixelSize": ROW_HEIGHT}, "fields": "pixelSize"}}]}))


# --------------------------------------------------------------------------- #
# Reading and writing
# --------------------------------------------------------------------------- #

def read_all(sheets, file_id: str, tab: str, width: int = 0) -> list:
    """Every row of a tab as typed values (dates as serial numbers), header row included."""
    resp = history._read(sheets.spreadsheets().values().get(
        spreadsheetId=file_id, range=f"'{tab}'",
        valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER"))
    return resp.get("values", [])


def table_of(values: list) -> tuple:
    """(header, rows as dicts {header: value}) for a tab read with read_all. Unknown columns stay
    in the dict too, so nothing the owner added is lost sight of."""
    if not values:
        return [], []
    header = [str(h) for h in values[0]]
    rows = []
    for raw in values[1:]:
        row = {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(header) if h}
        rows.append(row)
    return header, rows


def write_columns(sheets, file_id: str, tab: str, header: list, first_row: int, columns: dict):
    """Writes whole columns: {header name: [values from first_row down]}. Columns whose header is
    not in the tab are skipped. One call."""
    updates = []
    for name, values in columns.items():
        c = col_of(header, name)
        if c is None or not values:
            continue
        letter = letters(c)
        updates.append((f"'{tab}'!{letter}{first_row}:{letter}{first_row + len(values) - 1}",
                        [[v] for v in values]))
    tabs.patch_cells(sheets, file_id, updates)


def sort_by_score(sheets, file_id: str, sid: int, header: list, rows: int):
    """Sorts the data rows of a tab by Score, highest first; whole rows move together."""
    c = col_of(header, content.SCORE)
    if c is None or rows < 2:
        return
    history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
        {"sortRange": {"range": _range(sid, r1=1, r2=rows + 1, c1=0, c2=len(header)),
                       "sortSpecs": [{"dimensionIndex": c, "sortOrder": "DESCENDING"}]}}]}))
