"""A new site whose articles sit at the root: the tabs follow the sitemaps, the site's files go to its own Drive folder,
and a run can be for one site only. A fake site (example.test); all pages and searches are invented."""

import json
import unittest
from unittest import mock

import content
import content_run
import gsc_pulse
import provision
import site_check
import sites
import test_content_run as tcr
from test_content_run import ROOT, Base
from test_history import FakeSheets, Req, http_error
from test_own_access import FakeDrive

ART = f"{ROOT}/istanbul-weather-in-february/"
ABOUT = f"{ROOT}/about-us/"
HOME = f"{ROOT}/"
CAT = f"{ROOT}/category/travel-guide/"
AUTHOR = f"{ROOT}/author/mehdi/"
FA_POST = f"{ROOT}/%D8%B3%D9%81%D8%B1-%D8%A8%D9%87-%D8%A7%D8%B3%D8%AA%D8%A7%D9%86%D8%A8%D9%88%D9%84/"     # سفر-به-استانبول
FA_PAGE = f"{ROOT}/%D8%AE%D8%B1%DB%8C%D8%AF-%D9%85%D9%84%DA%A9/"                                       # خرید-ملک
FA_Q1, FA_Q2 = "سفر به استانبول", "خرید ملک"

FILES = {f"{ROOT}/post-sitemap.xml": [HOME, ART, FA_POST],
         f"{ROOT}/page-sitemap.xml": [HOME, ABOUT, FA_PAGE],
         f"{ROOT}/category-sitemap.xml": [CAT],
         f"{ROOT}/author-sitemap.xml": [AUTHOR]}
PAGES = {ART: (20, 900, 5.0), ABOUT: (5, 100, 3.0), CAT: (3, 200, 7.0), AUTHOR: (2, 300, 8.0), FA_POST: (6, 300, 6.0),
         FA_PAGE: (4, 200, 6.0), HOME: (100, 5000, 1.5)}
PAIRS = [("istanbul weather in february", ART, 20, 900, 5.0), ("about us", ABOUT, 5, 100, 3.0),
         ("travel guide", CAT, 3, 200, 7.0), ("mehdi author question", AUTHOR, 2, 300, 8.0),
         (FA_Q1, FA_POST, 6, 300, 6.0), (FA_Q2, FA_PAGE, 4, 200, 6.0)]

TABS = [{"name": "Articles", "sitemap": "post-sitemap", "exclude_regex": r"^https://example\.test/?$"},
        {"name": "Landing Pages", "sitemap": "page-sitemap"},
        {"name": "Categories", "sitemap": "category-sitemap"}]


def site_with(**extra):
    block = {"enabled": True, "min_gap_impressions_90d": 100, "tabs": TABS}
    block.update(extra)
    entry = {"name": "site-4", "site_url": "sc-domain:example.test", "auth": "me",
             "deck": {"enabled": True, "language": "en", "site_label": "Example", "recipients": []},
             "content": block}
    site = sites.parse_sites_json(json.dumps([entry]))[0]
    site.reports_sheet_id = "REP"
    return site


class ConfigTests(unittest.TestCase):

    def test_a_tab_may_follow_a_sitemap_and_then_needs_no_address_pattern(self):
        site = site_with()
        self.assertEqual([t.sitemap for t in site.content.tabs], ["post-sitemap", "page-sitemap", "category-sitemap"])
        self.assertEqual({t.url_regex for t in site.content.tabs}, {"."})
        self.assertTrue(site.content.has_sitemap_tabs)

    def test_bad_tabs_are_refused(self):
        for tab in ({"name": "X"}, {"name": "X", "sitemap": "("}, {"name": "X", "sitemap": "a", "bogus": 1}):
            with self.assertRaises(sites.SitesConfigError, msg=tab):
                site_with(tabs=[tab])

    def test_the_drive_folder_is_part_of_the_site_and_never_printed(self):
        entry = {"name": "site-4", "site_url": "sc-domain:example.test", "auth": "me", "drive_folder_id": "FOLDER123",
                 "deck": {"enabled": False}, "content": {"enabled": False}}
        site = sites.parse_sites_json(json.dumps([entry]))[0]
        self.assertEqual(site.drive_folder_id, "FOLDER123")
        self.assertIn("FOLDER123", site.sensitive_values())

    def test_a_third_list_adds_a_site_and_leaves_the_others_as_they_are(self):
        base = json.dumps([{"name": "site-1", "site_url": "sc-domain:one.test", "sheet_id": "S1", "auth": "robot"}])
        extra = json.dumps([{"name": "site-1", "site_url": "sc-domain:one.test", "sheet_id": "S1x", "auth": "robot"}])
        more = json.dumps([{"name": "site-4", "site_url": "sc-domain:new.test", "auth": "me",
                            "deck": {"enabled": False}, "content": {"enabled": False}}])
        loaded = sites.load_sites(base, None, None, None, sites_json_extra=extra, sites_json_more=more)
        self.assertEqual([(s.name, s.sheet_id) for s in loaded], [("site-1", "S1x"), ("site-4", "")])
        only_more = sites.load_sites(base, None, None, None, sites_json_more=more)
        self.assertEqual([(s.name, s.sheet_id) for s in only_more], [("site-1", "S1"), ("site-4", "")])
        self.assertEqual([s.name for s in sites.load_sites(base, None, None, None)], ["site-1"])


