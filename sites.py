"""
GSC Pulse: site configuration
=============================
Loads the list of sites to sync, either from the SITES_JSON secret (several
sites) or from the older single-site settings (SITE_URL / SHEET_ID /
HISTORY_SHEET_ID, command-line flags, config.yaml).

SITES_JSON is a JSON list, one object per site:

    [
      {"name": "site-1", "site_url": "sc-domain:example.com",
       "sheet_id": "MAIN_SHEET_ID", "history_sheet_id": "HISTORY_SHEET_ID"},
      {"name": "site-2", "site_url": "https://www.example.org/",
       "sheet_id": "MAIN_SHEET_ID_2", "enabled": false}
    ]

Error messages never contain the content of the secret, only the position of
the problem, because logs of a public repository are visible to anyone.
"""

import json
import re
from dataclasses import dataclass, field

ALLOWED_FIELDS = ("name", "site_url", "sheet_id", "history_sheet_id", "reports_sheet_id",
                  "page_types", "thresholds", "weekly_segments", "enabled")
NAME_PATTERN   = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
LEGACY_NAME    = "site-1"
TYPE_NAME_PATTERN = re.compile(r"[\w][\w \-]{0,39}")  # letters (any script), digits, space, - _
RESERVED_SEGMENTS = ("all", "other")
MAX_PAGE_TYPES = 20
WHOLE_NUMBER_MIN = {"movers_top_n": 1, "alarm_max_per_period": 1, "alarm_page_loss_shown": 0}

# Report thresholds (all optional in SITES_JSON, per site)
THRESHOLD_DEFAULTS = {
    "lost_new_min_impressions":    10,   # Lost / New keywords need at least this many impressions
    "movers_top_n":               100,   # rows per mover list (keywords, pages)
    "alarm_clicks_change_pct":     20,   # a segment moves this much month on month
    "alarm_clicks_high_pct":       40,   # ... and is "high" severity from here
    "alarm_min_clicks":            30,   # ignore segments smaller than this (noise)
    "alarm_lost_keyword_clicks":   10,   # a lost keyword had at least this many clicks
    "alarm_page_loss_pct":         50,   # a page lost this much of its clicks
    "alarm_page_loss_min_clicks":  30,   # ... having had at least this many in the month before
    "alarm_page_loss_shown":        5,   # page losses listed per period (the rest become one line)
    "alarm_max_per_period":        10,   # R Alarms rows per period, all types together
    "alarm_aio_drop_pct":          30,   # AIO impressions fell this much month on month
    "alarm_new_pattern_pages":      5,   # pages in Other that share a first path segment or host
}


class SitesConfigError(Exception):
    """The site configuration is unusable. The message is safe to print."""


@dataclass
class Site:
    name: str
    site_url: str = ""
    sheet_id: str = ""
    history_sheet_id: str = ""
    reports_sheet_id: str = ""
    page_types: list = field(default_factory=list)       # [{"name": ..., "regex": ...}]
    thresholds: dict = field(default_factory=dict)       # overrides of THRESHOLD_DEFAULTS
    weekly_segments: bool = False
    enabled: bool = True

    def sensitive_values(self) -> list:
        """Everything that must never appear in a public log."""
        ids = [v for v in (self.site_url, self.sheet_id, self.history_sheet_id,
                           self.reports_sheet_id) if v]
        return ids + [t["regex"] for t in self.page_types]

    def threshold(self, key: str):
        """A report threshold: the site's own value, else the default."""
        return self.thresholds.get(key, THRESHOLD_DEFAULTS[key])


def _flag(entry: dict, key: str, where: str) -> bool:
    value = entry.get(key, False)
    if not isinstance(value, bool):
        raise SitesConfigError(f"{where}: '{key}' must be true or false")
    return value


