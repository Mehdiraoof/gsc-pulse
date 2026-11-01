"""
PulseO: copy old content sheets into the content dashboard (v2.6)
==================================================================
`content.import_from` lists old sheets ({sheet_id, source_tab, target_tab}). The first time the
content dashboard is refreshed with such an entry, every row of the old tab is matched to a page
of the new tab and its values are copied over. Then the entry is recorded as done in the file's
hidden state tab and never runs again.

What happens to a row of the old sheet
  * Columns are found by header name. Some old names are synonyms (Suggested Title is Meta Title,
    Meta Description is One-Sentence Description, Suggested Outlinks is Related URLs, Note, Notes
    and Note/Suggested Changes are Notes/Checklist). Columns the dashboard no longer has (Pillar,
    Cluster, Topic, Page type ...) are written into Notes/Checklist as "Pillar: x · Cluster: y".
  * "N/A", "TBD" and empty cells are empty. Dates become YYYY-MM-DD.
  * Matching, first hit wins: the normalized URL; the old Slug against the path of a page; the
    old Primary Keyword against the top searches of a page; the old Primary Keyword against a
    new-topic row that has no page; the words of the old Primary Keyword (and Secondary Keywords)
    against the words of each URL slug (all words, or at least 60%, and exactly one page that
    nobody else has taken). A weaker fit, or several pages that fit, is never merged: the row is
    added and "Possible match: <address>" is written in the Import Log and in the row's Alerts.
  * A matched row keeps what the old sheet says in every column that is the owner's (Primary
    Keyword, Status, To Do, the planning columns); the dashboard fills the performance columns.
  * An old row that matches nothing is still added, never dropped: with its URL when it had one
    (the page may start to rank later), without a URL when it had none (a topic). Its Status is
    kept as it is, whatever the word.

Pure functions plus the Google read in `read_source`. Nothing here prints a keyword or a URL.
"""

import hashlib
import re
from dataclasses import dataclass, field

from googleapiclient.errors import HttpError

import content
import history

NOTES = "Notes/Checklist"
EMPTY_WORDS = {"", "n/a", "na", "#n/a", "tbd"}
DATE_COLUMNS = ("Briefed Date", "Published/Updated Date")
WORDS = "Words Count (Suggested)"
SECONDARY = "Secondary Keywords"
TOP_QUERIES = 5                    # a page's top searches that the old Primary Keyword is compared with
WORDS_CLEAR = 0.6                  # share of the keyword's words that must be in the URL slug
WORDS_POSSIBLE = 0.4               # below this a page is not even a possible match
MAX_POSSIBLE = 3                   # addresses written in a "Possible match"
POSSIBLE_SEPARATOR = " | "

# old header (lower case) -> header of the dashboard
SYNONYMS = {
    "suggested title": "Meta Title",
    "meta description": "One-Sentence Description",
    "suggested outlinks": "Related URLs",
    "note": NOTES, "notes": NOTES, "note/suggested changes": NOTES,
}


class ImportFailed(Exception):
    """The old sheet cannot be read. The message is safe to print (no id, no name)."""


def _h(name) -> str:
    return " ".join(str(name).lower().split())


# the dashboard's columns that an old sheet may fill: everything the owner writes. The columns the
# tool computes every month are left out; Secondary Keywords is handled on its own.
TOOL_COLUMNS = {_h(c) for c in content.OWNED_MONTHLY if c != SECONDARY}
TARGETS = {_h(c): c for c in content.HEADERS if _h(c) not in TOOL_COLUMNS}


def spec_key(spec) -> str:
    """The name under which a finished import is recorded: no id, no tab name."""
    raw = f"{spec.sheet_id}|{spec.source_tab}|{spec.target_tab}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Reading the old sheet
# --------------------------------------------------------------------------- #

