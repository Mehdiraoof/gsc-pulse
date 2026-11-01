"""
PulseO: content creation (briefs and drafts for the rows of a content dashboard)
=================================================================================
For every row of a site's content dashboard:

  * To Do "Brief" and Status not "Briefed": a brief (a Google Doc in the site's Briefs folder with the
    ten sections), then the row shows Briefed, the date, the link, and To Do "Write" (no address yet)
    or "Update" (the page exists).
  * To Do "Draft" and the row has a brief: the full article (a Google Doc in the Drafts folder), checked,
    then the row shows Drafted, To Do "Review", the link and a note.
  * To Do "Draft" without a brief: skipped, with the note "Set To Do to Brief first" (once).
  * A site without an SOP gets briefs but no drafts.

The AI writes plain Markdown in a fixed layout (ai_client.py); everything that can be made true in code
is made true in code (content_docs.py: Persian letters and digits, dashes, links, headings), and
content_check.py tests the result. A draft goes through two passes: write, then an editor pass that gets
the SOP checklist, the brief and the problems the checker found.

Safety: the only cells a run may change are Attachments (added to), Status, To Do, Briefed Date, Notes
(added to), and Meta Title, Outlines, Slug, Words Count and Related URLs only when empty. Rows are never
deleted or moved; only the briefs and drafts it made itself are ever written. A limit of the AI
(its daily limit, or this run's own) stops the run cleanly: what is done stays, the rest waits.
Nothing here prints a keyword, an address, a prompt or a text.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import ai_client
import content
import content_check as cc
import content_docs as cd
import content_run
import content_sheet as cs
import gdocs
import site_check
import tabs

LANGUAGE_NAMES = {"en": "English", "fa": "Persian (Farsi)"}
NOTE_NO_BRIEF = "Set To Do to Brief first"
NOTE_DRAFT_READY = "Draft ready. Check the items under Quality check."
MAX_SOP_CHARS, MAX_BRIEF_CHARS, MAX_PAGE_CHARS = 12000, 16000, 12000
CANDIDATES = 12                      # pages of the same site offered for internal links
MAX_AI_ERRORS_IN_A_ROW = 3
ALLOWED_COLUMNS = cc.ALLOWED_COLUMNS
FILL_ONLY_IF_EMPTY = cc.FILL_ONLY_IF_EMPTY


@dataclass
class CreateConfig:
    label: str
    language: str                    # "fa" or "en"
    domain: str                      # the site's address, to tell internal links from others
    tabs: list                       # names of the tabs of the dashboard
    sop_doc_id: str = ""
    briefs_folder_id: str = ""
    drafts_folder_id: str = ""
    extra_rules: str = ""
    max_briefs: int = 10
    max_drafts: int = 5
    max_requests: int = 30           # AI requests in one run
    daily_requests: int = 0          # the day's own limit (0: only the provider's)
    ai_drafts: bool = True           # false: the site stops at the brief and a Draft row is left untouched


@dataclass
class Item:
    tab: str
    sheet_row: int                   # the row number in the sheet (the header is row 1)
    cells: dict
    header: list

    def get(self, name: str) -> str:
        return str(self.cells.get(name, "") or "").strip()

    @property
    def keyword(self) -> str:
        return " ".join(self.get(content.KEY).split())

    @property
    def url(self) -> str:
        return self.get(content.URL)

    @property
    def key(self) -> str:
        return content.row_key(self.cells)


@dataclass
class CreateResult:
    briefs: list = field(default_factory=list)       # [(keyword, link)]
    drafts: list = field(default_factory=list)
    no_brief: list = field(default_factory=list)     # keywords of Draft rows that got the note
    waiting: int = 0                                 # rows left for the next run
    stopped: str = ""                                # why the run stopped early, or ""
    problems: list = field(default_factory=list)     # short, content-free sentences
    requests: int = 0
    font_rewrites: int = 0
    processed_keys: set = field(default_factory=set)  # content.row_key of every row the run touched

    @property
    def did_something(self) -> bool:
        return bool(self.briefs or self.drafts or self.no_brief or self.stopped or self.problems)

    def text(self) -> str:
        parts = [f"{len(self.briefs)} briefs", f"{len(self.drafts)} drafts"]
        if self.no_brief:
            parts.append(f"{len(self.no_brief)} draft rows without a brief")
        if self.waiting:
            parts.append(f"{self.waiting} rows waiting")
        if self.stopped:
            parts.append(f"stopped: {self.stopped}")
        parts += self.problems
        return ", ".join(parts) + f" ({self.requests} AI requests)"


# --------------------------------------------------------------------------- #
# The dashboard
# --------------------------------------------------------------------------- #

def load_items(sheets, file_id: str, tab_names: list) -> list:
    items = []
    info = cs.sheet_info(sheets, file_id)
    for tab in tab_names:
        if tab not in info:
            continue
        header, rows = cs.table_of(cs.read_all(sheets, file_id, tab))
        items += [Item(tab, i + 2, row, header) for i, row in enumerate(rows)]
    return items


def write_row(sheets, file_id: str, item: Item, changes: dict):
    """Writes the cells of one row. Only the cells a run may change; a column the tab does not have is
    skipped."""
    illegal = set(changes) - ALLOWED_COLUMNS
    if illegal:
        raise ValueError(f"content creation may not change {len(illegal)} of these columns")
    updates = []
    for name, value in changes.items():
        c = cs.col_of(item.header, name)
        if c is not None:
            updates.append((f"'{item.tab}'!{cs.letters(c)}{item.sheet_row}", [[value]]))
    tabs.patch_cells(sheets, file_id, updates)
    for name, value in changes.items():
        item.cells[name] = value


def appended(old, add: str) -> str:
    old = str(old or "").strip()
    return f"{old}\n{add}" if old else add


def link_candidates(items: list, item: Item, n: int = CANDIDATES) -> list:
    """[(keyword, address)] of the live pages of the same dashboard that fit the row's topic best."""
    topic = content.topic_words(item.keyword)
    scored = []
    for other in items:
        if not other.url or other.key == item.key or other.get(content.STATUS).lower() == "gone":
            continue
        words = content.topic_words(other.keyword) | content.topic_words(content.slug_of(other.url))
        scored.append((-len(topic & words), other.sheet_row, other.keyword or content.slug_of(other.url), other.url))
    scored.sort()
    seen, out = set(), []
    for _, _, kw, url in scored:
        key = content.url_key(url)
        if key not in seen:
            seen.add(key)
            out.append((kw, content.display_url(url)))
    return out[:n]


