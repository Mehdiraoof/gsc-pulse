"""
PulseO: a refresh tried on a COPY of a real content dashboard
=============================================================
Before a new rule reaches a real dashboard it is tried on a copy of it. The real file is only read: Drive
copies it (with its formats and its hidden state tab) into the folder "PulseO Test", and one forced refresh runs
on the copy with the settings under test. Nothing is emailed. The log holds counts and tab names only, never an
address or a keyword.

The settings under test are not written in the repository: they come from the repository variable
CONTENT_COPY_OVERRIDES, JSON like  {"content": {"skip_types": [...]}, "split": {"Program": [{name, regex}, ...]}}.
"""

import content
import content_sheet as cs
import provision
import sites

FOLDER = "Copy tests"
OWNER_COLUMNS = (content.URL, content.KEY, content.STATUS, content.TODO, "PIC", "Notes/Checklist", "Briefed Date")


class CopyTestError(Exception):
    pass


def snapshot(sheets, file_id: str, tab_names: list) -> dict:
    """{tab: [owner columns of each row]} for the tabs that exist."""
    info = cs.sheet_info(sheets, file_id)
    out = {}
    for name in tab_names:
        if name in info:
            _, rows = cs.table_of(cs.read_all(sheets, file_id, name))
            out[name] = [tuple(str(r.get(c, "")) for c in OWNER_COLUMNS) for r in rows]
    return out


def make_copy(drive, source_id: str, name: str) -> str:
    root = provision.ensure_folder(drive, "PulseO Test")
    folder = provision.ensure_folder(drive, FOLDER, root)
    made = drive.files().copy(fileId=source_id, body={"name": name, "parents": [folder]}, fields="id").execute(
        num_retries=provision.RETRIES)
    return made["id"]


def language_rows(rows: list, folders: tuple) -> int:
    return sum(1 for r in rows if folders and sites.strip_language(r.get(content.URL, ""), folders) != r.get(content.URL, ""))


def run(drive, sheets, real_site, test_site, source_id: str, refresh, label: str, log=print) -> list:
    """Copies the dashboard `source_id`, points `test_site` at the copy, calls refresh(test_site) once and
    compares. Returns the lines of the report (counts and tab names). Raises CopyTestError when a row was lost or
    a column of the owner changed."""
    old_tabs = [t.name for t in real_site.content.tabs]
    copy_id = make_copy(drive, source_id, f"[Copy test] {label} Content Dashboard")
    test_site.content.sheet_id = copy_id
    before = snapshot(sheets, copy_id, old_tabs)
    all_before = {n: cs.table_of(cs.read_all(sheets, copy_id, n))[1] for n in before}
    ideas_before = {n: [r for r in rows if not str(r.get(content.URL, "")).strip() and str(r.get(content.KEY, "")).strip()]
                    for n, rows in all_before.items()}
    titles_before = list(cs.sheet_info(sheets, copy_id))

    refresh(test_site)

    info = cs.sheet_info(sheets, copy_id)
    new_names = [t.name for t in test_site.content.tabs]
    lines = [f"tabs before: {', '.join(n for n in titles_before if n in old_tabs)}",
             f"tabs after: {', '.join(n for n in new_names if n in info)}"]
    after = snapshot(sheets, copy_id, new_names)
    lost = changed = gone = 0
    status = OWNER_COLUMNS.index(content.STATUS)
    for tab, rows in before.items():
        now = after.get(tab, [])
        index = {r[:2]: r for r in now}
        for r in rows:
            if r[:2] not in index:
                lost += 1
            elif index[r[:2]] != r:
                new = index[r[:2]]
                rest_same = new[:status] + new[status + 1:] == r[:status] + r[status + 1:]
                if rest_same and new[status].strip().lower() in content.SETTLED_STATUSES:
                    gone += 1                                    # the page check found the page gone: not this change
                else:
                    changed += 1
    lines.append(f"rows of the old tabs: lost {lost}, owner columns changed {changed}, marked gone by the page check {gone}")
    folders = tuple(test_site.content.tabs[0].lang_folders) if test_site.content.tabs else ()
    covered = {}
    for tab in new_names:
        if tab not in info:
            continue
        _, rows = cs.table_of(cs.read_all(sheets, copy_id, tab))
        pages = [r for r in rows if str(r.get(content.URL, "")).strip()]
        was = len(before.get(tab, []))
        lines.append(f"tab {tab}: {len(rows)} rows ({len(rows) - was:+d}), {len(pages)} pages, "
                     f"{language_rows(pages, folders)} of them in another language")
        covered[tab] = rows
    new_only = [t for t in new_names if t in info and t not in old_tabs]
    keywords = set()
    for tab in new_only:
        for r in covered.get(tab, []):
            for text in (r.get(content.KEY, ""), r.get("Secondary Keywords", "")):
                keywords |= {content.norm(k) for k in str(text).replace("\n", ",").split(",") if k.strip()}
    old_ideas = [r for rows in ideas_before.values() for r in rows]
    answered = sum(1 for r in old_ideas if content.norm(r.get(content.KEY, "")) in keywords)
    still = sum(1 for tab, rows in ideas_before.items() if tab in after
                for r in rows if (str(r.get(content.URL, "")), str(r.get(content.KEY, ""))) in
                {(u, k) for u, k, *_ in after[tab]})
    lines.append(f"old idea rows: {len(old_ideas)}, still there {still}, now answered by a page row of a new tab {answered}")
    lines.append("language tabs: " + str(sum(1 for t in new_names if t in info and sites.language_code(t))))
    for line in lines:
        log(f"  copy test: {line}")
    log(f"  copy test: the copy is in your Drive: {sites_folder_hint()}")
    if lost or changed:
        raise CopyTestError(f"{lost} rows lost, {changed} rows changed in the copy")
    return lines


def sites_folder_hint() -> str:
    return f"PulseO Test / {FOLDER}"
