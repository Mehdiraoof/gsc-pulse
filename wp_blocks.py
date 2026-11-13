"""
PulseO: from the draft document to WordPress blocks
===================================================
The draft is a Google Document, read as HTML (its export). It becomes WordPress block markup, core blocks only:

  * the table before the first heading is the header box for the editor (meta title, meta description, sources, suggested
    video, ...): it never goes into the post, it goes into the Notes for editor box at the top
  * the first H1 is the post title; H2 and H3 stay; a second H1 becomes an H2
  * paragraphs, lists, tables, bold and italic; internal links of the site are kept, any other link keeps its text only
  * an image slot ([Image: description | alt | file.jpg]) and a human touch marker ([Personal experience: ...]) become a clearly
    marked box; an infographic slot ([Infographic: template | title | point; point | numbers]) is handed back as a slot, to be
    rendered and uploaded (or, if that cannot be done, left as a box with its description for the reviewer)
  * for an update the section "Changes from the current version" stays in the notes box, not in the post

Every box that must not go live starts with "⚠ Remove before publishing" (Persian «⚠ قبل از انتشار حذف شود»): the weekly
run looks for that text in what is published. Nothing here is written to a site.
"""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

import content_check

MARKER = {"en": "⚠ Remove before publishing", "fa": "⚠ قبل از انتشار حذف شود"}
MARKERS = tuple(MARKER.values())
IMAGE_SLOT = content_check.IMAGE_SLOT
INFOGRAPHIC_SLOT = re.compile(r"\[\s*(?:Infographic|اینفوگرافیک)\s*:\s*([^|\]]+?)\s*\|\s*([^|\]]+?)\s*\|\s*([^|\]]+?)\s*(?:\|\s*([^\]]*?)\s*)?\]",
                              re.IGNORECASE)
TOUCH = re.compile(r"\[\s*(?:Personal experience|Expert quote|تجربه شخصی|نظر متخصص)\s*:[^\]]*\]", re.IGNORECASE)
NEEDS_CHECKING = re.compile(r"\[needs checking\]", re.IGNORECASE)
VOID = {"br", "img", "hr", "meta", "link", "input"}
SKIP = {"style", "script", "title", "head"}
BOX_COLOR, BOX_BORDER = "#fff4cc", "#e0a800"


def has_marker(text: str) -> bool:
    """Is a box that must not go live in this content (as stored by WordPress: text, or with HTML entities)?"""
    plain = html.unescape(text or "")
    return any(m in plain for m in MARKERS)


# --------------------------------------------------------------------------- #
# A small tree of the HTML
# --------------------------------------------------------------------------- #

@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    kids: list = field(default_factory=list)            # Node or str

    def text(self) -> str:
        return "".join(k if isinstance(k, str) else k.text() for k in self.kids)


