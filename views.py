"""
PulseO: view tabs (v2.2)
========================
Designed, readable tabs on top of the `R ...` data tabs of a site's Reports sheet. They hold
formulas only: the numbers come from the R tabs, so they refresh by themselves and the
weekly run never rewrites them.

Tabs, left to right
  Dashboard    month (and segment) dropdown, takeaway, known event, scorecard, alarms, top 5 lists
  Monthly      one row per month, newest on top      Monthly · <segment>  one per page type
  Weekly       one row per ISO week, newest on top
  Lost / New / Growing / Declining Keywords, Growing / Declining Pages, YoY Growing / Declining
  Pages, Growing / Declining AIO Pages: a Month dropdown and one table
  Events       the only tab people write in: Month (YYYY-MM) and "What happened"
  then every other tab (R ..., Notes, AIO Raw, State, Page Type Discovery) in grey

Rules
  * Tabs are created first, formulas written after (a formula that points at a tab that does
    not exist yet stays broken), in one go, with USER_ENTERED so Sheets parses them.
  * `views_version` in the State tab: older than VIEWS_VERSION means the view tabs are deleted
    and rebuilt; otherwise only missing tabs are created. Normal runs write nothing else than
    the dropdowns of a new month (see `run_views`).
  * The Events tab and what people typed into it are never deleted or overwritten. Dropdown
    choices are never touched, except by the new-month rule.
  * After building, the tabs are read back; a cell showing #REF!, #NAME?, #VALUE! or #ERROR!
    fails the run, and the version is not stored, so the next run builds again.
"""

import re
from dataclasses import dataclass

import history
import reports
import state
import tabs

VIEWS_VERSION = 1
VERSION_KEY = "views_version"

DASHBOARD, MONTHLY_TAB, WEEKLY_TAB, EVENTS = "Dashboard", "Monthly", "Weekly", "Events"
SEGMENT_PREFIX = "Monthly · "
SHEET1 = "Sheet1"
ERRORS = ("#REF!", "#NAME?", "#VALUE!", "#ERROR!")

# ---- colours (hex, no #) ---------------------------------------------------
INK, TINT, CALLOUT, AMBER = "1B1E3C", "F3F4F8", "FFF6E8", "F2A541"
PERIWINKLE, SLATE, MUTED = "6C7AE0", "4A4F6A", "8A8FA6"
GREEN, GREEN_BG, RED, RED_BG = "1A9E6A", "E6F4EA", "D64545", "FCE8E6"
EVENT_TEXT, BAND, GREY, WHITE = "B26B00", "FAFAFC", "D9DBE5", "FFFFFF"

SANS, PERSIAN = "Lato", "Vazirmatn"

# ---- number formats ----------------------------------------------------------
COUNT = "#,##0"
DELTA = "+#,##0;-#,##0;0"
PCT = "+0.0%;-0.0%;0.0%"
PCT_PP = "+0.00%;-0.00%;0.00%"
CTR = "0.00%"
POSITION = "0.0"
POSITION_DELTA = "+0.0;-0.0;0.0"
MONTH = "mmmm yyyy"

# ---- sheet sizes -----------------------------------------------------------------
READBACK_ROWS = 60
MAX_REQUESTS = 250
MAX_RANGES = 200

# Spreadsheet locales whose formulas use ; instead of , (only used to explain a failure)
SEMICOLON_LOCALES = ("de", "fr", "es", "it", "pt", "nl", "ru", "tr", "pl", "cs", "sv", "da",
                     "nb", "no", "fi", "el", "hu", "ro", "bg", "uk", "sk", "sl", "hr", "sr",
                     "lt", "lv", "et", "id", "vi", "fa")


class ViewsError(Exception):
    """The view tabs could not be built. The message is safe to print."""


@dataclass
class ViewsResult:
    mode: str = "ok"                   # ok | built | repaired | skipped
    created: int = 0
    moved_to: str = ""                 # month the dropdowns were moved to
    note: str = ""

    def text(self) -> str:
        if self.note:
            return self.note
        if self.mode == "built":
            return f"built {self.created} tabs, read-back ok"
        if self.mode == "repaired":
            return f"repaired {self.created} missing tabs, read-back ok"
        if self.moved_to:
            return f"dropdowns moved to {self.moved_to}"
        return "ok"


# --------------------------------------------------------------------------- #
# What the tabs are
# --------------------------------------------------------------------------- #

@dataclass
class ListSpec:
    kind: str               # type in the R tab: Lost, New, Growing, Declining, YoY Growing, ...
    source: str             # R Keywords | R Pages | R AIO Pages
    title: str
    note: str
    sort: int               # column of the table to sort by (1-based)
    ascending: bool = False


KEYWORD_COLS = ["C", "D", "E", "F", "G", "H", "I", "J", "K"]
PAGE_COLS = ["N", "E", "F", "G", "H", "I", "J", "K", "L"]
AIO_COLS = ["G", "D", "E", "F"]
NUMBER_HEADERS = ["Clicks", "Clicks before", "Change", "Impressions", "Impr. before", "Change",
                  "Position", "Pos. before"]
NUMBER_FORMATS = [COUNT, COUNT, DELTA, COUNT, COUNT, DELTA, POSITION, POSITION]
CHANGE_COLUMNS = (3, 6)         # 0-based columns of the change figures
AIO_MISSING = ("AIO data not imported for this month. Export the Generative AI report and "
               "copy its tabs into this sheet.")
LIST_EMPTY = "Nothing in this list for this month."