# --------------------------------------------------------------------------- #
# What the AI is told
# --------------------------------------------------------------------------- #

SYSTEM = ("You are a senior SEO content strategist and editor. You write for a client's website and the "
          "result is published under the client's own name. Never mention yourself, AI, language models or "
          "any tool. Never invent facts, prices, statistics, quotes or sources: when you cannot verify "
          "something, say that it must be checked. Never use an em dash or an en dash; use a comma, a colon "
          "or a hyphen. Follow the output format exactly.")

BRIEF_PARTS = """\
1 = Task: say whether this is a new page or an update of the existing page, the article type (guide, how-to, list, comparison, review ...), the goal in one sentence, and the search intent it serves.
2 = Keywords, intent and reader: the target keyword, 3 to 8 secondary keywords (real searches people make), the intent, who the reader is and their pain point.
3 = Title tag, meta description, H1 and slug: exactly four lines with these English labels:
Title tag: ... (about 55 characters)
Meta description: ... (140 to 155 characters)
H1: ...
Slug: ... (lower-case words joined by hyphens, no domain, no slash; Persian words are fine for a Persian site)
4 = Outline: the full outline as a list, one line per heading, each starting with "H2:" or "H3:". The direct answer comes first, the follow-up questions around the topic (fan-outs) become sections. Finish with one line "Tables planned: none" or a description of each table (every comparison, price list or option list gets a table).
5 = Entities, facts and sources: the people, brands, places and concepts to name clearly; the facts the writer must get right, each with its source; the recommended sources (name and address). English sources first (official brand sites, expert publications, medical and scientific sources, reputable magazines). Persian sources only for local facts such as prices in Iran, availability, Iranian brands, salons and services, local rules.
6 = FAQ: 4 to 6 questions taken from real searches, each with a short note on the answer.
7 = Internal links: a table with the columns | From | To | Anchor text |. Use only addresses from the list of pages below. Give 3 to 5 rows for links FROM the new article (From = "this article"), and, if useful, rows for pages that should link TO it (From = their address, To = "this article").
8 = Word count, format and tone: one line "Word count: <a number>", then the format (headings, lists, tables, images) and the tone of voice.
9 = Keep, remove, rewrite: {nine}
10 = Writer checklist: a checklist, one "- [ ]" line per item, built from the site's SOP and this brief."""

NINE_NEW = 'This part is for updates only. For a new page write exactly "Not applicable".'
NINE_UPDATE = ("From the live page text below: what to keep, what to remove and what to rewrite, with the reason "
               "for each.")


