"""
PulseO: site configuration
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

AUTH_MODES = ("robot", "me")
ALLOWED_FIELDS = ("name", "site_url", "auth", "sheet_id", "history_sheet_id", "reports_sheet_id",
                  "page_types", "first_match_wins", "other_label", "other_deck_label", "opportunities",
                  "thresholds",
                  "weekly_segments",
                  "content",
                  "deck", "enabled")
NAME_PATTERN   = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
LEGACY_NAME    = "site-1"
TYPE_NAME_PATTERN = re.compile(r"[\w][\w \-]{0,39}")  # letters (any script), digits, space, - _
RESERVED_SEGMENTS = ("all", "other")
MAX_PAGE_TYPES = 20
WHOLE_NUMBER_MIN = {"movers_top_n": 1, "alarm_max_per_period": 1, "alarm_page_loss_shown": 0,
                    "weekly_movers_top_n": 1}

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
    "weekly_movers_top_n":         10,   # keywords per list in the weekly deck (R Weekly Movers)
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
    first_match_wins: bool = False       # page types checked in order; a page is in the first that matches
    other_label: str = "Other"           # name of the segment for the pages that match no page type
    other_deck_label: str = ""           # what the client deck calls that segment (else other_label)
    opportunities: dict = field(default_factory=dict)    # overrides of OPPORTUNITY_DEFAULTS (+ brand_terms)
    deck: "DeckConfig" = None
    content: "ContentConfig" = None                      # the content dashboard (v2.5)
    enabled: bool = True
    auth: str = "robot"             # robot: the service account; me: the owner's own Google account

    def sensitive_values(self) -> list:
        """Everything that must never appear in a public log."""
        ids = [v for v in (self.site_url, self.sheet_id, self.history_sheet_id,
                           self.reports_sheet_id) if v]
        return (ids + [t["regex"] for t in self.page_types] + self.deck.sensitive_values()
                + list(self.opportunities.get("brand_terms", [])) + self.content.sensitive_values())

    def __post_init__(self):
        if self.deck is None:
            self.deck = DeckConfig()
        if self.content is None:
            self.content = ContentConfig()

    def deck_label(self, segment: str) -> str:
        """What the client deck calls a segment: its page type's deck_label, else the label itself."""
        for t in self.page_types:
            if t["name"] == segment:
                return t.get("deck_label") or segment
        if segment == self.other_label:
            return self.other_deck_label or segment
        return segment

    def opportunity_setting(self, key: str):
        """A setting of the opportunity engine: the site's own value, else the default."""
        if key == "brand_terms":
            return self.opportunities.get("brand_terms", [])
        return self.opportunities.get(key, OPPORTUNITY_DEFAULTS[key])

    def threshold(self, key: str):
        """A report threshold: the site's own value, else the default."""
        return self.thresholds.get(key, THRESHOLD_DEFAULTS[key])


# --------------------------------------------------------------------------- #
# Decks (PowerPoint reports by email)
# --------------------------------------------------------------------------- #

DECK_FIELDS = ("enabled", "language", "site_label", "recipients", "weekly_recipients",
               "monthly_recipients", "weekly", "monthly",
               "brand_color", "accent_color", "logo_url", "ai_summary")
DECK_LANGUAGES = ("en", "fa")
EMAIL_PATTERN = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
ENV_RECIPIENT = re.compile(r"env:([A-Z][A-Z0-9_]*)")       # e.g. env:SMTP_USER: read at send time
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
MAX_RECIPIENTS = 20
OWN_ADDRESS = "env:SMTP_USER"          # what an empty recipients list stands for


@dataclass
class DeckConfig:
    enabled: bool = False
    language: str = "en"
    site_label: str = ""
    recipients: list = field(default_factory=list)       # addresses, or "env:NAME" for a secret
    weekly_recipients: list = None                       # None: same as recipients
    monthly_recipients: list = None
    weekly: bool = True
    monthly: bool = True
    brand_color: str = ""                                # overrides the impressions series colour
    accent_color: str = ""                               # overrides the clicks series colour
    logo_url: str = ""
    ai_summary: bool = False

    def recipients_for(self, kind: str) -> list:
        """The addresses of the 'weekly' or 'monthly' deck: its own list, else `recipients`."""
        own = self.weekly_recipients if kind == "weekly" else self.monthly_recipients
        return self.recipients if own is None else own

    def sensitive_values(self) -> list:
        every = self.recipients + (self.weekly_recipients or []) + (self.monthly_recipients or [])
        fixed = [r for r in every if not ENV_RECIPIENT.fullmatch(r)]
        return [v for v in [self.site_label, self.logo_url] + fixed if v]


