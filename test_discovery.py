"""
Tests for page type discovery (page_discovery.py) and the --discover-page-types command.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import gsc_pulse
import page_discovery
from test_history import FakeSheets, Req, http_error, patch_time

WWW  = "https://www.alpha.test/"
SHOP = "https://shop.alpha.test/"


def row(url, clicks, impressions):
    return {"keys": [url], "clicks": clicks, "impressions": impressions,
            "ctr": 0.0, "position": 5.0}


PAGES = [
    row(WWW, 50, 900),
    row(WWW + "en/article/a1", 30, 600),
    row(WWW + "en/article/a2?utm_source=x", 20, 400),
    row(WWW + "en/article/a3", 5, 100),
    row(WWW + "en/about", 2, 50),
    row(WWW + "fa/lonely", 1, 10),
    row(SHOP + "p/1", 8, 80),
    row(SHOP + "p/2", 4, 60),
    row(SHOP + "cart", 0, 5),
]


def by_pattern(table, level):
    return {r[1]: r for r in table[1:] if r[0] == level}


class AnalyzeTests(unittest.TestCase):

    def setUp(self):
        self.table, self.stats = page_discovery.analyze(PAGES)

    def test_header_and_total_row(self):
        self.assertEqual(self.table[0], page_discovery.HEADER)
        total = self.table[1]
        self.assertEqual(total[:5], ["Total", "(all pages in the period)", 9, 120, 2205])

    def test_subdomains_are_counted_with_all_their_pages(self):
        hosts = by_pattern(self.table, "Subdomain")
        self.assertEqual(set(hosts), {WWW, SHOP})
        self.assertEqual(hosts[WWW][2:5], [6, 108, 2060])      # pages, clicks, impressions
        self.assertEqual(hosts[SHOP][2:5], [3, 12, 145])

    def test_first_and_second_path_segments(self):
        p1 = by_pattern(self.table, "Path 1")
        self.assertEqual(p1[WWW + "en/"][2:5], [4, 57, 1150])
        self.assertEqual(p1[SHOP + "p/"][2:5], [2, 12, 140])
        p2 = by_pattern(self.table, "Path 2")
        self.assertEqual(p2[WWW + "en/article/"][2:5], [3, 55, 1100])

    def test_query_strings_do_not_create_groups(self):
        patterns = {r[1] for r in self.table[1:]}
        self.assertFalse(any("?" in p or "utm" in p for p in patterns))

    def test_single_page_folders_are_left_out_and_counted(self):
        p1 = by_pattern(self.table, "Path 1")
        self.assertNotIn(WWW + "fa/", p1)
        self.assertNotIn(SHOP + "cart/", p1)
        self.assertEqual(self.stats["hidden_small"], 6)   # 2 at Path 1 + 4 at Path 2

    def test_examples_are_the_top_three_by_clicks(self):
        en = by_pattern(self.table, "Path 1")[WWW + "en/"]
        self.assertEqual(en[6:9], [WWW + "en/article/a1", WWW + "en/article/a2?utm_source=x",
                                   WWW + "en/article/a3"])

    def test_groups_are_sorted_by_impressions(self):
        for level in page_discovery.LEVELS:
            impr = [r[4] for r in self.table[1:] if r[0] == level]
            self.assertEqual(impr, sorted(impr, reverse=True))

    def test_share_of_impressions(self):
        hosts = by_pattern(self.table, "Subdomain")
        self.assertEqual(hosts[WWW][5], round(100 * 2060 / 2205, 1))

    def test_group_with_fewer_than_three_examples_is_padded(self):
        p1 = by_pattern(self.table, "Path 1")[SHOP + "p/"]
        self.assertEqual(p1[6:9], [SHOP + "p/1", SHOP + "p/2", ""])

    def test_ties_are_stable(self):
        rows = [row(WWW + "x/b", 1, 10), row(WWW + "x/a", 1, 10)]
        table, _ = page_discovery.analyze(rows)
        self.assertEqual(by_pattern(table, "Path 1")[WWW + "x/"][6:8],
                         [WWW + "x/a", WWW + "x/b"])

    def test_empty_site(self):
        table, stats = page_discovery.analyze([])
        self.assertEqual(len(table), 2)
        self.assertEqual(stats["pages"], 0)

    def test_groups_per_level_are_capped(self):
        rows = [row(f"{WWW}s{i}/p{j}", 1, 1 + i) for i in range(400) for j in range(2)]
        table, _ = page_discovery.analyze(rows)
        self.assertEqual(len(by_pattern(table, "Path 1")), page_discovery.MAX_GROUPS["Path 1"])

    def test_fragment_variants_are_merged_into_one_page(self):
        rows = [row(WWW + "a/x", 10, 100), row(WWW + "a/x#i", 3, 40), row(WWW + "a/x#i-2", 2, 10),
                row(WWW + "a/y", 1, 5)]
        table, stats = page_discovery.analyze(rows)
        self.assertEqual(stats["pages"], 2)
        self.assertEqual(stats["fragments_merged"], 2)
        self.assertEqual(table[1][2:5], [2, 16, 155])                  # pages, clicks, impressions
        a = by_pattern(table, "Path 1")[WWW + "a/"]
        self.assertEqual((a[2], a[3], a[4]), (2, 16, 155))
        self.assertEqual(a[6:9], [WWW + "a/x", WWW + "a/y", ""])       # examples have no fragment

    def test_slug_start_groups_flat_slugs_by_their_first_word(self):
        rows = ([row(WWW + f"price-of-item-{i}/", 1, 10 + i) for i in range(3)]
                + [row(WWW + "price-list/", 1, 5), row(WWW + "guide-one/", 1, 5),
                   row(WWW + "guide-two/", 1, 5), row(WWW + "en/price-x", 1, 5)])
        table, _ = page_discovery.analyze(rows)
        slugs = by_pattern(table, "Slug start")
        self.assertEqual(set(slugs), {WWW + "price-"})                 # guide-: only 2 pages
        self.assertEqual(slugs[WWW + "price-"][2], 4)                  # the /en/ page is not flat

    def test_readable_pattern_column_decodes_percent_encoding(self):
        rows = [row(WWW + "%D9%85%D8%AF%D9%84-a/", 1, 5), row(WWW + "%D9%85%D8%AF%D9%84-b/", 1, 5),
                row(WWW + "%D9%85%D8%AF%D9%84-c/", 1, 5)]
        table, _ = page_discovery.analyze(rows)
        slug = by_pattern(table, "Slug start")[WWW + "%D9%85%D8%AF%D9%84-"]
        self.assertEqual(slug[-1], WWW + "مدل-")


class UrlGsc:
    """Search Console that answers the 'page' dimension with a fixed list."""

    def __init__(self, rows):
        self.rows, self.site_urls = rows, []

    def searchanalytics(self):
        return self

    def query(self, siteUrl, body):
        self.site_urls.append(siteUrl)
        page = self.rows[body["startRow"]: body["startRow"] + body["rowLimit"]]
        return Req(lambda: {"rows": page} if page else {})


SITES = [
    {"name": "site-a", "site_url": "sc-domain:alpha.test", "sheet_id": "A-MAIN-ID",
     "history_sheet_id": "A-HIST-ID", "reports_sheet_id": "A-REPORTS-ID"},
    {"name": "site-b", "site_url": "sc-domain:beta.test", "sheet_id": "B-MAIN-ID"},
    {"name": "site-c", "site_url": "sc-domain:gamma.test", "sheet_id": "C-MAIN-ID",
     "reports_sheet_id": "C-REPORTS-ID", "enabled": False},
]
SECRETS = ["sc-domain:alpha.test", "alpha.test", "A-MAIN-ID", "A-HIST-ID", "A-REPORTS-ID"]


class DiscoveryCommandTests(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.sheets = FakeSheets()
        for book in ("A-MAIN-ID", "A-HIST-ID", "A-REPORTS-ID"):
            self.sheets.add_book(book)
        self.gsc = UrlGsc(PAGES)

    def run_main(self, args=(), env=None):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("SITES_JSON", "SITE_URL", "SHEET_ID", "HISTORY_SHEET_ID",
                              "DISCOVER_SITE", "DAYS", "GITHUB_ACTIONS")}
        clean.update({"SITES_JSON": json.dumps(SITES), "GITHUB_ACTIONS": "true", **(env or {})})
        out, error = io.StringIO(), None
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(sys, "argv", ["gsc_pulse.py", "--config", "none.yaml", *args]), \
             mock.patch.object(gsc_pulse, "build",
                               lambda n, v, credentials=None: self.gsc if n == "webmasters" else self.sheets), \
             mock.patch.object(gsc_pulse, "get_credentials", lambda f: object()), \
             mock.patch.object(gsc_pulse, "PUBLIC_LOGS", True), \
             mock.patch.object(gsc_pulse, "_SENSITIVE", []), \
             contextlib.redirect_stdout(out):
            try:
                gsc_pulse.main()
            except (Exception, SystemExit) as e:     # noqa: BLE001
                error = e
        return out.getvalue(), error

    def tab(self, book="A-REPORTS-ID"):
        return self.sheets.books[book][page_discovery.TAB_NAME].data

    def test_flag_writes_only_the_discovery_tab_to_the_reports_sheet(self):
        out, err = self.run_main(["--discover-page-types", "site-a"])
        self.assertIsNone(err)
        data = self.tab()
        self.assertEqual(data[0], page_discovery.HEADER)
        self.assertGreater(len(data), 5)
        # nothing else ran: main and history sheets untouched, no v1 tabs, no history tabs
        self.assertEqual(set(self.sheets.books["A-MAIN-ID"]), {"Sheet1"})
        self.assertEqual(set(self.sheets.books["A-HIST-ID"]), {"Sheet1"})
        self.assertEqual(set(self.sheets.books["A-REPORTS-ID"]), {"Sheet1", page_discovery.TAB_NAME})
        self.assertEqual(set(self.gsc.site_urls), {"sc-domain:alpha.test"})

    def test_workflow_input_does_the_same_as_the_flag(self):
        out, err = self.run_main(env={"DISCOVER_SITE": "site-a"})
        self.assertIsNone(err)
        self.assertIn(page_discovery.TAB_NAME, self.sheets.books["A-REPORTS-ID"])

    def test_empty_input_means_a_normal_sync(self):
        # the workflow passes DISCOVER_SITE="" on scheduled runs: that must not trigger discovery
        out, err = self.run_main(env={"DISCOVER_SITE": ""})
        self.assertNotIn(page_discovery.TAB_NAME, self.sheets.books["A-REPORTS-ID"])
        self.assertIn("site-a", out)

    def test_log_shows_counts_only(self):
        out, err = self.run_main(["--discover-page-types", "site-a"])
        self.assertIn("9 pages (0 #fragment variants merged)", out)
        for secret in SECRETS + [WWW, SHOP, "shop.alpha"]:
            self.assertNotIn(secret, out)

    def test_unknown_site_name_is_refused_without_echoing_it(self):
        out, err = self.run_main(["--discover-page-types", "alpha.test"])
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertNotIn("alpha.test", str(err))
        self.assertNotIn(page_discovery.TAB_NAME, self.sheets.books["A-REPORTS-ID"])

    def test_site_without_reports_sheet_is_refused(self):
        out, err = self.run_main(["--discover-page-types", "site-b"])
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("reports_sheet_id", str(err))

    def test_disabled_site_is_refused(self):
        out, err = self.run_main(["--discover-page-types", "site-c"])
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("disabled", str(err))


if __name__ == "__main__":
    unittest.main()
