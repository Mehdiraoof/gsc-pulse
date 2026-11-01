"""Tests for importing old content sheets into the dashboard (v2.6).
Everything is invented: sites, pages, searches and the old sheets."""

import json
import unittest
from unittest import mock

import content
import content_import as ci
import content_run
import content_sheet as cs
import sites
import test_content_run as base
from test_content_run import A, B, HOME, ROOT, row

OLD_HEADER = ["Primary Keyword", "Status", "To Do", "Published URL", "Slug", "Suggested Title",
              "Meta Description", "Pillar", "Cluster", "Briefed Date", "Secondary Keywords", "Note"]
IMPORT = [{"sheet_id": "OLD-SHEET", "source_tab": "Old Blog", "target_tab": "Articles"}]


def old_rows():
    return [
        ["garden shoes", "Briefed", "Update", f"{ROOT}/article/a/#top", "N/A", "Great shoes", "A description",
         "Shoes", "Garden", "October 27, 2024", "a\n b", "note a"],
        ["old keyword", "Published", "Keep", "", "article/b", "N/A", "N/A", "", "", "N/A", "", "N/A"],
        ["c topic", "Pending", "", "", "", "", "", "", "", "", "", ""],
        ["best bread recipes", "Briefed", "Write", "", "", "", "", "", "", "", "", ""],
        ["ghost post", "Published", "Update", f"{ROOT}/article/ghost", "", "", "", "", "", "", "", ""],
        ["published idea", "Published", "", "", "", "", "", "", "", "", "", ""],
        ["twin", "Researched", "", f"{ROOT}/article/a?x=1", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "Lonely", "", "", "", ""],
    ]


C = f"{ROOT}/article/c"


def with_c():
    """The invented site gets one more article, found by its search."""
    pages = dict(base.PAGES)
    pages[C] = (4, 300, 8.0)
    pairs = list(base.PAIRS) + [("c topic", C, 4, 300, 8.0)]
    return mock.patch(f"{base.__name__}.PAGES", pages), mock.patch(f"{base.__name__}.PAIRS", pairs)