class SitemapReadingTests(unittest.TestCase):

    @staticmethod
    def urlset(*urls):
        return ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>").encode()

    def get(self, address):
        index = ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                 + "".join(f"<sitemap><loc>{f}</loc></sitemap>" for f in FILES) + "</sitemapindex>").encode()
        table = {f"{ROOT}/robots.txt": b"User-agent: *\nSitemap: http://example.test/sitemap_index.xml\n",
                 "http://example.test/sitemap_index.xml": index}
        table.update({f: self.urlset(*urls) for f, urls in FILES.items()})
        return (200, table[address]) if address in table else (404, b"")

    def test_each_page_is_listed_under_the_sitemap_file_it_came_from(self):
        files, ok = site_check.read_sitemap_files(ROOT, self.get)
        self.assertTrue(ok)
        self.assertEqual(files, {f: list(dict.fromkeys(urls)) for f, urls in FILES.items()})
        flat, ok = site_check.read_sitemaps(ROOT, self.get)
        self.assertEqual(sorted(flat), sorted({u for urls in FILES.values() for u in urls}))

    def test_without_a_readable_sitemap_nothing_is_read_and_it_says_so(self):
        self.assertEqual(site_check.read_sitemap_files(ROOT, lambda a: (404, b"")), ({}, False))
        self.assertEqual(site_check.read_sitemap_files("", self.get), ({}, False))

    def test_the_tabs_get_their_pages_from_the_files_their_pattern_matches(self):
        site = site_with()
        self.assertEqual(content_run.fill_tab_members(site.content, FILES), 3)
        names = {t.name: t for t in site.content.tabs}
        self.assertTrue(names["Articles"].matches(ART))
        self.assertFalse(names["Articles"].matches(HOME))                 # the home page is also in the post sitemap
        self.assertTrue(names["Landing Pages"].matches(HOME))
        self.assertTrue(names["Landing Pages"].matches(FA_PAGE))
        self.assertTrue(names["Categories"].matches(CAT))
        self.assertIsNone(site.content.tab_of(AUTHOR))                     # authors are in no tab
        self.assertIsNone(site.content.tab_of(f"{ROOT}/not-in-any-sitemap/"))
        self.assertEqual(site.content.tab_of(f"{ROOT}/istanbul-weather-in-february").name, "Articles")   # a slash does not matter


