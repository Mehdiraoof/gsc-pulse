"""
PulseO
=========
Syncs Google Search Console data to a Google Sheet automatically.
Built for SEO teams who want live, always-updated keyword + page data
without manual exports or third-party tools.

Author : Mehdi Raoof — https://linkedin.com/in/mehdiraoof/
Version: 1.1.0

Usage:
    python gsc_pulse.py                        # uses config.yaml
    python gsc_pulse.py --config my.yaml       # custom config file
    python gsc_pulse.py --days 30              # override date range
    python gsc_pulse.py --site https://example.com/ --sheet YOUR_SHEET_ID
"""

import os
import sys
import json
import time
import functools
import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Missing dependency: pip install pyyaml")
    sys.exit(1)

try:
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("Missing dependencies. Run: pip install -r requirements.txt")
    sys.exit(1)

import deck_runner
import mailer
import page_discovery
import reports
import views
import history
import sites


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

TOKEN_FILE = Path.home() / ".gsc_pulse_token.json"
DEFAULT_ROW_LIMIT = 5000     # GSC API max per request
QUERY_PAGE_LIMIT  = 2500     # smaller for the combined dimension tab


# --------------------------------------------------------------------------- #
# Privacy: on GitHub Actions, logs of public repos are visible to anyone,
# so site and sheet details are hidden from the output there.
# --------------------------------------------------------------------------- #

PUBLIC_LOGS = os.environ.get("GITHUB_ACTIONS") == "true"
_SENSITIVE = []


class GscPulseError(Exception):
    pass


def show(value: str) -> str:
    """Returns the value, or a placeholder when running with public logs."""
    return "(hidden)" if PUBLIC_LOGS else value


def scrub(text: str) -> str:
    """Removes site / sheet identifiers (in any encoding) from a message."""
    from urllib.parse import quote
    for v in _SENSITIVE:
        if not v:
            continue
        for form in {v, quote(v, safe=""), quote(v, safe=":/"), v.replace("sc-domain:", "")}:
            if form:
                text = text.replace(form, "(hidden)")
    return text


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def describe_error(e: Exception) -> str:
    """One-line, traceback-free description of an exception (not yet scrubbed)."""
    if isinstance(e, HttpError):
        return f"Google API error ({e.resp.status}): {e.reason}"
    return f"{type(e).__name__}: {e}"


def get_credentials(oauth_client_file: str):
    """
    Returns valid Google credentials.

    Mode 1 (servers / GitHub Actions): if the environment variable
    GSC_SERVICE_ACCOUNT_JSON holds a service account key, use it. No browser.

    Mode 2 (your own computer): OAuth sign-in. First run opens a browser and
    saves a token locally; later runs refresh it silently.
    """
    sa_json = os.environ.get("GSC_SERVICE_ACCOUNT_JSON")
    if sa_json:
        print("  Using service account credentials")
        info = json.loads(sa_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("  Refreshing access token...")
            creds.refresh(Request())
        else:
            if not Path(oauth_client_file).exists():
                print(f"\n  ERROR: credentials file not found at: {oauth_client_file}")
                print("  Download it from Google Cloud Console → APIs & Services → Credentials")
                sys.exit(1)
            print("  Opening browser for Google sign-in (one-time only)...")
            flow = InstalledAppFlow.from_client_secrets_file(oauth_client_file, SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())
        print(f"  Token saved to {TOKEN_FILE}")

    return creds


# --------------------------------------------------------------------------- #
# GSC data fetching
# --------------------------------------------------------------------------- #

def iter_gsc_pages(service, site_url: str, start_date: str, end_date: str,
                   dimensions: list, row_limit: int = DEFAULT_ROW_LIMIT,
                   data_state: str = None, num_retries: int = 0,
                   filter_groups: list = None):
    """
    Yields the rows of the GSC Search Analytics API one page at a time.
    Paginates automatically to retrieve more than one page of rows.
    data_state: None keeps the API default (final data only).
    """
    start_row = 0

    while True:
        body = {
            "startDate":  start_date,
            "endDate":    end_date,
            "dimensions": dimensions,
            "rowLimit":   row_limit,
            "startRow":   start_row,
        }
        if filter_groups:
            body["dimensionFilterGroups"] = filter_groups
        if data_state:
            body["dataState"] = data_state
        try:
            resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute(
                num_retries=num_retries)
        except HttpError as e:
            raise GscPulseError(f"Search Console API error ({e.resp.status}): {e.reason}") from None

        rows = resp.get("rows", [])
        if rows:
            yield rows

        if len(rows) < row_limit:
            break  # last page

        start_row += row_limit


def fetch_gsc(service, site_url: str, start_date: str, end_date: str,
              dimensions: list, row_limit: int = DEFAULT_ROW_LIMIT,
              data_state: str = None, filter_groups: list = None) -> list:
    """
    Fetches all rows from the GSC Search Analytics API for the given dimensions.
    Paginates automatically to retrieve more than 5,000 rows.
    """
    all_rows = []
    for page in iter_gsc_pages(service, site_url, start_date, end_date,
                               dimensions, row_limit, data_state,
                               filter_groups=filter_groups):
        all_rows.extend(page)
    return all_rows


def rows_to_table(rows: list, dimensions: list) -> list:
    """
    Converts raw GSC API rows into a list of lists ready for Google Sheets.
    CTR is stored as a percentage (e.g. 3.42 not 0.0342).
    Position is rounded to 1 decimal.
    """
    headers = [d.capitalize() for d in dimensions] + [
        "Clicks", "Impressions", "CTR (%)", "Avg. Position"
    ]
    table = [headers]

    for row in rows:
        keys       = row.get("keys", [])
        clicks     = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        ctr        = round(row.get("ctr", 0) * 100, 2)
        position   = round(row.get("position", 0), 1)
        table.append(keys + [clicks, impressions, ctr, position])

    return table


# --------------------------------------------------------------------------- #
# Google Sheets helpers
# --------------------------------------------------------------------------- #

def ensure_tab(sheets_service, spreadsheet_id: str, tab_name: str):
    """Creates a tab if it does not already exist."""
    spreadsheet = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()

    existing = [s["properties"]["title"] for s in spreadsheet["sheets"]]

    if tab_name not in existing:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
        ).execute()