class CleaningTests(unittest.TestCase):

    def test_empty_words_and_placeholders_are_empty(self):
        for v in (None, "", "  ", "N/A", "n/a", "TBD", "#N/A"):
            self.assertEqual(ci.clean(v), "")
        self.assertEqual(ci.clean("Meta Title", "Meta Title"), "")
        self.assertEqual(ci.clean(" x "), "x")

    def test_columns_are_converted(self):
        self.assertEqual(ci.column_value("Briefed Date", "October 27, 2024"), "2024-10-27")
        self.assertEqual(ci.column_value("Published/Updated Date", "Oct 3, 2025"), "2025-10-03")
        self.assertEqual(ci.column_value("Briefed Date", "next week"), "next week")
        self.assertEqual(ci.column_value(ci.WORDS, "2,500"), 2500)
        self.assertEqual(ci.column_value(ci.SECONDARY, "a\n b \n\nc"), "a, b, c")
        self.assertEqual(ci.column_value(content.URL, f"{ROOT}/x/%D8%A7#top"), f"{ROOT}/x/ا")
        self.assertEqual(ci.column_value("Outlines", " one \n two "), "one\ntwo")

    def test_headers_are_matched_with_synonyms_and_the_rest_goes_to_notes(self):
        header = ["Primary Keyword", "Suggested Title", "Meta Description", "Suggested Outlinks",
                  "Note/Suggested Changes", "Pillar", "Cluster", "Topic", "Page type", "Clicks (90d)"]
        rows = [["kw", "T", "D", "u1", "n", "p", "c", "t", "Blog Post", "99"]]
        (old,) = ci.parse_rows(header, rows)
        self.assertEqual(old.values, {"Primary Keyword": "kw", "Meta Title": "T",
                                      "One-Sentence Description": "D", "Related URLs": "u1",
                                      "Notes/Checklist": "n"})        # the tool's own column is left out
        self.assertEqual(ci.legacy_line(old), "Pillar: p · Cluster: c · Topic: t · Page type: Blog Post")
        vals = ci.values_for(old, None, "added")
        self.assertEqual(vals["Notes/Checklist"], "Pillar: p · Cluster: c · Topic: t · Page type: Blog Post\nn")

    def test_urls_are_normalized_before_rows_are_matched(self):
        same = ["https://www.example.test/a/b/", "http://example.test/a/b", "https://example.test/a//b#x",
                "https://example.test/a/b?utm=1#y"]
        self.assertEqual(len({content.url_key(u) for u in same}), 1)
        self.assertNotEqual(content.url_key("https://example.test/a/b"), content.url_key("https://example.test/a/c"))
        self.assertEqual(content.url_key("https://example.test"), content.url_key("https://example.test/"))
        self.assertEqual(content.display_url("https://example.test/a/?x=1#y"), "https://example.test/a/")

    def test_a_half_space_or_tatweel_makes_a_different_address(self):
        word = "میخواهم"                  # میخواهم
        zwnj = "می‌خواهم"          # می‌خواهم (half space)
        tatweel = "میـخواهم"
        root = "https://example.test/article/"
        keys = {content.url_key(root + w) for w in (zwnj, word, tatweel)}
        self.assertEqual(len(keys), 3)                                       # three pages, as in Search Console
        # ... but the Duplicate URL alert still sees them as look-alikes
        self.assertEqual(len({content.dup_key(root + w) for w in (zwnj, word, tatweel)}), 1)
        self.assertEqual(len({content.fold_url(content.url_key(root + w)) for w in (zwnj, word, tatweel)}), 1)

    def test_encoding_nfc_slash_query_fragment_http_and_www_do_not_make_a_different_address(self):
        zwnj = "می‌خواهم"
        root = "https://example.test/article/"
        spellings = [
            root + zwnj,
            root + "%D9%85%DB%8C%E2%80%8C%D8%AE%D9%88%D8%A7%D9%87%D9%85",             # percent-encoded
            root + "%D9%85%DB%8C%E2%80%8C%D8%AE%D9%88%D8%A7%D9%87%D9%85/?x=1#top",     # slash, query, fragment
            "http://www.example.test/article/" + zwnj,
        ]
        self.assertEqual(len({content.url_key(u) for u in spellings}), 1, [content.url_key(u) for u in spellings])
        composed, decomposed = root + "آب", root + "آب"   # آب, written two ways
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(content.url_key(composed), content.url_key(decomposed))
        self.assertNotEqual(content.url_key(root + zwnj), content.url_key(root + zwnj + "ن"))

    def test_the_page_variant_with_most_impressions_names_the_page(self):
        stats = content.build_pages({f"{ROOT}/p/": (1, 10, 5.0), f"{ROOT}/p": (9, 90, 5.0)}, [])
        (s,) = stats.values()
        self.assertEqual((s.url, s.impressions), (f"{ROOT}/p", 100))


class ConfigTests(unittest.TestCase):

    def parse(self, **extra):
        block = {"enabled": True, "tabs": [{"name": "Blog Posts", "url_regex": "x"}], **extra}
        return sites.parse_sites_json(json.dumps([{"name": "s", "site_url": "sc-domain:e.test", "auth": "me",
                                                   "content": block}]))[0].content

    def test_import_from_is_read(self):
        cfg = self.parse(import_from=[{"sheet_id": " ID ", "source_tab": "Old", "target_tab": "Blog Posts"}])
        self.assertEqual([(i.sheet_id, i.source_tab, i.target_tab) for i in cfg.imports], [("ID", "Old", "Blog Posts")])
        self.assertIn("ID", cfg.sensitive_values())

    def test_bad_import_from_is_refused_without_echoing_values(self):
        for bad in ([{"sheet_id": "S3CRET", "source_tab": "Old", "target_tab": "Nope"}],
                    [{"sheet_id": "S3CRET", "source_tab": "Old"}],
                    [{"sheet_id": "", "source_tab": "Old", "target_tab": "Blog Posts"}],
                    {"sheet_id": "S3CRET"}):
            with self.assertRaises(sites.SitesConfigError) as caught:
                self.parse(import_from=bad)
            self.assertNotIn("S3CRET", str(caught.exception))

    def test_the_log_tab_name_is_reserved(self):
        with self.assertRaises(sites.SitesConfigError):
            sites.parse_sites_json(json.dumps([{"name": "s", "site_url": "sc-domain:e.test", "auth": "me",
                                                "content": {"tabs": [{"name": "Import Log", "url_regex": "x"}]}}]))

    def test_tabs_match_on_the_address_without_query_and_fragment(self):
        tab = sites.ContentTab("T", r"^https://e\.test/[^/?#]+/?$")
        self.assertTrue(tab.matches("https://e.test/post/?replytocom=3#c"))
        self.assertFalse(tab.matches("https://e.test/a/b"))