class Tree(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self._stack = [self.root]
        self._skip = 0
        self.feed(source or "")
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        node = Node(tag, dict(attrs))
        self._stack[-1].kids.append(node)
        if tag not in VOID:
            self._stack.append(node)

    def handle_endtag(self, tag):
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        if not self._skip:
            self._stack[-1].kids.append(data)


# --------------------------------------------------------------------------- #
# Inline text
# --------------------------------------------------------------------------- #

def _style(node: Node, name: str) -> str:
    m = re.search(rf"{name}\s*:\s*([^;]+)", node.attrs.get("style") or "", re.IGNORECASE)
    return m.group(1).strip().lower() if m else ""


def is_internal(href: str, home: str) -> bool:
    host = urlsplit(href).netloc.lower().removeprefix("www.")
    here = urlsplit(home).netloc.lower().removeprefix("www.")
    return (not host and href.startswith("/")) or (bool(host) and host == here)


def inline(node: Node, home: str) -> str:
    """The inline HTML of a node: text escaped, bold and italic kept, internal links kept, other links as their text."""
    out = []
    for kid in node.kids:
        if isinstance(kid, str):
            out.append(html.escape(re.sub(r"\s+", " ", kid)))
            continue
        inner = inline(kid, home)
        bold = kid.tag in ("b", "strong") or _style(kid, "font-weight") in ("700", "bold", "800", "900")
        italic = kid.tag in ("i", "em") or _style(kid, "font-style") == "italic"
        if kid.tag == "br":
            out.append(" ")
            continue
        if kid.tag == "a":
            href = content_check.real_href(kid.attrs.get("href") or "")
            inner = f'<a href="{html.escape(href)}">{inner}</a>' if is_internal(href, home) and inner.strip() else inner
        if bold and inner.strip():
            inner = f"<strong>{inner}</strong>"
        if italic and inner.strip():
            inner = f"<em>{inner}</em>"
        out.append(inner)
    return "".join(out).strip()


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

def paragraph(text_html: str) -> str:
    return f"<!-- wp:paragraph -->\n<p>{text_html}</p>\n<!-- /wp:paragraph -->"


def heading(level: int, text_html: str) -> str:
    attr = "" if level == 2 else f' {{"level":{level}}}'
    return f'<!-- wp:heading{attr} -->\n<h{level} class="wp-block-heading">{text_html}</h{level}>\n<!-- /wp:heading -->'


def list_block(node: Node, home: str) -> str:
    ordered = node.tag == "ol"
    items = "\n".join(f"<!-- wp:list-item -->\n<li>{inline(li, home)}</li>\n<!-- /wp:list-item -->"
                      for li in node.kids if isinstance(li, Node) and li.tag == "li")
    attr = ' {"ordered":true}' if ordered else ""
    tag = "ol" if ordered else "ul"
    return f'<!-- wp:list{attr} -->\n<{tag} class="wp-block-list">\n{items}\n</{tag}>\n<!-- /wp:list -->'


def table_rows(node: Node) -> list:
    rows = []
    for tr in _find(node, "tr"):
        rows.append([(c.tag, c) for c in tr.kids if isinstance(c, Node) and c.tag in ("td", "th")])
    return rows


def _find(node: Node, tag: str) -> list:
    out = []
    for kid in node.kids:
        if isinstance(kid, Node):
            if kid.tag == tag:
                out.append(kid)
            else:
                out += _find(kid, tag)
    return out


def table_block(node: Node, home: str) -> str:
    rows = table_rows(node)
    if not rows:
        return ""
    head = rows[0] if all(tag == "th" for tag, _ in rows[0]) else None
    body = rows[1:] if head else rows

    def row(cells, tag):
        return "<tr>" + "".join(f"<{tag}>{inline(c, home)}</{tag}>" for _, c in cells) + "</tr>"
    thead = f"<thead>{row(head, 'th')}</thead>" if head else ""
    tbody = "<tbody>" + "".join(row(r, "td") for r in body) + "</tbody>"
    return f'<!-- wp:table -->\n<figure class="wp-block-table"><table>{thead}{tbody}</table></figure>\n<!-- /wp:table -->'


def box(lines: list, language: str) -> str:
    """A coloured group block that starts with the marker text. `lines` are paragraphs of inline HTML."""
    inner = "\n".join([paragraph(f"<strong>{html.escape(MARKER.get(language, MARKER['en']))}</strong>")] + [paragraph(l) for l in lines])
    style = (f'{{"color":{{"background":"{BOX_COLOR}"}},"border":{{"width":"2px","color":"{BOX_BORDER}"}},'
             f'"spacing":{{"padding":{{"top":"16px","bottom":"16px","left":"16px","right":"16px"}}}}}}')
    return (f'<!-- wp:group {{"style":{style},"className":"pulseo-remove"}} -->\n'
            f'<div class="wp-block-group pulseo-remove has-border-color has-background" '
            f'style="border-color:{BOX_BORDER};border-width:2px;background-color:{BOX_COLOR};padding-top:16px;padding-right:16px;'
            f'padding-bottom:16px;padding-left:16px">\n{inner}\n</div>\n<!-- /wp:group -->')


def image_block(media_id: int, url: str, alt: str) -> str:
    return (f'<!-- wp:image {{"id":{int(media_id)},"sizeSlug":"full"}} -->\n'
            f'<figure class="wp-block-image size-full"><img src="{html.escape(url)}" alt="{html.escape(alt)}" '
            f'class="wp-image-{int(media_id)}"/></figure>\n<!-- /wp:image -->')


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #

@dataclass
class Segment:
    kind: str                       # "html" | "infographic"
    html: str = ""
    spec: dict = field(default_factory=dict)       # for an infographic: template, title, points, numbers, and its box as a fallback


@dataclass
class Converted:
    title: str
    box: dict                       # the header box: {"Meta Title": "...", ...}
    segments: list
    image_slots: list               # [{"description", "alt", "file"}]
    touches: int = 0
    needs_checking: int = 0
    changes_html: list = field(default_factory=list)      # lines for the notes box (an update's "Changes from the current version", the quality check)
    warnings: list = field(default_factory=list)


def _box_rows(node: Node) -> dict:
    out = {}
    for cells in table_rows(node):
        texts = [re.sub(r"\s+", " ", c.text()).strip() for _, c in cells]
        if len(texts) >= 2 and texts[0]:
            out[texts[0]] = texts[1]
    return out


BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}
WRAPPERS = {"root", "html", "body", "div", "section", "article", "main", "span"}


def flatten(node: Node):
    """The block level elements of a document in order: wrappers (body, div, section ...) are opened up."""
    for kid in node.kids:
        if not isinstance(kid, Node):
            continue
        if kid.tag in BLOCK_TAGS:
            yield kid
        elif kid.tag in WRAPPERS:
            if any(isinstance(k, Node) and (k.tag in BLOCK_TAGS or k.tag in WRAPPERS) for k in kid.kids):
                yield from flatten(kid)
            else:
                yield kid