def write_tab(sheets_service, spreadsheet_id: str, tab_name: str, data: list):
    """Clears a tab and writes a 2-D list of values starting at A1."""
    ensure_tab(sheets_service, spreadsheet_id, tab_name)

    # Clear existing content
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'"
    ).execute()

    # Write new data
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": data}
    ).execute()

    print(f"  ✓  {tab_name:<20} {len(data) - 1:>5} rows")


def bold_header_row(sheets_service, spreadsheet_id: str, tab_name: str, sheet_id_int: int):
    """Makes the first row bold so it reads as a header."""
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId":       sheet_id_int,
                    "startRowIndex": 0,
                    "endRowIndex":   1,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold"
            }
        }]}
    ).execute()


# --------------------------------------------------------------------------- #
# One site, in phases (v1 tabs for every site first, then history, then Meta)
# --------------------------------------------------------------------------- #

V1_TABS = [
    ("Queries",     ["query"],         DEFAULT_ROW_LIMIT),
    ("Pages",       ["page"],          DEFAULT_ROW_LIMIT),
    ("Date Trend",  ["date"],          DEFAULT_ROW_LIMIT),
    ("Device",      ["device"],        DEFAULT_ROW_LIMIT),
    ("Country",     ["country"],       DEFAULT_ROW_LIMIT),
    ("Query×Page",  ["query", "page"], QUERY_PAGE_LIMIT),
]


@dataclass
class SiteRun:
    """What happened to one site during this run."""
    site: sites.Site
    status: str = "pending"          # pending | ok | failed | disabled
    failure: str = ""
    v1_rows: int = 0
    v1_done: bool = False
    history: object = None           # history.HistoryResult, when history ran
    history_meta: list = field(default_factory=list)
    history_text: str = "off"
    reports_text: str = "off"
    reports_result: object = None    # reports.ReportsResult, when the reports ran
    views_text: str = "off"
    decks_text: str = "off"

    @property
    def name(self) -> str:
        return self.site.name


def fail_site(run: SiteRun, error: Exception):
    """Records a failure. Never raises, so one site cannot block the others."""
    reason = scrub(describe_error(error))
    run.status = "failed"
    run.failure = run.failure or reason
    print(f"  ❌  {run.name}: failed ({reason})")


def site_header(run: SiteRun, phase: str):
    print(f"── {run.name} · {phase} " + "─" * max(3, 40 - len(run.name) - len(phase)))


