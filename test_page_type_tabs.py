"""The tabs of the content dashboard follow the page types of the site. A fake site (example.test): articles, songs,
videos, programs, a shop, a language folder (/en/) and a landing page. All names and searches are invented."""

import json
import unittest
from datetime import date
from unittest import mock

import content
import content_run
import content_sheet as cs
import sites
import test_content_run as tcr
from test_content_run import ROOT, TODAY, Base

DAY2 = date(2026, 10, 28)

A = f"{ROOT}/article/a"
EN_ARTICLE = f"{ROOT}/en/article/e1"
SONG = f"{ROOT}/song/s1"
EN_SONG = f"{ROOT}/en/song/s2"
VIDEO = f"{ROOT}/programepisode/v1"
PROGRAM = f"{ROOT}/program/p1"
SERIES = f"{ROOT}/programseries/q1"
SHOP = f"{ROOT}/shop/item"
ABOUT = f"{ROOT}/about"

PAGES = {A: (30, 1000, 6.0), EN_ARTICLE: (10, 400, 5.0), SONG: (40, 3000, 3.0), EN_SONG: (5, 300, 4.0),
         VIDEO: (20, 1200, 5.0), PROGRAM: (8, 500, 7.0), SERIES: (4, 200, 8.0), SHOP: (3, 800, 6.0),
         ABOUT: (50, 600, 2.0)}
PAIRS = [("garden shoes", A, 20, 600, 6.0), ("english garden words", EN_ARTICLE, 8, 300, 5.0),
         ("worship song lyrics", SONG, 30, 2500, 3.0), ("song one chords", SONG, 6, 400, 4.0),
         ("song one meaning", SONG, 4, 300, 5.0), ("english song words", EN_SONG, 5, 300, 4.0),
         ("episode title words", VIDEO, 15, 900, 5.0), ("program overview", PROGRAM, 8, 500, 7.0),
         ("buy widget online", SHOP, 3, 700, 6.0), ("about us", ABOUT, 40, 500, 2.0)]
SONG_SEARCHES = ["worship song lyrics", "song one chords", "song one meaning"]

PAGE_TYPES = [
    {"name": "Article", "regex": r"^https://example\.test/article/"},        # anchored: /en/article/ needs the language rule
    {"name": "Song", "regex": r"/song/"},
    {"name": "Video", "regex": r"/programepisode/"},
    {"name": "Program", "regex": r"/program/|/programseries/"},
    {"name": "Shop", "regex": r"/shop/"},
    {"name": "en", "regex": r"^https://example\.test/en(/|$)"}]
EXPLICIT = [{"name": "Articles", "url_regex": r"^https://example\.test/article/.+"},
            {"name": "Landing Pages", "url_regex": r"^https://example\.test/[^/?#.]+$", "exclude_regex": "/(en|home)$"}]


def site_with(content_block=None, page_types=PAGE_TYPES, **entry):
    block = {"enabled": True, "min_gap_impressions_90d": 100}
    block.update(content_block or {})
    site = sites.parse_sites_json(json.dumps([{
        "name": "site-x", "site_url": "sc-domain:example.test", "auth": "me", "page_types": page_types,
        "deck": {"enabled": True, "language": "en", "site_label": "Example", "recipients": []},
        "content": block, **entry}]))[0]
    site.reports_sheet_id = "REP"
    return site


DEFAULT = {"skip_types": ["Shop", "Other"], "tab_names": {"Article": "Articles", "Song": "Songs"}}
KALAMEH_LIKE = {**DEFAULT, "tabs": EXPLICIT}
LEGACY = {"tabs": EXPLICIT}


def names(site):
    return [t.name for t in site.content.tabs]


