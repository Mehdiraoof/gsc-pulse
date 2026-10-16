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
4. **GitHub:** create a private repo and upload `gsc_pulse.py`, `history.py`, `requirements.txt`
   and `.github/workflows/gsc_pulse.yml`.
5. **GitHub Secrets** (repo Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `GSC_SERVICE_ACCOUNT_JSON` | the full contents of the service account JSON key |
   | `SITE_URL` | your GSC property, e.g. `sc-domain:example.com` or `https://www.example.com/` |
   | `SHEET_ID` | the ID from the sheet URL (between `/d/` and `/edit`) |
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

## Using it with Claude

Once the sheet is updated, open a Claude session that has the Google Sheets
connector enabled and ask:

> "Read the Queries tab from my GSC sheet and tell me which keywords I should
> prioritize this month based on position and impressions."

Claude will read the live data and give you an SEO strategy grounded in your
actual numbers.

---

## Multiple sites

Copy `config.yaml` to `config-client2.yaml`, update the values, and run:

```bash
python gsc_pulse.py --config config-client2.yaml
```

One sheet per client, one command per sync.

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