def read_source(sheets, spec) -> tuple:
    """(header, rows) of the old tab as the owner sees it. Raises ImportFailed."""
    try:
        resp = history._read(sheets.spreadsheets().values().get(
            spreadsheetId=spec.sheet_id, range=f"'{spec.source_tab}'",
            valueRenderOption="FORMATTED_VALUE"))
    except HttpError as e:
        if e.resp.status in (403, 404):
            raise ImportFailed("an old sheet to import cannot be opened: share it (view access) with "
                               "the service account, or let anyone with the link view it") from None
        if e.resp.status == 400:
            raise ImportFailed("a tab to import was not found in its sheet") from None
        raise
    values = resp.get("values", [])
    if not values:
        raise ImportFailed("a tab to import is empty")
    return [str(c) for c in values[0]], values[1:]


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #

def clean(value, header: str = ""):
    """"" for empty, N/A, TBD and a placeholder that repeats the column name; text otherwise."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if text.lower() in EMPTY_WORDS or (header and text.lower() == header.strip().lower()):
        return ""
    return text


def lines_of(text: str) -> list:
    return [ln.strip() for ln in str(text).replace("\r", "").split("\n") if ln.strip()]


def column_value(target: str, value):
    """The cell as the dashboard keeps it."""
    if value == "":
        return ""
    if target in DATE_COLUMNS:
        d = content.parse_date(value)
        return d.isoformat() if d else str(value).strip()
    if target == WORDS:
        digits = re.sub(r"[,\s]", "", str(value))
        return int(digits) if digits.isdigit() else str(value).strip()
    if target == SECONDARY:
        return ", ".join(lines_of(value))
    if target == content.URL:
        return content.display_url(str(value))
    if "\n" in str(value):
        return "\n".join(lines_of(value))
    return str(value).strip()


# --------------------------------------------------------------------------- #
# The rows of the old sheet
# --------------------------------------------------------------------------- #

@dataclass
class OldRow:
    n: int                                   # row number in the old sheet
    values: dict = field(default_factory=dict)     # dashboard header -> cleaned value (non-empty only)
    legacy: list = field(default_factory=list)     # [(old header, value)] of columns the dashboard lacks
    slug: str = ""

    @property
    def keyword(self) -> str:
        return str(self.values.get(content.KEY, ""))

    @property
    def url(self) -> str:
        return str(self.values.get(content.URL, ""))

    @property
    def status(self) -> str:
        return str(self.values.get(content.STATUS, ""))


def parse_rows(header: list, rows: list) -> list:
    """The non-empty rows of the old tab as OldRow objects."""
    plan = []                                 # (column, dashboard header or None, old header)
    for i, name in enumerate(header):
        k = _h(name)
        if not k or k in TOOL_COLUMNS:
            continue
        plan.append((i, SYNONYMS.get(k) or TARGETS.get(k), str(name).strip()))
    out = []
    for r, raw in enumerate(rows, start=2):
        old = OldRow(n=r)
        for i, target, name in plan:
            cell = clean(raw[i] if i < len(raw) else "", name)
            if cell == "":
                continue
            if target is None:
                old.legacy.append((name, str(cell)))
            elif target == content.SLUG:
                old.slug = str(cell)
                old.values[target] = column_value(target, cell)
            elif target == NOTES and NOTES in old.values:
                old.values[NOTES] += "\n" + column_value(target, cell)
            else:
                old.values[target] = column_value(target, cell)
        if old.values or old.legacy:
            out.append(old)
    return out


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def slug_key(text: str) -> str:
    """A slug or a path as a comparable string: readable, no slashes at the ends, lower case."""
    t = content.fold_url(content.decode_url(text))
    t = re.sub(r"[?#].*$", "", re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", t, flags=re.IGNORECASE))
    return t.strip("/").lower()


def slug_words(url: str) -> set:
    """The topic words of the last part of an address (its slug); empty for the home page."""
    _, path = content._split_url(url)
    return content.topic_words(path.rstrip("/").rsplit("/", 1)[-1])


def keyword_phrases(old) -> list:
    """The word sets an old row can be matched on: its Primary Keyword, then each Secondary
    Keyword that has at least two words (one word says too little)."""
    out = []
    primary = content.topic_words(old.keyword)
    if primary:
        out.append(primary)
    for part in re.split(r"[,\n،]+", str(old.values.get(SECONDARY, ""))):
        words = content.topic_words(part)
        if len(words) >= 2:
            out.append(words)
    return out


def words_fit(old, index, claimed) -> list:
    """[(share of the keyword's words found in the slug, impressions, rec)] for the pages that
    fit at least a little, best first. A page someone else has taken is not offered."""
    phrases = keyword_phrases(old)
    if not phrases:
        return []
    scored = []
    for rec, slug, impressions in index.pages:
        if id(rec) in claimed or not slug:
            continue
        share = max(len(p & slug) / len(p) for p in phrases)
        if share >= WORDS_POSSIBLE:
            scored.append((share, impressions, rec))
    scored.sort(key=lambda s: (-s[0], -s[1], content.url_key(s[2]["row"].get(content.URL, ""))))
    return scored


def possible_text(recs: list) -> str:
    return POSSIBLE_SEPARATOR.join(content.display_url(str(r["row"].get(content.URL, "")))
                                   for r in recs[:MAX_POSSIBLE])


class Index:
    """The pages of a tab (rows that exist and pages that are about to get a row) by the things an
    old row can be matched on."""

    def __init__(self, recs: list, ctx):
        self.by_url, self.by_last, self.by_path = {}, {}, {}
        self.by_folded = {}                 # the fallback: spelling without half space / tatweel
        self.by_query, self.by_topic = {}, {}
        self.pages = []                     # (rec, words of the URL slug, impressions)
        for rec in recs:
            row = rec["row"]
            url = str(row.get(content.URL, "")).strip()
            if rec.get("remove"):           # a row that is going away is nobody's page
                continue
            if url:
                self.by_url.setdefault(content.url_key(url), rec)
                self.by_folded.setdefault(content.fold_url(content.url_key(url)), []).append(rec)
                path = slug_key(url)
                self.by_path.setdefault(path, []).append(rec)
                self.by_last.setdefault(path.rsplit("/", 1)[-1], []).append(rec)
                stats = rec.get("stats") or ctx.data.stats.get(content.url_key(url))
                self.pages.append((rec, slug_words(url), stats.impressions if stats else 0))
                pairs = [(q[0], q[2]) for q in
                         content.nonbrand_queries(stats, ctx.brand)[:TOP_QUERIES]] if stats else []
                kw = content.norm(row.get(content.KEY, ""))
                if kw:
                    pairs.append((kw, stats.impressions if stats else 0))
                for q, n in pairs:
                    self.by_query.setdefault(content.norm(q), []).append((n, rec))
            elif rec.get("topic") and not rec.get("new"):
                kw = content.norm(row.get(content.KEY, ""))
                if kw:
                    self.by_topic.setdefault(kw, []).append(rec)


def _free(recs, claimed):
    return [r for r in recs if id(r) not in claimed]


def find_matches(old_rows: list, index: Index, claimed: set = None, topics: bool = True) -> dict:
    """{OldRow.n: (kind, rec or None)}. kinds: url, slug, keyword, topic, words; (kind "duplicate",
    rec) when the page was already taken by an earlier old row; ("possible", [recs]) when pages
    fit the keyword's words but not clearly enough to merge; ("added", None) when nothing matches.
    `claimed`: pages that are already taken (ids of records); `topics`: match on a topic row of
    the file too (off when the old rows are themselves topic rows of the file)."""
    claimed = set() if claimed is None else claimed
    found = {}
    duplicate = set()
    for old in old_rows:                                              # 1. the URL
        if not old.url:
            continue
        rec = index.by_url.get(content.url_key(old.url))
        if rec is None:
            continue
        if id(rec) in claimed:
            found[old.n] = ("duplicate", rec)
            duplicate.add(old.n)
        else:
            claimed.add(id(rec))
            found[old.n] = ("url", rec)
    for old in old_rows:                          # 1b. the same address spelled with / without a half space
        if old.n in found or not old.url:         #     or tatweel: only when the exact address found nothing,
            continue                              #     and only if it points to one free page
        hits = _free(index.by_folded.get(content.fold_url(content.url_key(old.url)), []), claimed)
        if len(hits) == 1:
            claimed.add(id(hits[0]))
            found[old.n] = ("url~", hits[0])
    for old in old_rows:                                              # 2. the slug
        if old.n in found or not old.slug:
            continue
        s = slug_key(old.slug)
        if not s:
            continue
        hits = _free(index.by_path.get(s, []) if "/" in s else index.by_last.get(s, []), claimed)
        if len(hits) == 1:
            claimed.add(id(hits[0]))
            found[old.n] = ("slug", hits[0])
    for old in old_rows:                                              # 3. the keyword against top searches
        if old.n in found or not old.keyword:
            continue
        hits = _free([r for _, r in sorted(index.by_query.get(content.norm(old.keyword), []),
                                           key=lambda x: -x[0])], claimed)
        if hits:
            claimed.add(id(hits[0]))
            found[old.n] = ("keyword", hits[0])
    for old in old_rows:                                              # a topic row of the file
        if not topics or old.n in found or not old.keyword:
            continue
        hits = _free(index.by_topic.get(content.norm(old.keyword), []), claimed)
        if hits:
            claimed.add(id(hits[0]))
            found[old.n] = ("topic", hits[0])
    for old in old_rows:                                              # 4. the words of the URL slug
        if old.n in found or not old.keyword:
            continue
        fits = words_fit(old, index, claimed)
        clear = [f for f in fits if f[0] >= WORDS_CLEAR]
        if len(clear) == 1 and not old.url:        # a row that has its own address is never merged by words
            claimed.add(id(clear[0][2]))
            found[old.n] = ("words", clear[0][2])
        elif fits:                                 # 5. several fit, or only a weaker one: do not merge
            found[old.n] = ("possible", [f[2] for f in fits[:MAX_POSSIBLE]])
    for old in old_rows:
        found.setdefault(old.n, ("added", None))
    return found


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #

def legacy_line(old: OldRow) -> str:
    return " · ".join(f"{name}: {value}" for name, value in old.legacy)


def values_for(old: OldRow, rec, kind: str) -> dict:
    """What to write for an old row: its values, with the pieces that have no column of their own
    folded into Notes/Checklist (nothing is dropped)."""
    vals = dict(old.values)
    notes = [legacy_line(old)] if old.legacy else []
    has_page = rec is not None and bool(str(rec["row"].get(content.URL, "")).strip())
    if rec is not None:
        vals.pop(content.URL, None)               # the file's own page (or topic) row keeps its address
        dest = content.url_key(rec["row"].get(content.URL, "")) if has_page else ""
        if old.url and content.url_key(old.url) != dest:
            notes.append(f"Old URL: {old.url}")
        if has_page and SECONDARY in vals:        # the dashboard writes this column itself, every month
            notes.append(f"Secondary keywords before: {vals.pop(SECONDARY)}")
    if NOTES in vals:
        notes.append(vals[NOTES])
    if notes:
        vals[NOTES] = "\n".join(n for n in notes if n)
    return vals


@dataclass
class Entry:
    src_row: int
    keyword: str
    url: str
    status: str
    kind: str
    page: str = ""


@dataclass
class ImportReport:
    tab: str
    source_rows: int = 0
    entries: list = field(default_factory=list)
    skipped: int = 0
    possible: dict = field(default_factory=dict)      # row key -> "Possible match" addresses, for the Alerts

    def count(self, kind: str) -> int:
        return sum(1 for e in self.entries if e.kind == kind)

    def text(self) -> str:
        c = self.count
        return (f"{self.source_rows} old rows: {c('url')} by URL, {c('url~')} by URL spelled with or "
                f"without a half space or tatweel, {c('slug')} by slug, "
                f"{c('keyword')} by keyword, {c('words')} by URL words, {c('topic')} to a topic row; "
                f"{c('possible')} with a possible match (added, not merged), "
                f"{c('added') + c('duplicate')} unmatched (added as new rows)"
                + (f", {self.skipped} skipped (no keyword, no URL)" if self.skipped else ""))


def apply(old_rows: list, recs: list, ctx, tab: str) -> ImportReport:
    """Copies the old rows into the records of one tab (`recs` of content_run.refresh): matched
    records get the old values, unmatched old rows are appended as new records."""
    report = ImportReport(tab=tab, source_rows=len(old_rows))
    matches = find_matches(old_rows, Index(recs, ctx))
    for old in old_rows:
        kind, rec = matches[old.n]
        status = old.status
        possible = ""
        if kind == "possible":                    # added like an unmatched row, with a pointer
            possible = possible_text(rec)
            rec = None
        if kind in ("url", "url~", "slug", "keyword", "words", "topic"):
            vals = values_for(old, rec, kind)
            rec["row"].update(vals)
            rec["import"] = {**rec.get("import", {}), **vals}
            page = str(rec["row"].get(content.URL, "")) or str(rec["row"].get(content.KEY, ""))
            report.entries.append(Entry(old.n, old.keyword, old.url, status, kind,
                                        content.path_of(page) if page.startswith("http") else page))
            continue
        vals = values_for(old, None, kind)
        if old.url:
            vals[content.URL] = content.display_url(old.url)
            vals.setdefault(content.STATUS, "Published")
            new = {"row": dict(vals), "new": True, "topic": False, "blank": False, "import": vals}
        elif old.keyword:
            vals.setdefault(content.STATUS, "Inboxed")
            new = {"row": dict(vals), "new": True, "topic": True, "blank": False, "import": vals}
        else:
            report.skipped += 1
            report.entries.append(Entry(old.n, "", "", status, "skipped"))
            continue
        if possible:
            new["possible"] = possible
            report.possible[content.row_key(new["row"])] = possible
        recs.append(new)
        report.entries.append(Entry(old.n, old.keyword, old.url, vals.get(content.STATUS, ""), kind,
                                    "(a duplicate of an earlier row)" if kind == "duplicate"
                                    else (content.POSSIBLE_ALERT + possible) if possible else ""))
    return report


LOG_HEADER = ["Source tab", "Source row", "Primary Keyword", "Status", "Old URL", "Matched by", "Page"]
KIND_TEXT = {"url": "URL", "url~": "URL (half space / tatweel differs)", "slug": "Slug", "keyword": "Keyword",
             "words": "URL words", "topic": "Topic row",
             "possible": "Possible match: added, not merged",
             "added": "No match: added as a new row", "duplicate": "Same URL as an earlier row: added",
             "skipped": "Skipped: no keyword and no URL",
             "r-slug": "Repair: merged by slug", "r-keyword": "Repair: merged by keyword",
             "r-words": "Repair: merged by URL words", "r-possible": "Repair: possible match, not merged",
             "r-none": "Repair: still no match"}


def log_rows(spec, report: ImportReport) -> list:
    return [[spec.source_tab, e.src_row, e.keyword, e.status, e.url, KIND_TEXT.get(e.kind, e.kind),
             e.page] for e in report.entries]


# --------------------------------------------------------------------------- #
# Repair: rows an earlier import added without a page (once, when the config asks for it)
# --------------------------------------------------------------------------- #

REPAIR_REASON = "Merged into its page row (import repair)"


@dataclass
class RepairReport(ImportReport):
    def text(self) -> str:
        c = self.count
        return (f"repair of {self.tab}: {self.source_rows} imported rows without a page checked: "
                f"{c('r-slug')} merged by slug, {c('r-keyword')} by keyword, {c('r-words')} by URL words; "
                f"{c('r-possible')} with a possible match (not merged), {c('r-none')} still without a page")


def old_row_of(row: dict, n: int) -> OldRow:
    """An imported topic row of the file, read back as an old row: its owner's columns."""
    old = OldRow(n=n, slug=str(row.get(content.SLUG, "")).strip())
    for name, value in row.items():
        target = TARGETS.get(_h(name))
        if target is None or target == content.URL or str(value).strip() == "":
            continue
        old.values[target] = value if isinstance(value, (int, float)) else str(value).strip()
    return old


def merge_into(page_row: dict, vals: dict) -> dict:
    """What of an old row goes into a page row that already exists. The columns the dashboard fills
    itself when it creates a row (keyword, status, to do, intent, slug ...) take the old value;
    every other column only where the page row has nothing; notes are added to."""
    out = {}
    for name, value in vals.items():
        have = str(page_row.get(name, "")).strip()
        if name == NOTES and have:
            if str(value).strip() and str(value).strip() not in have:
                out[name] = have + "\n" + str(value).strip()
        elif name in content.ON_CREATE or not have:
            out[name] = value
    return out


def repair(entries: list, recs: list, ctx, tab: str) -> RepairReport:
    """Matches again the rows an earlier import added as topics (no page found then), with the
    rules as they are now, and merges each clearly matched one into its page row: the page row gets
    the old row's values, the old row is marked for removal (the caller copies it to `Removed Pages`
    first). A possible match is only listed. `entries`: the Import Log rows of the source, as dicts
    by LOG_HEADER names; `recs`: the records of the target tab."""
    report = RepairReport(tab=tab)
    index = Index(recs, ctx)
    claimed = set()                              # pages an earlier import already gave to an old row
    for e in entries:
        if str(e.get("Matched by", "")) in {KIND_TEXT[k] for k in ("url", "url~", "slug", "keyword", "words")}:
            claimed.update(id(r) for r in index.by_path.get(slug_key(str(e.get("Page", ""))), []))
    todo, taken = [], set()
    for e in entries:
        if (str(e.get("Matched by", "")) not in (KIND_TEXT["added"], KIND_TEXT["possible"])
                or str(e.get("Old URL", "")).strip()):
            continue
        keyword = content.norm(e.get("Primary Keyword", ""))
        rec = next((r for r in recs if keyword and r.get("topic") and not r.get("new") and not r.get("remove")
                    and id(r) not in taken and content.norm(r["row"].get(content.KEY, "")) == keyword), None)
        if rec is not None:
            taken.add(id(rec))
            todo.append((e, rec))
    olds, owners = [], {}
    for i, (e, rec) in enumerate(todo, start=1):
        old = old_row_of(rec["row"], i)
        olds.append(old)
        owners[old.n] = (e, rec)
    report.source_rows = len(olds)
    matches = find_matches(olds, index, claimed=claimed, topics=False)
    for old in olds:
        e, topic_rec = owners[old.n]
        kind, rec = matches[old.n]
        src = e.get("Source row", "")
        if kind in ("slug", "keyword", "words"):
            vals = merge_into(rec["row"], values_for(old, rec, kind))
            rec["row"].update(vals)
            rec["import"] = {**rec.get("import", {}), **vals}
            topic_rec["remove"] = REPAIR_REASON
            topic_rec["remove_kind"] = "merged"
            topic_rec["moved_to"] = content.display_url(str(rec["row"].get(content.URL, "")))
            report.entries.append(Entry(src, old.keyword, "", old.status, "r-" + kind,
                                        content.path_of(str(rec["row"].get(content.URL, "")))))
        elif kind == "possible":
            text = possible_text(rec)
            topic_rec["possible"] = text
            report.possible[content.row_key(topic_rec["row"])] = text
            report.entries.append(Entry(src, old.keyword, "", old.status, "r-possible",
                                        content.POSSIBLE_ALERT + text))
        else:
            report.entries.append(Entry(src, old.keyword, "", old.status, "r-none"))
    return report
