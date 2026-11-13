"""
PulseO: what a WordPress install says about the brand of its site (read only)
=============================================================================
During the WordPress access check the install is asked, with GET requests only, for the three things the infographic cards can
use: the logo, the site icon and the theme colours. Nothing found is used on its own: each finding is listed in the Brand tab of the
site's content dashboard, and a colour is used on a card only after the owner writes "yes" in its Use column. The first colour
marked yes is the main colour, the second the accent. The brand name shown in the footer of the cards is a row of the same tab for each
install (its Found text is the name; the install's own site title is the first proposal): the Persian install can have its own name. A colour the owner gave in words (BRAND_GIVEN, "site-1=#8d6bff") is listed
with Use already "yes". The log of the run holds counts only: no address, no colour of a named site, no file name.

Where each thing is read from:
  logo         the site logo of the REST index (a media file), else the image of the logo link in the home page
  site icon    the site icon of the REST index, else the icon link of the home page
  colours      the theme-color tag of the home page, and the colour variables the theme or the page builder prints in the home page
               (--wp--preset--color--*, --e-global-color-*, --ast-global-color-*, --global-palette*, --primary, --accent ...)
"""

import re
import urllib.parse

import content_sheet as cs
import history
import tabs
import wp_check

TAB = "Brand"
HEADER = ["Site", "Install", "Item", "Found", "Use", "Note"]
MAX_COLOURS = 10
NAME_NOTE = "The name shown in the footer of the cards of this install. Change the text if it should be another, then write yes in Use."
HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?\b")
VARIABLE = re.compile(r"(--(?:wp--preset--color--|e-global-color-|ast-global-color-|global-palette|theme-color|brand|primary|accent|main|"
                      r"color-primary|color-accent|color-main)[\w-]*)\s*:\s*([^;}{]+)", re.I)
THEME_COLOUR = re.compile(r"<meta[^>]+name=[\"']theme-color[\"'][^>]*>", re.I)
CONTENT = re.compile(r"content=[\"']([^\"']+)[\"']", re.I)
ICON_LINK = re.compile(r"<link[^>]+rel=[\"'](?:shortcut )?icon[\"'][^>]*>|<link[^>]+rel=[\"']apple-touch-icon[\"'][^>]*>", re.I)
HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
LOGO_IMG = re.compile(r"<img[^>]+class=[\"'][^\"']*custom-logo[^\"']*[\"'][^>]*>", re.I)
SRC = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.I)


def normal_hex(text: str) -> str:
    """#rrggbb in lower case from #rgb, #rrggbb or #rrggbbaa; "" when the text holds no colour."""
    m = HEX.search(text or "")
    if not m:
        return ""
    digits = m.group(1).lower()
    return "#" + (digits if len(digits) == 6 else "".join(c * 2 for c in digits))


def colours_in(page: str) -> list:
    """[(where it is from, #rrggbb)]: the theme-color tag first, then the colour variables of the page, each colour once."""
    found, seen = [], set()

    def add(label, text):
        value = normal_hex(text)
        if value and value not in seen and len(found) < MAX_COLOURS:
            seen.add(value)
            found.append((label, value))
    for tag in THEME_COLOUR.findall(page or ""):
        m = CONTENT.search(tag)
        add("theme-color tag", m.group(1) if m else "")
    for name, value in VARIABLE.findall(page or ""):
        add(name.lstrip("-"), value)
    return found


def _first(pattern, tag_pattern, page: str, base: str) -> str:
    for tag in tag_pattern.findall(page or ""):
        m = pattern.search(tag)
        if m:
            return urllib.parse.urljoin(base + "/", m.group(1))
    return ""


def discover(install: wp_check.Install, opener=wp_check.default_opener) -> dict:
    """{"title", "logo", "icon", "colours": [(where, hex)], "ok"}: what the install and its home page say. Never raises."""
    client = wp_check.Client(install, opener)
    out = {"title": "", "logo": "", "icon": "", "colours": [], "ok": False}
    status, _, root = client.get("/", auth=False)
    if isinstance(root, dict):
        out["title"] = str(root.get("name") or "")
        out["icon"] = str(root.get("site_icon_url") or "")
        logo_id = root.get("site_logo")
        if isinstance(logo_id, int) and logo_id:
            s, _, media = client.get(f"/wp/v2/media/{logo_id}", {"_fields": "source_url"}, auth=False)
            if s == 200 and isinstance(media, dict):
                out["logo"] = str(media.get("source_url") or "")
    code, _, raw = opener(install.base + "/", {"Accept": "text/html", "User-Agent": wp_check.UA})
    page = raw.decode("utf-8", errors="replace") if code == 200 and raw else ""
    if page:
        out["ok"] = True
        out["colours"] = colours_in(page)
        out["logo"] = out["logo"] or _first(SRC, LOGO_IMG, page, install.base)
        out["icon"] = out["icon"] or _first(HREF, ICON_LINK, page, install.base)
    else:
        out["ok"] = bool(isinstance(root, dict))
    return out


