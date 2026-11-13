"""
PulseO: from an approved draft to a WordPress draft, and from an approved update into the live page
==================================================================================================
Two flows, both only through the WordPress core REST API (wp_api.Wp), and neither ever publishes:

  To WordPress   the row's To Do is "To WordPress": the draft document becomes block HTML (wp_blocks), its infographics are made
                 and uploaded (infographic), and a DRAFT post is made: a new post, or, when the row is a live page, a separate
                 draft "Update · [title]". The row shows Status "In WordPress", the link to the draft and a note with the date.
  Apply update   the row's To Do is "Apply update": title (only if changed), content and excerpt of the update draft are copied
                 into the ORIGINAL post (URL, post ID, slug, date, featured image, author and comments stay), WordPress keeps
                 the old version as a revision, the temporary draft goes to the trash, and the row shows Status "Updated".

An update is refused, and the reason put in the row's note, when a box "⚠ Remove before publishing" is still in the draft, when
the original post was edited after the draft was made, or when the new content is under 60% of the original (unless the note
of the row says that shorter is intended). Nothing here prints an address, a title or a secret.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import unquote, urlsplit

import infographic
import wp_blocks
from wp_api import WpError

TO_WORDPRESS, APPLY_UPDATE = "To WordPress", "Apply update"
STATUS_IN_WP, STATUS_UPDATED = "In WordPress", "Updated"
UPDATE_PREFIX = "Update · "
MIN_SHARE = 0.6
INTENDED = "shorter is intended"
TOKEN = re.compile(r"\[wp update draft=(\d+) original=(\d+) modified=([^\]\s]+)(?: lang=(\w+))?\]")


@dataclass
class Target:
    """One WordPress install a row goes to."""
    wp: object                      # wp_api.Wp
    home: str                       # the address of the site, for telling an internal link
    language: str = "en"
    brand: object = None            # infographic.Brand
    renderer: object = None         # infographic.ChromiumRenderer or a fake; None: no infographics, boxes stay
    save_copy: object = None        # (file name, PNG bytes) -> None: a copy in the site's Drafts folder


@dataclass
class Outcome:
    ok: bool
    note: str                        # one sentence for the row's note (no tool name)
    changes: dict = field(default_factory=dict)      # {column: value} for the dashboard row
    post_id: int = 0
    infographics: int = 0
    log: list = field(default_factory=list)          # [(action, post id)] for the WordPress Log


def plain_text(content: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content or ""))).strip()


def slug_of(url: str) -> str:
    """The slug of a live page address: the last part of its path ("" for the home page)."""
    path = urlsplit(url or "").path.strip("/")
    return unquote(path.split("/")[-1]) if path else ""


def preview_link(base: str, post_id: int) -> str:
    return f"{base}/?p={int(post_id)}&preview=true"


def _build_content(conv, target: Target, slug: str, article_text: str, extra_notes: list, outcome_log: list) -> tuple:
    """(the post content, how many infographics were made, notes lines). The notes box comes first."""
    parts, made, lines = [], 0, list(extra_notes)
    planned = [s for s in conv.segments if s.kind == "infographic"]
    for seg in conv.segments:
        if seg.kind == "html":
            parts.append(seg.html)
            continue
        spec, why = dict(seg.spec), ""
        spec, dropped = infographic.keep_numbers_in_article(spec, article_text)
        why = infographic.valid(spec) or ("no way to draw it in this run" if target.renderer is None else "")
        data = b""
        if not why:
            try:
                data = infographic.render_card(target.renderer,
                                               infographic.build_html(spec, target.brand or infographic.Brand(language=target.language)))
            except Exception as e:  # noqa: BLE001  a picture that cannot be made is left as its box
                why = f"the renderer failed ({type(e).__name__})"
            why = why or infographic.check_png(data)
        if why:
            lines.append(f"Infographic \"{html.escape(spec['title'])}\" was not made ({html.escape(why)}): its box is in the text.")
            parts.append(seg.html)
            continue
        made += 1
        name = infographic.file_name(slug or conv.title, made)
        media = target.wp.upload_media(name, data, alt=spec["title"])
        outcome_log.append(("uploaded an infographic", media.get("id", 0)))
        if target.save_copy:
            target.save_copy(name, data)
        if dropped:
            lines.append(f"Numbers left out of the infographic because they are not in the article: {html.escape(', '.join(dropped))}.")
        parts.append(wp_blocks.image_block(media["id"], media.get("source_url", ""), spec["title"]))
    if not planned:
        lines.append("No infographic was planned in the draft: add one.")
    return parts, made, lines


def to_wordpress(row: dict, doc_html: str, target: Target, today: date, tab_hint: str = "", live_slug: str = "") -> Outcome:
    """The draft document of a row as a WordPress draft. `live_slug` (the slug of the live page) makes it an update draft."""
    language = target.language
    import content_docs as cd
    headings = cd.DRAFT_HEADINGS.get(language, {})
    conv = wp_blocks.convert(doc_html, target.home, language, headings.get("changes", "") if live_slug else "", headings.get("quality", ""))
    if not conv.title:
        return Outcome(False, f"Not sent to WordPress: the document has no title (H1). {today.isoformat()}")
    wp = target.wp
    original = {}
    if live_slug:
        original = wp.find_by_slug(live_slug, "publish")
        if not original:
            return Outcome(False, f"Not sent to WordPress: the live page was not found in WordPress. {today.isoformat()}")
    category, category_note = wp_blocks.suggest_category(_categories(wp), tab_hint, str(row.get("Content Type", "")))
    extra = [category_note] if category_note else []
    slug = str(row.get("Slug", "")).strip().strip("/").split("/")[-1] if row.get("Slug") else ""
    article_text = plain_text(" ".join(s.html for s in conv.segments if s.kind == "html"))
    log = []
    parts, made, lines = _build_content(conv, target, slug, article_text, extra, log)
    box = wp_blocks.notes_box(conv, language, lines)
    content = "\n\n".join([box] + parts)
    title = (UPDATE_PREFIX + original["title"]["raw"]) if live_slug else conv.title
    post = wp.create_draft(title, content, slug="" if live_slug else slug, categories=[category] if category else None)
    pid = post["id"]
    log.append(("created a draft", pid))
    link = preview_link(wp.base, pid)
    token = (f" [wp update draft={pid} original={original['id']} modified={original['modified']} lang={language}]" if live_slug else "")
    note = (f"In WordPress as a draft{' (update)' if live_slug else ''}, {today.isoformat()}: open the preview, add images, fill the "
            f"SEO title and description, remove the boxes, then {'publish' if not live_slug else 'set To Do to Apply update'}.{token}")
    return Outcome(True, note, {"Status": STATUS_IN_WP, "To Do": "Review", "_attach": link, "_note": note}, pid, made, log)


def _categories(wp) -> list:
    status, _, data = wp._call("GET", "/wp/v2/categories", params={"per_page": 100, "_fields": "id,name"})
    return data if status == 200 and isinstance(data, list) else []


def apply_update(row: dict, target: Target, today: date) -> Outcome:
    """The update draft of a row into the original post. Refuses, with the reason, instead of applying when it is not safe."""
    notes = str(row.get("Notes/Checklist", ""))
    found = TOKEN.findall(notes)
    if not found:
        return Outcome(False, f"Not applied: no update draft is recorded for this row. {today.isoformat()}")
    draft_id, original_id, modified = int(found[-1][0]), int(found[-1][1]), found[-1][2]
    wp = target.wp
    draft, original = wp.read_post(draft_id), wp.read_post(original_id)
    new_content = draft["content"]["raw"]
    if draft.get("status") != "draft":
        return Outcome(False, f"Not applied: the update draft is not a draft any more. {today.isoformat()}")
    if wp_blocks.has_marker(new_content):
        return Outcome(False, f"Not applied: a box \"Remove before publishing\" is still in the draft. {today.isoformat()}")
    if original["modified"] != modified:
        return Outcome(False, f"Not applied: the live page was edited after the draft was made; compare them first. {today.isoformat()}")
    old_len, new_len = len(plain_text(original["content"]["raw"])), len(plain_text(new_content))
    if old_len and new_len < MIN_SHARE * old_len and INTENDED not in notes.lower():
        return Outcome(False, f"Not applied: the new text is {new_len * 100 // old_len}% of the current one; write \"{INTENDED}\" "
                              f"in the note if that is right. {today.isoformat()}")
    fields = {"content": new_content}
    title = draft["title"]["raw"].removeprefix(UPDATE_PREFIX)
    if title != original["title"]["raw"]:
        fields["title"] = title
    excerpt = draft["excerpt"]["raw"]
    if excerpt and excerpt != original["excerpt"]["raw"]:
        fields["excerpt"] = excerpt
    wp.apply_to_post(original_id, fields)
    wp.trash_draft(draft_id)
    note = f"Updated in WordPress, {today.isoformat()}: the old version is in the post's revisions."
    return Outcome(True, note, {"Status": STATUS_UPDATED, "To Do": "Keep", "Published/Updated Date": today.isoformat(), "_note": note},
                   original_id, 0, [("copied an update into the post", original_id), ("moved a draft to the trash", draft_id)])


def posts_with_marker(wp, today: date, days: int = 30) -> list:
    """[(post id, link)] of the posts published in the last `days` days that still hold a box "Remove before publishing"."""
    since = (today - timedelta(days=days)).isoformat() + "T00:00:00"
    return [(p["id"], p.get("link", "")) for p in wp.published_since(since) if wp_blocks.has_marker(p.get("content", {}).get("raw", ""))]
