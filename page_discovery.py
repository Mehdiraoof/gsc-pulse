"""
PulseO: page type discovery
==============================
A helper for writing `page_types` rules. It lists how a site's URLs are laid out
(hosts, first path segments, second path segments, and for flat "/some-slug/"
sites the first word of the slug) with page counts, clicks, impressions and
example URLs, in a `Page Type Discovery` tab of the site's Reports sheet. Rules
are never written automatically: a person (or Claude in chat) reads the tab and
decides which patterns are worth a page type.

URL fragments (`#section`) are dropped and the numbers of the variants added up,
because they are the same page. The log only ever shows counts. The URLs end up
in the private Reports sheet.
"""

from urllib.parse import unquote, urlsplit

TAB_NAME   = "Page Type Discovery"
HEADER     = ["Level", "Pattern", "Pages", "Clicks", "Impressions", "Impr share (%)",
              "Example 1", "Example 2", "Example 3", "Pattern (readable)"]
LEVELS     = ("Subdomain", "Path 1", "Path 2", "Slug start")
MIN_PAGES  = {"Path 1": 2, "Path 2": 2, "Slug start": 3}   # a folder with one page is not a type
MAX_GROUPS = {"Subdomain": 300, "Path 1": 300, "Path 2": 300, "Slug start": 150}


def _keys(url: str) -> list:
    """The group keys one URL belongs to, one per level that applies."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}/"
    segments = [s for s in parts.path.split("/") if s]
    keys = [("Subdomain", base)]
    if len(segments) >= 1:
        keys.append(("Path 1", f"{base}{segments[0]}/"))
    if len(segments) >= 2:
        keys.append(("Path 2", f"{base}{segments[0]}/{segments[1]}/"))
    if len(segments) == 1 and "-" in segments[0]:
        # a flat slug such as /word-another-word/: its first word is a candidate rule
        keys.append(("Slug start", f"{base}{segments[0].split('-')[0]}-"))
    return keys


def merge_fragments(rows: list) -> tuple:
    """Adds up the rows of the same URL that differ only by #fragment.
    Returns ({url: [clicks, impressions]}, number of rows merged away)."""
    merged = {}
    for row in rows:
        parts = urlsplit(row["keys"][0])
        url = parts._replace(fragment="").geturl()
        m = merged.setdefault(url, [0, 0])
        m[0] += int(row.get("clicks", 0))
        m[1] += int(row.get("impressions", 0))
    return merged, len(rows) - len(merged)


def analyze(rows: list) -> tuple:
    """
    rows: GSC API rows with dimension 'page'.
    Returns (table, stats): table is the sheet content (header first), stats is a dict
    of plain counts that is safe to log.
    """
    pages, merged_away = merge_fragments(rows)
    groups = {}
    total_clicks = total_impr = 0
    for url, (clicks, impr) in pages.items():
        total_clicks += clicks
        total_impr += impr
        for key in _keys(url):
            g = groups.setdefault(key, {"pages": 0, "clicks": 0, "impr": 0, "top": []})
            g["pages"] += 1
            g["clicks"] += clicks
            g["impr"] += impr
            g["top"].append((clicks, impr, url))

    def share(impr):
        return round(100 * impr / total_impr, 1) if total_impr else 0

    table = [HEADER, ["Total", "(all pages in the period)", len(pages), total_clicks,
                      total_impr, 100 if total_impr else 0, "", "", "", ""]]
    stats = {"pages": len(pages), "fragments_merged": merged_away, "groups": 0, "hidden_small": 0}

    for level in LEVELS:
        found = [(k[1], g) for k, g in groups.items() if k[0] == level]
        if level in MIN_PAGES:
            stats["hidden_small"] += sum(1 for _, g in found if g["pages"] < MIN_PAGES[level])
            found = [(p, g) for p, g in found if g["pages"] >= MIN_PAGES[level]]
        found.sort(key=lambda pg: (-pg[1]["impr"], -pg[1]["clicks"], pg[0]))
        for pattern, g in found[:MAX_GROUPS[level]]:
            examples = [u for _, _, u in sorted(g["top"], key=lambda t: (-t[0], -t[1], t[2]))[:3]]
            examples += [""] * (3 - len(examples))
            table.append([level, pattern, g["pages"], g["clicks"], g["impr"],
                          share(g["impr"]), *examples, unquote(pattern)])
            stats["groups"] += 1
    return table, stats


def run_discovery(fetch_pages, sheets_service, write_tab, reports_sheet_id: str, log=print) -> dict:
    """
    fetch_pages(dimensions) -> list of GSC rows for the period
    write_tab(sheets_service, sheet_id, tab, table) writes the result (clears the tab first)
    """
    rows = fetch_pages(["page"])
    table, stats = analyze(rows)
    write_tab(sheets_service, reports_sheet_id, TAB_NAME, table)
    log(f"  Page type discovery: {stats['pages']} pages ({stats['fragments_merged']} "
        f"#fragment variants merged) in {stats['groups']} groups "
        f"({stats['hidden_small']} small folders left out)")
    return stats
