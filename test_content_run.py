"""Tests for the content dashboard: creating the file and refreshing it (v2.5).
Search Console, Sheets and Drive are in-memory fakes; all sites, pages and searches are invented."""

import json
import unittest
from datetime import date

import autocomplete
import content
import content_run
import content_sheet as cs
import provision
import sites
from test_history import FakeSheets, patch_time
from test_own_access import FakeDrive

TODAY, FINAL = date(2026, 10, 27), date(2026, 10, 24)
ROOT = "https://example.test"
A, B, ABOUT, HOME = f"{ROOT}/article/a", f"{ROOT}/article/b", f"{ROOT}/about", f"{ROOT}/"

PAGES = {A: (30, 1000, 6.0), B: (2, 10, 12.0), ABOUT: (50, 600, 2.0), HOME: (200, 9000, 1.5)}
PAIRS = [("garden shoes", A, 20, 600, 6.0), ("how to plant a tree", A, 5, 200, 7.0),
         ("example brand", A, 5, 200, 1.0), ("tiny question", B, 0, 10, 12.0),
         ("about us", ABOUT, 40, 500, 2.0), ("what is example", ABOUT, 10, 100, 2.5),
         ("best bread recipes", HOME, 3, 500, 9.0), ("bread recipe easy", HOME, 2, 300, 11.0),
         ("example", HOME, 150, 8000, 1.5)]


def row(keys, clicks, impressions, position):
    return {"keys": keys, "clicks": clicks, "impressions": impressions, "position": position}