def rows_for(install: wp_check.Install, found: dict, given: str = "") -> list:
    """The rows of the Brand tab for one install: [Site, Install, Item, Found, Use, Note]."""
    rows, label = [], install.label or "all"
    rows.append([install.site, label, "Brand name", found.get("title") or "", "", NAME_NOTE])
    rows.append([install.site, label, "Logo", found.get("logo") or "not found", "", "Shown for you; the cards do not use a logo yet."])
    rows.append([install.site, label, "Site icon", found.get("icon") or "not found", "", "Shown for you; the cards do not use it."])
    have = {value for _, value in found.get("colours", [])}
    for where, value in found.get("colours", []):
        note = f"From the {where}." + (" The colour you gave." if value == given else "")
        rows.append([install.site, label, "Colour", value, "yes" if value == given else "", note])
    if given and given not in have:
        rows.append([install.site, label, "Colour", given, "yes", "The colour you gave (not found in the install)."])
    return rows


def lines_for(install: wp_check.Install, found: dict) -> list:
    """The line for the public log: yes and no, counts. No address, no colour, no name."""
    if not found.get("ok"):
        return [f"{install.name}: brand: the install could not be read"]
    return [f"{install.name}: brand: logo {'yes' if found.get('logo') else 'no'}, site icon {'yes' if found.get('icon') else 'no'}, "
            f"{len(found.get('colours', []))} colours found"]


def given_colours(env) -> dict:
    """{site name: #rrggbb} from BRAND_GIVEN ("site-1=#8d6bff, site-2=#112233")."""
    out = {}
    for part in (env.get("BRAND_GIVEN") or "").split(","):
        name, _, value = part.partition("=")
        if name.strip() and normal_hex(value):
            out[name.strip().lower()] = normal_hex(value)
    return out


def write_rows(sheets, file_id: str, rows: list) -> int:
    """Adds the rows that are not in the Brand tab yet (the same site, install, item and finding); what is there stays, and the Use
    column is the owner's. Returns the number of rows added."""
    titles, _ = history.grid_info(sheets, file_id)
    tabs.ensure_tab(sheets, file_id, titles, TAB, HEADER)
    key = lambda r: tuple(str(c) for c in (r[:3] if len(r) > 2 and r[2] == "Brand name" else r[:4]))      # noqa: E731  the owner edits a name
    have = {key(r) for r in cs.read_all(sheets, file_id, TAB)[1:]}
    fresh = [r for r in rows if key(r) not in have]
    if fresh:
        tabs.append(sheets, file_id, TAB, fresh)
    return len(fresh)


def approved(sheets, file_id: str, site_name: str, label: str = "") -> list:
    """The colours the owner marked yes for an install, in the order of the tab: [#rrggbb]. A row of the install "all" counts for
    every install."""
    try:
        values = cs.read_all(sheets, file_id, TAB)
    except Exception:  # noqa: BLE001  no Brand tab yet: nothing is approved
        return []
    out = []
    for r in values[1:]:
        r = list(r) + [""] * (6 - len(r))
        if (str(r[0]).strip().lower() == site_name.lower() and str(r[2]).strip().lower() == "colour"
                and str(r[4]).strip().lower() in ("yes", "y", "true")
                and str(r[1]).strip().lower() in ("all", "", (label or "all").lower())):
            value = normal_hex(str(r[3]))
            if value and value not in out:
                out.append(value)
    return out


def approved_name(sheets, file_id: str, site_name: str, label: str = "") -> str:
    """The brand name the owner marked yes for an install (the row of the install itself before a row for "all"); "" when none."""
    try:
        values = cs.read_all(sheets, file_id, TAB)
    except Exception:  # noqa: BLE001  no Brand tab yet
        return ""
    own = (label or "all").lower()
    found = {}
    for r in values[1:]:
        r = list(r) + [""] * (6 - len(r))
        if (str(r[0]).strip().lower() == site_name.lower() and str(r[2]).strip().lower() == "brand name"
                and str(r[4]).strip().lower() in ("yes", "y", "true") and str(r[3]).strip()):
            found.setdefault(str(r[1]).strip().lower() or "all", str(r[3]).strip())
    return found.get(own) or found.get("all") or ""


def apply(brand, colours: list, name: str = ""):
    """The brand with what the owner approved: the colours (the first is the main colour, the second the accent) and the name.
    Nothing approved: unchanged."""
    import dataclasses
    changes = {}
    if colours:
        changes["primary"] = colours[0]
        if len(colours) > 1:
            changes["accent"] = colours[1]
    if name:
        changes["name"] = name
    return dataclasses.replace(brand, **changes) if changes else brand
