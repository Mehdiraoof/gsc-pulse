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
                  "drive_folder_id", "page_types", "first_match_wins", "other_label", "other_deck_label", "opportunities",
                  "thresholds",
                  "weekly_segments",
                  "content", "bing", "technical",
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
    drive_folder_id: str = ""            # the Drive folder of the site's files (empty: PulseO / <site label>)
    page_types: list = field(default_factory=list)       # [{"name": ..., "regex": ...}]
    thresholds: dict = field(default_factory=dict)       # overrides of THRESHOLD_DEFAULTS
    weekly_segments: bool = False
    first_match_wins: bool = False       # page types checked in order; a page is in the first that matches
    other_label: str = "Other"           # name of the segment for the pages that match no page type
    other_deck_label: str = ""           # what the client deck calls that segment (else other_label)
    opportunities: dict = field(default_factory=dict)    # overrides of OPPORTUNITY_DEFAULTS (+ brand_terms)
    deck: "DeckConfig" = None
    content: "ContentConfig" = None                      # the content dashboard (v2.5)
    bing: "BingConfig" = None                            # Bing Webmaster data
    technical: "TechnicalConfig" = None                  # the daily health checks and the Technical sheet
    enabled: bool = True
    auth: str = "robot"             # robot: the service account; me: the owner's own Google account

    def sensitive_values(self) -> list:
        """Everything that must never appear in a public log."""
        ids = [v for v in (self.site_url, self.sheet_id, self.history_sheet_id, self.drive_folder_id,
                           self.reports_sheet_id) if v]
        return (ids + [t["regex"] for t in self.page_types] + self.deck.sensitive_values()
                + list(self.opportunities.get("brand_terms", [])) + self.content.sensitive_values()
                + ([self.bing.site_url] if self.bing.site_url else [])
                + list(self.technical.key_pages_extra) + list(self.technical.money_pages))

    def __post_init__(self):
        if self.technical is None:
            self.technical = TechnicalConfig()
        if self.deck is None:
            self.deck = DeckConfig()
        if self.content is None:
            self.content = ContentConfig()
        if self.bing is None:
            self.bing = BingConfig()

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

CONTENT_FIELDS = ("enabled", "sheet_id", "tabs", "page_type_tabs", "skip_types", "tab_names",
                  "seasonal_regex", "min_gap_impressions_90d", "keyword_strong_match", "keyword_min_impressions",
                  "import_from", "gap_exclude_terms", "repair_imports", "languages", "language_min_share", "extra_sitemaps",
                  "sop_doc_id", "briefs_folder_id", "drafts_folder_id", "language", "extra_rules",
                  "max_briefs_per_run", "max_drafts_per_run", "ai", "ai_drafts")
AI_FIELDS = ("provider", "model", "fallback_model", "max_requests_per_run", "daily_requests")
AI_PROVIDERS = ("gemini", "anthropic")
MAX_EXCLUDE_TERMS = 200
IMPORT_FIELDS = ("sheet_id", "source_tab", "target_tab")
MAX_IMPORTS = 8
CONTENT_TAB_FIELDS = ("name", "url_regex", "exclude_regex", "description", "gaps", "landing", "sitemap")
RESERVED_CONTENT_TABS = ("guide", "_state", "keyword seeds", "keyword ideas", "import log",
                         "removed pages")
MAX_CONTENT_TABS = 8


LANGUAGE_CODES = frozenset(
    "aa ab af ak am an ar as ay az ba be bg bn bo br bs ca ce co cs cy da de dv el en eo es et eu fa fi fj fo fr fy "
    "ga gd gl gn gu he hi hr ht hu hy id ig is it ja jv ka kk km kn ko ku ky la lb lo lt lv mg mi mk ml mn mr ms mt "
    "my nb ne nl nn no oc om or pa pl ps pt qu rm ro ru rw sa sd si sk sl sm sn so sq sr st su sv sw ta te tg th ti "
    "tk tl tr tt ug uk ur uz vi xh yi yo zh zu".split())
