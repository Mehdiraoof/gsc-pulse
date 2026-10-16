"""
GSC Pulse: AI Overview (Generative AI report) imports
=====================================================
Search Console has no API for the Generative AI report. Once a month the report
is exported to Google Sheets and its `Pages` and `Dates` tabs are copied into the
site's Reports sheet as `AIO YYYY-MM Pages` and `AIO YYYY-MM Dates`.

Each run this module:
  1. finds such tabs (names starting with `AIO ` and ending in `Pages` or `Dates`),
  2. reads the month from the name and the numbers from the tab, matching columns
     by keywords and by what the values look like (the export may be localized),
  3. stores them in the `AIO Raw` tab (a re-imported month replaces the old one),
  4. deletes the import tab once its data is safely stored.

A tab that cannot be read is left untouched and reported (log + `R Alarms`).
Nothing is ever skipped silently.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

import history
import periods
import tabs

TAB_NAME   = re.compile(r"AIO (\d{4})-(\d{2}) (Pages|Dates)")
RAW_TAB    = "AIO Raw"
RAW_HEADER = ["Period", "Kind", "Date or Page", "AIO Impressions"]

IMPR_WORDS = ("impression", "impr", "نمایش", "نمود", "ایمپرشن")
URL_WORDS  = ("page", "url", "صفحه", "نشانی", "آدرس")
DATE_WORDS = ("date", "day", "تاریخ", "روز")

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
                 "%m/%d/%Y", "%d.%m.%Y")


class AioImportError(Exception):
    """A tab could not be read. The message tells the user what to look at."""


@dataclass
class ImportResult:
    months: dict = field(default_factory=dict)      # label -> {"pages": n, "days": m}
    problems: list = field(default_factory=list)    # [(period, message)]
    changed: set = field(default_factory=set)       # month labels whose AIO data changed


# --------------------------------------------------------------------------- #
# Reading numbers, dates and tables defensively
# --------------------------------------------------------------------------- #

def _clean(cell) -> str:
    return str(cell).translate(_DIGITS).replace("‌", "").replace("\xa0", " ").strip()


def parse_number(cell):
    """'1,234' / '۱٬۲۳۴' / '12.5' -> number; anything else -> None."""
    t = _clean(cell)
    for thousands in (",", "٬", "،", " "):
        t = t.replace(thousands, "")
    t = t.replace("٫", ".")
    return float(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def parse_date(cell):
    t = _clean(cell)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def _is_url(cell) -> bool:
    t = _clean(cell)
    return "://" in t or t.startswith("www.")


def _header_has(cell, words) -> bool:
    t = _clean(cell).lower()
    return any(w in t for w in words)


def _share(values, test) -> float:
    values = [v for v in values if _clean(v) != ""]
    return sum(1 for v in values if test(v)) / len(values) if values else 0.0


def _column(rows, i):
    return [r[i] if i < len(r) else "" for r in rows]


def _table(values: list):
    """(header cells or None, data rows) with empty rows dropped."""
    rows = [[_clean(c) for c in r] for r in values if any(_clean(c) != "" for c in r)]
    if not rows:
        raise AioImportError("the tab is empty")
    for i, row in enumerate(rows[:5]):
        if any(_header_has(c, IMPR_WORDS + URL_WORDS + DATE_WORDS) and not _is_url(c)
               and parse_number(c) is None and parse_date(c) is None for c in row):
            return row, rows[i + 1:]
    first = rows[0]
    if all(parse_number(c) is None and parse_date(c) is None and not _is_url(c) for c in first):
        return first, rows[1:]
    return None, rows


def _pick(header, data, words, looks_like, what):
    width = max(len(r) for r in data + ([header] if header else []))
    if header:
        named = [i for i, c in enumerate(header) if _header_has(c, words)]
        if len(named) == 1:
            return named[0]
    fits = [i for i in range(width) if _share(_column(data, i), looks_like) >= 0.8]
    if len(fits) == 1:
        return fits[0]
    raise AioImportError(f"could not tell which column holds the {what}"
                         f"{' (several columns fit)' if len(fits) > 1 else ''}; "
                         f"expected a column named like {words[0]!r}")


def _impressions_column(header, data, taken):
    width = max(len(r) for r in data)
    if header:
        named = [i for i, c in enumerate(header) if _header_has(c, IMPR_WORDS) and i not in taken]
        if len(named) == 1:
            return named[0]
    numeric = [i for i in range(width) if i not in taken
               and _share(_column(data, i), lambda v: parse_number(v) is not None) >= 0.8]
    if len(numeric) == 1:
        return numeric[0]
    raise AioImportError("could not tell which column holds the impressions "
                         "(expected a column named like 'Impressions')")


def parse_pages(values: list) -> tuple:
    """-> ({url: impressions}, number of rows skipped). #fragment variants are added up."""
    header, data = _table(values)
    key = _pick(header, data, URL_WORDS, _is_url, "page URLs")
    imp = _impressions_column(header, data, {key})
    out, skipped = {}, 0
    for r in data:
        url, n = (r[key] if key < len(r) else ""), parse_number(r[imp] if imp < len(r) else "")
        if not _is_url(url) or n is None:
            skipped += 1
            continue
        url = urlsplit(url)._replace(fragment="").geturl()
        out[url] = out.get(url, 0) + int(round(n))
    if not out:
        raise AioImportError("no row with a page URL and an impressions number was found")
    return out, skipped


