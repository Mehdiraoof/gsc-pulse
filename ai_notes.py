"""
PulseO: the "AI Notes" tab, the meeting point between a report run and the writer of its texts
==============================================================================================
While the tool serves only its owner's projects, every piece of AI-written text in a report is written by
the owner's own Claude Pro plan through a scheduled task, never by an API inside this tool. The sheet is the
handoff and nothing secret changes hands:

  1. The sync run writes one row per text slot into the tab "AI Notes" of the site's Reports sheet:
     Report (weekly or monthly) | Period | Slot | Input | Text | Status | Language | Created.
     Input holds the facts and the instruction; Status is `waiting`.
  2. The scheduled task reads the rows that are `waiting`, writes the Text and sets Status to `ready`.
  3. The second run builds the decks and uses the Text of every row that is `ready`. A slot that is still
     `waiting` is simply left out of the deck (no placeholder is ever shown) and the log says so.

The Text and Status columns belong to the task: this module only reads them, and creates a row (Status
`waiting`) or refreshes the Input of a row that is still waiting. Old rows stay: they are the history.
Nothing here calls an AI.
"""

import json
import re
from dataclasses import dataclass
from datetime import date

import history
import tabs

TAB = "AI Notes"
HEADER = ["Report", "Period", "Slot", "Input", "Text", "Status", "Language", "Created"]
LAST_COL = "H"
COL = {name: i for i, name in enumerate(HEADER)}
WAITING, READY = "waiting", "ready"
WEEKLY_SUMMARY, OUR_WORK_INTRO, CLIENT_SLIDES = "weekly_summary", "our_work_intro", "client_slides"
SLOTS = {"weekly": (WEEKLY_SUMMARY,), "monthly": (OUR_WORK_INTRO, CLIENT_SLIDES)}
LANGUAGE_NAMES = {"en": "English", "fa": "Persian (Farsi)"}


@dataclass
class Slot:
    report: str          # weekly | monthly
    period: str          # "2026-W43" or "2026-10", as the Reports sheet names it
    slot: str
    input: str
    language: str        # en | fa

    @property
    def key(self) -> tuple:
        return (self.report, str(self.period), self.slot)


# --------------------------------------------------------------------------- #
# The tab
# --------------------------------------------------------------------------- #

def ensure_tab(sheets, reports_id: str):
    titles, _ = history.grid_info(sheets, reports_id)
    tabs.ensure_tab(sheets, reports_id, titles, TAB, HEADER)


def _cell(row: list, name: str) -> str:
    i = COL[name]
    return str(row[i]).strip() if len(row) > i and row[i] is not None else ""


def read_rows(sheets, reports_id: str) -> list:
    """[{'row': sheet row number, 'Report': ..., ...}] of the tab (empty when the tab does not exist yet)."""
    titles, _ = history.grid_info(sheets, reports_id)
    if TAB not in titles:
        return []
    out = []
    for i, raw in enumerate(tabs.read_rows(sheets, reports_id, TAB, LAST_COL)):
        if not any(str(c).strip() for c in raw):
            continue
        out.append({"row": i + 2, **{name: _cell(raw, name) for name in HEADER}})
    return out


def prepare(sheets, reports_id: str, wanted: list, today: date, log=print) -> dict:
    """Makes sure there is one row for each wanted slot. A new row is `waiting`. The Input of a row that is
    still waiting is refreshed (the numbers may have changed); a row that is ready, or that is old, is never
    touched, and the Text column is never written. Returns {"added": n, "refreshed": n}."""
    ensure_tab(sheets, reports_id)
    have = {(r["Report"], r["Period"], r["Slot"]): r for r in read_rows(sheets, reports_id)}
    new_rows, updates, refreshed = [], [], 0
    for s in wanted:
        row = have.get(s.key)
        if row is None:
            new_rows.append([s.report, str(s.period), s.slot, s.input, "", WAITING, s.language, today.isoformat()])
        elif row["Status"].lower() != READY and row["Input"] != s.input:
            updates.append((f"'{TAB}'!{chr(65 + COL['Input'])}{row['row']}", [[s.input]]))
            refreshed += 1
    if updates:
        tabs.patch_cells(sheets, reports_id, updates)
    tabs.append(sheets, reports_id, TAB, new_rows, log)
    return {"added": len(new_rows), "refreshed": refreshed}


def texts_for(sheets, reports_id: str, report: str, period: str) -> tuple:
    """({slot: text} of the rows that are ready and have a text, [slots still waiting]) for one report."""
    ready, waiting = {}, []
    for r in read_rows(sheets, reports_id):
        if r["Report"] != report or r["Period"] != str(period):
            continue
        if r["Status"].lower() == READY and r["Text"]:
            ready[r["Slot"]] = r["Text"]
        else:
            waiting.append(r["Slot"])
    return ready, waiting


# --------------------------------------------------------------------------- #
# What the task is told (the Input column)
# --------------------------------------------------------------------------- #

RULES = ("Write plain, warm, factual text for readers who are not SEO experts. Use only the facts below; "
         "never invent a number. Never mention a tool, a program or artificial intelligence, and never use "
         "the letters AI. No dashes (use a comma or a colon).")


def weekly_summary_input(aggregated: str, label: str, period_title: str, lang: str) -> str:
    return (f"TASK: write the summary slide of the weekly SEO report of {label}, {period_title}, in "
            f"{LANGUAGE_NAMES.get(lang, 'English')}.\n{RULES}\n"
            "FORMAT: first 4 to 6 lines that each start with \"- \" (the biggest change first, each names its "
            "number, a likely reason only when the data shows one), then a line \"Next steps:\" and 2 or 3 lines "
            "that each start with \"- \" (concrete recommended next steps).\n\nDATA\n" + aggregated)


def our_work_intro_input(label: str, period_title: str, lang: str, facts: str) -> str:
    return (f"TASK: write ONE or TWO short sentences that introduce the slide \"Our work and its results\" in "
            f"the monthly report of {label}, {period_title}, in {LANGUAGE_NAMES.get(lang, 'English')}. "
            f"It tells the client what was done this period and that the next lines show what it achieved.\n"
            f"{RULES}\nFACTS\n{facts}")


# --------------------------------------------------------------------------- #
# Reading what the task wrote
# --------------------------------------------------------------------------- #

_NEXT = re.compile(r"^\s*(next steps?|گام[‌ ]?های بعدی)\s*[:：]?\s*$",
                   re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*•●]|\d+[.)])\s+(.*\S)\s*$")


def parse_weekly_summary(text: str):
    """(bullets, next_steps) from the task's text, or None when it holds no bullet. The text may be the
    "- ..." layout the Input asks for, or the JSON {"bullets": [...], "next_steps": [...]}."""
    text = str(text or "").strip()
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            bullets = [str(b).strip() for b in data.get("bullets", []) if str(b).strip()][:6]
            steps = [str(s).strip() for s in data.get("next_steps", []) if str(s).strip()][:3]
            if bullets:
                return bullets, steps
        except (ValueError, AttributeError):
            pass
    bullets, steps, into_steps = [], [], False
    for line in text.splitlines():
        if _NEXT.match(line):
            into_steps = True
            continue
        b = _BULLET.match(line)
        if b:
            (steps if into_steps else bullets).append(b.group(1).strip())
    bullets, steps = bullets[:6], steps[:3]
    return (bullets, steps) if bullets else None