class ConfigTests(unittest.TestCase):

    def test_a_site_with_only_the_old_tab_list_is_exactly_as_before(self):
        site = site_with(LEGACY)
        self.assertEqual(names(site), ["Articles", "Landing Pages"])
        self.assertFalse(site.content.page_type_tabs)
        self.assertEqual(site.content.notes, [])

    def test_with_no_tab_list_every_page_type_makes_a_tab_in_the_order_of_the_page_types(self):
        site = site_with(DEFAULT)
        self.assertEqual(names(site), ["Articles", "Songs", "Video", "Program"])
        self.assertEqual([t.gaps for t in site.content.tabs], [True, False, False, False])    # ideas go to the first tab

    def test_skipped_types_get_no_tab_and_the_rest_is_a_tab_unless_skipped_too(self):
        self.assertEqual(names(site_with({"skip_types": ["Shop"], "tab_names": {"Article": "Articles"}})),
                         ["Articles", "Song", "Video", "Program", "Other"])
        self.assertNotIn("Shop", names(site_with(DEFAULT)))
        self.assertNotIn("Other", names(site_with(DEFAULT)))

    def test_a_language_is_never_a_tab(self):
        self.assertNotIn("en", names(site_with({"tab_names": {}})))
        for name in ("fa", "English", "Persian", "ar", "fa-ir"):
            types = [{"name": "Article", "regex": "/a/"}, {"name": name, "regex": f"/{name}/"}]
            self.assertEqual(names(site_with({"skip_types": ["Other"]}, page_types=types)), ["Article"], name)
        types = [{"name": "Article", "regex": "/a/"}, {"name": "It", "regex": "/it/"}, {"name": "Shop", "regex": "/s/"}]
        self.assertIn("It", names(site_with({"skip_types": ["Other"]}, page_types=types)))          # not a language code

    def test_a_type_with_the_name_of_a_configured_tab_is_covered_by_it(self):
        site = site_with(KALAMEH_LIKE)
        self.assertEqual(names(site), ["Articles", "Landing Pages", "Songs", "Video", "Program"])
        self.assertTrue(any("covered by the tab Articles" in n for n in site.content.notes))
        self.assertEqual([t.gaps for t in site.content.tabs], [True, False, False, False, False])

    def test_over_eight_tabs_the_last_types_are_named_in_the_notes(self):
        types = [{"name": f"Type{i}", "regex": f"/t{i}/"} for i in range(1, 11)]
        site = site_with({"skip_types": ["Other"]}, page_types=types)
        self.assertEqual(len(site.content.tabs), sites.MAX_CONTENT_TABS)
        (note,) = [n for n in site.content.notes if n.startswith("NO TAB")]
        self.assertIn("Type9, Type10", note)
        self.assertIn("2 page type", note)

    def test_nothing_left_is_an_error_not_a_crash_later(self):
        with self.assertRaises(sites.SitesConfigError):
            site_with({"skip_types": ["Article", "Song", "Video", "Program", "Shop", "Other"]})

    def test_first_match_wins_keeps_a_page_out_of_the_tabs_of_later_types_but_not_because_of_a_language(self):
        site = site_with({**DEFAULT, "tab_names": {}}, first_match_wins=True)
        video = next(t for t in site.content.tabs if t.name == "Video")
        self.assertNotIn("en", video.exclude_regex)                       # a language is not an earlier type
        self.assertIn("/song/", video.exclude_regex)                      # the song type is

    def test_the_language_folder_is_taken_off_before_a_page_is_placed(self):
        self.assertEqual(sites.strip_language(f"{ROOT}/en/song/x", ("en",)), f"{ROOT}/song/x")
        self.assertEqual(sites.strip_language(f"{ROOT}/en", ("en",)), f"{ROOT}/")
        self.assertEqual(sites.strip_language(f"{ROOT}/english/x", ("en",)), f"{ROOT}/english/x")
        self.assertEqual(sites.strip_language("/en/x?y=1", ("en",)), "/x?y=1")
        self.assertEqual(sites.strip_language(f"{ROOT}/en/song/x", ()), f"{ROOT}/en/song/x")
        site = site_with(DEFAULT)
        self.assertEqual(site.content.tab_of(EN_ARTICLE).name, "Articles")
        self.assertEqual(site.content.tab_of(EN_SONG).name, "Songs")
        self.assertIsNone(site.content.tab_of(SHOP))

    def test_bad_values_are_refused(self):
        for bad in ({"skip_types": "Shop"}, {"tab_names": {"Song": "no/slash"}}, {"page_type_tabs": "yes"}, {"tabs": []}):
            with self.assertRaises(sites.SitesConfigError, msg=bad):
                site_with(bad)


