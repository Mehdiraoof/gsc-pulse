"""
GSC Pulse
=========
Syncs Google Search Console data to a Google Sheet automatically.
Built for SEO teams who want live, always-updated keyword + page data
without manual exports or third-party tools.

Author : Mehdi Raoof — https://linkedin.com/in/mehdiraoof/
Version: 1.0.0

Usage:
    python gsc_pulse.py                        # uses config.yaml
    python gsc_pulse.py --config my.yaml       # custom config file
    python gsc_pulse.py --days 30              # override date range
    python gsc_pulse.py --site https://example.com/ --sheet YOUR_SHEET_ID
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
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

def fetch_gsc(service, site_url: str, start_date: str, end_date: str,
              dimensions: list, row_limit: int = DEFAULT_ROW_LIMIT) -> list:
    """
    Fetches all rows from the GSC Search Analytics API for the given dimensions.
    Paginates automatically to retrieve more than 5,000 rows.
    """
    all_rows = []
    start_row = 0

    while True:
        body = {
            "startDate":  start_date,
            "endDate":    end_date,
            "dimensions": dimensions,
            "rowLimit":   row_limit,
            "startRow":   start_row,
        }
        try:
            resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        except HttpError as e:
            raise GscPulseError(f"Search Console API error ({e.resp.status}): {e.reason}") from None

        rows = resp.get("rows", [])
        all_rows.extend(rows)

        if len(rows) < row_limit:
            break  # last page

        start_row += row_limit

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
# Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="GSC Pulse — sync Google Search Console data to Google Sheets"
    )
    parser.add_argument("--config", default="config.yaml",
                        help="Path to YAML config file (default: config.yaml)")
    parser.add_argument("--site",  help="Override: GSC site URL")
    parser.add_argument("--sheet", help="Override: Google Sheet ID")
    parser.add_argument("--days",  type=int, help="Override: number of days to pull")
    args = parser.parse_args()

    # ---- Load config -------------------------------------------------------
    # Priority: command-line flags > environment variables > config.yaml
    cfg = {}
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}

    site_url   = args.site  or os.environ.get("SITE_URL") or cfg.get("site_url")
    sheet_id   = args.sheet or os.environ.get("SHEET_ID") or cfg.get("sheet_id")
    oauth_file = cfg.get("oauth_client_file", "credentials.json")
    days       = args.days  or int(os.environ.get("DAYS") or cfg.get("days", 90))

    if not site_url or not sheet_id:
        print("\n  ERROR: site_url and sheet_id are required.")
        print("  Set them in config.yaml, as SITE_URL / SHEET_ID environment variables,")
        print("  or with --site / --sheet.")
        sys.exit(1)

    _SENSITIVE.extend([site_url, sheet_id])

    # GSC data runs ~2-3 days behind, so stop 3 days ago to avoid half-empty days
    end_dt     = datetime.today() - timedelta(days=3)
    start_dt   = end_dt - timedelta(days=days - 1)
    end_date   = end_dt.strftime("%Y-%m-%d")
    start_date = start_dt.strftime("%Y-%m-%d")

    # ---- Banner ------------------------------------------------------------
    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  GSC Pulse  ·  by Mehdi Raoof               │")
    print("  └─────────────────────────────────────────────┘")
    print(f"  Site   : {show(site_url)}")
    print(f"  Period : {start_date}  →  {end_date}  ({days} days)")
    print(f"  Sheet  : {show('https://docs.google.com/spreadsheets/d/' + sheet_id)}")
    print()

    # ---- Auth --------------------------------------------------------------
    print("Authenticating...")
    creds         = get_credentials(oauth_file)
    gsc_service   = build("webmasters", "v3",  credentials=creds)
    sheets_service = build("sheets",    "v4",  credentials=creds)
    print("  ✓  Connected to Google APIs\n")

    # ---- Fetch + write tabs ------------------------------------------------
    print("Fetching data from Search Console and writing to Sheets...")
    print(f"  {'Tab':<22} {'Rows':>5}")
    print(f"  {'-'*22} {'-'*5}")

    tabs = [
        ("Queries",     ["query"],         DEFAULT_ROW_LIMIT),
        ("Pages",       ["page"],          DEFAULT_ROW_LIMIT),
        ("Date Trend",  ["date"],          DEFAULT_ROW_LIMIT),
        ("Device",      ["device"],        DEFAULT_ROW_LIMIT),
        ("Country",     ["country"],       DEFAULT_ROW_LIMIT),
        ("Query×Page",  ["query", "page"], QUERY_PAGE_LIMIT),
    ]

    for tab_name, dimensions, limit in tabs:
        rows  = fetch_gsc(gsc_service, site_url, start_date, end_date, dimensions, limit)
        table = rows_to_table(rows, dimensions)
        write_tab(sheets_service, sheet_id, tab_name, table)

    # ---- Meta tab ----------------------------------------------------------
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta_data = [
        ["Property",        site_url],
        ["Date range",      f"{start_date} → {end_date}"],
        ["Days",            days],
        ["Last updated",    now_str],
        ["Built with",      "GSC Pulse by Mehdi Raoof — linkedin.com/in/mehdiraoof/"],
    ]
    write_tab(sheets_service, sheet_id, "Meta", meta_data)

    # ---- Done --------------------------------------------------------------
    print()
    print(f"  ✅  Done — updated at {now_str}")
    print(f"  Sheet: {show('https://docs.google.com/spreadsheets/d/' + sheet_id)}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # clean, scrubbed message instead of a raw traceback
        if isinstance(e, HttpError):
            msg = f"Google API error ({e.resp.status}): {e.reason}"
        else:
            msg = f"{type(e).__name__}: {e}"
        print(f"\n  ❌  {scrub(msg)}")
        print("  Tip: check that the service account has access to the property and the sheet,")
        print("  and that the site URL matches the GSC property exactly.")
        sys.exit(1)