def row_facts(item: Item) -> str:
    lines = [f"Keyword: {item.keyword}"]
    for label, col in (("Secondary keywords", "Secondary Keywords"), ("Search intent", "Intent"),
                       ("Content type", "Content Type"), ("Follow-up questions people ask", content.FANOUTS),
                       ("Notes", "Notes/Checklist"), ("What the data suggests", "Suggested Action"),
                       ("Why", "Why"), ("Competing pages", "Competing Pages")):
        if item.get(col):
            lines.append(f"{label}: {item.get(col)[:600]}")
    for label, col in (("Clicks (90 days)", "Clicks (90d)"), ("Impressions (90 days)", "Impressions (90d)"),
                       ("Average position", "Avg. Position")):
        if item.get(col):
            lines.append(f"{label}: {item.get(col)}")
    lines.append(f"Page: update of {item.url}" if item.url else "Page: a new page (it does not exist yet)")
    return "\n".join(lines)


def pages_list(candidates: list) -> str:
    return "\n".join(f"- {kw}: {url}" for kw, url in candidates) or "(none)"


def brief_prompt(cfg: CreateConfig, item: Item, sop: str, page_text: str, candidates: list) -> str:
    update = bool(item.url)
    research = ("Research in English with Google Search: translate the keyword and its follow-up questions into English "
                "and read strong English sources. " if cfg.language == "fa" else "Research with Google Search. ")
    local = ("Use Persian sources only for local facts (prices in Iran, availability, Iranian brands, salons and services, "
             "local rules), plus ONE short Persian search to see what Persian searchers expect and what the Persian "
             "top results cover. " if cfg.language == "fa" else "")
    return f"""STEP: BRIEF
Write a content brief for ONE page of the website "{cfg.label}". A writer will follow it without asking questions.

THE ROW
{row_facts(item)}

THE SITE'S SOP (it wins over your habits)
{sop[:MAX_SOP_CHARS] or '(the site has no SOP)'}
{('Extra rules: ' + cfg.extra_rules) if cfg.extra_rules else ''}

{('THE LIVE PAGE (its current text)' + chr(10) + page_text[:MAX_PAGE_CHARS]) if update and page_text else ''}

PAGES OF THE SAME SITE THAT YOU MAY LINK TO (keyword: address)
{pages_list(candidates)}

HOW TO RESEARCH
{research}{local}Look at what the top results cover, find the facts that must be right, and list the sources you recommend in part 5. If you cannot search the web right now, rely only on what you know for certain: name a source only when you are sure it exists, and write [needs checking] after any fact or source you could not verify.

LANGUAGE
Write the brief in {LANGUAGE_NAMES[cfg.language]}. Keep the English labels of part 3 ("Title tag:", "Meta description:", "H1:", "Slug:"), "Word count:" and "Tables planned:" exactly as written.

OUTPUT FORMAT
Return exactly ten parts. Each part starts with a line made of "=== N ===" (N from 1 to 10) and there is nothing before the first line. Do not write the titles of the parts (they are added for you). Inside a part use plain Markdown: lists with "- ", tables with "|".
{BRIEF_PARTS.format(nine=NINE_UPDATE if update else NINE_NEW)}
"""


def draft_rules(cfg: CreateConfig, item: Item, links: list, has_comparison: bool) -> str:
    h = cd.DRAFT_HEADINGS[cfg.language]
    touch = ("[تجربه شخصی: ...] and [نظر متخصص: ...]" if cfg.language == "fa"
             else "[Personal experience: ...] and [Expert quote: ...]")
    slot = "[عکس: description | alt text | file-name.jpg]" if cfg.language == "fa" else "[Image: description | alt text | file-name.jpg]"
    persian = ("Write natural Persian, never a word-for-word translation. Use the Persian letters ی and ک (never ي or ك), "
               "Persian digits (۰۱۲۳۴۵۶۷۸۹) in running text, and a zero-width non-joiner where Persian spelling needs one. "
               "A number inside a Latin word (S24, 5G) stays as it is. " if cfg.language == "fa" else "")
    return f"""\
- Exactly ONE H1 (a line starting with "# "); it is the page title. Then H2 and H3 headings.
- The direct answer comes first, in the first paragraph under the H1. Short paragraphs, lists where they help.
- {'A table (Markdown with |) for EVERY comparison, price list or option list. The brief plans at least one table.' if has_comparison else 'A table for every comparison, price list or option list there is.'}
- A title with a number ("10 ideas ...") must contain exactly that many items or sections.
- The FAQ is an H2 whose text is exactly "{h['faq']}", with each question as an H3 and the answer below it.
- Finish the article with an H2 whose text is exactly "{h['summary']}": a short summary and one next step for the reader.
- Internal links: use 3 to 5 links, each address ONCE, only from the list of allowed addresses, written as Markdown links [anchor text](address). No other internal links.
- Image slots: at least three paragraphs of the form {slot} where a picture belongs.
- Human touch: 2 to 4 markers in total, in the form {touch}, where the writer must add a real personal experience or a real expert quote. Never invent a quote or an experience: write what is needed, not a made-up story.
- Never invent facts, prices, statistics or quotes. A number or claim that you could not verify is followed by [needs checking].
- Do not mention AI, models, tools or the SOP in the article. No em dash and no en dash.
- {persian}Write the article in {LANGUAGE_NAMES[cfg.language]}.
{('- This is an UPDATE of the live page: keep what is good, and add an H2 "' + h['changes'] + '" near the end that lists what changed and why.') if item.url else ''}
ALLOWED ADDRESSES FOR INTERNAL LINKS (keyword: address)
{pages_list(links)}
"""


