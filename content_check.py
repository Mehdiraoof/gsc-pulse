"""
PulseO: the automatic checker for content creation (briefs and drafts)
======================================================================
Content creation writes Google Docs (briefs, drafts) and changes a few cells of a content dashboard.
This module decides, without a person reading anything, whether a run did what the "done" checklist
asks. It has three kinds of checks:

  * documents: `check_brief` and `check_draft` read the HTML of a doc (the HTML PulseO wrote, or
    what Google exports from the doc) and test the structure, the language rules and the banned
    words (checklist items 1, 5, 6, 7 and 8);
  * the dashboard: `diff_dashboard` compares the rows before and after a run and finds every cell
    that changed although it should not have (item 9);
  * the rest of a run: `check_emails` (item 10) and `check_no_new_docs` (item 11).

Every check returns a list of Failure(item, code, where, detail). A Failure never holds a piece of
the document, a keyword or an address: only the checklist item, a short code, a row number or a
count. A report can therefore be printed in a public log even when the documents are real.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import content
import tabs

# --------------------------------------------------------------------------- #
# What a failure looks like
# --------------------------------------------------------------------------- #


@dataclass
class Failure:
    item: int            # the number in the "done" checklist
    code: str            # a short word: "two-h1", "no-table", "arabic-letter" ...
    where: str = ""      # "doc", "row 7", "column Status" ... never content
    detail: str = ""     # counts only

    def text(self) -> str:
        return f"item {self.item} {self.code}" + (f" ({self.where})" if self.where else "") + \
            (f": {self.detail}" if self.detail else "")


@dataclass
class Report:
    failures: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def add(self, failures):
        self.failures.extend(failures)
        return self

    def codes(self) -> set:
        return {f.code for f in self.failures}

    def items(self) -> list:
        return sorted({f.item for f in self.failures})

    def text(self) -> str:
        if self.ok:
            return "all checks passed"
        return f"{len(self.failures)} checks failed: " + "; ".join(f.text() for f in self.failures)


# --------------------------------------------------------------------------- #
# Reading a document
# --------------------------------------------------------------------------- #

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_TEXT_BLOCKS = set(_HEADINGS) | {"p", "li", "td", "th", "caption"}
_VOID = {"br", "img", "meta", "link", "hr", "input"}
_DIR_STYLE = re.compile(r"direction\s*:\s*(rtl|ltr)", re.IGNORECASE)


@dataclass
class Block:
    tag: str
    text: str
    dir: str            # "rtl", "ltr" or "" (nothing says)
    in_box: bool        # inside the header box for the editor
    level: int = 0      # 1 to 6 for a heading


def real_href(href: str) -> str:
    """Google wraps every link of an exported document in its own redirect address
    (https://www.google.com/url?q=<the address>&...): the address behind it."""
    if href.startswith(("https://www.google.com/url?", "http://www.google.com/url?")):
        q = parse_qs(urlsplit(href).query).get("q")
        if q:
            return q[0]
    return href


class Doc(HTMLParser):
    """The parts of an HTML document the checks need. The header box for the editor is the table
    that comes before the first heading (Google drops the id a document is made with)."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.blocks, self.links, self.lists, self.styles = [], [], [], []
        self.tables = 0
        self.full_text = ""
        self._stack = []          # [tag, dir, in_box]
        self._block = None        # [tag, parts, dir, in_box] of the open text block
        self._lists = []          # counters of the open lists
        self._skip = 0            # inside <style>, <script>, <title>
        self._style = False
        self._seen_heading = False
        self._box_done = False
        self.feed(html or "")
        self.close()

    # ---- parser events ------------------------------------------------------------
    def _own_dir(self, attrs: dict) -> str:
        d = (attrs.get("dir") or "").lower()
        if d in ("rtl", "ltr"):
            return d
        m = _DIR_STYLE.search(attrs.get("style") or "")
        return m.group(1).lower() if m else ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("style"):
            self.styles.append(attrs["style"])
        if tag in ("style", "script", "title"):
            self._skip += 1
            self._style = tag == "style"
            return
        if tag == "br":
            self._text(" ")
            return
        if tag in _VOID:
            return
        parent_dir = self._stack[-1][1] if self._stack else ""
        in_box = (self._stack[-1][2] if self._stack else False) or attrs.get("id") == "header-box"
        if tag in _HEADINGS:
            self._seen_heading = True
        if tag == "table" and not self._seen_heading and not self._box_done:
            in_box = self._box_done = True
        self._stack.append([tag, self._own_dir(attrs) or parent_dir, in_box])
        if tag == "table" and not in_box:
            self.tables += 1
        if tag in ("ul", "ol"):
            self._lists.append(0)
        if tag == "li" and self._lists:
            self._lists[-1] += 1
        if tag == "a" and attrs.get("href"):
            self.links.append((real_href(attrs["href"]), in_box))
        if tag in _TEXT_BLOCKS:
            self._block = [tag, [], self._stack[-1][1], in_box]

    def handle_endtag(self, tag):
        if tag in ("style", "script", "title"):
            self._skip = max(0, self._skip - 1)
            self._style = False
            return
        if tag in _VOID:
            return
        if tag in _TEXT_BLOCKS or tag in ("div", "tr", "table", "ul", "ol"):
            self.full_text += "\n"
        if tag in _TEXT_BLOCKS and self._block and self._block[0] == tag:
            text = " ".join("".join(self._block[1]).split())
            self.blocks.append(Block(tag, text, self._block[2], self._block[3], _HEADINGS.get(tag, 0)))
            self._block = None
        if tag in ("ul", "ol") and self._lists:
            self.lists.append(self._lists.pop())
        for i in range(len(self._stack) - 1, -1, -1):          # close back to the matching open tag
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._skip:
            if self._style:
                self.styles.append(data)
            return
        self._text(data)

    def _text(self, data: str):
        self.full_text += data
        if self._block is not None:
            self._block[1].append(data)

    # ---- what the checks ask ---------------------------------------------------------
    def headings(self, level: int = 0) -> list:
        return [b for b in self.blocks if b.level and (not level or b.level == level)]

    def style_text(self) -> str:
        return " ".join(self.styles)


# --------------------------------------------------------------------------- #
# Words that must never be in a document
# --------------------------------------------------------------------------- #

BANNED = re.compile(r"pulseo|claude|gemini|chatgpt|openai", re.IGNORECASE)
AI_WORD = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")
DASHES = ("—", "–")                                # em dash, en dash


def check_banned(doc: Doc) -> list:
    """Checklist item 6: no tool name, no 'AI' or 'Claude', no em or en dash."""
    out = []
    text = doc.full_text
    if BANNED.search(text) or AI_WORD.search(text):
        out.append(Failure(6, "tool-name", "doc"))
    n = sum(text.count(d) for d in DASHES)
    if n:
        out.append(Failure(6, "dash", "doc", f"{n} found"))
    return out


# --------------------------------------------------------------------------- #
# Persian
# --------------------------------------------------------------------------- #

ARABIC_LETTERS = "يكى"                        # ي ك ى: Arabic forms of ی and ک
LATIN_DIGITS = re.compile(r"[0-9]")
ARABIC_DIGITS = re.compile(r"[٠-٩]")
PERSIAN_LETTER = re.compile(r"[؀-ۿ]")
LATIN_LETTER = re.compile(r"[A-Za-z]")
IMAGE_SLOT = re.compile(r"\[\s*(?:عکس|تصویر|Image|Photo)\s*:[^\]|]*\|[^\]|]*\|[^\]]*\]", re.IGNORECASE)
URL_LIKE = re.compile(r"https?://\S+|www\.\S+")


def _prose_blocks(doc: Doc) -> list:
    return [b for b in doc.blocks if not b.in_box and b.text]


def check_persian(doc: Doc) -> list:
    """Checklist item 7: no Arabic ي/ك, Persian digits only (a number inside a Latin word such as
    S24 stays Latin), every Persian paragraph right to left, the font Vazirmatn."""
    out = []
    if any(ch in doc.full_text for ch in ARABIC_LETTERS):
        n = sum(doc.full_text.count(ch) for ch in ARABIC_LETTERS)
        out.append(Failure(7, "arabic-letter", "doc", f"{n} found"))
    latin = arabic = 0
    for b in _prose_blocks(doc):
        text = IMAGE_SLOT.sub(" ", URL_LIKE.sub(" ", b.text))
        for token in text.split():
            if LATIN_LETTER.search(token):
                continue
            latin += len(LATIN_DIGITS.findall(token))
        arabic += len(ARABIC_DIGITS.findall(text))
    if latin:
        out.append(Failure(7, "latin-digit", "doc", f"{latin} found"))
    if arabic:
        out.append(Failure(7, "arabic-digit", "doc", f"{arabic} found"))
    wrong = [b for b in _prose_blocks(doc) if PERSIAN_LETTER.search(b.text) and b.dir != "rtl"]
    if wrong:
        out.append(Failure(7, "not-rtl", "doc", f"{len(wrong)} paragraphs"))
    if "vazirmatn" not in doc.style_text().lower():
        out.append(Failure(7, "font", "doc"))
    return out


# --------------------------------------------------------------------------- #
# Numbers in titles
# --------------------------------------------------------------------------- #

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
NON_TOPIC = re.compile(r"faq|frequently|summary|conclusion|quality|پرسش|سوال|جمع.?بندی|نتیجه|خلاصه|کنترل کیفیت|بررسی کیفیت",
                       re.IGNORECASE)


def check_counts(doc: Doc) -> list:
    """Checklist item 8: a number in the title ('۱۰ ایده', '10 ideas') matches the content: some
    list has exactly that many items, or that many sections (H2 or H3) carry the topic."""
    h1 = doc.headings(1)
    if not h1:
        return []
    m = re.search(r"(?<![\d.,])(\d{1,3})(?![\d.,])\s+\S+", h1[0].text.translate(_DIGITS))
    if not m:
        return []
    n = int(m.group(1))
    if n < 2:
        return []
    h2 = [b for b in doc.headings(2) if not NON_TOPIC.search(b.text)]
    h3 = [b for b in doc.headings(3) if not NON_TOPIC.search(b.text)]
    if n in doc.lists or len(h2) == n or len(h3) == n:
        return []
    return [Failure(8, "count-mismatch", "doc", f"title says {n}, lists {sorted(set(doc.lists))}, "
                                                f"h2 {len(h2)}, h3 {len(h3)}")]


# --------------------------------------------------------------------------- #
# Briefs
# --------------------------------------------------------------------------- #

# The ten sections of a brief, in order: (name, patterns that make a heading count for it). English or
# Persian headings both count. Section 9 is only for a page that already exists.
BRIEF_SECTIONS = [
    ("task", [r"\btask\b", r"وظیفه"]),
    ("keywords, intent and reader", [r"keyword", r"کلمات? کلیدی|کلمه.? کلیدی"]),
    ("title tag, meta description, H1 and slug", [r"title tag|meta description", r"تگ عنوان|متا"]),
    ("outline", [r"outline", r"ساختار|سرفصل"]),
    ("entities, facts and sources", [r"entit|facts|sources", r"موجودیت|واقعیت|منابع"]),
    ("faq", [r"faq|frequently", r"پرسش|سوال"]),
    ("internal links", [r"internal link", r"لینک.{0,6}داخلی|پیوند.{0,6}داخلی"]),
    ("word count, format and tone", [r"word count|tone", r"تعداد کلمات|لحن"]),
    ("keep, remove, rewrite", [r"keep|remove|rewrite", r"حفظ|حذف|بازنویسی"]),
    ("writer checklist", [r"checklist", r"چک.?لیست"]),
]
UPDATE_ONLY = {"keep, remove, rewrite"}


def check_brief(html: str, *, update: bool = False) -> list:
    """Checklist items 1 and 6 for a brief: the ten sections are there (the ninth only for a page that
    already exists), one H1 at most, and nothing banned."""
    doc = Doc(html)
    out = []
    heads = [b.text for b in doc.headings()]
    missing = []
    for name, patterns in BRIEF_SECTIONS:
        if name in UPDATE_ONLY and not update:
            continue
        if not any(re.search(p, h, re.IGNORECASE) for h in heads for p in patterns):
            missing.append(name)
    if missing:
        out.append(Failure(1, "missing-section", "doc", f"{len(missing)} missing"))
    if len(doc.headings(1)) > 1:
        out.append(Failure(1, "two-h1", "doc"))
    return out + check_banned(doc)

# --------------------------------------------------------------------------- #
# Drafts
# --------------------------------------------------------------------------- #

FAQ_HEADING = re.compile(r"faq|frequently asked|پرسش.{0,8}متداول|سوال.{0,8}متداول|سوالات", re.IGNORECASE)
SUMMARY_HEADING = re.compile(r"summary|conclusion|جمع.?بندی|نتیجه.?گیری|خلاصه", re.IGNORECASE)
QUALITY_HEADING = re.compile(r"quality check|بررسی کیفیت|کنترل کیفیت", re.IGNORECASE)
MIN_LINKS, MAX_LINKS = 3, 5
HUMAN_TOUCH = {"fa": re.compile(r"\[\s*(?:تجربه[\s\u200c]+شخصی|نظر[\s\u200c]+متخصص)\s*:[^\]]*\]"),
               "en": re.compile(r"\[\s*(?:Personal experience|Expert quote)\s*:[^\]]*\]", re.IGNORECASE)}
MIN_TOUCHES, MAX_TOUCHES = 2, 4


def _host(url: str) -> str:
    host, _ = content._split_url(url)
    return host


def check_draft(html: str, *, language: str, domain: str, has_comparison: bool) -> list:
    """Checklist items 5, 6, 7 and 8 for a draft. `domain` is the site's address (internal links
    are the ones to it, or relative); `has_comparison`: the brief has a comparison, a price or an
    option list, so the draft needs a table."""
    doc = Doc(html)
    out = []
    h1 = doc.headings(1)
    if len(h1) != 1:
        out.append(Failure(5, "h1-count", "doc", f"{len(h1)} found"))
    faq = [b for b in doc.headings() if FAQ_HEADING.search(b.text)]
    if not faq:
        out.append(Failure(5, "no-faq", "doc"))
    elif not any(b.level == 2 for b in faq):
        out.append(Failure(5, "faq-not-h2", "doc"))
    summary = [b for b in doc.headings() if SUMMARY_HEADING.search(b.text) and not FAQ_HEADING.search(b.text)]
    if not summary:
        out.append(Failure(5, "no-summary", "doc"))
    elif not any(b.level == 2 for b in summary):
        out.append(Failure(5, "summary-not-h2", "doc"))
    if has_comparison and doc.tables < 1:
        out.append(Failure(5, "no-table", "doc"))
    site = _host(domain)
    internal = [href for href, in_box in doc.links
                if not in_box and (not _host(href) or _host(href) == site) and not href.startswith("#")]
    keys = [content.url_key(h) for h in internal]
    if not MIN_LINKS <= len(internal) <= MAX_LINKS:
        out.append(Failure(5, "link-count", "doc", f"{len(internal)} internal links"))
    if len(set(keys)) != len(keys):
        out.append(Failure(5, "link-repeated", "doc", f"{len(keys) - len(set(keys))} repeated"))
    if not IMAGE_SLOT.search(doc.full_text):
        out.append(Failure(5, "no-image-slot", "doc"))
    touches = len(HUMAN_TOUCH.get(language, HUMAN_TOUCH["en"]).findall(doc.full_text))
    if not MIN_TOUCHES <= touches <= MAX_TOUCHES:
        out.append(Failure(5, "human-touch-count", "doc", f"{touches} found"))
    if not any(QUALITY_HEADING.search(b.text) for b in doc.headings()):
        out.append(Failure(5, "no-quality-check", "doc"))
    if not any(True for _ in doc.blocks if _.in_box):
        out.append(Failure(5, "no-header-box", "doc"))
    out += check_banned(doc)
    if language == "fa":
        out += check_persian(doc)
    out += check_counts(doc)
    return out


# --------------------------------------------------------------------------- #
# The dashboard before and after a run
# --------------------------------------------------------------------------- #

# Content creation may change only these cells, and only of the rows it works on.
ALLOWED_COLUMNS = {"Attachments", "Status", "To Do", "Briefed Date", "Notes/Checklist", "Meta Title",
                   "Outlines", "Slug", "Words Count (Suggested)", "Related URLs"}
FILL_ONLY_IF_EMPTY = {"Meta Title", "Outlines", "Slug", "Words Count (Suggested)", "Related URLs"}
APPEND_ONLY = {"Attachments", "Notes/Checklist"}


@dataclass
class Snapshot:
    header: list
    rows: list          # one dict per data row, by header name

    @staticmethod
    def of(values: list) -> "Snapshot":
        header, rows = [], []
        if values:
            header = [str(h) for h in values[0]]
            for raw in values[1:]:
                rows.append({h: (raw[i] if i < len(raw) else "") for i, h in enumerate(header) if h})
        return Snapshot(header, rows)


def _same(a, b) -> bool:
    return tabs.same(a, b) if (a != "" or b != "") else True


def _key(row: dict) -> str:
    return content.row_key(row)


def diff_dashboard(before: Snapshot, after: Snapshot, processed: set) -> list:
    """Checklist item 9. `processed`: row keys (content.row_key) of the rows the run worked on. Every
    other row must be exactly as it was; a processed row may change only ALLOWED_COLUMNS, a
    column in FILL_ONLY_IF_EMPTY only when it was empty, one in APPEND_ONLY only by adding to it; no
    row is added, removed or turned into another. Rows are matched by key, not by position."""
    out = []
    if before.header != after.header:
        out.append(Failure(9, "header-changed", "header"))
    after_by_key = {}
    for i, row in enumerate(after.rows, start=2):
        after_by_key.setdefault(_key(row), []).append((i, row))
    for i, row in enumerate(before.rows, start=2):
        k = _key(row)
        pool = after_by_key.get(k) or []
        if not pool:
            out.append(Failure(9, "row-lost", f"row {i}"))
            continue
        j, new = pool.pop(0)
        for column in sorted(set(row) | set(new)):
            old, now = row.get(column, ""), new.get(column, "")
            if _same(old, now):
                continue
            where = f"row {i} column {column}"
            if k not in processed:
                out.append(Failure(9, "other-row-changed", where))
            elif column not in ALLOWED_COLUMNS:
                out.append(Failure(9, "cell-not-allowed", where))
            elif column in FILL_ONLY_IF_EMPTY and str(old).strip() != "":
                out.append(Failure(9, "filled-cell-overwritten", where))
            elif column in APPEND_ONLY and str(old).strip() and not str(now).startswith(str(old)):
                out.append(Failure(9, "not-an-append", where))
    for k, pool in after_by_key.items():
        for j, _ in pool:
            out.append(Failure(9, "row-added", f"row {j}"))
    return out


# --------------------------------------------------------------------------- #
# Emails and repeated runs
# --------------------------------------------------------------------------- #

def check_emails(sent: list, expected: dict) -> list:
    """Checklist item 10. `sent`: [(site, subject, body)]. `expected`: {site: [links that must be in
    the email]}; a site with no links gets no email, any other site exactly one."""
    out = []
    for site, links in expected.items():
        mails = [(s, b) for name, s, b in sent if name == site]
        if not links:
            if mails:
                out.append(Failure(10, "email-without-work", site))
            continue
        if len(mails) != 1:
            out.append(Failure(10, "email-count", site, f"{len(mails)} emails"))
            continue
        subject, body = mails[0]
        missing = [l for l in links if l not in body]
        if missing:
            out.append(Failure(10, "link-missing", site, f"{len(missing)} of {len(links)} links"))
        if BANNED.search(subject + body) or AI_WORD.search(subject + body):
            out.append(Failure(10, "tool-name", site))
    return out


def check_no_new_docs(before: dict, after: dict) -> list:
    """Checklist item 11. `before`, `after`: {folder id: set of doc ids}. A second run must not add a
    doc anywhere (nor remove one)."""
    out = []
    for folder in sorted(set(before) | set(after)):
        added = set(after.get(folder, ())) - set(before.get(folder, ()))
        gone = set(before.get(folder, ())) - set(after.get(folder, ()))
        if added:
            out.append(Failure(11, "doc-added", "folder", f"{len(added)} new"))
        if gone:
            out.append(Failure(11, "doc-removed", "folder", f"{len(gone)} missing"))
    return out
