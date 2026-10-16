"""
GSC Pulse: report periods
=========================
Calendar months and Monday-to-Sunday (ISO) weeks, and which of them are
*complete*: a period counts only when its last day is on or before the last
final date Search Console has, and when Search Console data covers all of it.
A partial month or week is never reported as if it were complete.
"""

from datetime import date, timedelta


def month_label(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def add_months(first: date, n: int) -> date:
    """First day of the month that lies n months from the month of `first`."""
    index = first.year * 12 + (first.month - 1) + n
    return date(index // 12, index % 12 + 1, 1)


def month_end(first: date) -> date:
    return add_months(first, 1) - timedelta(days=1)


def parse_month(label: str) -> date:
    """'2026-09' -> first day of that month. Raises ValueError for anything else."""
    year, month = label.split("-")
    if len(year) != 4 or len(month) != 2:
        raise ValueError(label)
    return date(int(year), int(month), 1)


def complete_months(earliest: date, final: date, first_data: date) -> list:
    """
    [(label, first day, last day)] oldest first. A month is complete when
      * its last day is on or before `final`,
      * it starts on or after `earliest` (Search Console still has all of it), and
      * the site already had data on its first day (`first_data`), so the first,
        partly empty month of a young site is not reported.
    """
    start = max(earliest, first_data)
    cur = date(start.year, start.month, 1)
    if cur < start:
        cur = add_months(cur, 1)
    out = []
    while month_end(cur) <= final:
        out.append((month_label(cur), cur, month_end(cur)))
        cur = add_months(cur, 1)
    return out


def complete_weeks(earliest: date, final: date, first_data: date) -> list:
    """[(label, Monday, Sunday)] oldest first, with the same rules as complete_months."""
    start = max(earliest, first_data)
    monday = start + timedelta(days=(7 - start.weekday()) % 7)
    out = []
    while monday + timedelta(days=6) <= final:
        out.append((week_label(monday), monday, monday + timedelta(days=6)))
        monday += timedelta(days=7)
    return out


def same_week_last_year(monday: date):
    """Monday of the same ISO week one year earlier, or None (week 53 has no twin)."""
    iso = monday.isocalendar()
    try:
        return date.fromisocalendar(iso[0] - 1, iso[1], 1)
    except ValueError:
        return None