def _parse_recipients(rec, at: str, key: str) -> list:
    """A recipients list. Empty means the owner only (SMTP_USER), who also gets every deck that
    goes to other people (see mailer.split_recipients)."""
    if not isinstance(rec, list) or len(rec) > MAX_RECIPIENTS:
        raise SitesConfigError(f"{at}: '{key}' must be a list of up to {MAX_RECIPIENTS} "
                               f"email addresses (empty means: send to SMTP_USER only)")
    for r in rec:
        if not isinstance(r, str) or not (EMAIL_PATTERN.fullmatch(r.strip())
                                          or ENV_RECIPIENT.fullmatch(r.strip())):
            raise SitesConfigError(f"{at}: every recipient must be an email address "
                                   f"(or env:NAME for an address kept in a secret)")
    return [r.strip() for r in rec] or [OWN_ADDRESS]


def _parse_deck(value, where: str) -> DeckConfig:
    """Validates the deck block. Messages say where the problem is, never what the value is."""
    if value is None:
        return DeckConfig()
    at = f"{where}, deck"
    if not isinstance(value, dict):
        raise SitesConfigError(f"{at}: must be a JSON object")
    if set(value) - set(DECK_FIELDS):
        raise SitesConfigError(f"{at}: has an unknown field (allowed: {', '.join(DECK_FIELDS)})")
    for key in ("enabled", "weekly", "monthly", "ai_summary"):
        if key in value and not isinstance(value[key], bool):
            raise SitesConfigError(f"{at}: '{key}' must be true or false")
    cfg = DeckConfig(enabled=value.get("enabled", True), weekly=value.get("weekly", True),
                     monthly=value.get("monthly", True), ai_summary=value.get("ai_summary", False))
    if not cfg.enabled:
        return cfg
    lang = value.get("language", "en")
    if lang not in DECK_LANGUAGES:
        raise SitesConfigError(f"{at}: 'language' must be one of {', '.join(DECK_LANGUAGES)}")
    cfg.language = lang
    label = value.get("site_label")
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60 \
            or any(ord(c) < 32 for c in label):
        raise SitesConfigError(f"{at}: 'site_label' is required (short text, at most 60 characters)")
    cfg.site_label = label.strip()
    cfg.recipients = _parse_recipients(value.get("recipients"), at, "recipients")
    for key in ("weekly_recipients", "monthly_recipients"):
        if key in value:
            setattr(cfg, key, _parse_recipients(value[key], at, key))
    for key in ("brand_color", "accent_color"):
        v = value.get(key, "")
        if v and (not isinstance(v, str) or not HEX_COLOR.fullmatch(v)):
            raise SitesConfigError(f"{at}: '{key}' must look like #1F3A5F")
        setattr(cfg, key, v or "")
    logo = value.get("logo_url", "")
    if logo and (not isinstance(logo, str) or not logo.startswith("https://") or len(logo) > 500):
        raise SitesConfigError(f"{at}: 'logo_url' must be an https:// address")
    cfg.logo_url = logo or ""
    if not (cfg.weekly or cfg.monthly):
        raise SitesConfigError(f"{at}: switch on 'weekly' or 'monthly' (or set enabled to false)")
    return cfg




# --------------------------------------------------------------------------- #
# The content dashboard (v2.5)
# --------------------------------------------------------------------------- #

CONTENT_FIELDS = ("enabled", "sheet_id", "tabs", "seasonal_regex", "min_gap_impressions_90d",
                  "import_from", "gap_exclude_terms", "repair_imports")
MAX_EXCLUDE_TERMS = 200
IMPORT_FIELDS = ("sheet_id", "source_tab", "target_tab")
MAX_IMPORTS = 8
CONTENT_TAB_FIELDS = ("name", "url_regex", "exclude_regex", "description", "gaps", "landing")
RESERVED_CONTENT_TABS = ("guide", "_state", "keyword seeds", "keyword ideas", "import log",
                         "removed pages")
MAX_CONTENT_TABS = 8


@dataclass
class ContentTab:
    name: str
    url_regex: str
    exclude_regex: str = ""
    description: str = ""
    gaps: bool = False             # new-topic rows (content gaps) are proposed for this tab
    landing: bool = False          # landing pages: Navigational intent, Pillar content type

    def matches(self, url: str) -> bool:
        """Does this page belong to the tab? Tried on the URL as given and on its readable form."""
        from urllib.parse import unquote
        bare = re.sub(r"[?#].*$", "", unquote(url, errors="replace"))     # no query, no fragment
        for candidate in {url, unquote(url, errors="replace"), bare}:
            if re.search(self.url_regex, candidate) and not (
                    self.exclude_regex and re.search(self.exclude_regex, candidate)):
                return True
        return False


