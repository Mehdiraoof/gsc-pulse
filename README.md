# GSC Pulse

Sync your Google Search Console data to a Google Sheet automatically.  
No third-party tools. No BigQuery costs. Just the official GSC API + Python.

Built by **Mehdi Raoof** · [linkedin.com/in/mehdiraoof](https://linkedin.com/in/mehdiraoof/)

---

## What it does

Runs a script that pulls 90 days (or however many you want) of Search Console data
and writes it to a Google Sheet with 7 clean tabs:

| Tab | Contents |
|---|---|
| Queries | All keywords: clicks, impressions, CTR, position |
| Pages | All URLs with the same metrics |
| Date Trend | Daily clicks and impressions over time |
| Device | Mobile vs desktop vs tablet breakdown |
| Country | Traffic split by country |
| Query×Page | Which keyword leads to which page (content gap analysis) |
| Meta | When it last ran, for which site and date range |

Once the sheet is live, Claude can read it on demand to give you SEO strategy,
content recommendations, and keyword analysis without any copy-pasting.

---

## Option A: Run on GitHub (recommended, nothing installed on your PC)

Runs every Monday on GitHub's servers, even when your computer is off.

1. **Google Cloud:** create a project, enable the **Google Search Console API**
   and **Google Sheets API**, then create a **service account** and download its
   JSON key (IAM & Admin → Service Accounts → Keys → Add key → JSON).
2. **Search Console:** Settings → Users and permissions → Add user → the service
   account email → **Restricted**.
3. **Google Sheet:** create a blank sheet and share it with the service account
   email as **Editor**.
4. **GitHub:** create a private repo and upload `gsc_pulse.py` and the other `.py` modules, `requirements.txt`
   and `.github/workflows/gsc_pulse.yml`.
5. **GitHub Secrets** (repo Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `GSC_SERVICE_ACCOUNT_JSON` | the full contents of the service account JSON key |
   | `SITE_URL` | your GSC property, e.g. `sc-domain:example.com` or `https://www.example.com/` |
   | `SHEET_ID` | the ID from the sheet URL (between `/d/` and `/edit`) |
   | `SITES_JSON` | *(optional)* several sites in one secret, see [Multiple sites](#multiple-sites). Replaces the three secrets above |
   | `HISTORY_SHEET_ID` | *(optional)* a second sheet that keeps every day forever, see [History](#history-keep-every-day-forever) |

6. **Actions tab → GSC Pulse weekly sync → Run workflow** to test. After that it
   runs every Monday at 07:00 Istanbul time on its own.

Domain properties in GSC are written as `sc-domain:example.com`.

---

## Option B: Run on your own computer

### Setup (one time, ~15 minutes)

### 1. Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project — name it anything (e.g. "GSC Pulse")
3. Go to **APIs & Services → Enable APIs & Services**
4. Search for and enable:
   - **Google Search Console API**
   - **Google Sheets API**
5. Go to **APIs & Services → Credentials**
6. Click **Create Credentials → OAuth 2.0 Client ID**
7. Application type: **Desktop app**
8. Click Create → **Download JSON**
9. Rename the downloaded file to `credentials.json` and put it in this folder

### 2. Google Sheet

1. Create a new blank Google Sheet at [sheets.google.com](https://sheets.google.com)
2. Name it something like "Example.com GSC Data"
3. Copy the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_IS_YOUR_SHEET_ID`**`/edit`

### 3. Config file

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and fill in:
- `site_url` — must match exactly how your site appears in GSC (check Settings in GSC)
- `sheet_id` — the ID you copied in step 2

### 4. Python dependencies

```bash
pip install -r requirements.txt
```

---

## Running it

```bash
python gsc_pulse.py
```

First run: a browser window opens for Google sign-in. Click through and
authorize access. Your token is saved to `~/.gsc_pulse_token.json` — every
run after this is silent and automatic.

### Options

```bash
python gsc_pulse.py --days 30          # pull last 30 days instead of 90
python gsc_pulse.py --config other.yaml   # use a different config (for multiple sites)
python gsc_pulse.py --site https://example.com/ --sheet SHEET_ID  # quick override
```

---

## Automating it

### Mac / Linux (cron)

Run weekly on Monday mornings at 8am:

```bash
crontab -e
# Add this line:
0 8 * * 1 cd /path/to/gsc-pulse && python gsc_pulse.py >> gsc_pulse.log 2>&1
```

### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create Basic Task → name it "GSC Pulse"
3. Trigger: Weekly, Monday
4. Action: Start a program → `python` → Arguments: `C:\path\to\gsc_pulse.py`

---

## History: keep every day forever

Search Console only keeps about 16 months of data. With history switched on, GSC Pulse
also appends every finished day to a **separate** Google Sheet, once, and never
overwrites it. The main sheet stays small and fast to read; the history sheet grows.

| Tab | One row per |
|---|---|
| `H Daily` | date |
| `H Queries` | date + query |
| `H Pages` | date + page |
| `H Device` | date + device |
| `H Country` | date + country |
| `State` | history tab: last date stored, rows total, when it was updated |

**Set it up**

1. Create a blank Google Sheet (a *new, separate one*, not the main sheet).
2. Share it with the service account email as **Editor**. The service account cannot
   create spreadsheets itself, so this has to be done by hand.
3. Add its ID as the repo secret `HISTORY_SHEET_ID` (or `history_sheet_id` in
   `config.yaml` / `--history-sheet` when running locally).

Without `HISTORY_SHEET_ID` the history step is skipped and everything else works as before.

**How it behaves**

- The first run is a **backfill** of everything Search Console still has (about 16 months).
  It works one calendar month at a time and stops starting new months after about
  10 minutes, so on a big site it may take a few runs. The next run simply continues
  from the `State` tab; nothing needs to be done by hand.
- After that, each run appends only the days that are new. The last day stored is the
  latest day Search Console reports as final, not "today minus 3".
- `State` is only moved forward after a month's rows are written. If a run is cut off
  in between, the next run removes the unrecorded rows and fetches that month again,
  so nothing is stored twice and no day is skipped.
- Rows are never edited or deleted in normal operation. Do not sort or insert rows
  in the `H ...` tabs, and do not edit `State`; the script relies on both.
- The main sheet's `Meta` tab shows the history status, the oldest and newest stored
  date (newest = the date all history tabs are complete through) and the total rows.

**The 10 million cell limit**

A Google spreadsheet holds at most 10,000,000 cells. The history tabs grow forever
(query rows are the biggest), so a busy site will eventually reach it. Before every
append GSC Pulse checks the size of the history spreadsheet and **stops appending at
9,000,000 cells**. The sync still succeeds; it writes a warning to the log and to the
`Meta` tab: *"History sheet almost full: create a new history sheet and update
HISTORY_SHEET_ID"*.

The fix is one new history sheet per year (or whenever the warning appears):

1. Create a new blank sheet and share it with the service account as Editor.
2. Replace the `HISTORY_SHEET_ID` secret with the new ID.
3. The next run starts the new sheet from scratch; it backfills only what Search
   Console still has. Keep the old sheet as the archive.

---

## Reports: the monthly and weekly SEO report, generated

With a **Reports sheet** per site, every run also builds the numbers of a monthly and weekly
SEO report, in a separate spreadsheet, in tabs that only ever grow (one row per period per
item, so they can be charted and later turned into presentations).

**Set it up:** create a blank Google Sheet for the site, share it with the service account
as **Editor**, and add its ID to the site's entry in `SITES_JSON` as `reports_sheet_id`.
Without `reports_sheet_id` the site gets no reports (the log says `reports: off`). No new
Google permissions are needed.

```json
{"name": "site-1", "site_url": "sc-domain:example.com", "sheet_id": "…", "history_sheet_id": "…",
 "reports_sheet_id": "…",
 "page_types": [
   {"name": "English", "regex": "/en/"},
   {"name": "Shop", "regex": "^https://shop\\.example\\.com/"},
   {"name": "Article", "regex": "/article/"}],
 "thresholds": {"lost_new_min_impressions": 10, "movers_top_n": 100},
 "weekly_segments": false}
```

| Tab | Content |
|---|---|
| `R Monthly` | Per month and segment: clicks, impressions, CTR, position, AI Overview impressions, and the change vs the previous month (MoM) and the same month last year (YoY), absolute and in %. |
| `R Weekly` | The same per ISO week (Monday to Sunday), with WoW and YoY. Segment `All` only; `"weekly_segments": true` adds the page types. |
| `R Keywords` | Per month vs the previous month: `Lost`, `New`, `Growing` and `Declining` keywords. |
| `R Pages` | Per month: `Growing` / `Declining` pages vs the previous month and `YoY Growing` / `YoY Declining` vs the same month last year, with the page type(s) of each page. |
| `R AIO Pages` | `Growing` / `Declining` pages by AI Overview impressions. |
| `R Alarms` | Plain-English alarms, at most 10 per period, with the clicks involved (see below). |
| `Notes` | Fixed explanations of how to read the numbers. |
| `AIO Raw` | The imported AI Overview data (you never edit it). |

**Rules that apply everywhere**

- **Complete periods only.** Months are calendar months, weeks are Monday to Sunday. A
  period is reported only after its last day has final data in Search Console; a partial
  month or week is never shown as if it were complete.
- **Closed periods are append-only.** A month or week is written once and not rewritten,
  even if Search Console later revises the numbers. The first run writes every complete
  month and week Search Console still has (about 16 months). The one exception: the AIO
  columns of existing rows are filled in when an AI Overview import arrives later.
- **`All`** uses a date-only query, exactly like Search Console with no filter. Segments use a
  page filter (`includingRegex`); `Other` excludes every page type's regex.
- Changes are empty when there is nothing to compare with: the first month, a base of 0 (for
  %), or a period older than the 16 months Search Console keeps (so YoY fills in as time passes).
- Query data leaves out anonymized queries: a keyword "missing" in a month means "not in
  Search Console's returned data".

**Page types**

Page types are optional and describe how a site's URLs are organised (folders, subdomains).
They live in `SITES_JSON`, because URL patterns can reveal a client. Types **may overlap**
(an English article counts in English and in Article, like when you filter in Search Console).
`Other` is automatic: the pages that match no rule. Without `page_types` only `All` is
reported, there is no `Other`, and there is no "new URL pattern" alarm. A site whose URLs are
flat slugs with no structure should simply have no page types.

To find good rules, write the discovery tab:

```bash
python gsc_pulse.py --discover-page-types site-1
```

or run the workflow manually and fill in the `discover_site` input with the site name. It
writes a `Page Type Discovery` tab to the Reports sheet: subdomains, first and second path
segments (and, for flat sites, the first word of the slug) with page counts, clicks,
impressions and example URLs. Rules are never written automatically; you decide which
patterns become page types.

**Alarms** (`R Alarms`: `Period, Severity, Area, Message, Impact (clicks)`). Each is a fixed rule
with a threshold you can change under `thresholds`:

- a segment's clicks changed by 20% or more vs the previous month (`high` from 40%), if the
  segment has at least 30 clicks (`alarm_clicks_change_pct`, `alarm_clicks_high_pct`, `alarm_min_clicks`)
- a lost keyword that had 10 or more clicks the month before (`alarm_lost_keyword_clicks`)
- a page that lost 50% or more of its clicks, having had 30 or more the month before
  (`alarm_page_loss_pct`, `alarm_page_loss_min_clicks`)
- AI Overview impressions down 30% or more (`alarm_aio_drop_pct`)
- a possible new section: 5 or more pages in `Other` sharing the first folder or a subdomain
  (`alarm_new_pattern_pages`), only for sites with page types
- an AI Overview import that could not be read (always `high`, always first)

**Keeping the list short.** The last column, *Impact*, is the number of clicks involved. Per period
R Alarms keeps at most **10 rows** (`alarm_max_per_period`), high severity first and then the
biggest impact. Page losses are the noisiest rule, so only the **5 pages with the most clicks lost**
are listed (`alarm_page_loss_shown`), followed by one line such as *"and 12 more pages lost 50%+
of their clicks"*. The full list of page changes is in `R Pages`.

A period's alarms are written when the period is first reported; if something new turns up
later for it (for example an AI Overview import), it is merged in and the period is cut to 10
again. To recompute **R Alarms for every past period** with the current rules, for example after
changing a threshold, run the workflow manually with *rebuild_alarms* ticked. This rewrites only
R Alarms; the data tabs are never touched. (An R Alarms tab written by an older version, without
the Impact column, is rebuilt automatically on the next run.)

Other thresholds: `lost_new_min_impressions` (10) and `movers_top_n` (100).

**AI Overview data (monthly, two minutes)**

Search Console has no API for the Generative AI report yet, so the data goes in through the
Reports sheet. Every month, open Search Console → Generative AI report → last full month, no
comparison → Export → **Google Sheets**. In the exported spreadsheet, right-click the
**Pages** tab and the **Dates** (daily chart) tab → *Copy to → Existing spreadsheet* → the
site's Reports sheet, then rename the copied tabs to `AIO YYYY-MM Pages` and
`AIO YYYY-MM Dates` (for example `AIO 2026-09 Pages` and `AIO 2026-09 Dates`).

On the next run the tabs are read (columns are matched by name and by what the values look
like, so a localized export works), stored in `AIO Raw`, and deleted. Importing a month again
replaces that month. If a tab name or its content cannot be understood, the tab is left
untouched and the problem is written to `R Alarms` (area `AIO import`).

---

## Using it with Claude

Once the sheet is updated, open a Claude session that has the Google Sheets
connector enabled and ask:

> "Read the Queries tab from my GSC sheet and tell me which keywords I should
> prioritize this month based on position and impressions."

Claude will read the live data and give you an SEO strategy grounded in your
actual numbers.

---

## Multiple sites

One run can sync several sites, each into **its own** sheets (nothing is shared between
sites). Put them in a single secret called `SITES_JSON`, a JSON list with one object per site:

```json
[
  {"name": "site-1", "site_url": "sc-domain:example.com",
   "sheet_id": "MAIN_SHEET_ID", "history_sheet_id": "HISTORY_SHEET_ID"},
  {"name": "site-2", "site_url": "https://www.example.org/",
   "sheet_id": "MAIN_SHEET_ID_2", "history_sheet_id": "HISTORY_SHEET_ID_2"},
  {"name": "site-3", "site_url": "sc-domain:example.net",
   "sheet_id": "MAIN_SHEET_ID_3", "enabled": false}
]
```

| Field | |
|---|---|
| `name` | **required.** A short, **neutral label** (`site-1`, `client-a`). Use letters, digits, space, `-` and `_` only. |
| `site_url` | required. The Search Console property, exactly as shown there (`sc-domain:example.com` for domain properties). |
| `sheet_id` | required. The main sheet (the 90-day tabs and `Meta`). |
| `history_sheet_id` | optional. A separate sheet for the permanent [history](#history-keep-every-day-forever). Without it, history is off for that site. |
| `enabled` | optional. `false` skips the site; the log says `<name>: disabled`. Handy while access is still being set up. |

> **Use a neutral `name`, never the real domain or client.** Logs of a public repository
> are visible to anyone, and the `name` is the only thing about a site that is printed.
> `site_url`, `sheet_id` and `history_sheet_id` are hidden from every log message and error.

**How a run goes.** Sites are processed one after another (never in parallel), sharing the
Sheets API throttle. First the 90-day tabs of **every** site are written, then the history
step runs for each site, all sharing one time budget (about 10 minutes per run). If the
budget runs out, the remaining backfills pause and continue on the next run.

**Failures are isolated.** If one site fails (no access, quota, ...), the log says
`<name>: failed (reason)`, the other sites carry on, and the run ends red so you notice.
The end of the log has one summary line per site (status, rows written, history status),
without URLs or IDs.

**Without `SITES_JSON`** the old settings keep working: the `SITE_URL`, `SHEET_ID` and
`HISTORY_SHEET_ID` secrets (or `config.yaml` / the command-line flags) describe a single
site. If `SITES_JSON` is set, it wins. A bad `SITES_JSON` stops the run with a clear
message that says where the problem is, but never prints what the secret contains.

**Adding a site**

1. **Search Console:** Settings → Users and permissions → add the service account email
   as **Restricted** user on the new property.
2. **Google Sheets:** create two blank sheets (main + history) and share both with the
   service account email as **Editor**. The service account cannot create sheets itself.
3. Add an object for it to the `SITES_JSON` secret (Settings → Secrets and variables →
   Actions → edit `SITES_JSON`), with a neutral `name`. You can add it with
   `"enabled": false` first and switch it on once the access is in place.
4. Run **GSC Pulse weekly sync** manually. A big site's history backfill may take more
   than one run; re-run until the `State` tab of its history sheet shows the latest date.

Running locally for a single site works as before; for several sites locally, set the
`SITES_JSON` environment variable to the same JSON.

---

## What's next (roadmap)

- [ ] Email report after each sync (summary of top movers)
- [ ] Comparison mode: this period vs. previous period
- [ ] Slack notification when impressions drop >20%
- [ ] Web UI so clients can trigger a sync themselves
- [ ] GitHub Actions support for fully server-less automation

---

## License

MIT — free to use, modify, and share. Attribution appreciated.