OUTPUT_ARTICLE = """\
OUTPUT FORMAT
Return these parts, each starting with its own line, and nothing before the first line:
=== META ===
Meta title: ...
Meta description: ...
Slug: ...
Article type: ...
Keywords: ...
Word count: <a number>
=== ARTICLE ===
(the whole article in Markdown, starting with the H1; do NOT write a quality-check section)
"""

OUTPUT_EDIT = OUTPUT_ARTICLE + """\
=== QUALITY ===
A Markdown list. One line for each item of the SOP checklist, starting "- ✓ " if the article meets it and
"- ✗ " if it does not (say why in a few words). Then a line "To verify:" and a list of everything the editor
must check before publishing (every place marked [needs checking], every price or claim).
"""


def draft_prompt(cfg: CreateConfig, item: Item, sop: str, brief_text: str, links: list, has_comparison: bool,
                 page_text: str) -> str:
    return f"""STEP: DRAFT
Write the full article for ONE page of the website "{cfg.label}", following the brief.

THE ROW
{row_facts(item)}

THE BRIEF (follow it)
{brief_text[:MAX_BRIEF_CHARS]}

THE SITE'S SOP (it wins over your habits)
{sop[:MAX_SOP_CHARS]}
{('Extra rules: ' + cfg.extra_rules) if cfg.extra_rules else ''}

{('THE LIVE PAGE (its current text)' + chr(10) + page_text[:MAX_PAGE_CHARS]) if item.url and page_text else ''}

RULES FOR THE ARTICLE
{draft_rules(cfg, item, links, has_comparison)}
{OUTPUT_ARTICLE}"""


PROBLEM_TEXT = {
    "h1-count": "The article must have exactly one H1 (a line starting with \"# \").",
    "no-faq": "Add the FAQ: an H2 with the exact FAQ heading, with each question as an H3.",
    "faq-not-h2": "The FAQ heading must be an H2.",
    "no-summary": "Add the summary: an H2 with the exact summary heading, a short summary and one next step.",
    "summary-not-h2": "The summary heading must be an H2.",
    "no-table": "Add a Markdown table for the comparison, price list or option list the brief plans.",
    "link-count": "Use 3 to 5 internal links from the allowed list.",
    "link-repeated": "Use each internal address only once.",
    "no-image-slot": "Add image slots in the exact form given in the rules.",
    "human-touch-count": "Include 2 to 4 human touch markers in the exact form given in the rules.",
    "count-mismatch": "The title contains a number: the article must contain exactly that many items or sections.",
    "tool-name": "Remove every mention of AI, a model, a tool or a product that is not part of the topic.",
    "dash": "Remove every em dash and en dash.",
}


def edit_prompt(cfg: CreateConfig, item: Item, sop: str, brief_text: str, links: list, has_comparison: bool,
                article: str, meta: dict, failures: list) -> str:
    problems = [PROBLEM_TEXT[f.code] for f in failures if f.code in PROBLEM_TEXT]
    listed = "\n".join(f"- {p}" for p in dict.fromkeys(problems)) or "- (the automatic checks found nothing)"
    return f"""STEP: EDIT
You are the editor. Below is a draft of an article. Check it against the brief and the SOP checklist, fix every
problem, and return the complete corrected article (not a list of changes).

THE BRIEF
{brief_text[:MAX_BRIEF_CHARS]}

THE SITE'S SOP
{sop[:MAX_SOP_CHARS]}
{('Extra rules: ' + cfg.extra_rules) if cfg.extra_rules else ''}

PROBLEMS FOUND BY AN AUTOMATIC CHECK (fix all of them)
{listed}

RULES FOR THE ARTICLE
{draft_rules(cfg, item, links, has_comparison)}
THE DRAFT
Meta title: {meta.get('meta title', '')}
Meta description: {meta.get('meta description', '')}
Slug: {meta.get('slug', '')}
Article type: {meta.get('article type', '')}
Keywords: {meta.get('keywords', '')}
Word count: {meta.get('word count', '')}

{article}

{OUTPUT_EDIT}"""