class FakeSiteBase(Base):

    def setUp(self):
        super().setUp()
        for target, value in (("PAGES", PAGES), ("PAIRS", PAIRS)):
            patcher = mock.patch.object(tcr, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.site = site_with()
        content_run.fill_tab_members(self.site.content, FILES)

    def urls(self, tab):
        return {r[content.URL] for r in self.table(tab)[1] if r.get(content.URL)}

    def sitemap(self):
        return lambda: ([u for urls in FILES.values() for u in urls], True)


class TabsFollowSitemapsTests(FakeSiteBase):

    def test_root_level_articles_and_pages_land_in_their_own_tabs_and_no_tab_is_made_for_a_language(self):
        self.run_it(sitemap=self.sitemap())
        titles = list(self.fs.books[self.file_id])
        self.assertEqual(titles[:4], ["Articles", "Landing Pages", "Categories", "Guide"])
        self.assertFalse([t for t in titles if t.lower() in ("persian", "english", "fa", "en")])
        shown = lambda u: content.display_url(u)                                # noqa: E731
        self.assertEqual(self.urls("Articles"), {shown(ART), shown(FA_POST)})
        self.assertEqual(self.urls("Landing Pages"), {shown(HOME), shown(ABOUT), shown(FA_PAGE)})
        self.assertEqual(self.urls("Categories"), {shown(CAT)})
        every = self.urls("Articles") | self.urls("Landing Pages") | self.urls("Categories")
        self.assertNotIn(shown(AUTHOR), every)

    def test_a_search_on_a_page_of_any_tab_is_not_an_idea_but_one_on_a_page_in_no_tab_is(self):
        self.run_it(sitemap=self.sitemap())
        ideas = {r[content.KEY] for tab in ("Articles", "Landing Pages", "Categories")
                 for r in self.table(tab)[1] if not r.get(content.URL) and r.get(content.KEY)}
        for query in ("istanbul weather in february", "about us", "travel guide", FA_Q1, FA_Q2):
            self.assertNotIn(query, ideas, query)
        self.assertIn("mehdi author question", ideas)

    def test_both_languages_live_in_the_same_tab(self):
        self.run_it(sitemap=self.sitemap())
        keywords = {r[content.KEY] for r in self.table("Articles")[1] if r.get(content.URL)}
        self.assertEqual(keywords, {"istanbul weather in february", FA_Q1})

    def test_a_second_run_changes_no_row_and_a_page_that_left_the_sitemap_keeps_its_row(self):
        self.run_it(sitemap=self.sitemap())
        before = {tab: sorted(self.urls(tab)) for tab in ("Articles", "Landing Pages", "Categories")}
        smaller = {f: [u for u in urls if u != FA_POST] for f, urls in FILES.items()}
        for tab in self.site.content.tabs:
            tab.members = set()
        content_run.fill_tab_members(self.site.content, smaller)
        self.run_it(force=True, today=tcr.TODAY.replace(day=28), sitemap=lambda: ([u for v in smaller.values() for u in v], True))
        self.assertEqual({tab: sorted(self.urls(tab)) for tab in before}, before)
        urls = [r[content.URL] for r in self.table("Articles")[1] if r.get(content.URL)]
        self.assertEqual(len(urls), len(set(urls)))


class LoadMembersTests(unittest.TestCase):

    def setUp(self):
        gsc_pulse._MEMBERS_LOADED.clear()
        self.addCleanup(gsc_pulse._MEMBERS_LOADED.clear)

    def test_an_unreadable_sitemap_leaves_the_content_step_alone_for_the_run(self):
        site = site_with()
        with mock.patch.object(site_check, "read_sitemap_files", return_value=({}, False)):
            self.assertFalse(gsc_pulse.load_tab_members(site, ROOT))
        self.assertEqual({t.members for t in site.content.tabs if t.members}, set())

    def test_a_readable_sitemap_is_read_once_per_run_and_fills_the_tabs(self):
        site = site_with()
        with mock.patch.object(site_check, "read_sitemap_files", return_value=(FILES, True)) as m:
            self.assertTrue(gsc_pulse.load_tab_members(site, ROOT))
            self.assertTrue(gsc_pulse.load_tab_members(site, ROOT))
        self.assertEqual(m.call_count, 1)
        self.assertTrue(site.content.tab_of(ART))

    def test_a_site_with_address_tabs_needs_no_sitemap(self):
        site = tcr.make_site()
        with mock.patch.object(site_check, "read_sitemap_files", side_effect=AssertionError("read")):
            self.assertTrue(gsc_pulse.load_tab_members(site, ROOT))


class OneSiteOnlyTests(unittest.TestCase):

    def runs(self):
        return [gsc_pulse.SiteRun(site_with()), gsc_pulse.SiteRun(sites.Site(name="site-1", enabled=True))]

    def test_every_other_site_is_switched_off(self):
        runs = self.runs()
        gsc_pulse.select_only(runs, " Site-4 ")
        self.assertEqual([r.status for r in runs], ["pending", "disabled"])

    def test_without_a_name_nothing_changes_and_an_unknown_name_is_refused(self):
        runs = self.runs()
        gsc_pulse.select_only(runs, "")
        self.assertEqual([r.status for r in runs], ["pending", "pending"])
        with self.assertRaises(gsc_pulse.GscPulseError):
            gsc_pulse.select_only(runs, "site-9")


class FoldersTests(unittest.TestCase):

    def setUp(self):
        self.fs = FakeSheets()
        self.drive = FakeDrive(self.fs)
        self.logs = []
        self.site = site_with()
        self.project = provision.ensure_folder(self.drive, "Project folder")

    def parents(self, folder_id):
        return self.drive.files_[folder_id]["parents"]

    def test_the_files_go_to_the_project_folder_when_the_tool_can_open_it(self):
        self.site.drive_folder_id = self.project
        self.assertEqual(provision.site_folder_for(self.drive, self.site, "Example", self.logs.append), (self.project, True))
        self.assertEqual(self.logs, [])

    def test_a_folder_the_tool_cannot_open_falls_back_to_its_own_and_the_log_names_no_id(self):
        self.site.drive_folder_id = "SOMEONE-ELSES-FOLDER"
        folder, in_project = provision.site_folder_for(self.drive, self.site, "Example", self.logs.append)
        self.assertFalse(in_project)
        self.assertNotEqual(folder, "SOMEONE-ELSES-FOLDER")
        self.assertTrue(self.logs)
        self.assertNotIn("SOMEONE-ELSES-FOLDER", " ".join(self.logs))

    def test_content_seo_and_briefs_are_made_once_next_to_the_other_files_and_content_creation_stays_off(self):
        self.site.drive_folder_id = self.project
        made = provision.content_folders(self.drive, self.site, "Example", self.logs.append)
        self.assertEqual(self.parents(made["content_seo"]), [self.project])
        self.assertEqual(self.parents(made["briefs"]), [made["content_seo"]])
        self.assertTrue(made["in_project"])
        again = provision.content_folders(self.drive, self.site, "Example", self.logs.append)
        self.assertEqual((again["content_seo"], again["briefs"]), (made["content_seo"], made["briefs"]))
        self.assertEqual(sorted(self.drive.names(provision.FOLDER)).count("Briefs"), 1)
        self.assertFalse(self.site.content.creation_enabled)                       # the Briefs id is not put into the config
        self.assertNotIn(made["briefs"], " ".join(self.logs))

    def test_the_sheets_and_the_content_dashboard_are_made_in_the_project_folder(self):
        self.site.drive_folder_id = self.project
        self.site.content.sheet_id = ""
        self.site.reports_sheet_id = ""                                            # (the helper site has a fake Reports id)
        made = provision.provision_site(self.drive, self.fs, self.site, None, self.logs.append)
        self.assertEqual(made.created, 3)
        for name in ("Example · PulseO Data", "Example · PulseO History", "Example · PulseO Reports"):
            fid = self.drive.id_of(name)
            self.assertEqual(self.parents(fid), [self.project], name)
        dash, created = provision.content_file(self.drive, self.fs, self.site, "Example")
        self.assertTrue(created)
        self.assertEqual(self.parents(dash), [self.project])


class AccessCheckTests(unittest.TestCase):
    """The read-only check of what the tool can open: results by label, never an id or a name, nothing written."""

    class Drive:
        def __init__(self, table):
            self.table, self.calls = table, []

        def files(self):
            return self

        def spreadsheets(self):
            return self

        def get(self, fileId=None, fields=None, spreadsheetId=None):
            fid = fileId or spreadsheetId
            self.calls.append(fid)

            def run():
                if fid not in self.table:
                    raise http_error(404, "not found: " + fid)
                return {"id": fid, "mimeType": self.table[fid]}
            return Req(run)

    FOLDER, XLSX, SHEET = ("application/vnd.google-apps.folder",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           "application/vnd.google-apps.spreadsheet")

    def test_what_each_client_can_open_is_said_by_label(self):
        me = self.Drive({"MINE": self.FOLDER})
        sa_drive = self.Drive({"OLD": self.XLSX, "COPY": self.SHEET})
        sa_sheets = self.Drive({"COPY": self.SHEET})
        lines = gsc_pulse.access_report(
            [{"label": "folder", "id": "OTHERS"}, {"label": "mine", "id": "MINE"}, {"label": "old file", "id": "OLD"},
             {"label": "converted", "id": "COPY"}], me, sa_drive, sa_sheets)
        self.assertEqual(lines[0], "folder: the owner's client refused (404); service account refused (404); as a spreadsheet refused (404)")
        self.assertIn("the owner's client ok (folder)", lines[1])
        self.assertEqual(lines[2], "old file: the owner's client refused (404); service account ok (xlsx file); as a spreadsheet refused (404)")
        self.assertEqual(lines[3], "converted: the owner's client refused (404); service account ok (Google Sheet); as a spreadsheet ok (Google Sheet)")
        self.assertNotIn("OLD", " ".join(lines) + "COPY".join(lines))
        self.assertNotIn("not found", " ".join(lines))

    def test_a_client_that_could_not_be_made_is_reported_not_guessed(self):
        lines = gsc_pulse.access_report([{"label": "x", "id": "A"}], None, None, None)
        self.assertEqual(lines, ["x: the owner's client not tried; service account not tried; as a spreadsheet not tried"])


if __name__ == "__main__":
    unittest.main()