def sync_main_tabs(gsc_service, sheets_service, run: SiteRun, start_date: str, end_date: str):
    """Phase 1: the v1 tabs (rolling window) of one site."""
    site = run.site
    site_header(run, "Search Console tabs")
    print(f"  Site   : {show(site.site_url)}")
    print(f"  Sheet  : {show('https://docs.google.com/spreadsheets/d/' + site.sheet_id)}")
    print(f"  {'Tab':<22} {'Rows':>5}")
    print(f"  {'-'*22} {'-'*5}")
    for tab_name, dimensions, limit in V1_TABS:
        rows  = fetch_gsc(gsc_service, site.site_url, start_date, end_date, dimensions, limit)
        table = rows_to_table(rows, dimensions)
        write_tab(sheets_service, site.sheet_id, tab_name, table)
        run.v1_rows += len(table) - 1
    run.v1_done = True
    print()


def sync_history(gsc_service, sheets_service, run: SiteRun, started_at: float, off_text: str):
    """Phase 2: the permanent history of one site (skipped when it has no history sheet)."""
    site = run.site
    site_header(run, "History")
    if not site.history_sheet_id:
        print(f"History: off ({off_text})\n")
        return
    print(f"History: on  ({show('https://docs.google.com/spreadsheets/d/' + site.history_sheet_id)})")
    try:
        result = history.run_history(
            sheets_service, site.history_sheet_id,
            functools.partial(iter_gsc_pages, gsc_service, site.site_url,
                              num_retries=history.RETRIES),
            started_at=started_at, clock=time.monotonic, budget=history.TIME_BUDGET_S,
        )
        run.history = result
        run.history_meta = result.meta_rows()
        run.history_text = result.status
    except Exception as e:  # v1 tabs are already written; report and fail at the end
        run.history_meta = [["History status", "error (see run log)"]]
        run.history_text = "error"
        fail_site(run, e)
    print()


def sync_reports(gsc_service, sheets_service, run: SiteRun, rebuild_alarms: bool = False):
    """Phase 1b: the report tabs of one site (skipped when it has no reports sheet)."""
    site = run.site
    site_header(run, "Reports")
    if not site.reports_sheet_id:
        print("reports: off\n")
        return
    today = date.today()

    def fetch(dimensions, first, last, filter_groups=None):
        rows = []
        for page in iter_gsc_pages(gsc_service, site.site_url, first.isoformat(), last.isoformat(),
                                   dimensions, history.HISTORY_ROW_LIMIT,
                                   num_retries=history.RETRIES, filter_groups=filter_groups):
            rows.extend(page)
        return rows

    final = history.last_final_date(
        functools.partial(iter_gsc_pages, gsc_service, site.site_url, num_retries=history.RETRIES),
        today)
    if final is None:
        run.reports_text = "waiting for final data"
        print("reports: no final Search Console data available yet\n")
        return
    try:
        result = reports.run_reports(sheets_service, site.reports_sheet_id, site, fetch, final,
                                     today, rebuild_alarms=rebuild_alarms)
    except Exception:
        run.reports_text = "failed"
        raise
    run.reports_result = result
    run.reports_text = result.summary()
    print()


def sync_views(sheets_service, run: SiteRun, rebuild_views: bool = False):
    """Phase 1c: the readable view tabs (Dashboard, Monthly, ...) of one site's Reports sheet."""
    site = run.site
    site_header(run, "Views")
    if not site.reports_sheet_id:
        print("views: off (no reports sheet)\n")
        return
    done = run.reports_result
    try:
        result = views.run_views(
            sheets_service, site.reports_sheet_id, site,
            newest_month=done.newest_month if done else "",
            new_month=done.new_month if done else "",
            rebuild=rebuild_views, log=print)
    except Exception:
        run.views_text = "failed"
        raise
    run.views_text = result.text()
    print()


def sync_decks(sheets_service, run: SiteRun, smtp, rebuild_decks: bool = False):
    """Phase 1c: the weekly and monthly decks of one site, built from its Reports sheet and emailed."""
    site = run.site
    if not site.deck.enabled:
        return
    site_header(run, "Decks")
    if not site.reports_sheet_id:
        print("decks: off (no reports sheet)\n")
        return
    if smtp is None:
        run.decks_text = "off (no SMTP settings)"
        print("decks: off (no SMTP settings)\n")
        return
    result = deck_runner.run_decks(sheets_service, site.reports_sheet_id, site, smtp, os.environ,
                                   rebuild_decks, print, scrub)
    run.decks_text = result.text()
    if result.failed:
        raise GscPulseError(f"deck delivery failed: {', '.join(result.failed)}")
    print()