# --------------------------------------------------------------------------- #
# Building the documents
# --------------------------------------------------------------------------- #

def _numbered(text: str) -> dict:
    """{1..10: text} of a brief answer."""
    out = {}
    for name, body in cd.split_sections(text).items():
        if name.isdigit():
            out[int(name)] = body
    return out


def build_brief(cfg: CreateConfig, item: Item, parts: dict) -> tuple:
    """(html, facts) of a brief from its parts. `facts`: what the dashboard cells and the draft need."""
    update = bool(item.url)
    headings = cd.BRIEF_HEADINGS[cfg.language]
    body = [f"<h1>{cd.htmllib.escape(cd.clean_text(item.keyword, cfg.language))}</h1>"]
    for n in range(1, 11):
        if n == cd.UPDATE_ONLY_SECTION and not update:
            continue
        md = cd.clean_text(parts.get(n, ""), cfg.language)
        body.append(f"<h2>{headings[n - 1]}</h2>" + cd.md_to_html(md))
    fields = cd.parse_fields(parts.get(3, ""))
    wc = re.search(r"\d[\d,]*", cd.parse_fields(parts.get(8, "")).get("word count", "").translate(content._FA))
    outline = "\n".join(ln.strip().lstrip("-* ").strip() for ln in parts.get(4, "").splitlines()
                        if re.match(r"^\s*[-*]?\s*H[23]\s*:", ln))
    urls = list(dict.fromkeys(m.rstrip(".,)") for m in re.findall(r"https?://[^\s|)\]]+", parts.get(7, ""))))
    facts = {"title tag": fields.get("title tag", ""), "slug": fields.get("slug", ""),
             "words": int(wc.group(0).replace(",", "")) if wc else "", "outline": outline[:1500], "urls": urls}
    return cd.wrap(cfg.language, "".join(body)), facts


def tables_planned(brief_text: str) -> bool:
    m = re.search(r"Tables planned\s*:\s*(.*)", brief_text, re.IGNORECASE)
    return bool(m) and not re.match(r"^\W*(none|no|nothing|n/?a|هیچ|ندارد)\b", m.group(1).strip(), re.IGNORECASE)


def split_draft(text: str, keyword: str) -> tuple:
    """(meta fields, article Markdown, quality Markdown) of an answer."""
    parts = cd.split_sections(text)
    article = parts.get("ARTICLE") or (text if re.search(r"^#\s", text, re.MULTILINE) else "")
    return cd.parse_fields(parts.get("META", "")), article.strip(), parts.get("QUALITY", "").strip()


def cut_quality(md: str) -> str:
    """The article without any quality-check section the model wrote itself (one is added in code)."""
    lines = md.split("\n")
    for i, ln in enumerate(lines):
        h = cd._HEADING.match(ln.strip())
        if h and cc.QUALITY_HEADING.search(h.group(2)):
            return "\n".join(lines[:i]).rstrip()
    return md


def fix_headings(md: str, title: str, lang: str) -> str:
    """One H1 (later ones become H2; none: the title is added), the FAQ and summary headings are H2."""
    out, seen = [], False
    for ln in md.split("\n"):
        h = cd._HEADING.match(ln.strip())
        if h:
            level, text = len(h.group(1)), h.group(2)
            if level == 1:
                if seen:
                    level = 2
                seen = True
            if (cc.FAQ_HEADING.search(text) or (cc.SUMMARY_HEADING.search(text))) and level != 2 and level != 1:
                level = 2
            ln = "#" * level + " " + text
        out.append(ln)
    md = "\n".join(out)
    return md if seen else f"# {title}\n\n{md}"


def limit_touches(md: str, lang: str) -> str:
    """No more than four human-touch markers: the later ones are removed."""
    pattern = cc.HUMAN_TOUCH[lang]
    count = 0

    def keep(m):
        nonlocal count
        count += 1
        return m.group(0) if count <= cc.MAX_TOUCHES else ""
    return pattern.sub(keep, md)


