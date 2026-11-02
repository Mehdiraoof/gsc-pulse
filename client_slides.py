"""
PulseO: the owner's Claude plan chooses the slides of the client's monthly deck
==============================================================================
The sync run lists every slide the monthly deck could have (id, title, one line about its data) in the Input of
the AI Notes slot `client_slides`. The task answers with the ids it wants, in order, one line of reason each. The
deck is built from that list only. The cover slide and the "Our work and its results" slide(s) are always kept. A
slot that is still waiting, or an answer that cannot be used, gives today's full deck. The weekly deck is never
touched. Nothing here calls an AI.
"""

import re

import client_i18n

ALWAYS = ("work", "work2")            # the cover is not a listed slide: it is always the first slide
MIN_CHOSEN = 3                        # fewer usable ids than this: the full deck (a one-slide report is a mistake)
SUMMARY_LENGTH = 150

TITLE_KEYS = {"headline": "c_t_headline", "scorecard": "c_t_score", "climbs": "c_t_climb", "grew": "c_t_grew",
              "new": "c_t_new", "work": "c_t_ourwork", "work2": "c_t_ourwork", "trend": "c_t_trend",
              "sections": "c_t_sections", "wins": "c_t_wins", "pages": "c_t_pages", "ideas": "c_t_ideas",
              "next": "c_t_next", "watch": "c_t_watch", "focus": "c_t_focus"}


def _n(d, key) -> int:
    value = d.get(key) if isinstance(d, dict) else None
    return len(value) if isinstance(value, (list, tuple)) else 0


def _cut(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= SUMMARY_LENGTH else text[:SUMMARY_LENGTH - 1].rstrip() + "…"


def summary(sid: str, d: dict) -> str:
    """One line about the data of a slide: counts and the slide's own first words, never invented."""
    d = d if isinstance(d, dict) else {}
    if sid == "headline":
        return _cut(d.get("headline", ""))
    if sid == "scorecard":
        return _cut("; ".join(f"{c.get('label', '')} {c.get('value', '')}" for c in d.get("cards", [])))
    if sid == "climbs":
        card = d.get("card") or {}
        return _cut(f"{_n(d, 'rows')} rankings that climbed" +
                    (f"; average position {card.get('from')} → {card.get('to')}" if card else ""))
    if sid == "grew":
        return _cut(f"{_n(d, 'keywords')} searches and {_n(d, 'pages')} pages that gained clicks vs last year")
    if sid == "new":
        top = (d.get("cards") or [{}])[0].get("keyword", "")
        return _cut(f"{_n(d, 'cards')} new searches finding the site" + (f"; top: {top}" if top else ""))
    if sid in ALWAYS:
        return _cut(f"{_n(d, 'rows')} pieces of work with clicks and position before and after")
    if sid == "trend":
        labels = d.get("labels") or []
        return _cut(f"clicks by month, {labels[0]} to {labels[-1]}" if labels else d.get("title", ""))
    if sid == "sections":
        return _cut(f"{_n(d, 'rows')} sections of the site with clicks and change")
    if sid == "wins":
        return _cut(f"{_n(d, 'rows')} searches close to the top results")
    if sid == "pages":
        return _cut(f"{_n(d, 'rows')} pages worth updating")
    if sid == "ideas":
        return _cut(f"{_n(d, 'cards')} content ideas from what people search")
    if sid == "next":
        return _cut(d.get("title", ""))
    if sid == "watch":
        return _cut(f"{_n(d, 'items')} things being watched, no action yet")
    if sid == "focus":
        return _cut(f"{_n(d, 'cards')} priorities for next month")
    return ""


def title(sid: str, d: dict) -> str:
    """The English title of the slide (the Input is read in English whatever the language of the deck)."""
    T = client_i18n.table("en")
    text = T.get(TITLE_KEYS.get(sid, ""), sid)
    if isinstance(d, dict) and d.get("title") and sid in ("next", "trend"):
        return str(d["title"])
    return re.sub(r"\{[^}]*\}", "…", text)


def listing(slides: list) -> list:
    """[(id, title, summary)] of the slides the deck could have, in the deck's own order."""
    return [(sid, title(sid, payload), summary(sid, payload)) for sid, payload in slides]


def input_text(label: str, period_title: str, lang: str, slides: list) -> str:
    rows = "\n".join(f"{sid} | {t} | {s}" for sid, t, s in listing(slides))
    return (f"TASK: choose the slides of the monthly SEO report that the client of {label} will read for "
            f"{period_title}. The reader is not an SEO expert. Keep what is clear, useful and new this month; leave "
            f"out what is repeated, weak or confusing this month.\n"
            f"ALWAYS KEPT: the cover slide and the slide(s) with id work or work2 (\"Our work and its results\") are in "
            f"the deck whatever you answer. You may place them; if you do not, they stay where they are.\n"
            f"RULES: use only the ids below, each at most once, in the order you want them. Never mention a tool, a "
            f"program or artificial intelligence, and never use the letters AI. No dashes in the reasons (use a comma "
            f"or a colon). Do not invent anything: you only choose.\n"
            f"FORMAT: one line per slide you want, exactly: id | one-line reason (in "
            f"{'Persian (Farsi)' if lang == 'fa' else 'English'}). Nothing else.\n\n"
            f"SLIDES (id | title | what its data shows)\n{rows}")


_LINE = re.compile(r"^\s*(?:[-*•●]|\d+[.)])?\s*`?([a-z][a-z0-9_]*)`?\s*(?:[|:：]|[-–—]\s)\s*(.*?)\s*$")


def parse(text: str, known) -> list:
    """[(id, reason)] in the order given, only ids that exist, each once. Lines look like `id | reason`
    (`id: reason` and `id - reason` also work); a JSON {"slides": [{"id", "reason"}]} is understood too."""
    text = str(text or "").strip()
    known = set(known)
    out, seen = [], set()

    def add(sid, reason):
        sid = str(sid).strip().lower()
        if sid in known and sid not in seen:
            seen.add(sid)
            out.append((sid, str(reason or "").strip()))

    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if m:
        import json
        try:
            data = json.loads(m.group(0))
            items = data.get("slides", []) if isinstance(data, dict) else data
            for it in items:
                if isinstance(it, dict):
                    add(it.get("id", ""), it.get("reason", ""))
                elif isinstance(it, str):
                    add(it, "")
        except (ValueError, AttributeError):
            pass
        if out:
            return out
    for line in text.splitlines():
        hit = _LINE.match(line)
        if hit:
            add(hit.group(1), hit.group(2))
    return out


def choose(text: str, available: list, log=print) -> list:
    """The ids of the deck, in order, or [] to build today's full deck. `available` is the deck's own order."""
    parsed = parse(text, available)
    if len(parsed) < MIN_CHOSEN:
        log(f"decks: monthly slide choice not used ({len(parsed)} usable ids); the full deck is built")
        return []
    order = [sid for sid, _ in parsed]
    for sid in ALWAYS:                                  # the work slides are never dropped
        if sid in available and sid not in order:
            before = [s for s in available[:available.index(sid)] if s in order]
            order.insert(order.index(before[-1]) + 1 if before else 0, sid)
    log(f"decks: monthly slides chosen: {len(order)} of {len(available)} (the reasons are in AI Notes)")
    return order
