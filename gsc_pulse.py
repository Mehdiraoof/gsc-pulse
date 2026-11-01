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
import re
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

import auth
import content_import
import content_run
import fake_site
import deck_runner
import mailer
import page_discovery
import provision
import reports
import site_check
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
    content_text: str = "off"
    decks_text: str = "off"
    gsc: object = None               # the Google clients this site is read and written with
    sheets: object = None
    drive: object = None

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


def source_reader(run: SiteRun):
    """spec -> (header, rows) of an old sheet named in content.import_from. The sheet is read as the
    service account (it can open sheets shared with it, or with anyone who has the link); the
    owner's own client is the second try. Nothing is ever written to an old sheet."""
    clients = []

    def read(spec):
        if not clients:
            raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON")
            if raw:
                try:
                    creds = service_account.Credentials.from_service_account_info(
                        json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
                    clients.append(build("sheets", "v4", credentials=creds))
                except (ValueError, KeyError):
                    print("  content: the service account key could not be used to read an old sheet")
            clients.append(run.sheets)
        first = None
        for svc in clients:
            try:
                return content_import.read_source(svc, spec)
            except content_import.ImportFailed as e:
                first = first or e
        raise first

    return read


def sync_content(gsc_service, run: SiteRun, smtp, force: bool = False, cleanup_gone_since: str = ""):
    """Phase 1c2: the content dashboard of one site (own-access sites only: PulseO has to be able
    to create the file in the owner's Drive)."""
    site = run.site
    if not site.content.enabled:
        return
    site_header(run, "Content")
    if site.auth != "me" or run.drive is None:
        run.content_text = "off (needs own access)"
        print('content: off (the dashboard is created in your Drive: use "auth": "me")\n')
        return
    if not site.reports_sheet_id:
        run.content_text = "off (no reports sheet)"
        print("content: off (no reports sheet)\n")
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
        run.content_text = "waiting for final data"
        print("content: no final Search Console data available yet\n")
        return
    notify = (lambda subject, body: notify_owner_or_raise(smtp, subject, body)) if smtp else None
    origin = site_check.origin_of(site.site_url)
    try:
        result = content_run.run_content(
            run.drive, run.sheets, site, site.reports_sheet_id, fetch, final, today, notify, print,
            force=force, read_source=source_reader(run) if site.content.imports else None,
            history_pages=((lambda: history.read_page_urls(run.sheets, site.history_sheet_id))
                           if site.history_sheet_id else None),
            sitemap=lambda: site_check.read_sitemaps(origin, site_check.default_get),
            probe=site_check.fetch_probe, cleanup_gone_since=cleanup_gone_since)
    except Exception:
        run.content_text = "failed"
        raise
    run.content_text = result.text()
    print(f"content: {result.text()}")
    if result.refreshed and (not result.created or result.imports or result.repairs) and smtp:
        subject, body = content_run.owner_email(result, site.deck.site_label or site.name,
                                                provision.link(result.file_id))
        notify_owner(smtp, subject, body)
    print()


def deck_rebuild_for(run: SiteRun, everything: bool, site_name: str, kind: str):
    """
    What to rebuild for this site: True (both decks, every site: the rebuild_decks input), a set
    of kinds for the one site named by rebuild_deck_site ("monthly", "weekly", or both when the
    kind is empty or "both"), or False.
    """
    if everything:
        return True
    if site_name and run.name.lower() == site_name:
        return {"monthly", "weekly"} if kind in ("", "both") else {kind} & {"monthly", "weekly"}
    return False


def sync_decks(sheets_service, run: SiteRun, smtp, rebuild_decks=False, mark_only=False, test=False):
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
                                   rebuild_decks, print, scrub, mark_only=mark_only, test=test)
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
              + (f" | content: {run.content_text}" if run.site.content.enabled else "")
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


NO_ACCESS = 'no Search Console access with "me" auth'
LOST_ACCESS_SUBJECT = "Lost access to your Google account"
LOST_ACCESS_BODY = ("Access to your Google account was lost. Please authorize it again (README steps: "
                    "'Using your own Google access'). Sites that use the service account kept "
                    "running; the sites that use your own access were skipped.")