def fetch(dimensions, first, last, groups=None):
    """Search Console: the 90-day tables, a scaled copy for each month, and one page over a range."""
    if groups:                                           # a before/after look at one page
        url = groups[0]["filters"][0]["expression"]
        return [row([url], 10, 100, 8.0)] if last < date(2026, 8, 1) else [row([url], 15, 100, 6.0)]
    if dimensions == ["page"]:
        if (last - first).days < 40:                     # one month: September is twice as big as the others
            scale = 3 if first == date(2026, 9, 1) else 6
            rows = [row([u], c // scale, n // scale, p) for u, (c, n, p) in PAGES.items()]
            if first <= date(2026, 7, 1):                # a page that used to get traffic and lost it
                rows.append(row([f"{ROOT}/article/gone"], 5, 50, 9.0))
            return rows
        return [row([u], c, n, p) for u, (c, n, p) in PAGES.items()]
    if dimensions == ["query", "page"]:
        return [row([q, u], c, n, p) for q, u, c, n, p in PAIRS]
    totals = {}
    for q, u, c, n, p in PAIRS:
        t = totals.setdefault(q, [0, 0, 0.0])
        t[0] += c
        t[1] += n
        t[2] += p * n
    return [row([q], c, n, w / n) for q, (c, n, w) in totals.items()]


def make_site(**content_extra):
    block = {"enabled": True, "min_gap_impressions_90d": 100, "seasonal_regex": "ramadan",
             "tabs": [{"name": "Articles", "url_regex": r"^https://example\.test/article/.+"},
                      {"name": "Landing Pages", "url_regex": r"^https://example\.test/[^/?#.]+$",
                       "exclude_regex": "/(en|home)$"}]}
    block.update(content_extra)
    site = sites.parse_sites_json(json.dumps([{
        "name": "site-x", "site_url": "sc-domain:example.test", "auth": "me",
        "deck": {"enabled": True, "language": "en", "site_label": "Example", "recipients": []},
        "content": block}]))[0]
    site.reports_sheet_id = "REP"
    return site


class Base(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.drive = FakeDrive(self.fs)
        self.fs.add_book("REP", {"AIO Raw": (10, 4)})
        self.site = make_site()
        self.sent, self.logs = [], []
        self.file_id = None

    def run_it(self, force=False, today=TODAY, final=FINAL, http=None, site=None):
        result = content_run.run_content(
            self.drive, self.fs, site or self.site, "REP", fetch, final, today,
            notify=lambda s, b: self.sent.append((s, b)), log=self.logs.append, force=force,
            http_get=http or (lambda url: (200, "[]")), sleep=lambda s: None)
        self.file_id = result.file_id
        return result

    # ---- reading and changing the file the way the owner would ------------------------
    def table(self, tab):
        return cs.table_of(cs.read_all(self.fs, self.file_id, tab))

    def find(self, tab, url):
        return next(r for r in self.table(tab)[1] if r.get(content.URL) == url)

    def topic(self, tab, keyword):
        return next((r for r in self.table(tab)[1] if r.get(content.KEY) == keyword and not r.get(content.URL)), None)

    def raw(self, tab):
        return self.fs.books[self.file_id][tab]

    def edit(self, tab, url, name, value):
        t = self.raw(tab)
        header = t.data[0]
        url_col, col = header.index(content.URL), header.index(name)
        for r in t.data[1:]:
            if len(r) > url_col and r[url_col] == url:
                r.extend([""] * (len(header) - len(r)))
                r[col] = value
                return
        raise AssertionError("row not found")

    def add_row(self, tab, **cells):
        t = self.raw(tab)
        header = t.data[0]
        t.data.append([cells.get(h, "") for h in header])
        t.row_count = max(t.row_count, len(t.data))


class CreateTests(Base):

    def test_first_run_creates_the_file_next_to_the_index_and_emails_once(self):
        result = self.run_it()
        self.assertTrue(result.created and result.refreshed)
        self.assertEqual(self.drive.names(provision.SHEET).count("[Example] Content Dashboard"), 1)
        self.assertEqual([s for s, _ in self.sent], ["Content dashboard ready · Example"])
        index = self.fs.books[self.drive.id_of(provision.INDEX_NAME)][provision.INDEX_TAB].data
        self.assertEqual(index[0][6], "content_id")
        self.assertEqual(index[1][0], "site-x")
        self.assertEqual(index[1][6], self.file_id)
        self.assertEqual(list(self.fs.books[self.file_id]),
                         ["Articles", "Landing Pages", "Guide", "Keyword Seeds", "Keyword Ideas", "_state"])

    def test_every_tab_has_the_agreed_headers_and_the_guide_its_title(self):
        self.run_it()
        for name in ("Articles", "Landing Pages"):
            self.assertEqual(self.raw(name).data[0], content.HEADERS)
        self.assertEqual(len(content.HEADERS), 42)
        self.assertEqual(self.raw("Keyword Seeds").data[0], cs.SEEDS_HEADER)
        self.assertEqual(self.raw("Keyword Ideas").data[0], cs.IDEAS_HEADER)
        self.assertEqual(self.raw("Guide").data[0][0], "Example Content Dashboard · How to use this sheet")
        self.assertEqual(self.raw("Guide").data[3][0], "Tabs")

    def test_the_second_run_in_the_same_month_changes_nothing(self):
        self.run_it()
        before = json.dumps(self.raw("Articles").data)
        result = self.run_it()
        self.assertFalse(result.created or result.refreshed)
        self.assertEqual(json.dumps(self.raw("Articles").data), before)
        self.assertEqual(len(self.sent), 1)                         # no second "ready" email
        self.assertEqual(self.drive.names(provision.SHEET).count("[Example] Content Dashboard"), 1)

    def test_a_forced_refresh_runs_again(self):
        self.run_it()
        self.assertTrue(self.run_it(force=True).refreshed)

    def test_a_new_month_refreshes(self):
        self.run_it()
        result = self.run_it(today=date(2026, 11, 3), final=date(2026, 10, 31))
        self.assertTrue(result.refreshed)
        self.assertEqual(result.month, "2026-10")

    def test_nothing_the_file_shows_names_the_tool(self):
        self.run_it()
        for name, tab in self.fs.books[self.file_id].items():
            text = json.dumps(tab.data, ensure_ascii=False).lower()
            self.assertNotIn("pulse", text, name)
        self.assertNotIn("pulse", "[Example] Content Dashboard".lower())


class RowTests(Base):

    def setUp(self):
        super().setUp()
        self.result = self.run_it()

    def test_published_pages_get_a_row_in_their_tab(self):
        articles = {r[content.URL] for r in self.table("Articles")[1] if r[content.URL]}
        self.assertEqual(articles, {A, B})
        self.assertEqual({r[content.URL] for r in self.table("Landing Pages")[1]}, {ABOUT})
        self.assertEqual(self.result.new_pages, 3)                 # the home page belongs to no tab

    def test_a_page_row(self):
        r = self.find("Articles", A)
        self.assertEqual((r[content.KEY], r[content.STATUS], r[content.TODO]), ("garden shoes", "Published", "Update"))
        self.assertEqual(r["Suggested Action"], "Update to reach the top 3")
        self.assertEqual(r["Why"], "On page 1 (position 6.0) with 1,000 impressions.")
        self.assertEqual((r["Clicks (90d)"], r["Impressions (90d)"], r["Avg. Position"]), ("30", "1000", "6.0"))
        self.assertEqual(r["CTR"], "0.03")
        self.assertEqual(r["Primary KW Impr."], "600")
        self.assertEqual(r["Secondary Keywords"], "how to plant a tree")
        self.assertEqual(r["Query Fan-outs"], "how to plant a tree")
        self.assertEqual(r["Ranking Queries"], "3")
        self.assertEqual((r["Intent"], r["Keyword Type"], r["Content Type"]), ("Informational", "General", "Cluster"))
        self.assertEqual(r["Slug"], "/article/a")
        self.assertEqual(float(r["Clicks MoM"]), 1.0)             # September is twice August
        self.assertIn(r["Priority"], content.PRIORITIES)
        self.assertEqual(r["Alerts"], "")

    def test_a_weak_page_is_a_prune_candidate(self):
        r = self.find("Articles", B)
        self.assertEqual((r[content.TODO], r["Suggested Action"]), ("Keep", "Prune or merge candidate"))
        self.assertIn("Prune candidate", r["Alerts"])

    def test_brand_searches_are_never_a_primary_keyword(self):
        for tab in ("Articles", "Landing Pages"):
            for r in self.table(tab)[1]:
                self.assertNotIn("example", str(r[content.KEY]).lower())

    def test_a_landing_page_row(self):
        r = self.find("Landing Pages", ABOUT)
        self.assertEqual((r["Intent"], r["Content Type"], r["Slug"]), ("Navigational", "Pillar", "/about"))
        self.assertEqual(r[content.KEY], "about us")

    def test_new_topics_come_from_searches_that_land_on_the_home_page(self):
        t = self.topic("Articles", "best bread recipes")
        self.assertIsNotNone(t)
        self.assertEqual((t[content.STATUS], t[content.TODO], t[content.URL]), ("Inboxed", "Write", ""))
        self.assertEqual(t["Suggested Action"], "New page")
        self.assertEqual(t["Alerts"], "No fitting page")
        self.assertEqual(t["Secondary Keywords"], "bread recipe easy")
        self.assertEqual(t["Why"], "800 impressions across 2 searches land on /, which doesn't cover this topic.")
        self.assertEqual(t["Impressions (90d)"], "800")
        self.assertEqual(self.result.new_topics, 1)
        self.assertIsNone(self.topic("Landing Pages", "best bread recipes"))     # topics go to the first tab

    def test_searches_a_content_page_already_answers_are_no_topics(self):
        keywords = {r[content.KEY] for r in self.table("Articles")[1]}
        self.assertNotIn("about us", keywords)

    def test_the_state_remembers_the_refresh_and_the_proposed_topic(self):
        state = {tuple(r[:2]): r[2] for r in map(list, self.raw("_state").data[1:])}
        self.assertEqual(state[("meta", "last_refresh")], "2026-09")
        self.assertIn(("gap", "best bread recipes"), state)

    def test_the_urgent_rows_go_into_the_owner_note(self):
        subject, body = content_run.owner_email(
            content_run.ContentResult(month="2026-09", new_pages=2, new_topics=1, gone=1,
                                      urgent=[("garden shoes", "Update to reach the top 3", "Articles")]),
            "Example", "https://docs.example.test/x")
        self.assertEqual(subject, "Content dashboard refreshed · Example · 2026-09")
        self.assertIn("New pages added: 2", body)
        self.assertIn("- garden shoes: Update to reach the top 3 [Articles]", body)


class OwnershipTests(Base):

    def setUp(self):
        super().setUp()
        self.run_it()

    def test_a_refresh_never_changes_what_the_owner_wrote(self):
        self.edit("Articles", A, content.STATUS, "Briefed")
        self.edit("Articles", A, content.TODO, "Rewrite")
        self.edit("Articles", A, content.KEY, "my own keyword")
        self.edit("Articles", A, "Entities", "people and places")
        self.edit("Articles", A, "Notes/Checklist", "call the author")
        self.edit("Articles", A, content.FANOUTS, "my own questions")
        self.edit("Articles", A, content.RECOMMENDATIONS, "add three links")
        self.edit("Articles", A, "PIC", "Sara")
        self.run_it(force=True)
        r = self.find("Articles", A)
        self.assertEqual((r[content.STATUS], r[content.TODO], r[content.KEY]), ("Briefed", "Rewrite", "my own keyword"))
        self.assertEqual((r["Entities"], r["Notes/Checklist"], r["PIC"]), ("people and places", "call the author", "Sara"))
        self.assertEqual((r[content.FANOUTS], r[content.RECOMMENDATIONS]), ("my own questions", "add three links"))
        self.assertEqual(r["Clicks (90d)"], "30")                   # PulseO's own columns are still written
        self.assertEqual(r["Primary KW Impr."], "")                 # the metrics follow the owner's keyword

    def test_empty_fanouts_and_recommendations_are_filled_but_never_overwritten(self):
        self.edit("Articles", A, content.FANOUTS, "")
        self.run_it(force=True)
        self.assertEqual(self.find("Articles", A)[content.FANOUTS], "how to plant a tree")

    def test_columns_are_found_by_name_after_the_owner_moved_them(self):
        t = self.raw("Articles")
        header = t.data[0]
        a, b = header.index("Score"), header.index("Entities")
        self.edit("Articles", A, "Entities", "people and places")
        for r in t.data:                                            # swap two columns in every row
            r.extend([""] * (len(header) - len(r)))
            r[a], r[b] = r[b], r[a]
        self.run_it(force=True)
        r = self.find("Articles", A)
        self.assertEqual(r["Entities"], "people and places")
        self.assertEqual(r["Score"], str(int(float(r["Score"]))))
        self.assertGreater(int(r["Score"]), 0)

    def test_a_deleted_topic_is_not_proposed_again(self):
        t = self.raw("Articles")
        index = next(i for i, r in enumerate(t.data) if i and r and r[0] == "best bread recipes")
        t.data[index] = []
        self.run_it(force=True)
        self.assertIsNone(self.topic("Articles", "best bread recipes"))
        kinds = {tuple(r[:2]): r[0] for r in map(list, self.raw("_state").data[1:])}
        self.assertEqual(kinds[("dismissed", "best bread recipes")], "dismissed")
        self.run_it(force=True)
        self.assertIsNone(self.topic("Articles", "best bread recipes"))

    def test_a_topic_the_owner_keeps_is_updated_but_not_duplicated(self):
        self.run_it(force=True)
        rows = [r for r in self.table("Articles")[1] if r.get(content.KEY) == "best bread recipes"]
        self.assertEqual(len(rows), 1)

    def test_pasting_the_url_links_a_topic_to_its_page(self):
        new_url = f"{ROOT}/article/bread"
        t = self.raw("Articles")
        header = t.data[0]
        index = next(i for i, r in enumerate(t.data) if i and r and r[0] == "best bread recipes")
        t.data[index][header.index(content.URL)] = new_url
        t.data[index][header.index(content.STATUS)] = "Published"
        self.run_it(force=True)
        r = self.find("Articles", new_url)
        self.assertEqual(r[content.KEY], "best bread recipes")                # still the owner's row
        self.assertEqual(r["Suggested Action"], "Waiting for data")            # no impressions for that page yet


class RulesInTheFileTests(Base):

    def setUp(self):
        super().setUp()
        self.run_it()

    def test_a_published_page_without_impressions_for_two_months_is_gone(self):
        self.add_row("Articles", **{content.KEY: "old topic", content.URL: f"{ROOT}/article/gone",
                                    content.STATUS: "Published", content.TODO: "Keep"})
        self.add_row("Articles", **{content.KEY: "idea", content.URL: f"{ROOT}/article/draft",
                                    content.STATUS: "Inboxed"})
        result = self.run_it(force=True)
        gone = self.find("Articles", f"{ROOT}/article/gone")
        self.assertEqual(gone[content.STATUS], "Gone")
        self.assertEqual(gone["Suggested Action"], "Check the redirect or removal")
        self.assertIn("Gone", gone["Alerts"])
        self.assertEqual(self.find("Articles", f"{ROOT}/article/draft")[content.STATUS], "Inboxed")
        self.assertEqual(result.gone, 1)
        self.assertEqual(gone[content.TODO], "Keep")                        # only the status is written

    def test_a_gone_status_is_only_ever_set_for_published_rows(self):
        self.add_row("Articles", **{content.KEY: "later topic", content.URL: f"{ROOT}/article/later",
                                    content.STATUS: "Later"})
        self.run_it(force=True)
        self.assertEqual(self.find("Articles", f"{ROOT}/article/later")[content.STATUS], "Later")

    def test_duplicate_urls_are_flagged(self):
        self.add_row("Articles", **{content.KEY: "twin", content.URL: f"{ROOT}/Article/A/", content.STATUS: "Later"})
        self.run_it(force=True)
        self.assertIn("Duplicate URL", self.find("Articles", A)["Alerts"])
        self.assertIn("Duplicate URL", self.find("Articles", f"{ROOT}/Article/A/")["Alerts"])

    def test_impact_since_update(self):
        serial = lambda d: (d - date(1899, 12, 30)).days
        self.edit("Articles", A, content.PUBLISHED_DATE, serial(date(2026, 8, 1)))
        self.edit("Articles", B, content.PUBLISHED_DATE, serial(date(2026, 10, 20)))
        self.run_it(force=True)
        self.assertEqual(self.find("Articles", A)[content.IMPACT], "Clicks 10 → 15 (+50%), Pos 8.0 → 6.0")
        self.assertEqual(self.find("Articles", B)[content.IMPACT], "Too early (7 days)")

    def test_a_persian_url_is_kept_readable(self):
        pages = {"https://example.test/article/%DA%A9%D8%AA%D8%A7%D8%A8": (5, 300, 8.0)}
        stats = content.build_pages(pages, [("کتاب", "https://example.test/article/%DA%A9%D8%AA%D8%A7%D8%A8", 5, 300, 8.0)])
        self.assertEqual(list(stats), ["https://example.test/article/کتاب"])


class IdeasTests(Base):

    def setUp(self):
        super().setUp()
        self.run_it()
        self.calls = []

    def http(self, url):
        self.calls.append(url)
        q = url.split("&q=")[1].split("&")[0]
        return 200, json.dumps([q, [f"{q} one", "garden shoes extra"]])

    def add_seed(self, seed="shoes", active="Yes"):
        self.add_row("Keyword Seeds", Seed=seed, Language="en", Country="", Active=active)

    def test_an_active_seed_writes_new_ideas_once_a_week(self):
        self.add_seed()
        result = self.run_it(http=self.http)
        self.assertEqual(len(self.calls), 34)
        keywords = [r["Keyword"] for r in self.table("Keyword Ideas")[1]]
        self.assertIn("shoes one", keywords)
        self.assertEqual(len(keywords), len(set(keywords)))
        self.assertEqual(self.table("Keyword Seeds")[1][0]["Last run"], "2026-10-27")
        self.assertEqual(result.ideas, len(keywords))
        self.calls.clear()
        self.run_it(http=self.http)
        self.assertEqual(self.calls, [])                              # not due again for a week
        self.run_it(http=self.http, today=date(2026, 11, 4))          # due again, but Google's answers are cached
        self.assertEqual(self.calls, [])
        self.assertEqual(self.table("Keyword Seeds")[1][0]["Last run"], "2026-11-04")

    def test_ideas_fill_the_search_console_columns_when_the_site_already_gets_impressions(self):
        self.add_seed()
        self.run_it(http=self.http)
        idea = next(r for r in self.table("Keyword Ideas")[1] if r["Keyword"] == "garden shoes extra")
        self.assertEqual(idea["Impr (90d)"], "")                      # not a search the site is shown for
        self.assertEqual((idea["Seed"], idea["Language"], idea["First Found"]), ("shoes", "en", "2026-10-27"))

    def test_an_inactive_seed_does_nothing(self):
        self.add_seed(active="No")
        self.run_it(http=self.http)
        self.assertEqual(self.calls, [])

    def test_google_asking_us_to_slow_down_stops_softly(self):
        self.add_seed()
        result = self.run_it(http=lambda url: (429, ""))
        self.assertIn("429", result.autocomplete)
        self.assertEqual(self.table("Keyword Seeds")[1][0]["Last run"], "")      # the seed will be tried again
        self.assertTrue(any("stopped early" in l for l in self.logs))

    def test_an_idea_marked_add_becomes_a_topic_row_at_the_next_refresh(self):
        self.add_seed()
        self.run_it(http=self.http)
        t = self.raw("Keyword Ideas")
        header = t.data[0]
        index = next(i for i, r in enumerate(t.data) if i and r[0] == "shoes one")
        t.data[index].extend([""] * (len(header) - len(t.data[index])))
        t.data[index][header.index("Decision")] = "Add"
        result = self.run_it(force=True, http=self.http)
        r = self.topic("Articles", "shoes one")
        self.assertIsNotNone(r)
        self.assertEqual((r[content.STATUS], r[content.TODO]), ("Inboxed", "Write"))
        self.assertEqual(result.ideas_added, 1)
        self.run_it(force=True, http=self.http)
        self.assertEqual(len([x for x in self.table("Articles")[1] if x.get(content.KEY) == "shoes one"]), 1)


class ConfigGuardTests(Base):

    def test_a_file_it_cannot_open_is_explained(self):
        from googleapiclient.errors import HttpError
        from test_history import http_error

        class Denied(FakeSheets):
            def get(self, spreadsheetId, fields=None):
                raise http_error(404, "not found")

        site = make_site(sheet_id="SOMEONE-ELSES-FILE")
        self.fs = Denied()
        self.drive = FakeDrive(self.fs)
        with self.assertRaises(content_run.ContentError) as cm:
            self.run_it(site=site)
        self.assertNotIn("SOMEONE", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