def assemble_draft(cfg: CreateConfig, item: Item, meta: dict, article: str, quality: str, failures: list) -> str:
    h = cd.DRAFT_HEADINGS[cfg.language]
    lang = cfg.language
    labels = cd.HEADER_LABELS[lang]
    values = [meta.get("meta title", ""), meta.get("meta description", ""), meta.get("slug", ""),
              meta.get("article type", ""), meta.get("keywords", "") or item.keyword, meta.get("word count", "")]
    rows = [(k, cd.fa_letters(v) if lang == "fa" else v) for k, v in zip(labels, values)]
    if failures:
        auto = "- ✗ Automatic checks that still fail: " + ", ".join(sorted({f.code for f in failures}))
    else:
        auto = "- ✓ Automatic checks passed: structure, links, human touch markers, language rules"
    q_md = cd.clean_text((quality or "").strip(), lang)
    body = (cd.header_box(rows) + cd.md_to_html(article)
            + f"<h2>{h['quality']}</h2>" + cd.md_to_html(q_md + ("\n" if q_md else "") + auto))
    return cd.wrap(lang, body)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def read_text(drive, doc_id: str) -> str:
    return cd.html_to_text(gdocs.export_html(drive, doc_id))


def fetch_page(http_get, url: str) -> str:
    try:
        status, body = http_get(url)
    except Exception:                                  # noqa: BLE001  a page that cannot be read is no reason to stop
        return ""
    if status != 200 or not body:
        return ""
    return cd.html_to_text(body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body))


def brief_step(drive, sheets, file_id, cfg, items, ai, today, http_get, sop, result, log):
    todo = [it for it in items if it.get(content.TODO).lower() == "brief"
            and it.get(content.STATUS).lower() != "briefed" and it.keyword]
    result.waiting += max(0, len(todo) - cfg.max_briefs)
    errors = 0
    for n, item in enumerate(todo[:cfg.max_briefs]):
        name = f"Brief · {item.keyword}"
        existing = gdocs.find_doc(drive, name, cfg.briefs_folder_id)     # an earlier run stopped after the doc
        facts = {"title tag": "", "slug": "", "words": "", "outline": "", "urls": []}
        try:
            if existing:
                doc_id = existing
            else:
                candidates = link_candidates(items, item)
                page = fetch_page(http_get, item.url) if item.url else ""
                text = ai.generate(SYSTEM, brief_prompt(cfg, item, sop, page, candidates), search=True)
                parts = _numbered(text)
                needed = [n for n in range(1, 11) if n != cd.UPDATE_ONLY_SECTION or item.url]
                missing = [n for n in needed if not parts.get(n)]
                if missing:                                               # one more try for the missing parts
                    again = _numbered(ai.generate(
                        SYSTEM, brief_prompt(cfg, item, sop, page, candidates)
                        + f"\nYour last answer lacked part(s) {', '.join(map(str, missing))}. Return all ten parts again, complete.",
                        search=True))
                    parts = again if len([n for n in needed if again.get(n)]) > len(needed) - len(missing) else parts
                html, facts = build_brief(cfg, item, parts)
                found = cc.check_brief(html, update=bool(item.url))
                if found:
                    result.problems.append(f"a brief failed {len(found)} automatic checks")
                doc_id, rewrites = gdocs.write_doc(drive, name, html, cfg.briefs_folder_id, cfg.language)
                result.font_rewrites += rewrites
            errors = 0
        except ai_client.QuotaExhausted as e:
            result.stopped = str(e)
            result.waiting += len(todo[:cfg.max_briefs]) - n
            return
        except ai_client.AiError as e:
            log(f"  AI: {e}")
            errors += 1
            result.problems.append("a brief could not be written (no answer from the writing service)")
            if errors >= MAX_AI_ERRORS_IN_A_ROW:
                result.stopped = "no answer from the writing service three times in a row"
                result.waiting += len(todo[:cfg.max_briefs]) - n - 1
                return
            continue
        changes = {content.STATUS: "Briefed", content.TODO: "Update" if item.url else "Write",
                   "Briefed Date": today.isoformat(), "Attachments": appended(item.get("Attachments"), gdocs.doc_link(doc_id))}
        fills = (("Meta Title", cd.clean_text(facts["title tag"], cfg.language)),
                 ("Slug", cd.fa_letters(facts["slug"]) if cfg.language == "fa" else facts["slug"]),
                 ("Words Count (Suggested)", facts["words"]),
                 ("Outlines", cd.clean_text(facts["outline"], cfg.language)),
                 ("Related URLs", "\n".join(facts["urls"][:8])))
        for col, value in fills:
            if value != "" and col in FILL_ONLY_IF_EMPTY and not item.get(col):
                changes[col] = value
        write_row(sheets, file_id, item, changes)
        result.briefs.append((item.keyword, gdocs.doc_link(doc_id)))
        result.processed_keys.add(item.key)