LISTS = {
    "Lost Keywords": ListSpec(
        "Lost", reports.KEYWORDS, "Lost keywords",
        "Had impressions last month and none this month (Search Console no longer returns "
        "them). Sorted by impressions last month.", 6),
    "New Keywords": ListSpec(
        "New", reports.KEYWORDS, "New keywords",
        "No impressions last month, some this month. Sorted by impressions.", 5),
    "Growing Keywords": ListSpec(
        "Growing", reports.KEYWORDS, "Growing keywords",
        "The keywords that gained the most clicks compared with last month.", 4),
    "Declining Keywords": ListSpec(
        "Declining", reports.KEYWORDS, "Declining keywords",
        "The keywords that lost the most clicks compared with last month.", 4, True),
    "Growing Pages": ListSpec(
        "Growing", reports.PAGES, "Growing pages",
        "The pages that gained the most clicks compared with last month.", 4),
    "Declining Pages": ListSpec(
        "Declining", reports.PAGES, "Declining pages",
        "The pages that lost the most clicks compared with last month.", 4, True),
    "YoY Growing Pages": ListSpec(
        "YoY Growing", reports.PAGES, "Growing pages, year on year",
        "The pages that gained the most clicks compared with the same month last year "
        "('before' is last year).", 4),
    "YoY Declining Pages": ListSpec(
        "YoY Declining", reports.PAGES, "Declining pages, year on year",
        "The pages that lost the most clicks compared with the same month last year "
        "('before' is last year).", 4, True),
    "Growing AIO Pages": ListSpec(
        "Growing", reports.AIO_PAGES, "Growing pages in AI Overviews",
        "The pages that gained the most AI Overview impressions compared with last month.", 4),
    "Declining AIO Pages": ListSpec(
        "Declining", reports.AIO_PAGES, "Declining pages in AI Overviews",
        "The pages that lost the most AI Overview impressions compared with last month.", 4, True),
}


@dataclass
class TabSpec:
    name: str
    kind: str                       # dashboard | monthly | weekly | list | events
    color: str
    rows: int
    cols: int
    freeze: int
    segment: str = ""               # monthly: "" for the whole site
    spec: object = None             # ListSpec for list tabs
    hide_grid: bool = False


def expected_tabs(site) -> list:
    """The view tabs of a site, left to right."""
    out = [TabSpec(DASHBOARD, "dashboard", AMBER, 60, 14, 2, hide_grid=True),
           TabSpec(MONTHLY_TAB, "monthly", INK, 200, 13, 2)]
    for t in site.page_types:
        out.append(TabSpec(SEGMENT_PREFIX + t["name"], "monthly", INK, 200, 13, 2, segment=t["name"]))
    if site.page_types:
        out.append(TabSpec(SEGMENT_PREFIX + "Other", "monthly", INK, 200, 13, 2, segment="Other"))
    out.append(TabSpec(WEEKLY_TAB, "weekly", INK, 400, 12, 2))
    for name, spec in LISTS.items():
        big = spec.kind in ("Lost", "New")
        width = 4 if spec.source == reports.AIO_PAGES else 9
        out.append(TabSpec(name, "list", INK, 3000 if big else 1000, width, 4, spec=spec))
    out.append(TabSpec(EVENTS, "events", SLATE, 500, 4, 1))
    return out


def is_view_name(title: str) -> bool:
    return title in (DASHBOARD, MONTHLY_TAB, WEEKLY_TAB, EVENTS) or title in LISTS \
        or title.startswith(SEGMENT_PREFIX)


def month_dropdowns(tab_specs: list) -> list:
    """[(tab, A1 cell)] of every month dropdown."""
    return [(t.name, "C2" if t.kind == "dashboard" else "B2")
            for t in tab_specs if t.kind in ("dashboard", "list")]


# --------------------------------------------------------------------------- #
# Small helpers for the Sheets API requests
# --------------------------------------------------------------------------- #

def col_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def rgb(hex_: str) -> dict:
    return {"red": int(hex_[0:2], 16) / 255, "green": int(hex_[2:4], 16) / 255,
            "blue": int(hex_[4:6], 16) / 255}


def rng(sid, r1, r2, c1, c2) -> dict:
    """0-based, end exclusive."""
    return {"sheetId": sid, "startRowIndex": r1, "endRowIndex": r2,
            "startColumnIndex": c1, "endColumnIndex": c2}


def text(size=10, bold=False, italic=False, color=INK, font=SANS) -> dict:
    return {"fontFamily": font, "fontSize": size, "bold": bold, "italic": italic,
            "foregroundColor": rgb(color)}


def repeat(sid, r1, r2, c1, c2, **fmt) -> dict:
    """Formats a block. Keyword names are the userEnteredFormat fields (bg, text, h, v, wrap,
    number, rtl, border); only the fields given are touched."""
    cell = {}
    if "bg" in fmt:
        cell["backgroundColor"] = rgb(fmt["bg"])
    if "text" in fmt:
        cell["textFormat"] = fmt["text"]
    if "h" in fmt:
        cell["horizontalAlignment"] = fmt["h"]
    if "v" in fmt:
        cell["verticalAlignment"] = fmt["v"]
    if "wrap" in fmt:
        cell["wrapStrategy"] = fmt["wrap"]
    if "number" in fmt:
        kind = "TEXT" if fmt["number"] == "@" else ("DATE" if fmt["number"] == MONTH else "NUMBER")
        cell["numberFormat"] = {"type": kind, "pattern": fmt["number"]}
    if fmt.get("rtl"):
        cell["textDirection"] = "RIGHT_TO_LEFT"
    if "border" in fmt:
        side = {"style": "SOLID_MEDIUM", "color": rgb(fmt["border"])}
        cell["borders"] = {"top": side, "bottom": side, "left": side, "right": side}
    if "bottom" in fmt:
        cell["borders"] = {"bottom": {"style": "SOLID", "color": rgb(fmt["bottom"])}}
    return {"repeatCell": {"range": rng(sid, r1, r2, c1, c2), "cell": {"userEnteredFormat": cell},
                           "fields": "userEnteredFormat(" + ",".join(sorted(cell)) + ")"}}