def parse_dates(values: list, month: date) -> tuple:
    """-> ({date: impressions}, number of rows skipped). Dates must lie in `month`."""
    header, data = _table(values)
    key = _pick(header, data, DATE_WORDS, lambda v: parse_date(v) is not None, "dates")
    imp = _impressions_column(header, data, {key})
    out, skipped = {}, 0
    for r in data:
        d = parse_date(r[key] if key < len(r) else "")
        n = parse_number(r[imp] if imp < len(r) else "")
        if d is None or n is None:
            skipped += 1
            continue
        out[d] = out.get(d, 0) + int(round(n))
    if not out:
        raise AioImportError("no row with a date and an impressions number was found")
    if any(periods.month_label(d) != periods.month_label(month) for d in out):
        raise AioImportError("the dates in the tab are not all in the month named in the tab "
                             "name (check the month in the name)")
    return out, skipped


# --------------------------------------------------------------------------- #
# AIO Raw
# --------------------------------------------------------------------------- #

def load_raw(sheets, reports_id: str):
    """-> (daily {date: impressions}, pages {month label: {url: impressions}})"""
    daily, pages = {}, {}
    for row in tabs.read_rows(sheets, reports_id, RAW_TAB, "D"):
        if len(row) < 4:
            continue
        period, kind, key, value = row[0], row[1], row[2], row[3]
        try:
            n = int(round(float(value)))
        except ValueError:
            continue
        if kind == "date":
            try:
                daily[datetime.strptime(key, "%Y-%m-%d").date()] = n
            except ValueError:
                continue
        elif kind == "page":
            pages.setdefault(period, {})[key] = n
    return daily, pages


# --------------------------------------------------------------------------- #
# The import
# --------------------------------------------------------------------------- #

def run_import(sheets, reports_id: str, today: date, log=print) -> ImportResult:
    result = ImportResult()
    titles, _ = history.grid_info(sheets, reports_id)
    here = periods.month_label(today)

    found = {}      # (label, kind) -> tab title
    for title in sorted(titles):
        if not (title.startswith("AIO ") and title.endswith(("Pages", "Dates"))):
            continue
        m = TAB_NAME.fullmatch(title)
        if not m or not 1 <= int(m.group(2)) <= 12:
            result.problems.append((here, f"The tab \"{title}\" was not imported: its name must "
                                          f"look like \"AIO 2026-09 Pages\" or \"AIO 2026-09 "
                                          f"Dates\". The tab was left untouched."))
            continue
        found[(f"{m.group(1)}-{m.group(2)}", m.group(3))] = title

    parsed = {}     # (label, kind) -> {key: impressions}
    for (label, kind), title in sorted(found.items()):
        try:
            values = tabs.read_tab(sheets, reports_id, title)
            if kind == "Pages":
                data, skipped = parse_pages(values)
            else:
                data, skipped = parse_dates(values, periods.parse_month(label))
        except AioImportError as e:
            result.problems.append((label, f"The tab \"{title}\" was not imported: {e}. "
                                           f"The tab was left untouched."))
            continue
        parsed[(label, kind)] = data
        if skipped:
            log(f"  AIO {label}: {skipped} rows of the {kind} tab were not data rows and were skipped")

    if parsed:
        raw = [[r[0], r[1], r[2], tabs.number(r[3])]
               for r in tabs.read_rows(sheets, reports_id, RAW_TAB, "D") if len(r) >= 4]
        replaced = {(label, "date" if kind == "Dates" else "page") for label, kind in parsed}
        raw = [r for r in raw if (r[0], r[1]) not in replaced]
        for (label, kind), data in sorted(parsed.items()):
            for key, n in sorted(data.items(), key=lambda kv: str(kv[0])):
                if kind == "Dates":
                    raw.append([label, "date", key.isoformat(), n])
                else:
                    raw.append([label, "page", key, n])
        tabs.rewrite(sheets, reports_id, RAW_TAB, RAW_HEADER, raw)   # stored first ...
        tabs.delete_tabs(sheets, reports_id,                         # ... then the import tabs go
                         [titles[found[k]] for k in parsed if titles.get(found[k]) is not None])
        for (label, kind), data in sorted(parsed.items()):
            m = result.months.setdefault(label, {"pages": 0, "days": 0})
            m["pages" if kind == "Pages" else "days"] = len(data)
            result.changed.add(label)
        for label, m in sorted(result.months.items()):
            log(f"  AIO {label}: imported {m['pages']} pages, {m['days']} days")

    if result.problems:
        log(f"  AIO import: {len(result.problems)} tab(s) could not be imported (see R Alarms)")
    return result