def notify_owner(smtp, subject: str, body: str) -> bool:
    """Emails the owner (SMTP_USER). Never raises; says only whether it worked."""
    if smtp is None:
        print("  (no SMTP settings: the owner cannot be emailed)")
        return False
    try:
        mailer.send(smtp, mailer.build_plain(smtp.user, [smtp.user], subject, body))
    except mailer.MailError as e:
        print(f"  could not send the email ({scrub(str(e))})")
        return False
    return True


def check_own_sheets(site, sheets_service):
    """Sheets named in the configuration must be sheets PulseO created: the drive.file
    permission cannot open any other file."""
    for sheet_id in (site.sheet_id, site.history_sheet_id, site.reports_sheet_id):
        if not sheet_id:
            continue
        try:
            history._read(sheets_service.spreadsheets().get(spreadsheetId=sheet_id,
                                                           fields="spreadsheetId"))
        except HttpError as e:
            if e.resp.status in (403, 404):
                raise GscPulseError(
                    "a sheet in the configuration was not created by PulseO, so the "
                    "\"me\" permission cannot open it: remove its id from SITES_JSON and "
                    "PulseO creates its own") from None
            raise


def connect_me(runs: list, smtp):
    """
    Clients for the sites that run with the owner's own Google account, then, per site: the
    Search Console property check and the creation of its sheets. A site that cannot go on is
    marked failed here and skipped by every later phase; sites of the other auth type are untouched.
    """
    try:
        clients = auth.build_me_clients(os.environ, build)
    except auth.AccessLost as e:
        for r in runs:
            r.history_text = "skipped"
            fail_site(r, e)
        notify_owner(smtp, LOST_ACCESS_SUBJECT, LOST_ACCESS_BODY)           # one email per run
        return
    except auth.AuthError as e:
        for r in runs:
            r.history_text = "skipped"
            fail_site(r, e)
        return

    notify = (lambda subject, body: notify_owner_or_raise(smtp, subject, body)) if smtp else None
    refused = []
    for r in runs:
        site = r.site
        try:
            if not auth.access_to_property(clients.gsc, site.site_url):
                refused.append(site.site_url)
                raise GscPulseError(NO_ACCESS)
            check_own_sheets(site, clients.sheets)
            made = provision.provision_site(clients.drive, clients.sheets, site, notify)
            _SENSITIVE.extend(site.sensitive_values())                    # the new ids, too
            print(f"{r.name}: {made.text}")
        except Exception as e:
            r.history_text = "skipped"
            fail_site(r, e)
            continue
        r.gsc, r.sheets, r.drive = clients.gsc, clients.sheets, clients.drive
    if refused:
        notify_owner(smtp, "No Search Console access",
                     "These Search Console properties could not be read with your Google "
                     "account (not in your property list, or only an unverified owner):\n\n"
                     + "\n".join(refused) + "\n\nSites that were fine kept running.")


def build_test_site(reset: bool = False):
    """Makes (or finds) the fake test site in the owner's Drive, folder "PulseO Test", and tries the
    content checker on the sample documents as Google gives them back. Prints counts and the
    invented sample names only; the ids are in the test folder's own Index sheet."""
    try:
        clients = auth.build_me_clients(os.environ, build)
    except auth.AuthError as e:
        raise GscPulseError(f"test site: {e}") from None
    world = fake_site.build(clients.drive, clients.sheets, print, reset=reset)
    wrong = fake_site.check_samples_in_drive(clients.drive, world)
    print(f"  test site: {len(world.sites)} fake sites, {len(world.sample_ids)} sample documents, "
          f"{len(wrong)} the checker got wrong")
    for name, expected, found in wrong:
        print(f"    {name}: expected {sorted(expected)}, found {sorted(found)}")
    for name, _, _ in [w for w in wrong if w[0] in world.sample_ids][:2]:     # how Google wrote it (the sample text is invented)
        raw = fake_site.export_html(clients.drive, world.sample_ids[name])
        fonts = sorted(set(re.findall(r"font-family:\s*([^;}]+)", raw)))[:8]
        rtl_marks, h1s = raw.count('dir="rtl"'), raw.count('<h1')
        print(f"    export of {name}: {len(raw)} characters, fonts {fonts}, {rtl_marks} right-to-left marks, {h1s} H1")
    if wrong:
        raise GscPulseError(f"test site: the checker got {len(wrong)} samples wrong")
    print("  ✅  The test site is ready in your Drive (folder PulseO Test)")


