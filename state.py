"""
PulseO: the State tab of a Reports sheet
========================================
A small Key / Value / Updated at table that remembers what the tool has already done for
a site: which decks were sent, which version of the R Alarms wording and of the view tabs
the sheet holds. Kept in its own module because the report engine, the decks and the
views all use it.
"""

from datetime import datetime

import tabs

STATE_TAB = "State"
STATE_HEADER = ["Key", "Value", "Updated at"]


def read_state(sheets, reports_id: str) -> dict:
    """{key: (sheet row, value)}"""
    state = {}
    for i, row in enumerate(tabs.read_rows(sheets, reports_id, STATE_TAB, "C"), start=2):
        if row and row[0]:
            state[row[0]] = (i, row[1] if len(row) > 1 else "")
    return state


def get(state: dict, key: str, default: str = "") -> str:
    return state.get(key, (0, default))[1]


def write_state(sheets, reports_id: str, state: dict, key: str, value: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if key in state:
        row = state[key][0]
        tabs.patch_cells(sheets, reports_id, [(f"'{STATE_TAB}'!B{row}:C{row}", [[value, now]])])
    else:
        tabs.append(sheets, reports_id, STATE_TAB, [[key, value, now]])
        state[key] = (2 + len(state), value)
    state[key] = (state[key][0], value)