@dataclass
class ImportSpec:
    """One old sheet whose rows are copied, once, into a tab of the content file."""
    sheet_id: str
    source_tab: str
    target_tab: str


@dataclass
class ContentConfig:
    enabled: bool = False
    sheet_id: str = ""                                   # empty: PulseO creates the file
    tabs: list = field(default_factory=list)             # [ContentTab]
    seasonal_regex: str = ""
    min_gap_impressions_90d: int = 100
    imports: list = field(default_factory=list)          # [ImportSpec]
    gap_exclude_terms: list = field(default_factory=list)  # whole words that never become ideas
    repair_imports: bool = False                         # once: merge clearly matched imported rows

    def sensitive_values(self) -> list:
        rules = [t.url_regex for t in self.tabs] + [t.exclude_regex for t in self.tabs]
        ids = [i.sheet_id for i in self.imports]
        return [v for v in [self.sheet_id, self.seasonal_regex] + ids + rules if v]

    def tab_of(self, url: str):
        """The first tab a page belongs to, or None (pages that match no tab are ignored)."""
        return next((t for t in self.tabs if t.matches(url)), None)


def _regex(value, at: str, key: str, required: bool) -> str:
    if value is None or value == "":
        if required:
            raise SitesConfigError(f"{at}: '{key}' is required")
        return ""
    if not isinstance(value, str):
        raise SitesConfigError(f"{at}: '{key}' must be text")
    try:
        re.compile(value)
    except re.error:
        raise SitesConfigError(f"{at}: '{key}' is not a valid regular expression") from None
    return value


def _parse_content(value, where: str) -> "ContentConfig":
    """The optional `content` block. Messages say where the problem is, never what the value is."""
    if value is None:
        return ContentConfig()
    at = f"{where}, content"
    if not isinstance(value, dict):
        raise SitesConfigError(f"{at}: must be a JSON object")
    if set(value) - set(CONTENT_FIELDS):
        raise SitesConfigError(f"{at}: has an unknown field (allowed: {', '.join(CONTENT_FIELDS)})")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SitesConfigError(f"{at}: 'enabled' must be true or false")
    cfg = ContentConfig(enabled=enabled)
    if not enabled:
        return cfg
    sheet_id = value.get("sheet_id", "")
    if sheet_id is not None and (not isinstance(sheet_id, str) or len(sheet_id) > 200):
        raise SitesConfigError(f"{at}: 'sheet_id' must be text")
    cfg.sheet_id = (sheet_id or "").strip()
    cfg.seasonal_regex = _regex(value.get("seasonal_regex"), at, "seasonal_regex", False)
    gap = value.get("min_gap_impressions_90d", 100)
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or gap < 1:
        raise SitesConfigError(f"{at}: 'min_gap_impressions_90d' must be a number, 1 or more")
    cfg.min_gap_impressions_90d = gap
    tabs = value.get("tabs")
    if not isinstance(tabs, list) or not 1 <= len(tabs) <= MAX_CONTENT_TABS:
        raise SitesConfigError(f"{at}: 'tabs' must be a list of 1 to {MAX_CONTENT_TABS} "
                               f"{{name, url_regex}} objects")
    seen = set()
    for i, item in enumerate(tabs, start=1):
        tat = f"{at}, tab {i}"
        if not isinstance(item, dict) or "name" not in item or "url_regex" not in item \
                or set(item) - set(CONTENT_TAB_FIELDS):
            raise SitesConfigError(f"{tat}: must be an object with 'name' and 'url_regex' "
                                   f"(optional: {', '.join(CONTENT_TAB_FIELDS[2:])})")
        name = item["name"]
        if not isinstance(name, str) or not TYPE_NAME_PATTERN.fullmatch(name.strip()):
            raise SitesConfigError(f"{tat}: 'name' must be a short label (letters, digits, "
                                   f"space, - and _)")
        name = name.strip()
        if name.lower() in RESERVED_CONTENT_TABS or name.lower() in seen:
            raise SitesConfigError(f"{tat}: the name is reserved or used by another tab")
        seen.add(name.lower())
        for key in ("gaps", "landing"):
            if key in item and not isinstance(item[key], bool):
                raise SitesConfigError(f"{tat}: '{key}' must be true or false")
        description = item.get("description", "")
        if not isinstance(description, str) or len(description) > 300:
            raise SitesConfigError(f"{tat}: 'description' must be short text")
        cfg.tabs.append(ContentTab(
            name=name, url_regex=_regex(item["url_regex"], tat, "url_regex", True),
            exclude_regex=_regex(item.get("exclude_regex"), tat, "exclude_regex", False),
            description=description.strip(),
            gaps=item.get("gaps", i == 1),              # content gaps go to the first tab unless said otherwise
            landing=item.get("landing", "landing" in name.lower())))
    imports = value.get("import_from") or []
    if not isinstance(imports, list) or len(imports) > MAX_IMPORTS:
        raise SitesConfigError(f"{at}: 'import_from' must be a list of up to {MAX_IMPORTS} "
                               f"{{sheet_id, source_tab, target_tab}} objects")
    names = {t.name for t in cfg.tabs}
    for i, item in enumerate(imports, start=1):
        iat = f"{at}, import {i}"
        if not isinstance(item, dict) or set(item) != set(IMPORT_FIELDS) \
                or any(not isinstance(item[k], str) or not item[k].strip() or len(item[k]) > 200
                       for k in IMPORT_FIELDS):
            raise SitesConfigError(f"{iat}: must be an object with 'sheet_id', 'source_tab' and "
                                   f"'target_tab' (all text)")
        if item["target_tab"].strip() not in names:
            raise SitesConfigError(f"{iat}: 'target_tab' is not one of the configured tabs")
        cfg.imports.append(ImportSpec(item["sheet_id"].strip(), item["source_tab"].strip(),
                                      item["target_tab"].strip()))
    terms = value.get("gap_exclude_terms") or []
    if not isinstance(terms, list) or len(terms) > MAX_EXCLUDE_TERMS \
            or any(not isinstance(t, str) or not t.strip() or len(t) > 60 for t in terms):
        raise SitesConfigError(f"{at}: 'gap_exclude_terms' must be a list of up to "
                               f"{MAX_EXCLUDE_TERMS} short words")
    cfg.gap_exclude_terms = [t.strip() for t in terms]
    repair = value.get("repair_imports", False)
    if not isinstance(repair, bool):
        raise SitesConfigError(f"{at}: 'repair_imports' must be true or false")
    cfg.repair_imports = repair
    return cfg


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
        if not isinstance(item, dict) or not {"name", "regex"} <= set(item) <= {"name", "regex", "deck_label"}:
            raise SitesConfigError(f"{at}: must be an object with 'name' and 'regex' (and optionally 'deck_label')")
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
        entry = {"name": name, "regex": rx}
        if "deck_label" in item:
            entry["deck_label"] = _parse_deck_label(item["deck_label"], at, "deck_label")
        out.append(entry)
    return out