def draft_step(drive, sheets, file_id, cfg, items, ai, today, http_get, sop, result, log):
    todo = [it for it in items if it.get(content.TODO).lower() == "draft" and it.keyword]
    briefs_here = gdocs.docs_in(drive, cfg.briefs_folder_id)
    ready, missing = [], []
    for it in todo:
        ids = [i for i in gdocs.doc_ids_in(it.get("Attachments")) if i in briefs_here]
        (ready if ids else missing).append((it, ids))
    for it, _ in missing:                                   # no brief: a note (once), nothing else
        if NOTE_NO_BRIEF in it.get("Notes/Checklist"):
            continue                                         # already told: a repeated run stays quiet
        write_row(sheets, file_id, it, {"Notes/Checklist": appended(it.get("Notes/Checklist"), NOTE_NO_BRIEF)})
        result.no_brief.append(it.keyword)
        result.processed_keys.add(it.key)
    result.waiting += max(0, len(ready) - cfg.max_drafts)
    errors = 0
    for n, (item, ids) in enumerate(ready[:cfg.max_drafts]):
        name = f"Draft · {item.keyword}"
        existing = gdocs.find_doc(drive, name, cfg.drafts_folder_id)
        failures = []
        try:
            if existing:
                doc_id = existing
            else:
                brief_text = read_text(drive, ids[0])
                links = draft_links(items, item, brief_text, cfg)
                comparison = tables_planned(brief_text)
                page = fetch_page(http_get, item.url) if item.url else ""
                first = ai.generate(SYSTEM, draft_prompt(cfg, item, sop, brief_text, links, comparison, page), search=True)
                meta, article, _ = split_draft(first, item.keyword)
                if not article:
                    raise ai_client.AiError("the draft was not in the expected layout")
                article, failures = tidy(cfg, item, meta, article, links, comparison)
                second = ai.generate(SYSTEM, edit_prompt(cfg, item, sop, brief_text, links, comparison, article, meta, failures))
                meta2, article2, quality = split_draft(second, item.keyword)
                if article2:
                    meta, article = {**meta, **meta2}, article2
                article, failures = tidy(cfg, item, meta, article, links, comparison)
                html = assemble_draft(cfg, item, meta, article, quality, failures)
                final = cc.check_draft(html, language=cfg.language, domain=cfg.domain, has_comparison=comparison)
                if {f.code for f in final} != {f.code for f in failures}:       # the quality part added a problem of its own
                    failures = final
                    html = assemble_draft(cfg, item, meta, article, quality, failures)
                doc_id, rewrites = gdocs.write_doc(drive, name, html, cfg.drafts_folder_id, cfg.language)
                result.font_rewrites += rewrites
            errors = 0
        except ai_client.QuotaExhausted as e:
            result.stopped = str(e)
            result.waiting += len(ready[:cfg.max_drafts]) - n
            return
        except ai_client.AiError as e:
            log(f"  AI: {e}")
            errors += 1
            result.problems.append("a draft could not be written (no answer from the writing service)")
            if errors >= MAX_AI_ERRORS_IN_A_ROW:
                result.stopped = "no answer from the writing service three times in a row"
                result.waiting += len(ready[:cfg.max_drafts]) - n - 1
                return
            continue
        note = NOTE_DRAFT_READY if not failures else (
            f"Draft ready, but {len(failures)} automatic checks still fail ({', '.join(sorted({f.code for f in failures}))}). "
            "See Quality check.")
        write_row(sheets, file_id, item, {
            content.STATUS: "Drafted", content.TODO: "Review",
            "Attachments": appended(item.get("Attachments"), gdocs.doc_link(doc_id)),
            "Notes/Checklist": appended(item.get("Notes/Checklist"), note)})
        if failures:
            result.problems.append(f"a draft still fails {len(failures)} automatic checks")
        result.drafts.append((item.keyword, gdocs.doc_link(doc_id)))
        result.processed_keys.add(item.key)


