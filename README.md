# PulseO

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

6. **Actions tab → PulseO weekly sync → Run workflow** to test. After that it
   runs every Monday at 07:00 Istanbul time on its own.

Domain properties in GSC are written as `sc-domain:example.com`.

---

## Option B: Run on your own computer

### Setup (one time, ~15 minutes)

### 1. Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project — name it anything (e.g. "PulseO")
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
2. Create Basic Task → name it "PulseO"
3. Trigger: Weekly, Monday
4. Action: Start a program → `python` → Arguments: `C:\path\to\gsc_pulse.py`

---

## History: keep every day forever

Search Console only keeps about 16 months of data. With history switched on, PulseO
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
append PulseO checks the size of the history spreadsheet and **stops appending at
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
| `R Pages` | Per month: `Growing` / `Declining` pages vs the previous month and `YoY Growing` / `YoY Declining` vs the same month last year, with the page type(s) of each page and a readable `Path` in the last column (the URL decoded, domain removed; `R AIO Pages` has it too). |
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

Two optional settings of the site: `"first_match_wins": true` makes the types **exclusive**: they are
checked in the order given and a page belongs to the first one that matches, so the types and the
rest add up to `All` (an English article is then only English). `"other_label": "Blog & other"` renames
the segment of the pages that match no type (default `Other`; use it before the first reports are
written, because existing rows keep the old name).

The client deck can use friendlier names without touching the segment names in the sheets: add an
optional `"deck_label"` to a page type (`{"name": "Song", "regex": "...", "deck_label": "Songs"}`) and
`"other_deck_label"` at site level for the pages that match no type. Without them the deck uses the
segment name. In the deck, any address (`/path` or `http...`) is shown left to right even in Persian, and
slugs longer than about 40 characters are shortened with "…".

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
the Impact column or with the older wording, is rebuilt automatically once on the next run; the
wording version is kept in the `State` tab.) The sentences are written for people: month names
instead of period keys ("Clicks fell 32.4% vs August (481 to 325)."), the segment's name only for a
page type (never "All"), and a page by its readable path.

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

## Reading the Reports sheet

The `R ...` tabs are long lists made for code. On top of them PulseO builds a set of readable
**view tabs**: open the sheet and you understand the month in half a minute. They hold formulas
only, so they refresh by themselves when the R tabs grow; the weekly run never rewrites them.

| Tab | What it shows |
|---|---|
| `Dashboard` | A month dropdown (and a *Segment* dropdown when the site has page types), one sentence about the month, a known event if you wrote one, the scorecard (clicks, impressions, CTR, average position, AI Overview impressions, each vs last month and last year, with a 13-month sparkline), the month's alarms, and the top 5 pages lost and gained, lost keywords and new keywords. |
| `Monthly` | One row per month, newest on top, with changes, CTR, position, AI Overview impressions, year on year, and your note from `Events`. With page types there is also one `Monthly · <segment>` tab per segment (including *Other*). |
| `Weekly` | The same per ISO week (for example `W41 · 5 Oct – 11 Oct 2026`), compared with the week before. |
| `Lost Keywords`, `New Keywords`, `Growing Keywords`, `Declining Keywords` | A *Month* dropdown and the whole list for that month. |
| `Growing Pages`, `Declining Pages`, `YoY Growing Pages`, `YoY Declining Pages` | The same for pages (shown by their readable path). YoY compares with the same month last year. |
| `Gone Pages` | Pages that had impressions last month (or in the same month last year) and **none this month**: most likely redirected or removed, so they are not counted as declines. Check that the redirects pass the traffic. |
| `Growing AIO Pages`, `Declining AIO Pages` | Pages by AI Overview impressions. A month without an import says so. |
| `Impact Checker` | What did a change on a page do? You enter rows, formulas answer (see below). |
| `Events` | The tab you write in: events by month, and the Resolved list (see below). |
| `Opportunities`, `R ...`, `Notes`, `AIO Raw`, `State`, `Page Type Discovery` | The data, in grey, at the end. Do not edit them. |