def _parse_page_types(value, where: str) -> list:
    """Validates page_types. Messages say where the problem is, never what the rule is."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_PAGE_TYPES:
        raise SitesConfigError(f"{where}: 'page_types' must be a list of at most "
                               f"{MAX_PAGE_TYPES} {{name, regex}} objects")
    out, seen = [], set()
    for i, item in enumerate(value, start=1):
        at = f"{where}, page type {i}"
        if not isinstance(item, dict) or set(item) != {"name", "regex"}:
            raise SitesConfigError(f"{at}: must be an object with exactly 'name' and 'regex'")
        name, rx = item["name"], item["regex"]
        if not isinstance(name, str) or not TYPE_NAME_PATTERN.fullmatch(name.strip()):
            raise SitesConfigError(f"{at}: 'name' must be a short label (letters, digits, "
                                   f"space, - and _)")
        name = name.strip()
        if name.lower() in RESERVED_SEGMENTS:
            raise SitesConfigError(f"{at}: the names 'All' and 'Other' are reserved")
        if name.lower() in seen:
            raise SitesConfigError(f"{at}: the name is used by another page type")
        seen.add(name.lower())
        if not isinstance(rx, str) or not rx.strip():
            raise SitesConfigError(f"{at}: 'regex' must be a non-empty text value")
        try:
            re.compile(rx)
        except re.error:
            raise SitesConfigError(f"{at}: 'regex' is not a valid regular expression") from None
        out.append({"name": name, "regex": rx})
    return out


def _parse_thresholds(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SitesConfigError(f"{where}: 'thresholds' must be a JSON object")
    out = {}
    for key, v in value.items():
        if key not in THRESHOLD_DEFAULTS:
            raise SitesConfigError(f"{where}: 'thresholds' has an unknown setting (allowed: "
                                   f"{', '.join(THRESHOLD_DEFAULTS)})")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise SitesConfigError(f"{where}: threshold '{key}' must be a number, 0 or more")
        if key in WHOLE_NUMBER_MIN and (int(v) != v or v < WHOLE_NUMBER_MIN[key]):
            raise SitesConfigError(f"{where}: threshold '{key}' must be a whole number "
                                   f"of at least {WHOLE_NUMBER_MIN[key]}")
        out[key] = v
    return out


def _text(entry: dict, field: str, where: str, required: bool) -> str:
    value = entry.get(field)
    if value is None or value == "":
        if required:
            raise SitesConfigError(f"{where}: '{field}' is required")
        return ""
    if not isinstance(value, str) or not value.strip():
        raise SitesConfigError(f"{where}: '{field}' must be a text value")
    return value.strip()


def parse_sites_json(raw: str) -> list:
    """Validates the SITES_JSON text and returns a list of Site."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SitesConfigError(
            f"SITES_JSON is not valid JSON ({e.msg} at line {e.lineno}, column {e.colno})"
        ) from None

    if not isinstance(data, list) or not data:
        raise SitesConfigError("SITES_JSON must be a non-empty JSON list of site objects")

    sites, names = [], set()
    for i, entry in enumerate(data, start=1):
        where = f"SITES_JSON entry {i}"
        if not isinstance(entry, dict):
            raise SitesConfigError(f"{where}: must be a JSON object")
        if set(entry) - set(ALLOWED_FIELDS):
            raise SitesConfigError(
                f"{where}: has an unknown field (allowed: {', '.join(ALLOWED_FIELDS)})")

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise SitesConfigError(f"{where}: 'enabled' must be true or false")

        name = _text(entry, "name", where, required=True)
        if not NAME_PATTERN.fullmatch(name):
            raise SitesConfigError(
                f"{where}: 'name' must be a short neutral label such as site-1 "
                f"(letters, digits, space, - and _ only; never the real domain)")
        if name.lower() in names:
            raise SitesConfigError(f"{where}: the name is used by another entry")
        names.add(name.lower())

        where = f"SITES_JSON site '{name}'"
        site = Site(
            name=name,
            site_url=_text(entry, "site_url", where, required=enabled),
            sheet_id=_text(entry, "sheet_id", where, required=enabled),
            history_sheet_id=_text(entry, "history_sheet_id", where, required=False),
            reports_sheet_id=_text(entry, "reports_sheet_id", where, required=False),
            page_types=_parse_page_types(entry.get("page_types"), where),
            thresholds=_parse_thresholds(entry.get("thresholds"), where),
            weekly_segments=_flag(entry, "weekly_segments", where),
            enabled=enabled,
        )
        own = [site.sheet_id, site.history_sheet_id, site.reports_sheet_id]
        if len({v for v in own if v}) != len([v for v in own if v]):
            raise SitesConfigError(f"{where}: sheet_id, history_sheet_id and reports_sheet_id "
                                   f"must be three different sheets")
        sites.append(site)

    if not any(s.enabled for s in sites):
        raise SitesConfigError("SITES_JSON has no enabled site")

    # Nothing may be shared between sites' sheets
    seen = {}
    for s in sites:
        if not s.enabled:
            continue
        for kind, value in (("sheet_id", s.sheet_id), ("history_sheet_id", s.history_sheet_id),
                            ("reports_sheet_id", s.reports_sheet_id)):
            if value and value in seen:
                raise SitesConfigError(
                    f"site '{s.name}': '{kind}' is already used by site '{seen[value]}'")
            if value:
                seen[value] = s.name
    return sites


def legacy_sites(site_url, sheet_id, history_sheet_id) -> list:
    """The single-site setup (flags, env vars, config.yaml) as a one-item list."""
    return [Site(name=LEGACY_NAME, site_url=site_url or "", sheet_id=sheet_id or "",
                 history_sheet_id=history_sheet_id or "")]


def load_sites(sites_json, site_url, sheet_id, history_sheet_id, force_single=False) -> list:
    """
    Which sites to run.
      * force_single (command-line --site / --sheet given): just that one site.
      * SITES_JSON set and not blank: the list from it.
      * otherwise: the single site from site_url / sheet_id / history_sheet_id.
    """
    if not force_single and sites_json and sites_json.strip():
        return parse_sites_json(sites_json)
    sites = legacy_sites(site_url, sheet_id, history_sheet_id)
    if not sites[0].site_url or not sites[0].sheet_id:
        raise SitesConfigError(
            "no site configured: set the SITES_JSON secret, or site_url and sheet_id "
            "(config.yaml, SITE_URL / SHEET_ID environment variables, or --site / --sheet)")
    return sites