LANGUAGE_NAMES = {
    "english": "en", "persian": "fa", "farsi": "fa", "arabic": "ar", "turkish": "tr", "russian": "ru",
    "german": "de", "french": "fr", "spanish": "es", "italian": "it", "portuguese": "pt", "dutch": "nl",
    "chinese": "zh", "japanese": "ja", "korean": "ko", "hindi": "hi", "urdu": "ur", "hebrew": "he",
    "kurdish": "ku", "armenian": "hy", "azeri": "az", "pashto": "ps", "indonesian": "id", "polish": "pl",
    "ukrainian": "uk", "swedish": "sv", "greek": "el", "romanian": "ro", "bengali": "bn", "thai": "th",
    "vietnamese": "vi", "فارسی": "fa", "انگلیسی": "en", "عربی": "ar", "ترکی": "tr", "کردی": "ku"}


def language_code(name: str) -> str:
    """The language folder a page type name stands for ("en", "fa-ir", "English"), or "" when the name is not
    a language. A language is never a page type of the content dashboard: it never gets a tab, and its folder
    is ignored when a page is placed. A short code counts only in lower case (a type "It" is not Italian)."""
    n = str(name or "").strip()
    if re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", n) and n[:2] in LANGUAGE_CODES:
        return n
    return LANGUAGE_NAMES.get(n.lower(), "")


def strip_language(url: str, folders) -> str:
    """The address without a leading language folder: https://host/en/song/x -> https://host/song/x."""
    if not folders:
        return url
    m = re.match(r"^((?:https?:)?//[^/?#]+)?/(" + "|".join(re.escape(f) for f in folders) + r")(?=[/?#]|$)(.*)$",
                 url, re.IGNORECASE | re.DOTALL)
    if not m:
        return url
    rest = m.group(3)
    return (m.group(1) or "") + (rest if rest.startswith("/") else "/" + rest)