Tab colours: amber is the Dashboard, dark blue the views, slate the Events tab, grey the data.
Green numbers are good and red are bad; for the average position it is the other way round, because
a lower position is better. Everything that can hold Persian (keywords, paths, alarm messages, notes)
uses the Vazirmatn font.

**Using the dropdowns.** Choose a month in the highlighted cell at the top of a tab. Each tab has its
own dropdown. When a run adds a new complete month, every month dropdown moves to it by itself, so the
sheet opens on the newest report; on other runs your choices are left alone.

**Writing events.** Open `Events`, put the month in the first column as `YYYY-MM` (for example `2026-09`)
and what happened in the second ("Site migration", "Core update", ...). The note then shows on the
Dashboard under the takeaway, in the *Note* column of `Monthly`, and, for a week, in `Weekly` (by the
month of its Monday). The rows with a note are highlighted. Nothing ever overwrites `Events`.

**The Resolved list.** On the right of `Events` (columns D to G: `kind | value | note | added`) you list what
is already known and fixed. `kind` is a dropdown: `page` (the URL, or just its path), `keyword`, `month`
(`YYYY-MM`) or `segment` (the segment label). Anything listed here **never appears in the monthly
client deck**: not as a decline, not under "What we're watching", not as a trend annotation (a resolved
month is shown as "known and fixed"). The weekly deck, which is for you only, still shows everything.

**Gone pages.** A page that had impressions last month (or a year ago) and has none this month is
called *gone*, not a decliner: after a migration old URLs disappear and that is expected. They are
left out of every decline list in the monthly deck, which only says "N old URLs no longer show in
Google (expected after redirects)" from five pages on. The weekly deck has a table *Pages that
disappeared* (the top 10 by clicks last week) so you can check the redirects. The full list is in the
`Gone Pages` tab.

