"""
PulseO: the Resolved list
=========================
Mehdi lists what is already known and fixed in the `Events` tab, columns D:G
(`kind | value | note | added`), kind being page, keyword, month or segment. Anything listed
never appears in the monthly (client) deck: not as a decline, not in "what we're watching", not
as a trend annotation. The weekly deck, which is for Mehdi only, still shows everything.

  page      the exact URL, or its path (as shown in the views); matched after decoding
  keyword   the exact query (case does not matter)
  month     YYYY-MM
  segment   the segment label
"""

from dataclasses import dataclass, field

from googleapiclient.errors import HttpError

import tabs

EVENTS_TAB = "Events"
KINDS = ("page", "keyword", "month", "segment")
RANGE_LAST_COL = "G"
FIRST_COL = 4                       # D: the Resolved block starts at the fourth column


@dataclass
class Resolved:
    pages: set = field(default_factory=set)
    keywords: set = field(default_factory=set)
    months: set = field(default_factory=set)
    segments: set = field(default_factory=set)
    notes: dict = field(default_factory=dict)          # (kind, value) -> note

    def page(self, url: str) -> bool:
        return bool(url) and (url.strip() in self.pages or tabs.path_of(url) in self.pages
                              or tabs.path_of(url).strip("/") in self.pages)

    def keyword(self, query: str) -> bool:
        return bool(query) and query.strip().lower() in self.keywords

    def month(self, label: str) -> bool:
        return label in self.months

    def segment(self, name: str) -> bool:
        return bool(name) and name.strip().lower() in self.segments

    def note(self, kind: str, value: str) -> str:
        return self.notes.get((kind, value), "")

    def __bool__(self):
        return bool(self.pages or self.keywords or self.months or self.segments)


def parse(rows: list) -> Resolved:
    """rows: the cells of Events!D2:G. Unknown kinds and empty values are ignored."""
    out = Resolved()
    for row in rows:
        cells = [str(c).strip() for c in list(row) + [""] * (4 - len(row))]
        kind, value, note = cells[0].lower(), cells[1], cells[2]
        if kind not in KINDS or not value:
            continue
        if kind == "page":
            out.pages.add(value)
            out.pages.add(tabs.path_of(value))                 # a full URL or a path both work
            out.pages.add(tabs.path_of(value).strip("/"))
        elif kind == "keyword":
            out.keywords.add(value.lower())
        elif kind == "month":
            out.months.add(value)
        else:
            out.segments.add(value.lower())
        out.notes[(kind, value)] = note
    return out


def load(sheets, reports_id: str) -> Resolved:
    """The Resolved list of a Reports sheet (empty when there is no Events tab or nothing in it)."""
    try:
        rows = tabs.read_rows(sheets, reports_id, EVENTS_TAB, RANGE_LAST_COL, first_row=2)
    except HttpError:
        return Resolved()
    return parse([r[FIRST_COL - 1:] for r in rows if len(r) >= FIRST_COL])