def write_meta(sheets_service, run: SiteRun, start_date: str, end_date: str, days: int):
    """Phase 3: the Meta tab, which needs the history result."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta_data = [
        ["Property",        run.site.site_url],
        ["Date range",      f"{start_date} → {end_date}"],
        ["Days",            days],
        ["Last updated",    now_str],
    ] + run.history_meta + [
        ["Built with",      "PulseO by Mehdi Raoof — linkedin.com/in/mehdiraoof/"],
    ]
    write_tab(sheets_service, run.site.sheet_id, "Meta", meta_data)


def print_summary(runs: list):
    """One line per site: name, status, rows written, history status. No URLs or IDs."""
    print("Summary")
    for run in runs:
        if run.status == "disabled":
            print(f"  {run.name}: disabled")
            continue
        appended = run.history.appended if run.history else 0
        print(f"  {run.name}: {run.status} | rows written: v1 {run.v1_rows}, "
              f"history +{appended} | history: {run.history_text} | reports: {run.reports_text}"
              + (f" | views: {run.views_text}" if run.site.reports_sheet_id else "")
              + (f" | decks: {run.decks_text}" if run.site.deck.enabled else ""))
    print()


def discover_page_types(gsc_service, sheets_service, site, start_date: str, end_date: str):
    """Writes the Page Type Discovery tab to the site's Reports sheet. Nothing else runs."""
    site_header_text = "Page type discovery"
    print(f"── {site.name} · {site_header_text} " + "─" * 10)
    print(f"  Period : {start_date}  →  {end_date}")
    page_discovery.run_discovery(
        lambda dims: fetch_gsc(gsc_service, site.site_url, start_date, end_date, dims),
        sheets_service, write_tab, site.reports_sheet_id,
    )
    print("  Read the tab, then add `page_types` rules to the site's entry in SITES_JSON.")
    print("  Nothing is written automatically.\n")