**Impact Checker.** Type a page URL (exactly as Search Console shows it), the date of a change and a
note. Formulas compare the 28 days before with the 28 days after, from the History sheet: clicks,
impressions, CTR and average position, with the change. Until 28 days have passed after the date (plus
Search Console's two-day lag) it says *too early*. The first time, Google asks you to connect the
sheets: click *Allow access* in the cell. Row 4 is only an example. Your rows are never overwritten.

**Opportunities.** Every run the `Opportunities` tab is rewritten with a list of concrete things to do,
computed from the last 90 days (the Data sheet's Queries, Pages and Query×Page tabs) and the latest
month. Types: `quick_win` (a query at position 4 to 20), `ctr_fix` (top 5 but under half the usual
click rate), `update_page` (a page at position 4 to 10 with many impressions and a low click rate, with
a diagnosis), `decliner` (down on last month **and** last year, still visible), `question` (question
searches where the site ranks low), `cannibal` (several own pages competing for one query),
`check_page` (impressions down 80% or more), `aio` (AI Overview impressions up, clicks down) and
`seasonal` (grew strongly into next month last year). The *potential_gain_month* is an **estimate**:
`impressions_90d / 3 × max(0, target CTR − CTR)`, target 8% by default (a conservative top-3 click rate),
rounded to 5. The expected click rate by position is an industry-average estimate (position 1: 28%,
2: 15%, 3: 10%, 4: 7%, 5: 5%, 6: 4%, 7: 3%, 8: 2.5%, 9: 2%, 10: 1.8%, 11 to 20: 1%). Per site, in
`SITES_JSON`, all optional:

```json
"opportunities": {"brand_terms": ["yourbrand"], "min_impressions_90d": 300, "target_ctr_top3": 0.08}
```

Searches that contain a brand term are skipped. Besides `brand_terms`, the deck's site label and the
bare domain name (`kalameh` for `sc-domain:kalameh.com`) always count as brand terms. The **front page**
(`/` and any language root such as `/en`) is never a quick win, a page to update or a cannibalization
case, whatever the config says, and searches that look like an address (`www`, `http`, `.ir`, `.com`...)
are never suggested as keywords.

Not built: the "all its top 5 queries fell in position
at once" variant of `check_page` (it needs per-month query data for each page).

**Repairing and rebuilding.** The tool stores a `views_version` in the `State` tab. If you delete a view
tab, the next run creates it again. When a new version of PulseO changes the views, the next run
deletes and recreates them once (`Events` is kept as it is). To rebuild them by hand, for example
after you changed something in them, run the workflow manually with **rebuild_views** ticked. After
building, the tabs are read back and the run fails if a cell shows `#REF!`, `#NAME?`, `#VALUE!` or
`#ERROR!`. The formulas use commas between arguments, so the spreadsheet locale should be one that
does too (File → Settings → Locale; *United States* always works).

---

## Reports by email: PowerPoint decks

Every week and every month, PulseO can send a ready-to-present **PowerPoint (.pptx)** report per
site by email, built from the numbers in the site's Reports sheet (so [Reports](#reports-the-monthly-and-weekly-seo-report-generated)
must be set up first). Anyone who prefers Google Slides opens the attachment there: Slides
opens .pptx files directly.

- **Monthly deck** (up to 15 slides): a **client-ready report**, progress first, with real keyword and
  page names and numbers, concrete recommendations and an estimated gain. Title; this month in one
  line; scorecard; rankings that climbed; what grew; new searches; *Our work this month* (an empty,
  editable placeholder you fill in before forwarding); 13-month trend; performance by section (only for
  sites with page types); quick wins; pages to update; new content ideas; get ready for next month;
  what we're watching; next month's focus. Declines are shown in grey, not red, unless rankings really
  fell; there is no alarm slide, and anything on the Resolved list is left out. A slide without data
  is skipped. It is sent when a new complete month exists that was not sent yet.
- **Weekly deck** (6 slides, 7 when pages disappeared): summary, alarms (week-on-week drops of 20% or
  more), scorecard, 12-week trend, the top keyword movers and a table of pages that disappeared. It is
  for you: it stays issue-focused. It is sent once for the latest complete week.
- Slide titles state the takeaway ("Clicks down 32% this month, 91% vs last year"), generated
  from the data by fixed rules. Empty data becomes a sentence, never an empty table.
- The deck is built **in memory during the run** and exists only in the email. Nothing is
  uploaded, committed or stored as a workflow artifact (the repository and its artifacts are public).
- Which periods were sent is remembered in the `State` tab of the Reports sheet. Run the
  workflow manually with **rebuild_decks** ticked to build and send both decks of every site now, or
  fill in **rebuild_deck_site** (a site name) and optionally **rebuild_deck_kind** (`monthly`, `weekly`
  or `both`) to rebuild just that site's decks; the other sites are not resent.
- The monthly deck has no summary slide, so `ai_summary` only applies to the weekly deck.

**Set it up**

1. **Gmail:** turn on 2-Step Verification, then create an **App password** for "PulseO"
   (Google Account → Security → 2-Step Verification → App passwords).
2. **Repository secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | the Gmail address that sends the emails |
   | `SMTP_PASSWORD` | the app password (not your normal password) |
   | `ANTHROPIC_API_KEY` | *(optional)* for automatic AI summaries |

   Without the SMTP secrets nothing is built and the log says `decks: off (no SMTP settings)`.
3. **Per site**, add a `deck` block to its entry in `SITES_JSON`:

   ```json
   "deck": {
     "enabled": true,
     "language": "en",
     "site_label": "Example",
     "recipients": ["someone@example.com", "env:SMTP_USER"],
     "weekly": true,
     "monthly": true,
     "brand_color": "#1F3A5F",
     "accent_color": "#E07A2E",
     "logo_url": "https://example.com/logo.png",
     "ai_summary": false
   }
   ```

   | Field | |
   |---|---|
   | `enabled` | `false` (or no `deck` block) means no decks for the site. |
   | `language` | `en` or `fa` (see below). |
   | `site_label` | The client-facing name shown on the decks and in the email. It lives in the secret only. |
   | `recipients` | One or more addresses. `"env:SMTP_USER"` means "the address in the `SMTP_USER` secret", so it never has to be written anywhere else. `[]` means yourself only. Start with only yourself, review the look, then add team members or clients. |
   | `weekly_recipients`, `monthly_recipients` | Optional, same format: who gets the weekly or the monthly deck. If one is missing, `recipients` is used for that deck type. `[]` means yourself only. |
   | `weekly`, `monthly` | Which decks to send (default: both). |
   | `brand_color`, `accent_color` | Optional. They only change the chart colours (impressions and clicks); fonts, colours and layout come from the template. |
   | `logo_url` | Optional `https://` image, downloaded when the deck is built and shown on the title slide. If it cannot be downloaded the deck is built without it. |
   | `ai_summary` | `true` writes the Summary slide with Claude (below). |

**Adding client emails.** Edit the site's `deck` block in the secret that holds the site (`SITES_JSON`
or `SITES_JSON_EXTRA`). For example, weekly decks to you only and monthly decks also to the client:

```json
"deck": {"enabled": true, "language": "en", "site_label": "Example",
         "recipients": [], "weekly_recipients": [], "monthly_recipients": ["client@example.com"]}
```

The address in the `SMTP_USER` secret (you) **always gets every deck**. When a deck goes to other
people they are in *To* and you get a **blind copy** (Bcc), so they never see your address list
and you always have what they received. Empty lists mean you only. Decks exist only in the
email: nothing is saved to Drive or anywhere else. The change applies from the next run; to send
the current decks to the new address right away, run the workflow with *rebuild_decks* ticked
(this also resends them to everyone on the list).

Each email has its own subject, a short body with three headline numbers and the top alarm,
and the deck attached, so your inbox is the archive. If sending fails, it is retried once, the
error is logged (with addresses and passwords hidden) and the run ends red; decks that were sent
stay marked as sent.

**Design.** The look comes from a designed template (`templates/gsc_pulse_template.pptx`, and
`gsc_pulse_template_fa.pptx` for Persian) with named slide layouts. The code only fills their
placeholders and draws cards, tables and **native, editable PowerPoint charts** inside them. If a
layout is missing from the template, the run fails with a message naming it.

**Persian (`"language": "fa"`).** Every label comes from a translation table, text is
right-to-left, tables read right to left, and all Persian text uses the font **Vazirmatn**. Google
Slides has Vazirmatn; for PowerPoint, install it first (it is free, from Google Fonts), otherwise
Persian text falls back to another font. Keywords and URLs in English decks are Persian too: those
runs also use Vazirmatn. Alarm sentences are translated when they follow a known pattern.

**AI summary.** With `"ai_summary": true` and an `ANTHROPIC_API_KEY`, the Summary slide is written by
Claude: four to six plain-language bullets (biggest change first) and two or three next steps, in
the deck language. **Enabling this sends aggregated numbers and your top queries and pages to the
Anthropic API** (the scorecard, segments, alarms, top movers and AI search data of the period;
never credentials, IDs or full tables). It costs cents per report. The model can be changed with
the `AI_SUMMARY_MODEL` variable. If the key is missing or the request fails, the slide says
"Summary not generated automatically" and its speaker notes hold the same aggregated text, ready to
paste into Claude yourself.

---

## Using your own Google access

Clients are sometimes uneasy about adding a strange robot address (`…@…iam.gserviceaccount.com`)
to their Search Console. If you already have access to a client's property with your own Google
account, PulseO can read it **as you**, so nobody has to add anything. This is chosen per site:

```json
{"name": "site-2", "site_url": "sc-domain:example.com", "auth": "me",
 "deck": {"enabled": true, "language": "en", "site_label": "Example", "recipients": []}}
```

* `"auth": "robot"` (the default, and what every older site keeps doing): the service account,
  as before.
