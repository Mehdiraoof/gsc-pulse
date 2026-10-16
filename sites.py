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
from dataclasses import dataclass

ALLOWED_FIELDS = ("name", "site_url", "sheet_id", "history_sheet_id", "enabled")
NAME_PATTERN   = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
LEGACY_NAME    = "site-1"


class SitesConfigError(Exception):
    """The site configuration is unusable. The message is safe to print."""


@dataclass
class Site:
    name: str
    site_url: str = ""
    sheet_id: str = ""
    history_sheet_id: str = ""
    enabled: bool = True

    def sensitive_values(self) -> list:
        """Everything that must never appear in a public log."""
        return [v for v in (self.site_url, self.sheet_id, self.history_sheet_id) if v]


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
            enabled=enabled,
        )
        if site.history_sheet_id and site.history_sheet_id == site.sheet_id:
            raise SitesConfigError(f"{where}: 'history_sheet_id' must be a different sheet "
                                   f"than 'sheet_id'")
        sites.append(site)

    if not any(s.enabled for s in sites):
        raise SitesConfigError("SITES_JSON has no enabled site")

    # Nothing may be shared between sites' sheets
    seen = {}
    for s in sites:
        if not s.enabled:
            continue
        for kind, value in (("sheet_id", s.sheet_id), ("history_sheet_id", s.history_sheet_id)):
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