@dataclass
class ContentTab:
    name: str
    url_regex: str
    exclude_regex: str = ""
    description: str = ""
    gaps: bool = False             # new-topic rows (content gaps) are proposed for this tab
    landing: bool = False          # landing pages: Navigational intent, Pillar content type
    lang_folders: tuple = ()       # language folders of the site (/en/): ignored when a page is placed
    sitemap: str = ""              # a regex on the address of a sitemap file: the tab holds the pages listed in those files
    members: set = field(default_factory=set, compare=False, repr=False)    # url_key of those pages, read at run time

    def matches(self, url: str) -> bool:
        """Does this page belong to the tab? Tried on the URL as given and on its readable form, and with the
        language folder of the site (/en/) taken off: a page in another language is in the tab of its page type.
        A tab with a `sitemap` holds the pages that sitemap lists (the address pattern then only excludes)."""
        from urllib.parse import unquote
        if self.sitemap:
            from content import url_key                                  # (imported here: content does not import sites)
            return url_key(url) in self.members and not (
                self.exclude_regex and re.search(self.exclude_regex, unquote(url, errors="replace")))
        bare = re.sub(r"[?#].*$", "", unquote(url, errors="replace"))     # no query, no fragment
        for candidate in {url, unquote(url, errors="replace"), bare}:
            forms = {candidate, strip_language(candidate, self.lang_folders)}
            if any(re.search(self.url_regex, f) for f in forms) and not (
                    self.exclude_regex and any(re.search(self.exclude_regex, f) for f in forms)):
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
    keyword_strong_match: float = 0.8                    # a search that fits the page this well can be its Primary Keyword
    keyword_min_impressions: int = 10                    # a search with fewer impressions in 90 days cannot win
    imports: list = field(default_factory=list)          # [ImportSpec]
    gap_exclude_terms: list = field(default_factory=list)  # whole words that never become ideas
    repair_imports: bool = False                         # once: merge clearly matched imported rows
    # content creation (briefs and drafts), see content_create.py
    sop_doc_id: str = ""                                 # the site's SOP (a Google Doc); none: briefs only
    briefs_folder_id: str = ""
    drafts_folder_id: str = ""
    language: str = ""                                   # "fa" or "en"; empty: the language of the deck
    extra_rules: str = ""                                # free text added to every prompt
    max_briefs_per_run: int = 10
    max_drafts_per_run: int = 5
    ai: dict = field(default_factory=dict)               # provider, model, fallback_model, max_requests_per_run, daily_requests
    ai_drafts: bool = True                               # false: a Draft row is left untouched (the site stops at the brief)
    # page-type tabs (see resolve_content_tabs): every page type of the site gets a tab, except the skipped ones
    page_type_tabs: bool = False
    skip_types: list = field(default_factory=list)       # page type names (and the "other" label) that get no tab
    tab_names: dict = field(default_factory=dict)        # page type name -> tab name (Article -> Articles)
    notes: list = field(default_factory=list)            # what was decided about tabs, for the run log
    language_min_share: float = 0.5                      # a page of a language folder with a smaller share of its own script is not translated
    languages: dict = field(default_factory=dict)        # two languages of the site: {"en": "", "fa": "/fa"} (code -> folder)
    extra_sitemaps: list = field(default_factory=list)   # sitemap indexes robots.txt does not name (a language folder's)

    @property
    def creation_enabled(self) -> bool:
        return bool(self.briefs_folder_id)

    @property
    def has_sitemap_tabs(self) -> bool:
        return any(t.sitemap for t in self.tabs)

    def sensitive_values(self) -> list:
        rules = [t.url_regex for t in self.tabs] + [t.exclude_regex for t in self.tabs]
        ids = [i.sheet_id for i in self.imports]
        return [v for v in [self.sheet_id, self.seasonal_regex, self.sop_doc_id, self.briefs_folder_id,
                            self.drafts_folder_id] + ids + rules + list(self.extra_sitemaps) if v]

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
    strong = value.get("keyword_strong_match", 0.8)
    if isinstance(strong, bool) or not isinstance(strong, (int, float)) or not 0.3 <= strong <= 1:
        raise SitesConfigError(f"{at}: 'keyword_strong_match' must be a number from 0.3 to 1")
    cfg.keyword_strong_match = float(strong)
    min_kw = value.get("keyword_min_impressions", 10)
    if isinstance(min_kw, bool) or not isinstance(min_kw, int) or not 0 <= min_kw <= 10000:
        raise SitesConfigError(f"{at}: 'keyword_min_impressions' must be a whole number from 0 to 10000")
    cfg.keyword_min_impressions = min_kw
    share = value.get("language_min_share", 0.5)
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0.1 <= share <= 0.9:
        raise SitesConfigError(f"{at}: 'language_min_share' must be a number from 0.1 to 0.9")
    cfg.language_min_share = float(share)
    gap = value.get("min_gap_impressions_90d", 100)
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or gap < 1:
        raise SitesConfigError(f"{at}: 'min_gap_impressions_90d' must be a number, 1 or more")
    cfg.min_gap_impressions_90d = gap
    tabs = value.get("tabs")
    # no tab list, or a skip list / tab names written: the page types make the tabs
    page_type_tabs = value.get("page_type_tabs", tabs is None or "skip_types" in value or "tab_names" in value)
    if not isinstance(page_type_tabs, bool):
        raise SitesConfigError(f"{at}: 'page_type_tabs' must be true or false")
    if tabs is None and page_type_tabs:
        tabs = []
    if not isinstance(tabs, list) or not (1 <= len(tabs) <= MAX_CONTENT_TABS or not tabs and page_type_tabs):
        raise SitesConfigError(f"{at}: 'tabs' must be a list of 1 to {MAX_CONTENT_TABS} "
                               f"{{name, url_regex}} objects (or leave it out and let the page types make the tabs)")
    cfg.page_type_tabs = page_type_tabs
    skip = value.get("skip_types") or []
    if not isinstance(skip, list) or len(skip) > 30 or any(
            not isinstance(s, str) or not TYPE_NAME_PATTERN.fullmatch(s.strip()) for s in skip):
        raise SitesConfigError(f"{at}: 'skip_types' must be a list of page type names")
    cfg.skip_types = [s.strip() for s in skip]
    names_map = value.get("tab_names") or {}
    if not isinstance(names_map, dict) or len(names_map) > 30 or any(
            not isinstance(k, str) or not isinstance(v, str) or not TYPE_NAME_PATTERN.fullmatch(v.strip())
            for k, v in names_map.items()):
        raise SitesConfigError(f"{at}: 'tab_names' must map page type names to short tab names")
    cfg.tab_names = {k.strip(): v.strip() for k, v in names_map.items()}
    seen = set()
    for i, item in enumerate(tabs, start=1):
        tat = f"{at}, tab {i}"
        if not isinstance(item, dict) or "name" not in item or ("url_regex" not in item and "sitemap" not in item) \
                or set(item) - set(CONTENT_TAB_FIELDS):
            raise SitesConfigError(f"{tat}: must be an object with 'name' and 'url_regex' or 'sitemap' "
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
            name=name, url_regex=_regex(item.get("url_regex", "."), tat, "url_regex", True),
            sitemap=_regex(item.get("sitemap"), tat, "sitemap", False),
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
    for key in ("sop_doc_id", "briefs_folder_id", "drafts_folder_id"):
        v = value.get(key, "")
        if v is not None and (not isinstance(v, str) or len(v) > 200):
            raise SitesConfigError(f"{at}: '{key}' must be text")
        setattr(cfg, key, (v or "").strip())
    language = value.get("language", "")
    if language not in ("", "fa", "en"):
        raise SitesConfigError(f"{at}: 'language' must be fa or en")
    cfg.language = language
    langs = value.get("languages") or {}
    if langs:
        if (not isinstance(langs, dict) or len(langs) != 2
                or any(not isinstance(k, str) or k not in LANGUAGE_CODES or not isinstance(v, str)
                       or not re.fullmatch(r"(?:/[\w-]+)?/?", v.strip()) for k, v in langs.items())):
            raise SitesConfigError(f"{at}: 'languages' must name two languages and the folder of each, "
                                   f"like {{\"en\": \"\", \"fa\": \"/fa\"}}")
        cfg.languages = {k: v.strip().rstrip("/") for k, v in langs.items()}
    more = value.get("extra_sitemaps") or []
    if (not isinstance(more, list) or len(more) > 5
            or any(not isinstance(m, str) or not re.match(r"https?://\S+$", m.strip()) for m in more)):
        raise SitesConfigError(f"{at}: 'extra_sitemaps' must be a list of up to 5 sitemap addresses")
    cfg.extra_sitemaps = [m.strip() for m in more]
    rules = value.get("extra_rules", "")
    if not isinstance(rules, str) or len(rules) > 3000:
        raise SitesConfigError(f"{at}: 'extra_rules' must be text of at most 3000 characters")
    cfg.extra_rules = rules.strip()
    for key, default in (("max_briefs_per_run", 10), ("max_drafts_per_run", 5)):
        n = value.get(key, default)
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 50:
            raise SitesConfigError(f"{at}: '{key}' must be a whole number from 0 to 50")
        setattr(cfg, key, n)
    ai_drafts = value.get("ai_drafts", True)
    if not isinstance(ai_drafts, bool):
        raise SitesConfigError(f"{at}: 'ai_drafts' must be true or false")
    cfg.ai_drafts = ai_drafts
    ai = value.get("ai", {}) or {}
    if not isinstance(ai, dict) or set(ai) - set(AI_FIELDS):
        raise SitesConfigError(f"{at}: 'ai' must be an object with: {', '.join(AI_FIELDS)}")
    if ai.get("provider", "gemini") not in AI_PROVIDERS:
        raise SitesConfigError(f"{at}: 'ai.provider' must be gemini or anthropic")
    for key in ("model", "fallback_model"):
        if not isinstance(ai.get(key, ""), str) or len(ai.get(key, "")) > 100:
            raise SitesConfigError(f"{at}: 'ai.{key}' must be a model name")
    for key in ("max_requests_per_run", "daily_requests"):
        n = ai.get(key, 0)
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 1000:
            raise SitesConfigError(f"{at}: 'ai.{key}' must be a whole number from 0 to 1000")
    cfg.ai = dict(ai)
    return cfg


def resolve_content_tabs(site) -> None:
    """
    The tabs of the content dashboard. The tabs written in `content.tabs` come first and are kept as they are.
    With `page_type_tabs` every page type of the site that has no tab yet gets one, in the order of `page_types`
    (then the rest, named `other_label`), with no tab configuration:
      * a type named in `skip_types` gets no tab (its searches behave as before);
      * a language (en, fa, English ...) is never a page type here: no tab, and its folder (/en/) is taken off a
        page's address before the page is placed, so /en/song/x is a page of the Song tab;
      * a type whose tab name (`tab_names`, else the type name) is the name of a tab already there is covered by it;
      * over MAX_CONTENT_TABS the last types get no tab, and the notes say which.
    Everything decided is written to `content.notes` (page type names and counts only), printed by the run.
    """
    cfg = site.content
    if not cfg.enabled:
        return
    folders = tuple(dict.fromkeys(language_code(t["name"]) for t in site.page_types if language_code(t["name"])))
    for t in cfg.tabs:
        t.lang_folders = folders
    if not cfg.page_type_tabs:
        return
    if not cfg.tabs and not site.page_types:
        raise SitesConfigError(f"SITES_JSON site '{site.name}', content: needs 'tabs' or the site's 'page_types'")
    skip = {s.lower() for s in cfg.skip_types}
    known = {t["name"].lower() for t in site.page_types} | {site.other_label.lower()}
    cfg.notes += [f"skip_types names a page type the site does not have ({len(skip - known)})"] if skip - known else []
    taken = {t.name.lower() for t in cfg.tabs} | set(RESERVED_CONTENT_TABS)
    had_tabs = bool(cfg.tabs)
    derived, before = [], []
    for t in site.page_types:
        name, regex = t["name"], t["regex"]
        exclude = "|".join(f"(?:{r})" for r in before) if site.first_match_wins else ""
        if language_code(name):
            cfg.notes.append(f"page type {name}: a language, not a page type: no tab, its pages go to their own page type")
            continue
        before.append(regex)
        if name.lower() in skip:
            cfg.notes.append(f"page type {name}: on the skip list: no tab")
            continue
        derived.append((name, regex, exclude))
    if site.other_label.lower() not in skip:
        derived.append((site.other_label, ".", "|".join(f"(?:{r})" for r in before)))
    else:
        cfg.notes.append(f"page type {site.other_label}: on the skip list: no tab")
    room = MAX_CONTENT_TABS - len(cfg.tabs)
    over = []
    for name, regex, exclude in derived:
        tab_name = cfg.tab_names.get(name, name)
        if tab_name.lower() in taken:
            cfg.notes.append(f"page type {name}: covered by the tab {tab_name}" if tab_name.lower() in
                             {t.name.lower() for t in cfg.tabs} else f"page type {name}: its tab name {tab_name} is taken: no tab")
            continue
        if room <= 0:
            over.append(name)
            continue
        cfg.tabs.append(ContentTab(name=tab_name, url_regex=regex, exclude_regex=exclude,
                                   description=f"Pages of the type {name}.",
                                   gaps=not had_tabs and not cfg.tabs, lang_folders=folders))
        taken.add(tab_name.lower())
        room -= 1
    if over:
        cfg.notes.append(f"NO TAB for {len(over)} page type(s), over the limit of {MAX_CONTENT_TABS} tabs: "
                         f"{', '.join(over)}")
    if not cfg.tabs:
        raise SitesConfigError(f"SITES_JSON site '{site.name}', content: no tab is left (check skip_types)")


@dataclass
class BingConfig:
    """Bing Webmaster data of a site. On unless switched off; a run without the Bing key leaves it alone."""
    enabled: bool = True
    auth: str = "api_key"            # how the access is obtained (a key of PROVIDERS in bing.py)
    site_url: str = ""               # the address of the site in Bing; empty: found in the account by domain


def _parse_bing(value, where: str) -> BingConfig:
    if value is None:
        return BingConfig()
    where = f"{where}, bing"
    if not isinstance(value, dict):
        raise SitesConfigError(f"{where}: must be a JSON object")
    if set(value) - {"enabled", "auth", "site_url"}:
        raise SitesConfigError(f"{where}: has an unknown field (allowed: enabled, auth, site_url)")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SitesConfigError(f"{where}: 'enabled' must be true or false")
    import bing                                  # the providers are listed there
    auth = value.get("auth", "api_key")
    if auth not in bing.PROVIDERS:
        raise SitesConfigError(f"{where}: 'auth' must be one of {', '.join(sorted(bing.PROVIDERS))}")
    site_url = value.get("site_url", "")
    if not isinstance(site_url, str) or len(site_url) > 300:
        raise SitesConfigError(f"{where}: 'site_url' must be an address as text")
    return BingConfig(enabled=enabled, auth=auth, site_url=site_url.strip())


TECHNICAL_CHECKS = ("health", "inspection", "cwv", "crawl", "exports")      # later phases switch the others on
TECHNICAL_FIELDS = ("enabled", "key_pages_extra", "money_pages", "inspection_daily_max", "sitemap_drop_alert",
                    "confirm_minutes", "server_error_share", "large_change_share", "slow_seconds", "checks")


@dataclass
class TechnicalConfig:
    """The technical health of a site: daily checks (health), later the index watch, web vitals, the crawl and the exports.
    On unless switched off; it only runs in the daily technical run, never in the weekly sync."""
    enabled: bool = True
    key_pages_extra: list = field(default_factory=list)      # pages (paths or addresses) checked besides the top 20 by clicks
    money_pages: list = field(default_factory=list)          # pages that weigh more in the audit (later phase)
    inspection_daily_max: int = 1500                         # URL Inspection requests per day (never above 75% of the quota)
    sitemap_drop_alert: float = 0.2                          # a sitemap that loses more of its addresses than this is a problem
    confirm_minutes: int = 30                                # minutes between a failed check and the second check
    server_error_share: float = 0.2                          # more than this share of key pages failing with 5xx or no answer
    large_change_share: float = 0.3                          # more than this share of key pages changing status at once
    slow_seconds: float = 10.0                               # the home page must answer within this time
    checks: dict = field(default_factory=lambda: {k: True for k in TECHNICAL_CHECKS})


def _paths(value, at: str, key: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100 or not all(isinstance(p, str) and 0 < len(p.strip()) <= 500
                                                                  for p in value):
        raise SitesConfigError(f"{at}: '{key}' must be a list of at most 100 page paths or addresses")
    return [p.strip() for p in value]


def _number(value, at: str, key: str, low: float, high: float, whole: bool = False):
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high
    if not ok or (whole and int(value) != value):
        raise SitesConfigError(f"{at}: '{key}' must be {'a whole ' if whole else 'a '}number from {low} to {high}")
    return int(value) if whole else float(value)


def _parse_technical(value, where: str) -> TechnicalConfig:
    if value is None:
        return TechnicalConfig()
    at = f"{where}, technical"
    if not isinstance(value, dict):
        raise SitesConfigError(f"{at}: must be a JSON object")
    if set(value) - set(TECHNICAL_FIELDS):
        raise SitesConfigError(f"{at}: has an unknown field (allowed: {', '.join(TECHNICAL_FIELDS)})")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SitesConfigError(f"{at}: 'enabled' must be true or false")
    checks = {k: True for k in TECHNICAL_CHECKS}
    given = value.get("checks", {})
    if not isinstance(given, dict) or set(given) - set(TECHNICAL_CHECKS) or not all(isinstance(v, bool) for v in given.values()):
        raise SitesConfigError(f"{at}: 'checks' must be an object of true/false for: {', '.join(TECHNICAL_CHECKS)}")
    checks.update(given)
    d = TechnicalConfig()
    return TechnicalConfig(
        enabled=enabled,
        key_pages_extra=_paths(value.get("key_pages_extra"), at, "key_pages_extra"),
        money_pages=_paths(value.get("money_pages"), at, "money_pages"),
        inspection_daily_max=_number(value.get("inspection_daily_max", d.inspection_daily_max), at,
                                     "inspection_daily_max", 0, 1500, whole=True),
        sitemap_drop_alert=_number(value.get("sitemap_drop_alert", d.sitemap_drop_alert), at, "sitemap_drop_alert", 0.01, 0.9),
        confirm_minutes=_number(value.get("confirm_minutes", d.confirm_minutes), at, "confirm_minutes", 0, 120, whole=True),
        server_error_share=_number(value.get("server_error_share", d.server_error_share), at, "server_error_share", 0.05, 1),
        large_change_share=_number(value.get("large_change_share", d.large_change_share), at, "large_change_share", 0.1, 1),
        slow_seconds=_number(value.get("slow_seconds", d.slow_seconds), at, "slow_seconds", 1, 60),
        checks=checks)


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
            drive_folder_id=_text(entry, "drive_folder_id", where, required=False),
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
            bing=_parse_bing(entry.get("bing"), where),
            technical=_parse_technical(entry.get("technical"), where),
            enabled=enabled,
        )
        if site.other_label.lower() in {t["name"].lower() for t in site.page_types} | {"all"}:
            raise SitesConfigError(f"{where}: 'other_label' is already used by a page type "
                                   f"(or is 'All')")
        resolve_content_tabs(site)
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


def apply_overrides(raw: str, name: str, patch: dict) -> str:
    """SITES_JSON text with settings under test applied to one site (for a test on a copy, never written anywhere):
    patch["content"] is merged into the site's content block, and patch["split"] {type name: [{name, regex}, ...]}
    replaces a page type by those types."""
    data = _json_list(raw, "SITES_JSON")
    entry = next((e for e in data if isinstance(e, dict) and str(e.get("name", "")).strip().lower() == name.strip().lower()),
                 None)
    if entry is None:
        raise SitesConfigError("the site to test is not in SITES_JSON")
    entry["content"] = {**(entry.get("content") or {}), **(patch.get("content") or {})}
    split = patch.get("split") or {}
    if split:
        types = []
        for t in entry.get("page_types") or []:
            types += split[t.get("name")] if t.get("name") in split else [t]
        entry["page_types"] = types
    return json.dumps(data)


def load_sites(sites_json, site_url, sheet_id, history_sheet_id, force_single=False,
               sites_json_extra=None, sites_json_more=None) -> list:
    """
    Which sites to run.
      * force_single (command-line --site / --sheet given): just that one site.
      * SITES_JSON set and not blank: the list from it, with SITES_JSON_EXTRA and then SITES_JSON_MORE (same format,
        optional) merged in: an extra entry replaces the entry of the same name.
      * only SITES_JSON_EXTRA set: the list from it.
      * otherwise: the single site from site_url / sheet_id / history_sheet_id.
    """
    extras = [e for e in (sites_json_extra, sites_json_more) if e and e.strip()]       # the extra lists, in this order
    has_extra = bool(extras)
    if not force_single and (has_extra or (sites_json and sites_json.strip())):
        if has_extra:
            merged = sites_json or ""
            for extra in extras:
                merged = merge_sites_json(merged, extra)
            return parse_sites_json(merged)
        return parse_sites_json(sites_json)
    sites = legacy_sites(site_url, sheet_id, history_sheet_id)
    if not sites[0].site_url or not sites[0].sheet_id:
        raise SitesConfigError(
            "no site configured: set the SITES_JSON secret, or site_url and sheet_id "
            "(config.yaml, SITE_URL / SHEET_ID environment variables, or --site / --sheet)")
    return sites
