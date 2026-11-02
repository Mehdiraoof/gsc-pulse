"""
PulseO: "Our work and its results" as rows of text (client slide and weekly section)
=================================================================================
Turns measured work items (work_items.py) into the cells of a slide table, in the language of the deck.
No tool name and no mention of AI is ever put into a row; manual items carry the owner's own words.
"""

import i18n
import work_items as wi

CLIENT_ROWS_PER_SLIDE = wi.MAX_CLIENT_ITEMS
OWNER_ROWS = 10


def _arrow(lang: str) -> str:
    return "\u2190" if lang == "fa" else "\u2192"          # the arrow points from before to after, also right to left


def f1(x) -> str:
    return f"{x:.1f}"


def when(day, T, lang) -> str:
    return i18n.digits(f"{day.day} {T['months_short'][day.month - 1]}", lang)


def describe(item, T, short) -> str:
    if item.kind == wi.MANUAL:
        return item.what
    return T["w_new" if item.kind == wi.NEW else "w_updated"].format(page=short(item.title))


def _clicks(item, T, lang) -> str:
    imp = item.impact
    before = T["w_new_before"] if item.kind == wi.NEW and not imp.before.clicks else i18n.fmt_int(imp.before.clicks)
    return i18n.digits(f"{before} {_arrow(lang)} {i18n.fmt_int(imp.after.clicks)}", lang)


def _position(item, lang) -> str:
    imp = item.impact
    if imp.after.position is None:
        return ""
    if imp.before.position is None:
        return i18n.digits(f1(imp.after.position), lang)
    return i18n.digits(f"{f1(imp.before.position)} {_arrow(lang)} {f1(imp.after.position)}", lang)


def _versus(item, T, lang) -> str:
    imp = item.impact
    parts = []
    if imp.change is not None:
        parts.append(i18n.fmt_signed(imp.change * 100, 0, pct=True))
    if imp.site_change is not None:
        parts.append(T["w_site"].format(pct=i18n.fmt_signed(imp.site_change * 100, 0, pct=True)))
    return i18n.digits(" \u00b7 ".join(parts), lang)


def numbers_row(item, T, lang, short=lambda s: s) -> dict:
    """A row with the page's before and after (the weekly deck, and the client rows that show a result)."""
    what = describe(item, T, short)
    early = item.impact.status == wi.EARLY
    if early:
        what += f" ({T['w_early']})"
    return {"what": what, "date": when(item.day, T, lang), "clicks": _clicks(item, T, lang),
            "position": _position(item, lang), "versus": _versus(item, T, lang),
            "tone": "amber" if early else "green"}


def client_row(item, T, lang, short=lambda s: s) -> dict:
    """One row of the client slide. A row without a clear gain says only what was done and when: no number at all
    (the cells are empty), whatever the page did. The line that stands for other automatic items has no date."""
    if item.kind == wi.GROUP:
        return {"what": i18n.digits(T["w_group"].format(n=i18n.fmt_int(item.count)), lang), "date": "",
                "clicks": "", "position": "", "versus": "", "tone": "amber"}
    if item.impact is None or not item.numbers:
        return {"what": describe(item, T, short), "date": when(item.day, T, lang), "clicks": "", "position": "",
                "versus": "", "tone": "amber"}
    return numbers_row(item, T, lang, short)


def owner_row(item, T, lang, short=lambda s: s) -> dict:
    row = numbers_row(item, T, lang, short)
    status = {wi.GOOD: "w_s_good", wi.FLAT: "w_s_flat", wi.EARLY: "w_s_early"}.get(item.impact.status, "w_s_nodata")
    row["status"] = T[status] + ("" if item.show else f" · {T['w_hidden']}")
    row["tone"] = {"w_s_good": "green", "w_s_early": "amber"}.get(status, "red")
    return row


def client_pages(items: list, T, lang, intro: str, short=lambda s: s) -> list:
    """The client slide(s): [] when there is nothing to show, else one or two payloads of up to CLIENT_ROWS_PER_SLIDE
    rows (a long list is split in halves)."""
    if not items:
        return []
    rows = [client_row(it, T, lang, short) for it in items]
    if len(rows) <= CLIENT_ROWS_PER_SLIDE:
        parts = [rows]
    elif len(rows) <= CLIENT_ROWS_PER_SLIDE * wi.MAX_CLIENT_SLIDES:
        parts = [rows[:(len(rows) + 1) // 2], rows[(len(rows) + 1) // 2:]]
    else:                                                  # only the owner's manual lines can get here: none is dropped
        parts = [rows[i:i + CLIENT_ROWS_PER_SLIDE] for i in range(0, len(rows), CLIENT_ROWS_PER_SLIDE)]
    header = [T["w_col_work"], T["w_col_date"], T["w_col_clicks"], T["w_col_pos"], T["w_col_site"]]
    out = []
    for i, part in enumerate(parts, start=1):
        title = T["c_t_ourwork"] + (f" ({i}/{len(parts)})" if len(parts) > 1 else "")
        out.append({"title": title, "intro": intro if i == 1 else "", "header": header, "rows": part})
    return out