def notify_owner_or_raise(smtp, subject: str, body: str):
    """Like notify_owner, but a failure raises (provision logs it and tries again next run)."""
    mailer.send(smtp, mailer.build_plain(smtp.user, [smtp.user], subject, body))


def pick_discovery_site(site_list: list, name: str):
    """The enabled site to run discovery for. Errors never echo the requested name."""
    match = [s for s in site_list if s.name.lower() == name.strip().lower()]
    if not match:
        raise GscPulseError("page type discovery: that site name is not in SITES_JSON")
    site = match[0]
    if not site.enabled:
        raise GscPulseError("page type discovery: that site is disabled")
    if not site.reports_sheet_id and site.auth != "me":
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
    sites_json_extra = os.environ.get("SITES_JSON_EXTRA")
    try:
        site_list = sites.load_sites(
            sites_json,
            args.site  or os.environ.get("SITE_URL") or cfg.get("site_url"),
            args.sheet or os.environ.get("SHEET_ID") or cfg.get("sheet_id"),
            (args.history_sheet or os.environ.get("HISTORY_SHEET_ID")
             or cfg.get("history_sheet_id")),
            force_single=bool(args.site or args.sheet),
            sites_json_extra=sites_json_extra,
        )
    except sites.SitesConfigError as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)
    multi = (bool((sites_json or "").strip() or (sites_json_extra or "").strip())
             and not (args.site or args.sheet))
    history_off_text = ("no history_sheet_id for this site" if multi else "no HISTORY_SHEET_ID")

    for s in site_list:                     # every id, even of disabled sites, is scrubbed
        _SENSITIVE.extend(s.sensitive_values())
    _SENSITIVE.extend(auth.secret_values(os.environ))       # the OAuth client and token

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
    rebuild_deck_site = os.environ.get("REBUILD_DECK_SITE", "").strip().lower()      # one site only
    rebuild_deck_kind = os.environ.get("REBUILD_DECK_KIND", "").strip().lower()       # monthly | weekly | both
    mark_decks_site = os.environ.get("MARK_DECKS_SITE", "").strip().lower()           # one site: mark decks sent, email nothing
    test_deck = os.environ.get("TEST_DECK", "").strip().lower() in ("1", "true", "yes")   # with rebuild_deck_site: a test copy to the owner only
    rebuild_content_site = os.environ.get("REBUILD_CONTENT_SITE", "").strip().lower() # one site: refresh its content dashboard now
    cleanup_gone_site = os.environ.get("CLEANUP_GONE_SITE", "").strip().lower()       # one site: one-off clean-up of pages added already gone
    cleanup_gone_since = os.environ.get("CLEANUP_GONE_SINCE", "").strip()             # ... first warned on this day (YYYY-MM-DD)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleanup_gone_since):
        cleanup_gone_site = ""
    rebuild_views = os.environ.get("REBUILD_VIEWS", "").strip().lower() in ("1", "true", "yes")
    smtp = mailer.settings_from_env(os.environ)
    if smtp:
        _SENSITIVE.extend(smtp.sensitive_values())
    if os.environ.get("ANTHROPIC_API_KEY"):
        _SENSITIVE.append(os.environ["ANTHROPIC_API_KEY"].strip())
    for s in site_list:                     # an address kept in a secret (env:NAME) must not be printed either
        for kind in ("weekly", "monthly"):
            _SENSITIVE.extend(mailer.resolve_recipients(s.deck.recipients_for(kind), os.environ))

    # ---- The fake test site: its own command, nothing else runs (no real site is touched) --------
    if os.environ.get("BUILD_TEST_SITE", "").strip().lower() in ("1", "true", "yes"):
        build_test_site(reset=os.environ.get("RESET_TEST_SITE", "").strip().lower() in ("1", "true", "yes"))
        return

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

    # ---- Auth (one set of clients per auth type, shared by its sites) ------
    print("Authenticating...")
    targets = [r for r in enabled if r.site is discover_site] if discover_site else enabled
    robot_runs = [r for r in targets if r.site.auth == "robot"]
    me_runs = [r for r in targets if r.site.auth == "me"]
    if robot_runs or not me_runs:
        creds = get_credentials(oauth_file)
        gsc_service = build("webmasters", "v3", credentials=creds)
        sheets_service = build("sheets", "v4", credentials=creds)
        for r in robot_runs:
            r.gsc, r.sheets = gsc_service, sheets_service
    if me_runs:
        print("  Using your own Google account for the sites marked \"me\"")
        connect_me(me_runs, smtp)
    print("  ✓  Connected to Google APIs\n")
    if discover_site:
        run = targets[0]
        if run.status == "failed":
            raise GscPulseError("page type discovery: the site could not be reached")
        discover_page_types(run.gsc, run.sheets, discover_site, start_date, end_date)
        print(f"  ✅  Done — updated at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return

    # ---- Phase 1: v1 tabs of every site, one after another -----------------
    for r in enabled:
        if r.status == "failed":
            continue                        # no access with its auth type: nothing to read
        try:
            sync_main_tabs(r.gsc, r.sheets, r, start_date, end_date)
        except Exception as e:
            r.history_text = "skipped"
            fail_site(r, e)
            print()

    # ---- Phase 1b: reports of every site (before history, as agreed) -------
    for r in enabled:
        if not r.v1_done:
            continue
        try:
            sync_reports(r.gsc, r.sheets, r, rebuild_alarms)
        except Exception as e:
            fail_site(r, e)
            print()

    # ---- Phase 1c: view tabs (formulas on top of the report tabs) ----------
    for r in enabled:
        if not r.v1_done or r.status == "failed" and r.reports_result is None:
            continue
        try:
            sync_views(r.sheets, r, rebuild_views)
        except Exception as e:
            fail_site(r, e)
            print()

    # ---- Phase 1c2: content dashboards (a Google Sheet in the owner's Drive) -----
    for r in enabled:
        if not r.v1_done or r.status == "failed" and r.reports_result is None:
            continue
        try:
            sync_content(r.gsc, r, smtp,
                         force=bool(rebuild_content_site) and r.name.lower() == rebuild_content_site,
                         cleanup_gone_since=(cleanup_gone_since if r.name.lower() == cleanup_gone_site else ""))
        except Exception as e:
            fail_site(r, e)
            print()

    # ---- Phase 1d: decks (PowerPoint reports by email), still before history ---
    for r in enabled:
        if not r.v1_done:
            continue
        try:
            sync_decks(r.sheets, r, smtp, deck_rebuild_for(r, rebuild_decks, rebuild_deck_site,
                                                          rebuild_deck_kind),
                       mark_only=bool(mark_decks_site) and r.name.lower() == mark_decks_site,
                       test=test_deck and bool(rebuild_deck_site) and r.name.lower() == rebuild_deck_site)
        except Exception as e:
            r.decks_text = r.decks_text if r.decks_text != "off" else "failed"
            fail_site(r, e)
            print()

    # ---- Phase 2: history of every site (shares one time budget) -----------
    for r in enabled:
        if not r.v1_done:
            continue                        # v1 failed (no access?): nothing to append
        sync_history(r.gsc, r.sheets, r, started_at, history_off_text)

    # ---- Phase 3: Meta tab of every site -----------------------------------
    for r in enabled:
        if not r.v1_done:
            continue                        # the v1 phase failed: nothing to describe
        try:
            write_meta(r.sheets, r, start_date, end_date, days)
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