def _parse_deck_label(value, at: str, key: str) -> str:
    """The name a page type goes by in the client deck. The segment itself keeps its label."""
    if not isinstance(value, str) or not OTHER_LABEL_PATTERN.fullmatch(value.strip()):
        raise SitesConfigError(f"{at}: '{key}' must be a short label (letters, digits, space, - _ and &)")
    return value.strip()


OPPORTUNITY_DEFAULTS = {
    "min_impressions_90d": 300,      # queries / pages with fewer impressions in 90 days are ignored
    "target_ctr_top3": 0.08,         # conservative click rate "if it reached the top 3"
}
MAX_BRAND_TERMS = 30


def _parse_opportunities(value, where: str) -> dict:
    """Optional settings of the opportunity engine. Messages say where, never what."""
    if value is None:
        return {}
    at = f"{where}, opportunities"
    if not isinstance(value, dict):
        raise SitesConfigError(f"{at}: must be a JSON object")
    allowed = ("brand_terms",) + tuple(OPPORTUNITY_DEFAULTS)
    if set(value) - set(allowed):
        raise SitesConfigError(f"{at}: has an unknown setting (allowed: {', '.join(allowed)})")
    out = {}
    if "brand_terms" in value:
        terms = value["brand_terms"]
        if (not isinstance(terms, list) or len(terms) > MAX_BRAND_TERMS
                or any(not isinstance(t, str) or not t.strip() or len(t) > 60 for t in terms)):
            raise SitesConfigError(f"{at}: 'brand_terms' must be a list of up to {MAX_BRAND_TERMS} "
                                   f"short words")
        out["brand_terms"] = [t.strip() for t in terms]
    if "min_impressions_90d" in value:
        v = value["min_impressions_90d"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 1:
            raise SitesConfigError(f"{at}: 'min_impressions_90d' must be a number, 1 or more")
        out["min_impressions_90d"] = v
    if "target_ctr_top3" in value:
        v = value["target_ctr_top3"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise SitesConfigError(f"{at}: 'target_ctr_top3' must be a number above 0, at most 1 "
                                   f"(0.08 means 8%)")
        out["target_ctr_top3"] = v
    return out


OTHER_LABEL_PATTERN =re.compile(r"[\w][\w &\-]{0,39}")        # like a type name, and &


def _parse_other_label(value, where: str) -> str:
    if value is None:
        return "Other"
    if not isinstance(value, str) or not OTHER_LABEL_PATTERN.fullmatch(value.strip()):
        raise SitesConfigError(f"{where}: 'other_label' must be a short label (letters, digits, "
                               f"space, - _ and &)")
    return value.strip()


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
        auth = entry.get("auth", "robot")
        if auth not in AUTH_MODES:
            raise SitesConfigError(f"{where}: 'auth' must be one of {', '.join(AUTH_MODES)}")
        site = Site(
            name=name,
            auth=auth,
            site_url=_text(entry, "site_url", where, required=enabled),
            # a site that runs with the owner's own access gets its sheets created by PulseO
            sheet_id=_text(entry, "sheet_id", where, required=enabled and auth == "robot"),
            history_sheet_id=_text(entry, "history_sheet_id", where, required=False),
            reports_sheet_id=_text(entry, "reports_sheet_id", where, required=False),
            page_types=_parse_page_types(entry.get("page_types"), where),
            thresholds=_parse_thresholds(entry.get("thresholds"), where),
            weekly_segments=_flag(entry, "weekly_segments", where),
            first_match_wins=_flag(entry, "first_match_wins", where),
            opportunities=_parse_opportunities(entry.get("opportunities"), where),
            other_label=_parse_other_label(entry.get("other_label"), where),
            other_deck_label=(_parse_deck_label(entry["other_deck_label"], where, "other_deck_label")
                              if entry.get("other_deck_label") is not None else ""),
            deck=_parse_deck(entry.get("deck"), where),
            content=_parse_content(entry.get("content"), where),
            enabled=enabled,
        )
        if site.other_label.lower() in {t["name"].lower() for t in site.page_types} | {"all"}:
            raise SitesConfigError(f"{where}: 'other_label' is already used by a page type "
                                   f"(or is 'All')")
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


def _json_list(raw: str, label: str) -> list:
    """The entries of a SITES_JSON-style secret. Messages say where, never what."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SitesConfigError(
            f"{label} is not valid JSON ({e.msg} at line {e.lineno}, column {e.colno})") from None
    if not isinstance(data, list):
        raise SitesConfigError(f"{label} must be a JSON list of site objects")
    return data


def merge_sites_json(base: str, extra: str) -> str:
    """
    SITES_JSON plus SITES_JSON_EXTRA as one JSON text. An entry of the extra list replaces the
    entry of the same name (case does not matter) at its place; new names are added at the end.
    The merged text is validated like any SITES_JSON, so the same checks apply to the whole list.
    """
    entries = _json_list(base, "SITES_JSON") if base and base.strip() else []
    seen = set()
    for item in _json_list(extra, "SITES_JSON_EXTRA"):
        name = item.get("name") if isinstance(item, dict) else None
        key = name.strip().lower() if isinstance(name, str) else None
        if key is not None:
            if key in seen:
                raise SitesConfigError("SITES_JSON_EXTRA: the name is used by another entry")
            seen.add(key)
        at = next((i for i, e in enumerate(entries) if key is not None and isinstance(e, dict)
                   and isinstance(e.get("name"), str) and e["name"].strip().lower() == key), None)
        if at is None:
            entries.append(item)
        else:
            entries[at] = item
    return json.dumps(entries)


def load_sites(sites_json, site_url, sheet_id, history_sheet_id, force_single=False,
               sites_json_extra=None) -> list:
    """
    Which sites to run.
      * force_single (command-line --site / --sheet given): just that one site.
      * SITES_JSON set and not blank: the list from it, with SITES_JSON_EXTRA (same format,
        optional) merged in: an extra entry replaces the entry of the same name.
      * only SITES_JSON_EXTRA set: the list from it.
      * otherwise: the single site from site_url / sheet_id / history_sheet_id.
    """
    has_extra = bool(sites_json_extra and sites_json_extra.strip())
    if not force_single and (has_extra or (sites_json and sites_json.strip())):
        if has_extra:
            return parse_sites_json(merge_sites_json(sites_json or "", sites_json_extra))
        return parse_sites_json(sites_json)
    sites = legacy_sites(site_url, sheet_id, history_sheet_id)
    if not sites[0].site_url or not sites[0].sheet_id:
        raise SitesConfigError(
            "no site configured: set the SITES_JSON secret, or site_url and sheet_id "
            "(config.yaml, SITE_URL / SHEET_ID environment variables, or --site / --sheet)")
    return sites