def convert(source: str, home: str, language: str = "en", changes_heading: str = "", quality_heading: str = "") -> Converted:
    """The draft as blocks. `home` is the address of the site (what an internal link is), `changes_heading` the text of the
    H2 of an update's list of changes (its section goes to the notes box)."""
    tree = Tree(source)
    top = list(flatten(tree.root))
    done = Converted("", {}, [], [])
    seen_heading, in_changes = False, False
    segments, html_parts = [], []

    def flush():
        if html_parts:
            segments.append(Segment("html", "\n\n".join(html_parts)))
            html_parts.clear()

    for node in top:
        if isinstance(node, str) or not isinstance(node, Node):
            continue
        tag = node.tag
        if tag == "table" and not seen_heading and not done.box:
            done.box = _box_rows(node)
            continue
        level = int(tag[1]) if re.fullmatch(r"h[1-6]", tag) else 0
        if level:
            seen_heading = True
            text = inline(node, home)
            plain = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
            if level == 1 and not done.title:
                done.title = plain
                continue
            in_changes = level == 2 and plain in {h for h in (changes_heading, quality_heading) if h}      # sections for the notes box
            if in_changes:
                done.changes_html.append(f"<strong>{html.escape(plain)}</strong>")
                continue
            html_parts.append(heading(2 if level == 1 else min(level, 6), text))
            continue
        if in_changes:                                  # an update's list of changes goes to the notes box
            if tag in ("p",):
                done.changes_html.append(inline(node, home))
            elif tag in ("ul", "ol"):
                done.changes_html += [inline(li, home) for li in node.kids if isinstance(li, Node) and li.tag == "li"]
            continue
        if tag == "p":
            text_html = inline(node, home)
            plain = html.unescape(re.sub(r"<[^>]+>", "", text_html)).strip()
            if not plain:
                continue
            slot = IMAGE_SLOT.search(plain)
            info = INFOGRAPHIC_SLOT.search(plain)
            if info and plain == info.group(0):
                template, title, points, numbers = (info.group(i) or "" for i in (1, 2, 3, 4))
                pts = [p.strip() for p in points.split(";") if p.strip()]
                spec = {"template": template.strip().lower(), "title": title.strip(), "points": pts, "numbers": numbers.strip()}
                flush()
                segments.append(Segment("infographic", box([f"Infographic needed: {html.escape(title.strip())} "
                                                            f"({html.escape(template.strip())}): {html.escape('; '.join(pts))}"
                                                            + (f" · numbers: {html.escape(numbers.strip())}" if numbers.strip() else "")],
                                                           language), spec))
                continue
            if slot and plain == slot.group(0):
                parts = [x.strip(" []") for x in re.split(r"\|", re.sub(r"^\[\s*[^:]+:\s*", "", plain))]
                desc, alt, name = (parts + ["", "", ""])[:3]
                done.image_slots.append({"description": desc, "alt": alt, "file": name.rstrip("]")})
                html_parts.append(box([f"Image needed: {html.escape(desc)} · alt text: {html.escape(alt)} · file name: "
                                       f"{html.escape(name.rstrip(']'))}"], language))
                continue
            if TOUCH.fullmatch(plain):
                done.touches += 1
                html_parts.append(box([f"To add by the writer: {html.escape(plain)}"], language))
                continue
            done.needs_checking += len(NEEDS_CHECKING.findall(plain))
            html_parts.append(paragraph(text_html))
        elif tag in ("ul", "ol"):
            html_parts.append(list_block(node, home))
        elif tag == "table":
            block = table_block(node, home)
            if block:
                html_parts.append(block)
        elif tag in ("div", "span"):
            text_html = inline(node, home)
            if re.sub(r"<[^>]+>", "", text_html).strip():
                html_parts.append(paragraph(text_html))
    flush()
    done.segments = segments
    if not done.title:
        done.warnings.append("the document has no H1: there is no title")
    if not done.box:
        done.warnings.append("the document has no header box for the editor")
    return done


def notes_box(conv: Converted, language: str, extra: list = None) -> str:
    """The Notes for editor box at the top of the post: the header box rows (meta title and description are the SEO's job, done by
    hand in the site's SEO plugin), what is still to do in the text, and, for an update, the changes from the current version."""
    lines = ["<strong>Notes for editor</strong>"]
    for key, value in conv.box.items():
        lines.append(f"<strong>{html.escape(key)}:</strong> {html.escape(value)}")
    todo = []
    if conv.image_slots:
        todo.append(f"{len(conv.image_slots)} image{'s' if len(conv.image_slots) != 1 else ''} to add")
    if conv.touches:
        todo.append(f"{conv.touches} personal experience or expert quote to add")
    if conv.needs_checking:
        todo.append(f"{conv.needs_checking} fact{'s' if conv.needs_checking != 1 else ''} marked [needs checking]")
    if todo:
        lines.append("<strong>To do before publishing:</strong> " + "; ".join(todo))
    lines += list(extra or [])
    if conv.changes_html:
        lines += conv.changes_html
    return box(lines, language)


def suggest_category(categories: list, *hints: str):
    """(category id, "") for the category whose name matches a hint (a dashboard tab or a page type), else (None, a sentence for
    the notes). Never makes a category."""
    for hint in hints:
        h = (hint or "").strip().lower()
        if not h:
            continue
        for c in categories:
            name = str(c.get("name", "")).strip().lower()
            if name and (name == h or name in h or h in name):
                return c["id"], ""
    return None, "Category: not sure, so the default was kept: choose one."