def pick_discovery_site(site_list: list, name: str):
    """The enabled site to run discovery for. Errors never echo the requested name."""
    match = [s for s in site_list if s.name.lower() == name.strip().lower()]
    if not match:
        raise GscPulseError("page type discovery: that site name is not in SITES_JSON")
    site = match[0]
    if not site.enabled:
        raise GscPulseError("page type discovery: that site is disabled")
    if not site.reports_sheet_id:
        raise GscPulseError("page type discovery: that site has no reports_sheet_id")
    return site


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="PulseO — sync Google Search Console data to Google Sheets"
    )
    parser.add_argument("--config", default="config.yaml",
                        help="Path to YAML config file (default: config.yaml)")
    parser.add_argument("--site",  help="Override: GSC site URL (single-site run)")
    parser.add_argument("--sheet", help="Override: Google Sheet ID (single-site run)")
    parser.add_argument("--days",  type=int, help="Override: number of days to pull")
    parser.add_argument("--history-sheet",
                        help="Override: Google Sheet ID of the permanent history store")
    parser.add_argument("--discover-page-types", metavar="SITE_NAME",
                        help="Write the Page Type Discovery tab to the Reports sheet of this "
                             "site (a name from SITES_JSON) and exit; nothing else runs")
    args = parser.parse_args()
    started_at = time.monotonic()

    # ---- Load config -------------------------------------------------------
    # Priority: command-line flags > environment variables > config.yaml
    cfg = {}
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}

    oauth_file = cfg.get("oauth_client_file", "credentials.json")
    days       = args.days  or int(os.environ.get("DAYS") or cfg.get("days", 90))

    # Which sites? SITES_JSON (several sites) or the older single-site settings.
    sites_json = os.environ.get("SITES_JSON")
    try:
        site_list = sites.load_sites(
            sites_json,
            args.site  or os.environ.get("SITE_URL") or cfg.get("site_url"),
            args.sheet or os.environ.get("SHEET_ID") or cfg.get("sheet_id"),
            (args.history_sheet or os.environ.get("HISTORY_SHEET_ID")
             or cfg.get("history_sheet_id")),
            force_single=bool(args.site or args.sheet),
        )
    except sites.SitesConfigError as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)
    multi = bool(sites_json and sites_json.strip()) and not (args.site or args.sheet)
    history_off_text = ("no history_sheet_id for this site" if multi else "no HISTORY_SHEET_ID")

    for s in site_list:                     # every id, even of disabled sites, is scrubbed
        _SENSITIVE.extend(s.sensitive_values())

    # GSC data runs ~2-3 days behind, so stop 3 days ago to avoid half-empty days
    end_dt     = datetime.today() - timedelta(days=3)
    start_dt   = end_dt - timedelta(days=days - 1)
    end_date   = end_dt.strftime("%Y-%m-%d")
    start_date = start_dt.strftime("%Y-%m-%d")

    # ---- Page type discovery: its own command, nothing else runs ------------
    discover_name = (args.discover_page_types or os.environ.get("DISCOVER_SITE") or "").strip()
    discover_site = pick_discovery_site(site_list, discover_name) if discover_name else None
    rebuild_alarms = os.environ.get("REBUILD_ALARMS", "").strip().lower() in ("1", "true", "yes")
    rebuild_decks = os.environ.get("REBUILD_DECKS", "").strip().lower() in ("1", "true", "yes")
    rebuild_views = os.environ.get("REBUILD_VIEWS", "").strip().lower() in ("1", "true", "yes")
    smtp = mailer.settings_from_env(os.environ)
    if smtp:
        _SENSITIVE.extend(smtp.sensitive_values())
    if os.environ.get("ANTHROPIC_API_KEY"):
        _SENSITIVE.append(os.environ["ANTHROPIC_API_KEY"].strip())
    for s in site_list:                     # an address kept in a secret (env:NAME) must not be printed either
        _SENSITIVE.extend(mailer.resolve_recipients(s.deck.recipients, os.environ))

    runs = [SiteRun(s, status="pending" if s.enabled else "disabled") for s in site_list]
    enabled = [r for r in runs if r.status != "disabled"]

    # ---- Banner ------------------------------------------------------------
    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  PulseO  ·  by Mehdi Raoof                  │")
    print("  └─────────────────────────────────────────────┘")
    print(f"  Sites  : {len(enabled)} enabled, {len(runs) - len(enabled)} disabled")
    print(f"  Period : {start_date}  →  {end_date}  ({days} days)")
    print()
    for r in runs:
        if r.status == "disabled":
            print(f"{r.name}: disabled")
    print()

    # ---- Auth (once, shared by every site) ---------------------------------
    print("Authenticating...")
    creds          = get_credentials(oauth_file)
    gsc_service    = build("webmasters", "v3",  credentials=creds)
    sheets_service = build("sheets",    "v4",  credentials=creds)
    print("  ✓  Connected to Google APIs\n")
    if discover_site:
        discover_page_types(gsc_service, sheets_service, discover_site, start_date, end_date)
        print(f"  ✅  Done — updated at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return


    # ---- Phase 1: v1 tabs of every site, one after another -----------------
    for r in enabled:
        try:
            sync_main_tabs(gsc_service, sheets_service, r, start_date, end_date)
        except Exception as e:
            r.history_text = "skipped"
            fail_site(r, e)
            print()

    # ---- Phase 1b: reports of every site (before history, as agreed) -------
    for r in enabled:
        if not r.v1_done:
            continue
        try:
            sync_reports(gsc_service, sheets_service, r, rebuild_alarms)
        except Exception as e:
            fail_site(r, e)
            print()

    # ---- Phase 1c: view tabs (formulas on top of the report tabs) ----------
    for r in enabled:
        if not r.v1_done or r.status == "failed" and r.reports_result is None:
            continue
        try:
            sync_views(sheets_service, r, rebuild_views)
        except Exception as e:
            fail_site(r, e)
            print()

    # ---- Phase 1d: decks (PowerPoint reports by email), still before history ---
    for r in enabled:
        if not r.v1_done:
            continue
        try:
            sync_decks(sheets_service, r, smtp, rebuild_decks)
        except Exception as e:
            r.decks_text = r.decks_text if r.decks_text != "off" else "failed"
            fail_site(r, e)
            print()

    # ---- Phase 2: history of every site (shares one time budget) -----------
    for r in enabled:
        if not r.v1_done:
            continue                        # v1 failed (no access?): nothing to append
        sync_history(gsc_service, sheets_service, r, started_at, history_off_text)

    # ---- Phase 3: Meta tab of every site -----------------------------------
    for r in enabled:
        if not r.v1_done:
            continue                        # the v1 phase failed: nothing to describe
        try:
            write_meta(sheets_service, r, start_date, end_date, days)
        except Exception as e:
            fail_site(r, e)
        if r.status == "pending":
            r.status = "ok"

    # ---- Done --------------------------------------------------------------
    print()
    print_summary(runs)
    failed = [r.name for r in runs if r.status == "failed"]
    if failed:
        raise GscPulseError(f"{len(failed)} of {len(enabled)} site(s) failed: {', '.join(failed)}")
    print(f"  ✅  Done — updated at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # clean, scrubbed message instead of a raw traceback
        msg = describe_error(e)
        print(f"\n  ❌  {scrub(msg)}")
        print("  Tip: check that the service account has access to the property and the sheet,")
        print("  and that the site URL matches the GSC property exactly.")
        sys.exit(1)