def merge(sid, r1, r2, c1, c2) -> dict:
    return {"mergeCells": {"range": rng(sid, r1, r2, c1, c2), "mergeType": "MERGE_ALL"}}


def width(sid, c1, c2, px) -> dict:
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c1, "endIndex": c2},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def height(sid, r1, r2, px) -> dict:
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r1, "endIndex": r2},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def hide_columns(sid, c1, c2) -> dict:
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c1, "endIndex": c2},
        "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}


def dropdown(sid, r, c, source: str) -> dict:
    return {"setDataValidation": {"range": rng(sid, r, r + 1, c, c + 1), "rule": {
        "condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": "=" + source}]},
        "showCustomUi": True, "strict": True}}}


def _rule(sid, r1, r2, c1, c2, condition: dict, bg=None, color=None, bold=False) -> dict:
    fmt = {}
    if bg:
        fmt["backgroundColor"] = rgb(bg)
    if color or bold:
        fmt["textFormat"] = {"bold": bold}
        if color:
            fmt["textFormat"]["foregroundColor"] = rgb(color)
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [rng(sid, r1, r2, c1, c2)],
        "booleanRule": {"condition": condition, "format": fmt}}}}


def green_red(sid, r1, r2, c1, c2, reverse=False) -> list:
    """>0 green, <0 red (reversed for the average position: lower is better)."""
    up, down = (RED, GREEN) if reverse else (GREEN, RED)
    up_bg, down_bg = (RED_BG, GREEN_BG) if reverse else (GREEN_BG, RED_BG)
    return [
        _rule(sid, r1, r2, c1, c2, {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
              up_bg, up, True),
        _rule(sid, r1, r2, c1, c2, {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0"}]},
              down_bg, down, True)]


def custom(sid, r1, r2, c1, c2, formula: str, bg: str) -> dict:
    return _rule(sid, r1, r2, c1, c2,
                 {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, bg)


def literal(value: str) -> str:
    """A label as a cell value: never parsed as a formula."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "'") else value


# --------------------------------------------------------------------------- #
# Formulas
# --------------------------------------------------------------------------- #

RM = "'R Monthly'"


def _value(col: str, month: str, seg: str) -> str:
    """One number of R Monthly for a month and a segment."""
    return f"INDEX(FILTER({RM}!{col}2:{col},{RM}!A2:A={month},{RM}!B2:B={seg}),1)"


def _pct(col: str, seg: str) -> str:
    """A percent column of the chosen month, blank-safe (the sheet stores 12.3 for 12.3%)."""
    return f'=IFERROR(LET(v,{_value(col, "$C$2", seg)},IF(v="","",v/100)),"—")'


def dashboard_formulas(seg: str, with_segments: bool) -> dict:
    """{cell: formula} of the Dashboard."""
    cur = lambda col: _value(col, "$C$2", seg)                   # noqa: E731
    at = lambda col, key: _value(col, key, seg)                  # noqa: E731
    f = {
        "K1": '=TEXT(EDATE(DATE(LEFT($C$2,4),RIGHT($C$2,2),1),-1),"yyyy-mm")',
        "K2": '=TEXT(EDATE(DATE(LEFT($C$2,4),RIGHT($C$2,2),1),-12),"yyyy-mm")',
        "K3": '=TEXT(DATE(LEFT($C$2,4),RIGHT($C$2,2),1),"mmmm yyyy")',
        "B7": f'=IFERROR({cur("C")},"—")',
        "C7": f'=IFERROR({cur("D")},"—")',
        "D7": f'=IFERROR({cur("E")}/100,"—")',
        "E7": f'=IFERROR({cur("F")},"—")',
        "F7": f'=IFERROR(LET(v,{cur("G")},IF(v="","Not imported",v)),"Not imported")',
        "B8": _pct("I", seg),
        "C8": _pct("K", seg),
        "D8": f'=IFERROR(({cur("E")}-{at("E", "$K$1")})/100,"—")',
        "E8": f'=IFERROR({cur("F")}-{at("F", "$K$1")},"—")',
        "F8": _pct("M", seg),
        "B9": _pct("O", seg),
        "C9": _pct("Q", seg),
        "D9": f'=IFERROR(({cur("E")}-{at("E", "$K$2")})/100,"—")',
        "E9": f'=IFERROR({cur("F")}-{at("F", "$K$2")},"—")',
        "B4": ('=IFERROR("Clicks "&IF(B8<0,"down ","up ")&TEXT(ABS(B8),"0%")&" vs "'
               '&TEXT(DATE(LEFT($K$1,4),RIGHT($K$1,2),1),"mmmm")&IF(B9="","",", "&IF(B9<0,"down ","up ")'
               '&TEXT(ABS(B9),"0%")&" vs last year")&". Impressions "&IF(C8<0,"down ","up ")'
               '&TEXT(ABS(C8),"0%")&".","No data for this month.")'),
        "B5": '=IFERROR("Known event this month: "&VLOOKUP($C$2,Events!A:B,2,FALSE),"")',
        "B14": ("=IFERROR(SORT(FILTER({'R Alarms'!B2:B,'R Alarms'!C2:C,'R Alarms'!E2:E,'R Alarms'!D2:D},"
                "'R Alarms'!A2:A=$C$2),3,FALSE),\"No alarms this month.\")"),
        "B27": _top5(reports.PAGES, "E", "G", "N", "Declining", 2, True),
        "B35": _top5(reports.PAGES, "E", "G", "N", "Growing", 2, False),
        "B43": _top5(reports.KEYWORDS, "H", "I", "C", "Lost", 1, False),
        "B51": _top5(reports.KEYWORDS, "G", "D", "C", "New", 1, False),
    }
    for cell, col, color in (("B10", "C", AMBER), ("C10", "D", PERIWINKLE), ("D10", "E", SLATE),
                             ("E10", "F", SLATE), ("F10", "G", GREEN)):
        f[cell] = (f"=IFERROR(SPARKLINE(FILTER({RM}!{col}2:{col},{RM}!B2:B={seg},{RM}!A2:A>=$K$2,"
                   f'{RM}!A2:A<=$C$2),{{"charttype","column";"color","#{color}";"empty","zero"}}),"")')
    return f


def _top5(tab: str, a: str, b: str, c: str, kind: str, sort_col: int, ascending: bool) -> str:
    cols = ",".join(f"'{tab}'!{x}2:{x}" for x in (a, b, c))
    return (f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({{{cols}}},'{tab}'!A2:A=$C$2,'{tab}'!B2:B=\"{kind}\"),"
            f'{sort_col},{"TRUE" if ascending else "FALSE"}),5,3),"None this month.")')


def monthly_formula(segment: str) -> str:
    seg = '"' + (segment or "All").replace('"', '""') + '"'
    return (f"=IFERROR(LET(s,SORT(FILTER({RM}!A2:Q,{RM}!B2:B={seg}),1,FALSE),"
            'pc,LAMBDA(c,MAP(CHOOSECOLS(s,c),LAMBDA(v,IF(v="","",v/100)))),p,CHOOSECOLS(s,1),'
            "HSTACK(MAP(p,LAMBDA(x,DATE(LEFT(x,4),RIGHT(x,2),1))),CHOOSECOLS(s,3),CHOOSECOLS(s,8),"
            "pc(9),CHOOSECOLS(s,4),CHOOSECOLS(s,10),pc(11),pc(5),CHOOSECOLS(s,6),CHOOSECOLS(s,7),"
            'pc(15),pc(17),MAP(p,LAMBDA(x,IFERROR(VLOOKUP(x,Events!A:B,2,FALSE),""))))),'
            '"No monthly data yet.")')


def weekly_formula() -> str:
    mon = ('mon,LAMBDA(x,DATE(LEFT(x,4),1,4)-WEEKDAY(DATE(LEFT(x,4),1,4),3)+(RIGHT(x,2)-1)*7)')
    return ("=IFERROR(LET(s,SORT(FILTER('R Weekly'!A2:Q,'R Weekly'!B2:B=\"All\"),1,FALSE),"
            'pc,LAMBDA(c,MAP(CHOOSECOLS(s,c),LAMBDA(v,IF(v="","",v/100)))),p,CHOOSECOLS(s,1),'
            + mon + ","
            'HSTACK(MAP(p,LAMBDA(x,"W"&RIGHT(x,2)&" · "&TEXT(mon(x),"d mmm")&" – "'
            '&TEXT(mon(x)+6,"d mmm yyyy"))),CHOOSECOLS(s,3),CHOOSECOLS(s,8),pc(9),CHOOSECOLS(s,4),'
            'CHOOSECOLS(s,10),pc(11),pc(5),CHOOSECOLS(s,6),pc(15),pc(17),'
            'MAP(p,LAMBDA(x,IFERROR(VLOOKUP(TEXT(mon(x),"yyyy-mm"),Events!A:B,2,FALSE),""))))),'
            '"No weekly data yet.")')


def list_formula(spec: ListSpec) -> str:
    cols = {reports.KEYWORDS: KEYWORD_COLS, reports.PAGES: PAGE_COLS,
            reports.AIO_PAGES: AIO_COLS}[spec.source]
    array = ",".join(f"'{spec.source}'!{c}2:{c}" for c in cols)
    empty = AIO_MISSING if spec.source == reports.AIO_PAGES else LIST_EMPTY
    return (f"=IFERROR(SORT(FILTER({{{array}}},'{spec.source}'!A2:A=$B$2,"
            f"'{spec.source}'!B2:B=\"{spec.kind}\"),{spec.sort},"
            f'{"TRUE" if spec.ascending else "FALSE"}),"{empty}")')


# --------------------------------------------------------------------------- #
# Tab builders: (formatting requests, [(A1 range, [[value]])])
# --------------------------------------------------------------------------- #

def _base(sid, t: TabSpec) -> list:
    """Font and alignment of the whole tab, then the title bar of row 1."""
    size = 16 if t.kind == "dashboard" else 14
    return [
        repeat(sid, 0, t.rows, 0, t.cols, text=text(), v="MIDDLE"),
        repeat(sid, 0, 1, 0, t.cols, bg=INK, text=text(size, True, color=WHITE), v="MIDDLE"),
        height(sid, 0, 1, 40)]


def _cell(tab: str, a1: str, value) -> tuple:
    return (f"'{tab}'!{a1}", [[value]])


def _header_row(sid, row, c1, c2) -> list:
    return [repeat(sid, row, row + 1, c1, c2, bg=TINT, text=text(10, True, color=SLATE), h="CENTER",
                   v="MIDDLE", wrap="WRAP"), height(sid, row, row + 1, 32)]


def build_dashboard(sid, t: TabSpec, ctx: dict) -> tuple:
    req, cells = _base(sid, t), []
    with_seg = ctx["segments"]
    seg = "$E$2" if with_seg else '"All"'
    label = ctx["label"]
    title = f"{label} · SEO Dashboard" if label else "SEO Dashboard"
    cells.append(_cell(DASHBOARD, "B1", literal(title)))

    # column widths: A labels, B:F the scorecard, G:J room for the long texts, K:N hidden helpers
    req += [width(sid, 0, 1, 120), width(sid, 1, 6, 150), width(sid, 6, 10, 110),
            width(sid, 10, 14, 90), hide_columns(sid, 10, 14)]
    heights = {1: 32, 2: 12, 3: 52, 4: 24, 5: 24, 6: 48, 7: 26, 8: 26, 9: 44, 10: 12}
    for r, px in heights.items():
        req.append(height(sid, r, r + 1, px))

    # selectors
    cells += [_cell(DASHBOARD, "B2", "Month")]
    req += [repeat(sid, 1, 2, 1, 2, text=text(10, True, color=MUTED), h="RIGHT", v="MIDDLE"),
            repeat(sid, 1, 2, 2, 3, bg=CALLOUT, border=AMBER, text=text(12, True), h="CENTER",
                   v="MIDDLE"),
            dropdown(sid, 1, 2, ctx["month_source"])]
    if with_seg:
        cells.append(_cell(DASHBOARD, "D2", "Segment"))
        req += [repeat(sid, 1, 2, 3, 4, text=text(10, True, color=MUTED), h="RIGHT", v="MIDDLE"),
                repeat(sid, 1, 2, 4, 5, bg=CALLOUT, border=AMBER, text=text(12, True), h="CENTER",
                       v="MIDDLE"),
                dropdown(sid, 1, 2, "Dashboard!$N$2:$N")]
        # helper lists for the dropdowns: every month once (newest first), every segment once
        cells.append(_cell(DASHBOARD, "L2", "=SORT(UNIQUE(FILTER('R Monthly'!A2:A,'R Monthly'!A2:A<>\"\")),1,FALSE)"))
        cells.append(_cell(DASHBOARD, "N2", "=UNIQUE(FILTER('R Monthly'!B2:B,'R Monthly'!B2:B<>\"\"))"))
        cells.append(_cell(DASHBOARD, "E2", "All"))
    formulas = dashboard_formulas(seg, with_seg)
    cells += [(f"'{DASHBOARD}'!{a1}", [[v]]) for a1, v in formulas.items()]

    # takeaway and known event
    req += [merge(sid, 3, 4, 1, 10), merge(sid, 4, 5, 1, 10),
            repeat(sid, 3, 4, 1, 10, bg=CALLOUT, text=text(15, True), v="MIDDLE", wrap="WRAP",
                   h="LEFT"),
            repeat(sid, 4, 5, 1, 10, text=text(10, False, True, EVENT_TEXT), v="MIDDLE")]

    # scorecard
    for col, name in zip("BCDEF", ("Clicks", "Impressions", "CTR", "Avg. position", "AIO impressions")):
        cells.append(_cell(DASHBOARD, f"{col}6", name))
    cells += [_cell(DASHBOARD, "A8", "vs last month"), _cell(DASHBOARD, "A9", "vs last year"),
              _cell(DASHBOARD, "A10", "Last 13 months")]
    req += [repeat(sid, 5, 10, 1, 6, bg=TINT, h="CENTER", v="MIDDLE"),
            repeat(sid, 5, 6, 1, 6, bg=TINT, text=text(10, True, color=MUTED), h="CENTER", v="MIDDLE"),
            repeat(sid, 6, 7, 1, 6, bg=TINT, text=text(22, True), h="CENTER", v="MIDDLE"),
            repeat(sid, 7, 9, 1, 6, bg=TINT, text=text(11, True), h="CENTER", v="MIDDLE"),
            repeat(sid, 5, 10, 0, 1, text=text(10, False, True, MUTED), h="RIGHT", v="MIDDLE"),
            repeat(sid, 6, 7, 1, 3, number=COUNT), repeat(sid, 6, 7, 3, 4, number=CTR),
            repeat(sid, 6, 7, 4, 5, number=POSITION), repeat(sid, 6, 7, 5, 6, number=COUNT),
            repeat(sid, 7, 9, 1, 3, number=PCT), repeat(sid, 7, 9, 3, 4, number=PCT_PP),
            repeat(sid, 7, 9, 4, 5, number=POSITION_DELTA), repeat(sid, 7, 9, 5, 6, number=PCT)]
    req += green_red(sid, 7, 9, 1, 4) + green_red(sid, 7, 9, 4, 5, reverse=True) \
        + green_red(sid, 7, 9, 5, 6)

    # alarms
    cells += [_cell(DASHBOARD, "B12", "Alarms this month")]
    for col, name in zip("BCD", ("Severity", "Area", "Impact (clicks)")):
        cells.append(_cell(DASHBOARD, f"{col}13", name))
    cells.append(_cell(DASHBOARD, "E13", "What happened"))
    req += [height(sid, 11, 12, 30), repeat(sid, 11, 12, 1, 10, text=text(12, True), bottom=AMBER,
                                            v="MIDDLE"),
            *_header_row(sid, 12, 1, 10), merge(sid, 12, 13, 4, 10)]
    for r in range(13, 23):
        req.append(merge(sid, r, r + 1, 4, 10))
    req += [height(sid, 13, 23, 28),
            repeat(sid, 13, 23, 1, 4, h="CENTER", v="MIDDLE"),
            repeat(sid, 13, 23, 3, 4, number=COUNT),
            repeat(sid, 13, 23, 4, 10, text=text(10, font=PERSIAN), h="LEFT", v="MIDDLE"),
            _rule(sid, 13, 23, 1, 2, {"type": "TEXT_EQ", "values": [{"userEnteredValue": "high"}]},
                  RED, WHITE, True),
            _rule(sid, 13, 23, 1, 2, {"type": "TEXT_EQ", "values": [{"userEnteredValue": "medium"}]},
                  CALLOUT, EVENT_TEXT, True),
            _rule(sid, 13, 23, 1, 2, {"type": "TEXT_EQ", "values": [{"userEnteredValue": "low"}]},
                  TINT, SLATE, True)]

    # the four top-5 lists: (title row 1-based, title, headers, formats of the two numbers)
    blocks = [
        (25, "Pages that lost the most clicks", ("Clicks", "Change", "Page"), (COUNT, DELTA), True),
        (33, "Pages that gained the most clicks", ("Clicks", "Change", "Page"), (COUNT, DELTA), True),
        (41, "Keywords lost (had impressions last month, none now)",
         ("Impr. before", "Change", "Keyword"), (COUNT, DELTA), True),
        (49, "New keywords this month", ("Impressions", "Clicks", "Keyword"), (COUNT, COUNT), False)]
    for top, name, heads, formats, colour in blocks:
        t0 = top - 1                                   # 0-based row of the title
        cells.append(_cell(DASHBOARD, f"B{top}", name))
        for col, h in zip("BCD", heads):
            cells.append(_cell(DASHBOARD, f"{col}{top + 1}", h))
        req += [height(sid, t0, t0 + 1, 30),
                repeat(sid, t0, t0 + 1, 1, 10, text=text(12, True), bottom=AMBER, v="MIDDLE"),
                *_header_row(sid, t0 + 1, 1, 10), merge(sid, t0 + 1, t0 + 2, 3, 10)]
        for r in range(t0 + 2, t0 + 7):
            req.append(merge(sid, r, r + 1, 3, 10))
        req += [height(sid, t0 + 2, t0 + 7, 28),
                repeat(sid, t0 + 2, t0 + 7, 1, 3, h="CENTER", v="MIDDLE"),
                repeat(sid, t0 + 2, t0 + 7, 1, 2, number=formats[0]),
                repeat(sid, t0 + 2, t0 + 7, 2, 3, number=formats[1]),
                repeat(sid, t0 + 2, t0 + 7, 3, 10, text=text(11, font=PERSIAN), h="RIGHT", v="MIDDLE",
                       rtl=True)]
        if colour:
            req += green_red(sid, t0 + 2, t0 + 7, 2, 3)
    return req, cells


def build_monthly(sid, t: TabSpec, ctx: dict) -> tuple:
    weekly = t.kind == "weekly"
    ncols = t.cols
    heads = (["Week", "Clicks", "Change", "% Change", "Impressions", "Change", "% Change", "CTR",
              "Avg. position", "Clicks YoY", "Impr. YoY", "Note"] if weekly else
             ["Month", "Clicks", "Change", "% Change", "Impressions", "Change", "% Change", "CTR",
              "Avg. position", "AIO impressions", "Clicks YoY", "Impr. YoY", "Note"])
    formats = ([None, COUNT, DELTA, PCT, COUNT, DELTA, PCT, CTR, POSITION, PCT, PCT, None] if weekly
               else [MONTH, COUNT, DELTA, PCT, COUNT, DELTA, PCT, CTR, POSITION, COUNT, PCT, PCT, None])
    change_cols = (2, 3, 5, 6, 9, 10) if weekly else (2, 3, 5, 6, 10, 11)
    note = ncols - 1
    if weekly:
        title = "Weekly · every week, newest first"
    elif t.segment:
        title = f"Monthly · {t.segment}, every month, newest first"
    else:
        title = "Monthly · every month, newest first"
    req = _base(sid, t)
    cells = [_cell(t.name, "A1", literal(title)), _cell(t.name, "A3", weekly_formula() if weekly
                                                         else monthly_formula(t.segment))]
    for i, h in enumerate(heads):
        cells.append(_cell(t.name, f"{col_letter(i)}2", h))
    first = 2                                                       # first data row, 0-based
    req += [merge(sid, 0, 1, 0, ncols), *_header_row(sid, 1, 0, ncols),
            width(sid, 0, 1, 210 if weekly else 150), width(sid, 1, note, 95), width(sid, note, note + 1, 420),
            height(sid, first, t.rows, 28),
            repeat(sid, first, t.rows, 0, note, h="CENTER", v="MIDDLE"),
            repeat(sid, first, t.rows, note, note + 1, text=text(10, font=PERSIAN), h="RIGHT",
                   v="MIDDLE", rtl=True, wrap="WRAP"),
            custom(sid, first, t.rows, 0, ncols, "=ISEVEN(ROW())", BAND)]
    for i, number in enumerate(formats):
        if number:
            req.append(repeat(sid, first, t.rows, i, i + 1, number=number))
    for i in change_cols:
        req += green_red(sid, first, t.rows, i, i + 1)
    note_col = col_letter(note)
    req.append(custom(sid, first, t.rows, 0, ncols, f'=${note_col}3<>""', CALLOUT))
    return req, cells


def build_list(sid, t: TabSpec, ctx: dict) -> tuple:
    spec: ListSpec = t.spec
    aio = spec.source == reports.AIO_PAGES
    first_head = "Keyword" if spec.source == reports.KEYWORDS else "Page"
    heads = ([first_head, "AIO impressions", "AIO before", "Change"] if aio
             else [first_head] + NUMBER_HEADERS)
    formats = ([None, COUNT, COUNT, DELTA] if aio else [None] + NUMBER_FORMATS)
    ncols = len(heads)
    req = _base(sid, t)
    cells = [_cell(t.name, "A1", literal(spec.title)), _cell(t.name, "A2", "Month"),
             _cell(t.name, "A3", literal(spec.note)), _cell(t.name, "A5", list_formula(spec))]
    for i, h in enumerate(heads):
        cells.append(_cell(t.name, f"{col_letter(i)}4", h))
    req += [merge(sid, 0, 1, 0, ncols), merge(sid, 2, 3, 0, ncols),
            height(sid, 1, 2, 30),
            repeat(sid, 1, 2, 0, 1, text=text(10, True, color=MUTED), h="RIGHT", v="MIDDLE"),
            repeat(sid, 1, 2, 1, 2, bg=CALLOUT, border=AMBER, text=text(12, True), h="CENTER",
                   v="MIDDLE"),
            dropdown(sid, 1, 1, ctx["month_source"]),
            repeat(sid, 2, 3, 0, ncols, text=text(10, False, True, MUTED), v="MIDDLE"),
            *_header_row(sid, 3, 0, ncols),
            width(sid, 0, 1, 400), width(sid, 1, ncols, 95),
            height(sid, 4, t.rows, 28),
            repeat(sid, 4, t.rows, 1, ncols, h="CENTER", v="MIDDLE"),
            repeat(sid, 4, t.rows, 0, 1, text=text(11, font=PERSIAN), h="RIGHT", v="MIDDLE", rtl=True),
            custom(sid, 4, t.rows, 0, ncols, "=ISEVEN(ROW())", BAND)]
    for i, number in enumerate(formats):
        if number:
            req.append(repeat(sid, 4, t.rows, i, i + 1, number=number))
    for i in ((3,) if aio else CHANGE_COLUMNS):
        req += green_red(sid, 4, t.rows, i, i + 1)
    return req, cells


def build_events(sid, t: TabSpec, ctx: dict) -> tuple:
    req = [repeat(sid, 0, t.rows, 0, t.cols, text=text(), v="MIDDLE"),
           *_header_row(sid, 0, 0, 2),
           repeat(sid, 0, 1, 3, 4, text=text(10, False, True, MUTED), v="MIDDLE"),
           width(sid, 0, 1, 110), width(sid, 1, 2, 620), width(sid, 2, 3, 30), width(sid, 3, 4, 420),
           repeat(sid, 1, t.rows, 0, 1, number="@", h="CENTER"),
           repeat(sid, 1, t.rows, 1, 2, text=text(11, font=PERSIAN), wrap="WRAP", v="MIDDLE")]
    cells = []
    if ctx.get("events_new", True):
        cells = [_cell(EVENTS, "A1", "Month"), _cell(EVENTS, "B1", "What happened"),
                 _cell(EVENTS, "D1", "Write the month as YYYY-MM (for example 2026-09). The note shows "
                                     "in Monthly, Weekly and on the Dashboard.")]
    return req, cells


BUILDERS = {"dashboard": build_dashboard, "monthly": build_monthly, "weekly": build_monthly,
            "list": build_list, "events": build_events}


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #

def _send(sheets, reports_id, requests: list):
    for i in range(0, len(requests), MAX_REQUESTS):
        history._write(sheets.spreadsheets().batchUpdate(
            spreadsheetId=reports_id, body={"requests": requests[i:i + MAX_REQUESTS]}))


def _write_values(sheets, reports_id, cells: list):
    for i in range(0, len(cells), MAX_RANGES):
        history._write(sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=reports_id,
            body={"valueInputOption": "USER_ENTERED",
                  "data": [{"range": r, "values": v} for r, v in cells[i:i + MAX_RANGES]]}))


def _newest_month(sheets, reports_id) -> str:
    rows = tabs.read_rows(sheets, reports_id, reports.MONTHLY, "B")
    return max((r[0] for r in rows if r and re.fullmatch(r"\d{4}-\d{2}", r[0])), default="")


def _locale(sheets, reports_id) -> str:
    resp = history._read(sheets.spreadsheets().get(spreadsheetId=reports_id,
                                                   fields="properties.locale"))
    return resp.get("properties", {}).get("locale", "")


def _safe_name(name: str) -> str:
    """Segment tabs carry a page type name, which stays out of messages and logs."""
    return SEGMENT_PREFIX + "(segment)" if name.startswith(SEGMENT_PREFIX) else name


def read_back(sheets, reports_id: str, names: list, ranges_rows: int = READBACK_ROWS,
              locale: str = "") -> None:
    """Reads the top of every view tab; any formula error fails with a scrubbed message."""
    ranges = [f"'{n}'!A1:N{ranges_rows}" for n in names]
    problems = []
    for attempt in range(4):
        resp = history._read(sheets.spreadsheets().values().batchGet(
            spreadsheetId=reports_id, ranges=ranges))
        problems, loading = [], False
        for name, vr in zip(names, resp.get("valueRanges", [])):
            for r, row in enumerate(vr.get("values", []), start=1):
                for c, value in enumerate(row):
                    v = str(value)
                    if v.startswith(ERRORS):
                        problems.append((name, f"{col_letter(c)}{r}", v.split()[0]))
                    elif v == "Loading...":
                        loading = True
        if not loading or problems:
            break
        history._sleep(3)
    if problems:
        shown = ", ".join(f"{_safe_name(n)}!{cell} ({err})" for n, cell, err in problems[:6])
        more = f" and {len(problems) - 6} more" if len(problems) > 6 else ""
        hint = ""
        if locale[:2] in SEMICOLON_LOCALES:
            hint = (f" The spreadsheet locale ({locale}) may expect ; between formula arguments: "
                    f"set File > Settings > Locale to United States and run again.")
        raise ViewsError(f"views: formula errors after building: {shown}{more}.{hint}")


def run_views(sheets, reports_id: str, site, newest_month: str = "", new_month: str = "",
              rebuild: bool = False, log=print) -> ViewsResult:
    """
    Builds, upgrades or repairs the view tabs of one site.

    newest_month  newest complete month (default for the dropdowns of tabs built now)
    new_month     set when this run added a newer month to R Monthly: every month dropdown moves to it
    rebuild       delete and recreate all view tabs (Events is kept as it is)
    """
    result = ViewsResult()
    titles, _ = history.grid_info(sheets, reports_id)
    if reports.MONTHLY not in titles:
        result.mode, result.note = "skipped", "no report data yet"
        return result
    tabs.ensure_tab(sheets, reports_id, titles, state.STATE_TAB, state.STATE_HEADER)
    st = state.read_state(sheets, reports_id)
    try:
        stored = int(state.get(st, VERSION_KEY) or 0)
    except ValueError:
        stored = 0

    wanted = expected_tabs(site)
    names = [t.name for t in wanted]
    full = rebuild or stored < VIEWS_VERSION
    missing = [t for t in wanted if t.name not in titles or (full and t.kind != "events")]
    if not missing:
        if new_month:
            _write_values(sheets, reports_id, [
                (f"'{tab}'!{cell}", [["'" + new_month]]) for tab, cell in month_dropdowns(wanted)])
            result.moved_to = new_month
            log(f"  views: dropdowns moved to {new_month}")
        return result

    month = newest_month or _newest_month(sheets, reports_id)
    if not month:
        result.mode, result.note = "skipped", "no report data yet"
        return result
    locale = _locale(sheets, reports_id)

    # ---- 1. tabs: delete what is rebuilt, create everything that is missing -------------
    requests = []
    if full:
        for title, sid in titles.items():
            if is_view_name(title) and title != EVENTS:
                requests.append({"deleteSheet": {"sheetId": sid}})
    if SHEET1 in titles and not tabs.read_tab(sheets, reports_id, SHEET1):
        requests.append({"deleteSheet": {"sheetId": titles[SHEET1]}})
    adds = []
    for t in missing:
        props = {"title": t.name, "index": names.index(t.name),
                 "tabColorStyle": {"rgbColor": rgb(t.color)},
                 "gridProperties": {"rowCount": t.rows, "columnCount": t.cols,
                                    "frozenRowCount": t.freeze}}
        if t.hide_grid:
            props["gridProperties"]["hideGridlines"] = True
        adds.append(t.name)
        requests.append({"addSheet": {"properties": props}})
    grey = [sid for title, sid in titles.items()
            if not is_view_name(title) and title != SHEET1 and sid is not None]
    for sid in grey:
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sid, "tabColorStyle": {"rgbColor": rgb(GREY)}},
            "fields": "tabColorStyle"}})
    if EVENTS in titles and full:             # Events stays; only its place and colour are set
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": titles[EVENTS], "index": names.index(EVENTS),
                           "tabColorStyle": {"rgbColor": rgb(SLATE)}},
            "fields": "index,tabColorStyle"}})
    resp = history._write(sheets.spreadsheets().batchUpdate(
        spreadsheetId=reports_id, body={"requests": requests}))
    ids = {}
    add_replies = [r["addSheet"]["properties"]["sheetId"] for r in resp.get("replies", [])
                   if "addSheet" in r]
    for name, sid in zip(adds, add_replies):
        ids[name] = sid
    if EVENTS in titles and EVENTS not in ids:
        ids[EVENTS] = titles[EVENTS]
    log(f"  views: {len(adds)} tabs created" + (" (rebuild)" if full else ""))

    # ---- 2. formatting, then formulas (every tab exists now) --------------------------------
    segments = bool(site.page_types)
    ctx = {"label": getattr(getattr(site, "deck", None), "site_label", "") or "",
           "segments": segments,
           "month_source": "Dashboard!$L$2:$L" if segments else "'R Monthly'!A2:A"}
    format_requests, cells = [], []
    for t in wanted:
        if t.name in adds or (full and t.kind == "events"):
            reqs, vals = BUILDERS[t.kind](ids[t.name], t, dict(ctx, events_new=EVENTS in adds))
            format_requests += reqs
            cells += vals
    for tab, cell in month_dropdowns([t for t in wanted if t.name in adds]):
        cells.append((f"'{tab}'!{cell}", [["'" + month]]))
    _send(sheets, reports_id, format_requests)
    _write_values(sheets, reports_id, cells)
    if new_month and not full:                   # tabs that survived follow the new month too
        _write_values(sheets, reports_id, [
            (f"'{tab}'!{cell}", [["'" + new_month]])
            for tab, cell in month_dropdowns([t for t in wanted if t.name not in adds])])
        result.moved_to = new_month

    # ---- 3. read back, then remember the version ------------------------------------------
    read_back(sheets, reports_id, [t.name for t in wanted if t.kind != "events"], locale=locale)
    if full:
        state.write_state(sheets, reports_id, st, VERSION_KEY, str(VIEWS_VERSION))
    result.mode = "built" if full else "repaired"
    result.created = len(adds)
    log(f"  views: {result.text()}")
    return result