* `"auth": "me"`: your own Google account. `sheet_id`, `history_sheet_id` and `reports_sheet_id`
  are optional: PulseO **creates the sheets itself** in your Drive (folder `PulseO`, a folder per site,
  and `PulseO · Index` that remembers which sheet belongs to which site, so no ID has to be written
  down). Once per new site you get an email, `PulseO · sheets ready · <label>`, with the links. Ids
  you do put in `SITES_JSON` win over the Index, but they must be sheets PulseO created.
* `"recipients": []` in a `deck` means: send to the address in the `SMTP_USER` secret.

**Moving and renaming is safe.** Once the sheets exist, PulseO finds them by the IDs it keeps in
`PulseO · Index`, never by folder name or location. You can move the site folders and sheets that
PulseO created anywhere in your Drive, into project folders, and rename them (for example to
`Example · PulseO`); nothing breaks. Keep `PulseO · Index` where it is: it is found by its exact
name, so do not rename it. If you delete one of a site's sheets, the Index still lists it and the
site fails with a Google "not found" error until you clear that ID cell in the Index; PulseO then
creates a new, empty sheet for it on the next run.

**Moving an existing `robot` site to `me`.** Change `"auth"` to `"me"` and delete its three
`sheet_id` / `history_sheet_id` / `reports_sheet_id` lines (the old manually made sheets cannot be
opened with the `me` permission and stay untouched as an archive). PulseO creates new sheets,
backfills the history (it can take more than one run) and sends the "sheets ready" email. The new
Reports sheet has an empty `State`, so to avoid sending the decks again run the workflow with
**mark_decks_site** set to the site name: it marks the latest weekly and monthly decks as sent and
emails nothing. Use it for every run until the decks show `marked as sent`.

Before a `me` site is synced PulseO checks that the exact `site_url` is in your Search Console
property list with a permission above *unverified user*. If not, the site is skipped, the run goes
red (`site-2: failed (… no Search Console access with "me" auth)`, no property name in the log) and you
get an email with the name. If Google says the refresh token is no longer valid (`invalid_grant`), all
`me` sites are skipped, the `robot` sites keep running, the run goes red and you get one email.

**What the two permissions mean**
* `webmasters.readonly`: read Search Console. PulseO can never change anything there.
* `drive.file`: create files, and open only the files **PulseO itself created**. It cannot see
  the rest of your Drive.

**One-time setup (about 15 minutes, all in the browser)**
1. Google Cloud console → your project → *APIs & Services → Library* → enable **Google Drive API**
   (Search Console and Sheets are already on).
2. *Google Auth Platform → Audience*: user type **External**, then **Publish app** so the status is
   **In production**. (In *Testing* the token stops working after 7 days.)
3. *Data access*: add the two scopes above.
4. *Clients → Create client → Web application*, with the authorized redirect URI
   `https://developers.google.com/oauthplayground`. Keep the client ID and secret to yourself.
5. Open the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground), gear icon →
   *Use your own OAuth credentials*, paste the ID and secret. Enter the two scopes, **Authorize APIs**
   with the Google account that has the Search Console access, then **Exchange authorization code for
   tokens** and copy the **refresh token**.
6. GitHub → *Settings → Secrets and variables → Actions* → new secret `GSC_OAUTH_JSON`:
   `{"client_id":"…","client_secret":"…","refresh_token":"…"}`

Google shows *"Google hasn't verified this app"* during step 5: that is expected for a personal
tool. Choose *Advanced → Go to … (unsafe)*; it is your own app. The token is never printed or written
to a file; it is scrubbed from the logs like the other secrets.

**To revoke access:** myaccount.google.com → *Security* → *Third-party apps with account access*
→ remove the app. PulseO then reports `lost access` on the next run.

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

**Adding sites without retyping `SITES_JSON`:** a second, optional secret `SITES_JSON_EXTRA` has
the same format and is merged into the list at run time. If a name appears in both, the entry in
`SITES_JSON_EXTRA` wins (it replaces the whole entry, in its place), so you can also use it to
replace a site. The merged list is validated like any `SITES_JSON`. Secrets cannot be read back, so
this way you only ever paste the new sites.

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
4. Run **PulseO weekly sync** manually. A big site's history backfill may take more
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
