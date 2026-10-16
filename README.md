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
4. **GitHub:** create a private repo and upload `gsc_pulse.py`, `requirements.txt`
   and `.github/workflows/gsc_pulse.yml`.
5. **GitHub Secrets** (repo Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `GSC_SERVICE_ACCOUNT_JSON` | the full contents of the service account JSON key |
   | `SITE_URL` | your GSC property, e.g. `sc-domain:example.com` or `https://www.example.com/` |
   | `SHEET_ID` | the ID from the sheet URL (between `/d/` and `/edit`) |

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