def draft_links(items: list, item: Item, brief_text: str, cfg: CreateConfig) -> list:
    """The addresses a draft may link to: the ones the brief names (that are live pages of the dashboard),
    then the best fitting live pages, up to a dozen."""
    live = {content.url_key(it.url): (it.keyword or content.slug_of(it.url), content.display_url(it.url))
            for it in items if it.url and it.key != item.key}
    out, seen = [], set()
    for url in re.findall(r"https?://[^\s|)\]]+", brief_text):
        k = content.url_key(url.rstrip(".,)"))
        if k in live and k not in seen:
            seen.add(k)
            out.append(live[k])
    for kw, url in link_candidates(items, item):
        k = content.url_key(url)
        if k not in seen:
            seen.add(k)
            out.append((kw, url))
    return out[:CANDIDATES]


def tidy(cfg: CreateConfig, item: Item, meta: dict, article: str, links: list, comparison: bool) -> tuple:
    """(article, failures): the article made right where code can do it, and what the checker still finds."""
    lang = cfg.language
    article = cut_quality(cd.clean_text(article, lang))
    article = fix_headings(article, meta.get("meta title") or item.keyword, lang)
    article = limit_touches(article, lang)
    article = cd.repair_links(article, links, cfg.domain)
    html = assemble_draft(cfg, item, {k: v for k, v in meta.items()}, article, "", [])
    failures = cc.check_draft(html, language=lang, domain=cfg.domain, has_comparison=comparison)
    return article, failures


def config_from_site(site, label: str = "") -> CreateConfig:
    """The settings of a real site (sites.Site) for content creation."""
    c = site.content
    ai = c.ai or {}
    return CreateConfig(
        label=label or site.deck.site_label or site.name,
        language=c.language or site.deck.language or "en",
        domain=site_check.origin_of(site.site_url),
        tabs=[t.name for t in c.tabs],
        sop_doc_id=c.sop_doc_id, briefs_folder_id=c.briefs_folder_id, drafts_folder_id=c.drafts_folder_id,
        extra_rules=c.extra_rules, max_briefs=c.max_briefs_per_run, max_drafts=c.max_drafts_per_run,
        max_requests=ai.get("max_requests_per_run") or 30, daily_requests=ai.get("daily_requests", 0),
        ai_drafts=c.ai_drafts)


def run(drive, sheets, file_id: str, cfg: CreateConfig, ai, mode: str, today: date,
        http_get=site_check.default_get, log=print) -> CreateResult:
    """One run for one site. mode: "briefs", "drafts" or "both". Never raises for the AI's limit."""
    result = CreateResult()
    state = content_run.State(cs.read_all(sheets, file_id, cs.STATE, 4))
    day = today.isoformat()
    used_before = int(state.get("ai", day, "0") or 0)
    metered = ai_client.Metered(ai, cfg.max_requests, used_before, cfg.daily_requests)
    items = load_items(sheets, file_id, cfg.tabs)
    sop = ""
    if cfg.sop_doc_id:
        try:
            sop = read_text(drive, cfg.sop_doc_id)
        except Exception:                               # noqa: BLE001
            result.problems.append("the SOP could not be read, so no drafts")
    try:
        if mode in ("briefs", "both"):
            brief_step(drive, sheets, file_id, cfg, items, metered, today, http_get, sop, result, log)
        if mode in ("drafts", "both") and sop and cfg.ai_drafts and not result.stopped:
            draft_step(drive, sheets, file_id, cfg, items, metered, today, http_get, sop, result, log)
    finally:
        result.requests = metered.used
        if metered.used:
            state.set("ai", day, str(used_before + metered.used), today)
            state.save(sheets, file_id)
    if getattr(metered.client, "search_ok", True) is False:
        log("  note: the AI service gave no web search to this key, so research came from the model alone")
    log(f"  content creation: {result.text()}")
    return result


def email_text(label: str, result: CreateResult) -> tuple:
    """(subject, body) of the one email of a site for a run, or None when nothing was done."""
    if not result.did_something:
        return None
    lines = [label, ""]
    if result.briefs:
        lines += [f"New briefs ({len(result.briefs)}):"] + [f"- {kw}: {link}" for kw, link in result.briefs] + [""]
    if result.drafts:
        lines += [f"New drafts ({len(result.drafts)}):"] + [f"- {kw}: {link}" for kw, link in result.drafts] + [""]
    if result.no_brief:
        lines += [f"{len(result.no_brief)} rows set to Draft have no brief yet. Set their To Do to Brief first.", ""]
    if result.stopped:
        lines += [f"The run stopped early (the writing service reached a limit or did not answer). {result.waiting} rows wait for the next run.", ""]
    if result.problems:
        lines += ["Please look at: " + "; ".join(dict.fromkeys(result.problems)) + ".", ""]
    subject = (f"New briefs and drafts · {label}" if result.briefs or result.drafts
               else f"Briefs and drafts · {label}")
    return subject, "\n".join(lines)
