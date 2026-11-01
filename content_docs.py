"""
PulseO: the text of a brief or a draft
======================================
The AI writes plain Markdown; this module turns it into the HTML of a Google Doc and makes the
rules that can be enforced in code true in the text, so they do not depend on the model:

  * Persian text: Persian ی and ک, Persian digits (a number inside a Latin word such as S24 stays),
    the font and the direction on every paragraph;
  * no em or en dash;
  * links only to addresses on a list, each once.

Pure functions, no network. Nothing here prints a text.
"""

import html as htmllib
import re

import content
import content_check as cc

# --------------------------------------------------------------------------- #
# The page around the text
# --------------------------------------------------------------------------- #

STYLE = {"fa": "body{font-family:'Vazirmatn',Tahoma,sans-serif;line-height:1.9}",
         "en": "body{font-family:Arial,sans-serif;line-height:1.6}"}
BLOCK_TAG = re.compile(r"<(p|h[1-6]|li|td|th)>")


def inline_style(html: str, lang: str, rtl: bool = True) -> str:
    """Puts the font (and, for a right-to-left document, the direction) on every paragraph, heading,
    list item and cell: Google keeps what is written on the element but does not always keep what is
    written once for the whole page."""
    font = "font-family:Vazirmatn" if lang == "fa" else "font-family:Arial"
    attrs = f' style="{font}"' + (' dir="rtl"' if (lang == "fa" and rtl) else "")
    return BLOCK_TAG.sub(lambda m: f"<{m.group(1)}{attrs}>", html)


def page_start(lang: str, rtl: bool = True) -> str:
    attrs = ' lang="fa" dir="rtl"' if (lang == "fa" and rtl) else (' lang="fa"' if lang == "fa" else ' lang="en"')
    return f"<html{attrs}><head><meta charset=\"utf-8\"><style>{STYLE[lang]}</style></head><body>"


def wrap(lang: str, body: str, rtl: bool = True) -> str:
    """A whole HTML page around the body, with the font and direction on every block."""
    return inline_style(page_start(lang, rtl) + body + "</body></html>", lang, rtl)


def header_box(rows: list) -> str:
    """The table for the editor at the top of a draft: [(label, value)]."""
    cells = "".join(f"<tr><td>{htmllib.escape(str(k))}</td><td>{htmllib.escape(str(v))}</td></tr>" for k, v in rows)
    return f'<table id="header-box">{cells}</table>'


# --------------------------------------------------------------------------- #
# The headings of a brief and of a draft, in the site's language
# --------------------------------------------------------------------------- #

BRIEF_HEADINGS = {
    "en": ["Task", "Keywords, intent and reader", "Title tag, meta description, H1 and slug", "Outline",
           "Entities, facts and sources", "FAQ", "Internal links", "Word count, format and tone",
           "Keep, remove, rewrite", "Writer checklist"],
    "fa": ["وظیفه، نوع مقاله و هدف", "کلمات کلیدی، نیت جستجو و مخاطب",
           "تگ عنوان، متا دیسکریپشن، اچ یک و اسلاگ", "ساختار مقاله", "موجودیت‌ها، واقعیت‌ها و منابع",
           "پرسش‌های متداول", "لینک‌های داخلی", "تعداد کلمات، قالب و لحن", "حفظ، حذف و بازنویسی",
           "چک‌لیست نویسنده"],
}
UPDATE_ONLY_SECTION = 9                      # the ninth section is only for a page that already exists
DRAFT_HEADINGS = {
    "en": {"faq": "Frequently asked questions", "summary": "Summary", "quality": "Quality check",
           "changes": "Changes from the current version"},
    "fa": {"faq": "پرسش‌های متداول", "summary": "جمع‌بندی", "quality": "بررسی کیفیت",
           "changes": "تغییرات نسبت به نسخه فعلی"},
}
HEADER_LABELS = {
    "en": ["Meta title", "Meta description", "Slug", "Article type", "Keywords", "Word count"],
    "fa": ["عنوان متا", "توضیح متا", "اسلاگ", "نوع مقاله", "کلمات کلیدی", "تعداد کلمات"],
}