class FakeSiteBase(Base):

    def setUp(self):
        super().setUp()
        for target, value in (("PAGES", PAGES), ("PAIRS", PAIRS)):
            patcher = mock.patch.object(tcr, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.site = site_with(KALAMEH_LIKE)

    def tab_urls(self, tab):
        return {r[content.URL] for r in self.table(tab)[1] if r.get(content.URL)}

    def topic_keywords(self):
        return {r[content.KEY] for tab in self.table_names() for r in self.table(tab)[1]
                if not r.get(content.URL) and r.get(content.KEY)}

    def table_names(self):
        return [t.name for t in self.site.content.tabs]

    def everything(self, row):
        return " | ".join(str(row.get(c, "")) for c in (content.KEY, "Secondary Keywords"))


class PlacementTests(FakeSiteBase):

    def test_every_page_type_has_its_tab_and_each_page_is_in_the_tab_of_its_type(self):
        self.run_it()
        self.assertEqual([t for t in self.fs.books[self.file_id]][:6],
                         ["Articles", "Landing Pages", "Songs", "Video", "Program", "Guide"])
        self.assertEqual(self.tab_urls("Articles"), {A, EN_ARTICLE})
        self.assertEqual(self.tab_urls("Songs"), {SONG, EN_SONG})
        self.assertEqual(self.tab_urls("Video"), {VIDEO})
        self.assertEqual(self.tab_urls("Program"), {PROGRAM, SERIES})
        self.assertEqual(self.tab_urls("Landing Pages"), {ABOUT})

    def test_no_tab_for_a_skipped_type_a_language_or_the_rest(self):
        self.run_it()
        titles = list(self.fs.books[self.file_id])
        for banned in ("Shop", "en", "En", "English", "Other", "Removed Pages", "Import Log"):
            self.assertNotIn(banned, titles)
        every_url = set().union(*(self.tab_urls(t) for t in self.table_names()))
        self.assertNotIn(SHOP, every_url)

    def test_a_site_with_no_tab_list_at_all_gets_its_tabs_from_the_page_types(self):
        self.site = site_with(DEFAULT)
        self.run_it()
        self.assertEqual(list(self.fs.books[self.file_id])[:5], ["Articles", "Songs", "Video", "Program", "Guide"])
        self.assertEqual(self.tab_urls("Songs"), {SONG, EN_SONG})
        self.assertEqual(self.tab_urls("Articles"), {A, EN_ARTICLE})

    def test_the_guide_lists_the_new_tabs(self):
        self.run_it()
        guide = [r[0] for r in cs.read_all(self.fs, self.file_id, cs.GUIDE) if r]
        for tab in ("Articles", "Landing Pages", "Songs", "Video", "Program"):
            self.assertIn(tab, guide)

    def test_a_row_in_a_new_tab_follows_the_rules_for_rows(self):
        self.run_it()
        row = self.find("Songs", SONG)
        self.assertEqual(row[content.KEY], "worship song lyrics")          # from the page's own searches
        self.assertEqual(row[content.STATUS], "Published")
        self.assertIn("song one chords", row["Secondary Keywords"])
        self.assertNotIn("worship song lyrics", row["Secondary Keywords"])

    def test_the_tool_name_is_nowhere_in_the_new_tabs(self):
        self.run_it()
        for tab in ("Songs", "Video", "Program"):
            text = json.dumps(cs.read_all(self.fs, self.file_id, tab), ensure_ascii=False).lower()
            self.assertNotIn("pulseo", text)


class SearchesStayWithTheirPageTests(FakeSiteBase):

    def test_a_search_on_a_page_of_any_tab_is_never_an_idea(self):
        self.run_it()
        ideas = self.topic_keywords()
        for q in SONG_SEARCHES + ["episode title words", "program overview", "english song words", "english garden words"]:
            self.assertNotIn(q, ideas, q)
        # ... it belongs to its page
        every = {q: self.everything(self.find(tab, url)) for q, tab, url in (
            ("english song words", "Songs", EN_SONG), ("episode title words", "Video", VIDEO),
            ("program overview", "Program", PROGRAM))}
        for q, text in every.items():
            self.assertIn(q, text)
        self.assertIn("song one meaning", self.find("Songs", SONG)["Secondary Keywords"])

    def test_a_search_on_a_page_of_a_skipped_type_still_behaves_as_before(self):
        self.run_it()
        self.assertIn("buy widget online", self.topic_keywords())

    def test_the_rule_for_a_page_beyond_position_20_on_another_topic_stays(self):
        far = PAIRS + [("a long unrelated question", SONG, 1, 400, 35.0)]
        with mock.patch.object(tcr, "PAIRS", far):
            self.run_it()
        self.assertIn("a long unrelated question", self.topic_keywords())


class AnOlderDashboardTests(FakeSiteBase):
    """The file was made with the old tab list: its rows, your columns and its idea rows stay as they are."""

    OWNER = (content.URL, content.KEY, content.STATUS, content.TODO, "PIC", "Notes/Checklist", "Briefed Date")

    def setUp(self):
        super().setUp()
        self.old = site_with(LEGACY)
        self.new = site_with(KALAMEH_LIKE)

    def snapshot(self, tab, only_old=None):
        return {(r.get(content.URL), r.get(content.KEY)): {c: r.get(c, "") for c in self.OWNER}
                for r in self.table(tab)[1] if only_old is None or (r.get(content.URL), r.get(content.KEY)) in only_old}

    def test_new_tabs_are_added_and_nothing_you_wrote_changes(self):
        self.site = self.old
        self.run_it()
        self.assertEqual(list(self.fs.books[self.file_id])[:3], ["Articles", "Landing Pages", "Guide"])
        # the old way made idea rows from the searches that land on song, video and program pages
        old_ideas = self.topic_keywords()
        self.assertTrue({"worship song lyrics", "episode title words"} <= old_ideas)
        self.edit("Articles", A, "PIC", "Sara")
        self.edit("Articles", A, "Notes/Checklist", "call the author")
        self.edit("Landing Pages", ABOUT, "To Do", "Keep")
        before = {tab: self.snapshot(tab) for tab in ("Articles", "Landing Pages")}
        idea_rows = {k: v for k, v in before["Articles"].items() if not k[0]}
        self.assertTrue(idea_rows)

        self.site = self.new
        self.run_it(force=True, today=DAY2)
        titles = list(self.fs.books[self.file_id])
        self.assertEqual(titles[:6], ["Articles", "Landing Pages", "Songs", "Video", "Program", "Guide"])
        self.assertTrue(any("tab(s) added" in line for line in self.logs))
        for tab in ("Articles", "Landing Pages"):
            after = self.snapshot(tab, only_old=set(before[tab]))
            self.assertEqual(after, before[tab], tab)                # every old row is there, your columns the same
        # the old idea rows are never changed or deleted by this
        self.assertEqual({k: v for k, v in self.snapshot("Articles").items() if k in idea_rows}, idea_rows)
        self.assertEqual(self.find("Articles", A)["PIC"], "Sara")
        # the new tabs hold the pages
        self.assertEqual(self.tab_urls("Songs"), {SONG, EN_SONG})

    def test_a_second_refresh_changes_nothing_in_the_new_tabs_and_adds_no_tab(self):
        self.site = self.new
        self.run_it()
        before = [t.data[:] for t in self.fs.books[self.file_id].values()]
        titles = list(self.fs.books[self.file_id])
        self.run_it(force=True, today=DAY2)
        self.assertEqual(list(self.fs.books[self.file_id]), titles)
        self.assertEqual(len(self.table("Songs")[1]), 2)
        self.assertEqual(self.find("Songs", SONG)[content.KEY], "worship song lyrics")
        self.assertEqual(len(before), len(self.fs.books[self.file_id]))

    def test_a_row_you_wrote_in_a_new_tab_is_kept(self):
        self.site = self.new
        self.run_it()
        self.edit("Songs", SONG, content.KEY, "my own keyword")
        self.edit("Songs", SONG, "PIC", "Reza")
        self.run_it(force=True, today=DAY2)
        row = self.find("Songs", SONG)
        self.assertEqual((row[content.KEY], row["PIC"]), ("my own keyword", "Reza"))


class OverTheLimitTests(FakeSiteBase):

    def test_going_over_eight_tabs_is_said_in_the_log_and_in_the_owner_note(self):
        types = PAGE_TYPES + [{"name": f"Extra{i}", "regex": f"/extra{i}/"} for i in range(1, 6)]
        self.site = site_with({"skip_types": ["Shop", "Other"], "tab_names": DEFAULT["tab_names"], "tabs": EXPLICIT},
                              page_types=types)
        result = self.run_it()
        self.assertEqual(len(self.site.content.tabs), 8)
        self.assertTrue(any("NO TAB" in line for line in self.logs))
        self.assertTrue(result.tab_notes)
        _, body = content_run.owner_email(result, "Example", "link")
        self.assertIn("NO TAB", body)


class OverridesTests(unittest.TestCase):

    RAW = json.dumps([{"name": "site-x", "site_url": "sc-domain:example.test", "sheet_id": "S",
                       "page_types": [{"name": "Article", "regex": "/a/"}, {"name": "Program", "regex": "/p"}],
                       "content": {"enabled": True, "tabs": EXPLICIT}}])

    def test_the_content_block_is_merged_and_a_type_is_split_in_its_place(self):
        patch = {"content": {"skip_types": ["Other"]},
                 "split": {"Program": [{"name": "Videos", "regex": "/v/"}, {"name": "Programs", "regex": "/p/"}]}}
        out = json.loads(sites.apply_overrides(self.RAW, "SITE-X", patch))[0]
        self.assertEqual([t["name"] for t in out["page_types"]], ["Article", "Videos", "Programs"])
        self.assertEqual(out["content"]["skip_types"], ["Other"])
        self.assertEqual(out["content"]["tabs"], EXPLICIT)
        self.assertEqual(json.loads(self.RAW)[0]["page_types"][1]["name"], "Program")        # the original text is not touched

    def test_a_site_that_is_not_there_is_refused(self):
        with self.assertRaises(sites.SitesConfigError):
            sites.apply_overrides(self.RAW, "site-y", {})


class CopyTestTests(FakeSiteBase):
    """A refresh with the new settings on a copy of a dashboard made the old way: the real file is not touched."""

    def test_the_copy_gets_the_new_tabs_and_the_real_file_stays_as_it_was(self):
        import content_copy_test as cct
        self.site = site_with(LEGACY)
        self.run_it()
        real = self.file_id
        keep = {name: [r[:] for r in tab.data] for name, tab in self.fs.books[real].items()}
        new = site_with(KALAMEH_LIKE)

        def refresh(site):
            content_run.run_content(self.drive, self.fs, site, "REP", tcr.fetch, tcr.FINAL, DAY2, notify=None,
                                    log=self.logs.append, force=True, http_get=lambda url: (200, "[]"),
                                    sleep=lambda s: None)

        out = []
        lines = cct.run(self.drive, self.fs, self.site, new, real, refresh, "Example", log=out.append)
        self.assertEqual({name: tab.data for name, tab in self.fs.books[real].items()}, keep)      # the real file: read only
        text = "\n".join(lines)
        self.assertIn("lost 0, owner columns changed 0", text)
        self.assertIn("language tabs: 0", text)
        self.assertIn("tab Songs: 2 rows", text)
        self.assertIn("1 of them in another language", text)
        (copy_id,) = [i for i, f in self.drive.files_.items() if f["name"].startswith("[Copy test]")]
        self.assertNotEqual(copy_id, real)
        self.assertEqual(list(self.fs.books[copy_id])[:6], ["Articles", "Landing Pages", "Songs", "Video", "Program", "Guide"])
        self.assertEqual(self.sent, [])                                                           # nothing is emailed
        self.assertFalse(any(copy_id in line or real in line for line in out))                    # no file id in the log
        self.assertTrue(any("old idea rows" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