class MatchingTests(base.Base):
    """The importer's matching, on the invented site, through a whole run."""

    def reader(self, header=None, rows=None):
        self.reads = []

        def read(spec):
            self.reads.append(spec.target_tab)
            return (header or OLD_HEADER), (old_rows() if rows is None else rows)
        return read

    def run_import(self, reader=None, site=None, **kw):
        result = content_run.run_content(
            self.drive, self.fs, site or self.site, "REP", base.fetch, base.FINAL, base.TODAY,
            notify=lambda s, b: self.sent.append((s, b)), log=self.logs.append,
            http_get=lambda url: (200, "[]"), sleep=lambda s: None, read_source=reader or self.reader(), **kw)
        self.file_id = result.file_id
        return result

    def setUp(self):
        super().setUp()
        self.site = base.make_site(import_from=IMPORT)

    def test_every_old_row_lands_somewhere_and_says_how(self):
        pages, pairs = with_c()
        with pages, pairs:
            result = self.run_import()
        (spec, report), = result.imports
        kinds = {e.src_row: e.kind for e in report.entries}
        self.assertEqual(kinds, {2: "url", 3: "slug", 4: "keyword", 5: "added", 6: "added",
                                 7: "added", 8: "duplicate", 10: "skipped"})
        self.assertEqual((report.source_rows, report.skipped), (8, 1))
        self.assertIn("by URL", report.text())

    def test_a_row_matched_by_url_keeps_what_the_old_sheet_says(self):
        pages, pairs = with_c()
        with pages, pairs:
            self.run_import()
        a = next(r for r in self.table("Articles")[1] if r.get(content.KEY) == "garden shoes")
        self.assertEqual(a[content.URL], A)
        self.assertEqual((a[content.KEY], a[content.STATUS], a[content.TODO]), ("garden shoes", "Briefed", "Update"))
        self.assertEqual((a["Meta Title"], a["One-Sentence Description"], a["Briefed Date"]),
                         ("Great shoes", "A description", "2024-10-27"))
        self.assertEqual(a["Notes/Checklist"],
                         "Pillar: Shoes · Cluster: Garden\nSecondary keywords before: a, b\nnote a")
        self.assertEqual(a["Impressions (90d)"], "1000")                       # the tool fills the data columns
        self.assertNotIn("a, b", a["Secondary Keywords"])                      # (its own searches go here)

    def test_the_slug_and_the_keyword_match_pages_too(self):
        pages, pairs = with_c()
        with pages, pairs:
            self.run_import()
        b = self.find("Articles", B)
        self.assertEqual((b[content.KEY], b[content.STATUS], b[content.TODO], b["Slug"]),
                         ("old keyword", "Published", "Keep", "article/b"))
        c = self.find("Articles", C)
        self.assertEqual((c[content.KEY], c[content.STATUS]), ("c topic", "Pending"))   # an unknown status stays

    def test_rows_without_a_page_are_added_and_keep_their_status(self):
        pages, pairs = with_c()
        with pages, pairs:
            self.run_import()
        topic =self.topic("Articles", "best bread recipes")
        self.assertEqual((topic[content.STATUS], topic[content.TODO]), ("Briefed", "Write"))
        self.assertEqual(len([r for r in self.table("Articles")[1] if r.get(content.KEY) == "best bread recipes"]), 1)
        ghost = self.find("Articles", f"{ROOT}/article/ghost")
        self.assertEqual((ghost[content.KEY], ghost[content.STATUS]), ("ghost post", "Published"))
        self.assertEqual(ghost["Impressions (90d)"], "0")
        idea = self.topic("Articles", "published idea")
        self.assertEqual(idea[content.STATUS], "Published")
        self.assertEqual(idea["Alerts"], "Marked Published, page not found")
        lonely = [r for r in self.table("Articles")[1] if "Lonely" in str(r.get("Notes/Checklist"))]
        self.assertEqual(lonely, [])                                           # nothing to hold on to: skipped

    def test_the_alert_for_a_published_row_without_a_page_survives_the_next_refresh(self):
        pages, pairs = with_c()
        with pages, pairs:
            self.run_import()
            self.run_import(force=True)
        self.assertEqual(self.topic("Articles", "published idea")["Alerts"],
                         "Marked Published, page not found")

    def test_it_runs_once(self):
        pages, pairs = with_c()
        with pages, pairs:
            reader = self.reader()
            self.run_import(reader)
            rows = len(self.table("Articles")[1])
            second = self.run_import(reader, force=True)
        self.assertEqual(second.imports, [])
        self.assertEqual(len(self.table("Articles")[1]), rows)
        self.assertEqual(self.reads.count("Articles"), 1)

    def test_the_import_log_says_where_each_row_went(self):
        pages, pairs = with_c()
        with pages, pairs:
            self.run_import()
        header, log = self.table(cs.IMPORT_LOG)
        self.assertEqual(header, ci.LOG_HEADER)
        by_row = {int(r["Source row"]): r["Matched by"] for r in log}
        self.assertEqual(by_row[2], "URL")
        self.assertEqual(by_row[3], "Slug")
        self.assertEqual(by_row[4], "Keyword")
        self.assertTrue(by_row[5].startswith("No match"))
        self.assertTrue(by_row[10].startswith("Skipped"))

    ROOT_A = "https://example.test/article/"
    ZWNJ_WORD = "می‌خواهم"            # می‌خواهم (half space)
    PLAIN_WORD = "میخواهم"                  # میخواهم
    TATWEEL_WORD = "میـخواهم"

    def with_pages(self, *words):
        """The invented site also gets articles whose address holds the given spellings (encoded the
        way Search Console gives them)."""
        from urllib.parse import quote
        pages, pairs = dict(base.PAGES), list(base.PAIRS)
        for i, w in enumerate(words):
            url = self.ROOT_A + quote(w)
            pages[url] = (4, 300, 8.0)
            pairs.append((f"persian search {i}", url, 4, 300, 8.0))
        return mock.patch(f"{base.__name__}.PAGES", pages), mock.patch(f"{base.__name__}.PAIRS", pairs)

    def persian_rows(self):
        return [r for r in self.table("Articles")[1]
                if content.fold_url(str(r.get(content.URL))).endswith(self.PLAIN_WORD)]

    def test_the_import_falls_back_to_the_other_spelling_only_when_the_exact_address_is_missing(self):
        pages, pairs = self.with_pages(self.ZWNJ_WORD)
        reader = self.reader(rows=[["my keyword", "Briefed", "Write", self.ROOT_A + self.TATWEEL_WORD] + [""] * 8])
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["url~"])
        (row_,) = self.persian_rows()
        self.assertEqual((row_[content.KEY], row_[content.STATUS], row_["Impressions (90d)"]),
                         ("my keyword", "Briefed", "300"))
        self.assertIn("half space", result.imports[0][1].text())

    def test_the_exact_address_wins_and_the_other_spelling_stays_a_row_of_its_own(self):
        pages, pairs = self.with_pages(self.ZWNJ_WORD, self.PLAIN_WORD)
        reader = self.reader(rows=[["my keyword", "Briefed", "Write", self.ROOT_A + self.PLAIN_WORD] + [""] * 8])
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["url"])
        rows = self.persian_rows()
        self.assertEqual(len(rows), 2)                                         # two addresses, two rows
        by_status = sorted(r[content.STATUS] for r in rows)
        self.assertEqual(by_status, ["Briefed", "Published"])
        for r in rows:
            self.assertIn("Duplicate URL", r["Alerts"])                        # and the alert says so

    def test_two_spellings_are_never_merged_by_a_refresh(self):
        self.site = base.make_site()
        self.run_it()
        self.add_row("Articles", **{content.KEY: "mine", content.URL: self.ROOT_A + self.TATWEEL_WORD,
                                    content.STATUS: "Briefed"})
        pages, pairs = self.with_pages(self.ZWNJ_WORD)
        with pages, pairs:
            self.run_it(force=True)
            self.run_it(force=True)
        rows = self.persian_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r[content.STATUS] for r in rows), ["Briefed", "Published"])
        mine = next(r for r in rows if r[content.KEY] == "mine")
        self.assertEqual(mine["Impressions (90d)"], "0")                       # its own address has no data
        self.assertTrue(all("Duplicate URL" in r["Alerts"] for r in rows))

    def test_the_file_still_names_no_tool(self):
        pages, pairs = with_c()
        with pages, pairs:
            self.run_import()
        for tab, table in self.fs.books[self.file_id].items():
            for r in table.data:
                for cell in r:
                    self.assertNotIn("pulse", str(cell).lower(), tab)

    def test_an_existing_topic_row_takes_the_old_status(self):
        self.site = base.make_site()
        self.run_it()                                                          # the dashboard exists, with its topics
        n = len(self.table("Articles")[1])
        self.site = base.make_site(import_from=IMPORT)
        result = self.run_import(self.reader(rows=[["best bread recipes", "Briefed", "Write"] + [""] * 9]))
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["topic"])
        self.assertEqual(len(self.table("Articles")[1]), n)
        self.assertEqual(self.topic("Articles", "best bread recipes")[content.STATUS], "Briefed")

    def test_an_old_sheet_that_cannot_be_read_is_reported_and_tried_again_next_time(self):
        def broken(spec):
            raise ci.ImportFailed("an old sheet to import cannot be opened")
        result = self.run_import(broken)
        self.assertEqual(result.imports, [])
        self.assertEqual(result.import_problems, ["an old sheet to import cannot be opened"])
        self.assertTrue(result.refreshed)                                      # the rest of the run went on
        pages, pairs = with_c()
        with pages, pairs:
            again = self.run_import()
        self.assertEqual(len(again.imports), 1)

    def test_the_owner_note_tells_what_was_imported(self):
        pages, pairs = with_c()
        with pages, pairs:
            result = self.run_import()
        _, body = content_run.owner_email(result, "Example", "link")
        self.assertIn("Old sheet imported into Articles", body)
        self.assertIn("Import Log", body)

    # ---- the words of the URL slug (step 4) and the "possible match" (step 5) ---------------

    W1 = f"{ROOT}/article/indoor-plant-care-guide"
    W2 = f"{ROOT}/article/winter-boots-review"
    P1 = f"{ROOT}/article/blue-garden-hose"
    P2 = f"{ROOT}/article/garden-hose-reel"

    @staticmethod
    def old(keyword, status="", todo="", url="", slug="", secondary="", pillar="", cluster="", note=""):
        return [keyword, status, todo, url, slug, "", "", pillar, cluster, "", secondary, note]

    @staticmethod
    def pages_with(extra):
        """The invented site with more articles: {address: impressions}, each with a search of its own."""
        pages, pairs = dict(base.PAGES), list(base.PAIRS)
        for i, (url, impressions) in enumerate(extra.items()):
            pages[url] = (4, impressions, 8.0)
            pairs.append((f"unrelated search {i}", url, 4, impressions, 8.0))
        return mock.patch(f"{base.__name__}.PAGES", pages), mock.patch(f"{base.__name__}.PAIRS", pairs)

    def test_all_the_words_of_the_keyword_in_one_slug_is_a_match(self):
        pages, pairs = self.pages_with({self.W1: 300, self.W2: 200})
        reader = self.reader(rows=[self.old("plant care guide", "Briefed", "Write", pillar="Plants"),
                                   self.old("winter boots", "Researched")])
        with pages, pairs:
            result = self.run_import(reader)
        (spec, report), = result.imports
        self.assertEqual([e.kind for e in report.entries], ["words", "words"])
        one = self.find("Articles", self.W1)
        self.assertEqual((one[content.KEY], one[content.STATUS], one[content.TODO]),
                         ("plant care guide", "Briefed", "Write"))
        self.assertIn("Pillar: Plants", one["Notes/Checklist"])
        self.assertEqual(self.find("Articles", self.W2)[content.STATUS], "Researched")
        self.assertIsNone(self.topic("Articles", "plant care guide"))        # no second row for it
        self.assertIn("2 by URL words", report.text())
        by_row = {int(r["Source row"]): r["Matched by"] for r in self.table(cs.IMPORT_LOG)[1]}
        self.assertEqual(by_row, {2: "URL words", 3: "URL words"})

    def test_filler_words_and_the_half_space_do_not_matter_in_persian(self):
        from urllib.parse import quote
        shoes = ROOT + "/article/" + quote("کفش-باغ")                 # کفش-باغ
        want = ROOT + "/article/" + quote("می-خواهم-کفش")   # می-خواهم-کفش
        pages, pairs = self.pages_with({shoes: 300, want: 200})
        reader = self.reader(rows=[
            self.old("بهترین کفش باغ", "Briefed"),     # بهترین کفش باغ
            self.old("می‌خواهم کفش", "Published")])        # می‌خواهم کفش
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["words", "words"])
        self.assertEqual(self.find("Articles", content.display_url(shoes))[content.STATUS], "Briefed")
        self.assertEqual(self.find("Articles", content.display_url(want))[content.STATUS], "Published")

    def test_at_least_sixty_percent_of_the_words_is_enough(self):
        url = f"{ROOT}/article/garden-tools-shed-plans"
        pages, pairs = self.pages_with({url: 300})
        reader = self.reader(rows=[self.old("garden shed building plans", "Briefed")])   # 3 of 4 words
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["words"])
        self.assertEqual(self.find("Articles", url)[content.KEY], "garden shed building plans")

    def test_several_pages_that_fit_are_never_merged_and_the_row_points_to_them(self):
        pages, pairs = self.pages_with({self.P1: 400, self.P2: 300})
        reader = self.reader(rows=[self.old("garden hose", "Researched")])
        with pages, pairs:
            result = self.run_import(reader)
            (e,) = result.imports[0][1].entries
            self.assertEqual(e.kind, "possible")
            topic = self.topic("Articles", "garden hose")
            self.assertEqual(topic[content.STATUS], "Researched")
            expected = f"No fitting page, Possible match: {self.P1} | {self.P2}"
            self.assertEqual(topic["Alerts"], expected)
            self.assertNotEqual(self.find("Articles", self.P1)[content.KEY], "garden hose")
            self.run_import(reader, force=True)                              # the pointer survives the next refresh
            self.assertEqual(self.topic("Articles", "garden hose")["Alerts"], expected)
        log = {int(r["Source row"]): r for r in self.table(cs.IMPORT_LOG)[1]}
        self.assertTrue(log[2]["Matched by"].startswith("Possible match"))
        self.assertIn(self.P1, log[2]["Page"])
        self.assertEqual(result.imports[0][1].text().count("1 with a possible match"), 1)

    def test_a_weaker_fit_is_only_a_possible_match(self):
        url = f"{ROOT}/article/garden-pond-supplies"
        pages, pairs = self.pages_with({url: 250})
        reader = self.reader(rows=[self.old("garden pond building design guide", "Briefed")])   # 2 of 5 words
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["possible"])
        self.assertIn(f"Possible match: {url}", self.topic("Articles", "garden pond building design guide")["Alerts"])
        self.assertNotEqual(self.find("Articles", url)[content.KEY], "garden pond building design guide")

    def test_a_row_with_its_own_address_is_never_merged_by_words(self):
        pages, pairs = self.pages_with({self.W2: 200})
        elsewhere = f"{ROOT}/article/somewhere-else"
        reader = self.reader(rows=[self.old("winter boots", "Published", url=elsewhere)])
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["possible"])
        mine = self.find("Articles", elsewhere)                                # added with its own address
        self.assertEqual((mine[content.KEY], mine[content.STATUS]), ("winter boots", "Published"))
        self.assertIn(f"Possible match: {self.W2}", mine["Alerts"])
        self.assertNotEqual(self.find("Articles", self.W2)[content.KEY], "winter boots")

    def test_a_page_an_earlier_row_has_taken_is_not_offered_again(self):
        pages, pairs = self.pages_with({self.W1: 300})
        reader = self.reader(rows=[self.old("first", url=self.W1), self.old("plant care guide", "Briefed")])
        with pages, pairs:
            result = self.run_import(reader)
        self.assertEqual([e.kind for e in result.imports[0][1].entries], ["url", "added"])

    # ---- the repair: rows an earlier import added without a page ----------------------------

    def test_the_repair_merges_clear_matches_lists_possible_ones_and_runs_once(self):
        rows = [self.old("plant care guide", "Briefed", "Write", pillar="Plants", cluster="Care", note="keep me"),
                self.old("garden hose", "Researched")]
        reader = self.reader(rows=rows)
        self.run_import(reader)                                              # no fitting page yet: both become topics
        self.assertIsNotNone(self.topic("Articles", "plant care guide"))
        self.assertIsNotNone(self.topic("Articles", "garden hose"))
        self.site = base.make_site(import_from=IMPORT, repair_imports=True)  # the pages are in the sitemap now
        sitemap = lambda: ([self.W1, self.P1, self.P2], True)
        result = self.run_import(reader, sitemap=sitemap)
        (spec, report), = result.repairs
        self.assertEqual([e.kind for e in report.entries], ["r-words", "r-possible"])
        self.assertEqual(result.imports, [])                                 # the old sheet is not read again
        self.assertIsNone(self.topic("Articles", "plant care guide"))
        page = self.find("Articles", self.W1)
        self.assertEqual((page[content.KEY], page[content.STATUS], page[content.TODO]),
                         ("plant care guide", "Briefed", "Write"))
        self.assertEqual(page["Notes/Checklist"], "Pillar: Plants · Cluster: Care\nkeep me")
        (removed,) = self.table(cs.REMOVED)[1]                               # nothing is lost: the old row is kept
        self.assertEqual((removed["Primary Keyword"], removed["Reason"], removed["Moved To"]),
                         ("plant care guide", ci.REPAIR_REASON, self.W1))
        topic = self.topic("Articles", "garden hose")                        # a possible match is only listed
        self.assertEqual(topic["Alerts"], f"No fitting page, Possible match: {self.P1} | {self.P2}")
        self.assertNotEqual(self.find("Articles", self.P1)[content.KEY], "garden hose")
        how = [r["Matched by"] for r in self.table(cs.IMPORT_LOG)[1]]
        self.assertIn("Repair: merged by URL words", how)
        self.assertIn("Repair: possible match, not merged", how)
        self.assertIn("repair of Articles", result.text())
        self.assertEqual(result.removed, {"merged": 1})
        again = self.run_import(reader, force=True, sitemap=sitemap)         # once only
        self.assertEqual(again.repairs, [])
        self.assertEqual(len(self.table(cs.REMOVED)[1]), 1)
        self.assertEqual(self.topic("Articles", "garden hose")["Alerts"], topic["Alerts"])

    def test_the_repair_only_runs_when_the_config_asks_for_it(self):
        reader = self.reader(rows=[self.old("plant care guide", "Briefed")])
        self.run_import(reader)
        result = self.run_import(reader, force=True, sitemap=lambda: ([self.W1], True))
        self.assertEqual(result.repairs, [])
        self.assertIsNotNone(self.topic("Articles", "plant care guide"))       # nothing was merged
        self.assertNotIn(cs.REMOVED, self.fs.books[self.file_id])

    def test_a_page_an_earlier_import_gave_to_an_old_row_is_not_taken_by_the_repair(self):
        pages, pairs = self.pages_with({self.W1: 300})
        reader = self.reader(rows=[self.old("indoor plant care guide", url=self.W1, status="Briefed"),
                                   self.old("plant care guide", "Researched")])
        with pages, pairs:
            self.run_import(reader)                                          # row 2 took W1 by address
            self.site = base.make_site(import_from=IMPORT, repair_imports=True)
            result = self.run_import(reader, force=True)
        (spec, report), = result.repairs
        self.assertEqual([e.kind for e in report.entries], ["r-none"])
        self.assertEqual(self.find("Articles", self.W1)[content.KEY], "indoor plant care guide")
        self.assertIsNotNone(self.topic("Articles", "plant care guide"))


if __name__ == "__main__":
    unittest.main()