# --------------------------------------------------------------------------- #
# Persian
# --------------------------------------------------------------------------- #

_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩",
                               "۰۱۲۳۴۵۶۷۸۹")
_LATIN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_DIGIT_RUN = re.compile(r"(?<![A-Za-z0-9])[0-9]+(?![A-Za-z0-9])")      # a whole run of digits, not glued to a Latin letter
_UNTOUCHED = re.compile(r"\]\([^)\n]*\)|https?://\S+|www\.\S+")           # link targets and addresses
_SLOT = re.compile(r"\[[^\]\n]*\|[^\]\n]*\|[^\]\n]*\]")                   # [image: ... | alt | file name]


def _fa_text(text: str) -> str:
    text = text.translate(_LETTERS).translate(_ARABIC_DIGITS)
    return _DIGIT_RUN.sub(lambda m: m.group(0).translate(_LATIN_TO_FA), text)


JALALI_MONTHS = ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar", "Mehr", "Aban", "Azar", "Dey",
                 "Bahman", "Esfand"]


def to_jalali(d) -> tuple:
    """(year, month, day) of a date in the Persian calendar."""
    gy, gm, gd = d.year, d.month, d.day
    before = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + before[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def fa_letters(text: str) -> str:
    return text.translate(_LETTERS)


def normalize_fa(text: str) -> str:
    """Persian ی and ک, Persian digits. A number glued to a Latin letter (S24, 5G), an address and a link
    target stay as they are; an image slot only gets its letters fixed (its file name stays Latin)."""
    out, last = [], 0
    for m in re.finditer(f"{_UNTOUCHED.pattern}|{_SLOT.pattern}", text):
        out.append(_fa_text(text[last:m.start()]))
        piece = m.group(0)
        out.append(fa_letters(piece) if piece.startswith("[") and "](" not in piece else piece)
        last = m.end()
    out.append(_fa_text(text[last:]))
    return "".join(out)


_DIGITS = "0-9۰-۹٠-٩"


def strip_dashes(text: str, lang: str = "en") -> str:
    """No em or en dash: between numbers a hyphen, elsewhere a comma."""
    text = re.sub(f"(?<=[{_DIGITS}])\\s*[–—]\\s*(?=[{_DIGITS}])", "-", text)
    return re.sub(r"\s*[–—]\s*", "، " if lang == "fa" else ", ", text)


def clean_text(text: str, lang: str) -> str:
    """The rules that can be made true in code, applied to Markdown (a backslash before a bracket, which
    some models add, is taken away so that links, markers and image slots still read as such)."""
    text = strip_dashes(text.replace("\\[", "[").replace("\\]", "]"), lang)
    return normalize_fa(text) if lang == "fa" else text


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*•])\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(?:\d+|[۰-۹]+|[٠-٩]+)[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def _safe_href(url: str) -> str:
    url = htmllib.unescape(url)
    return htmllib.escape(url, quote=True) if re.match(r"^(https?://|/|#)", url) else "#"


def inline(text: str) -> str:
    """Bold and links of one line; everything else is text."""
    out = htmllib.escape(text, quote=False)
    out = _LINK.sub(lambda m: f'<a href="{_safe_href(m.group(2))}">{m.group(1)}</a>', out)
    return _BOLD.sub(r"<b>\1</b>", out)


def _starts_block(line: str) -> bool:
    s = line.strip()
    return bool(_HEADING.match(s) or s.startswith("|") or _BULLET.match(line) or _NUMBERED.match(line)
                or _RULE.match(line))


def md_to_html(md: str) -> str:
    """Headings, paragraphs, bullet and numbered lists, tables, bold and links."""
    lines = md.replace("\r", "").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s or _RULE.match(line):
            i += 1
            continue
        h = _HEADING.match(s)
        if h:
            n = len(h.group(1))
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
            i += 1
        elif s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                out.append(f"<table><tr>{head}</tr>{body}</table>")
        elif _BULLET.match(line) or _NUMBERED.match(line):
            ordered = bool(_NUMBERED.match(line)) and not _BULLET.match(line)
            items = []
            while i < len(lines) and (_NUMBERED.match(lines[i]) if ordered else _BULLET.match(lines[i])):
                items.append((_NUMBERED if ordered else _BULLET).match(lines[i]).group(1))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
        else:
            para = [s]
            i += 1
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return "".join(out)


def links_in(md: str) -> list:
    """[(anchor text, address)] of the Markdown links."""
    return [(m.group(1), m.group(2)) for m in _LINK.finditer(md)]


def repair_links(md: str, allowed: list, domain: str, low: int = cc.MIN_LINKS, high: int = cc.MAX_LINKS) -> str:
    """Makes the internal links of an article right: a link to an address that is not on the list
    (`allowed`: [(keyword, address)]) or to one already used loses its link (the words stay); more than
    `high` lose theirs from the last on; fewer than `low` are made up to it with a line of related
    reading before the FAQ. Links to other sites are left alone."""
    allowed_keys = {content.url_key(u): (kw, u) for kw, u in allowed}
    site = cc._host(domain)
    used = []

    def swap(m):
        anchor, url = m.group(1), m.group(2)
        internal = (not cc._host(url) or cc._host(url) == site) and not url.startswith("#")
        if not internal:
            return m.group(0)
        key = content.url_key(url)
        if key not in allowed_keys or key in used or len(used) >= high:
            return anchor
        used.append(key)
        return m.group(0)
    out = _LINK.sub(swap, md)
    missing = [(kw, u) for k, (kw, u) in allowed_keys.items() if k not in used][:max(0, low - len(used))]
    if len(used) < low and missing:
        line = " ".join(f"[{kw}]({u})" for kw, u in missing)
        out = _insert_before_faq(out, line)
    return out


def _insert_before_faq(md: str, line: str) -> str:
    lines = md.split("\n")
    for idx, ln in enumerate(lines):
        h = _HEADING.match(ln.strip())
        if h and len(h.group(1)) == 2 and cc.FAQ_HEADING.search(h.group(2)):
            return "\n".join(lines[:idx] + [line, ""] + lines[idx:])
    return md.rstrip() + "\n\n" + line + "\n"


# --------------------------------------------------------------------------- #
# What the AI answers with
# --------------------------------------------------------------------------- #

_SECTION_MARK = re.compile(r"^\s*={3,}\s*([A-Za-z0-9 _-]+?)\s*={3,}\s*$", re.MULTILINE)
_FIELD = re.compile(r"^\s*(?:[-*]\s*)?\**([A-Za-z][A-Za-z0-9 ]{0,30}?)\**\s*:\s*\**(.+?)\**\s*$", re.MULTILINE)


def split_sections(text: str) -> dict:
    """{NAME: text} of the parts of an answer, cut at lines like `=== 3 ===` or `=== META ===`."""
    marks = list(_SECTION_MARK.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.setdefault(m.group(1).strip().upper(), text[m.end():end].strip())
    return out


def parse_fields(text: str) -> dict:
    """{key in lower case: value} of the `Key: value` lines of a block; the first of a key wins."""
    out = {}
    for m in _FIELD.finditer(text):
        out.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    return out


def html_to_text(html: str) -> str:
    """The text of a document as lines; a heading gets # marks. Used to give a brief to the AI."""
    doc = cc.Doc(html)
    lines = []
    for b in doc.blocks:
        if not b.text:
            continue
        lines.append(("#" * b.level + " " if b.level else ("- " if b.tag == "li" else "")) + b.text)
    return "\n".join(lines)


def sections_of_brief(html: str) -> dict:
    """{section number 1..10: text} of a brief, found by its headings."""
    doc = cc.Doc(html)
    out, current = {}, None
    for b in doc.blocks:
        if b.level == 2:
            current = None
            for n, (_, patterns) in enumerate(cc.BRIEF_SECTIONS, start=1):
                if any(re.search(p, b.text, re.IGNORECASE) for p in patterns):
                    current = n
                    break
            continue
        if current and b.text:
            out[current] = (out.get(current, "") + "\n" + b.text).strip()
    return out
