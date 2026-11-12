"""The daily technical health checks (phase 1): the checks, the second check, the alerts, the Technical sheet.
The site, its pages, Search Console, Sheets, Drive and email are all in-memory fakes; the one real request test talks to a
tiny server on this computer. Every address is invented."""

import http.server
import json
import os
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import provision
import sites
import technical
import technical_run
from technical import Fetched
from test_history import FakeSheets, patch_time
from test_own_access import FakeDrive

HOME = "https://example.test"
ROOT = f"{HOME}/sitemap.xml"
DAY0 = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
ROBOTS_OK = f"User-agent: *\nDisallow: /private\nSitemap: {ROOT}\n"


def p(i: int) -> str:
    return f"{HOME}/a/p{i}"


def page_html(canonical="self", noindex=False, url="") -> str:
    head = "<title>A page</title>"
    if canonical:
        head += f'<link rel="canonical" href="{url if canonical == "self" else canonical}">'
    if noindex:
        head += '<meta name="robots" content="noindex, follow">'
    return f"<html><head>{head}</head><body>text</body></html>"


def urlset(n: int) -> bytes:
    urls = "".join(f"<url><loc>{HOME}/s/{i}</loc></url>" for i in range(n))
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>").encode()


class Web:
    """The site: pages for fetch(), files (robots.txt, sitemaps) for get(). A page can fail a number of times first."""

    def __init__(self):
        self.pages, self.files, self.flaky, self.calls = {}, {}, {}, []
        self.pages[HOME + "/"] = Fetched(200, {}, page_html(url=HOME + "/"), 0.2)
        for i in range(1, 26):
            self.pages[p(i)] = Fetched(200, {}, page_html(url=p(i)), 0.2)
        self.files[HOME + "/robots.txt"] = (200, ROBOTS_OK.encode())
        self.files[ROOT] = (200, urlset(30))

    def set_page(self, i, **kw):
        self.pages[p(i)] = Fetched(kw.pop("status", 200), kw.pop("headers", {}), page_html(url=p(i), **kw), 0.2)

    def fetch(self, url, follow=False):
        self.calls.append(url)
        if self.flaky.get(url):
            return Fetched(self.flaky[url].pop(0), {}, "", 0.1)
        return self.pages.get(url, Fetched(404, {}, "", 0.1))

    def get(self, url):
        return self.files.get(url, (404, b""))


def gsc_rows():
    return [{"keys": [p(i)], "clicks": 200 - i, "impressions": 1000} for i in range(1, 26)]


def make_sites(*specs) -> list:
    """specs: (name, label, technical config or None)"""
    entries = []
    for name, label, technical_cfg in specs:
        entry = {"name": name, "site_url": "sc-domain:example.test", "auth": "me",
                 "deck": {"enabled": True, "language": "en", "site_label": label, "recipients": []}}
        if technical_cfg is not None:
            entry["technical"] = technical_cfg
        entries.append(entry)
    return sites.parse_sites_json(json.dumps(entries))


class Base(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.drive = FakeDrive(self.fs)
        self.fs.add_book("REP", {"Dashboard": (5, 5)})
        self.web = Web()
        self.now = DAY0
        self.mails, self.logs, self.slept, self.errors = [], [], [], []
        self.api = []
        self.rows = gsc_rows()
        self.site = make_sites(("site-x", "Example", None))[0]
        self.site.reports_sheet_id = "REP"
        self.runs = [self.make_run(self.site)]

    def make_run(self, site):
        return SimpleNamespace(site=site, name=site.name, drive=self.drive, sheets=self.fs, gsc=None, status="pending")

    def deps(self, mail=True, mail_error=None):
        def send(subject, body):
            if mail_error:
                raise mail_error
            self.mails.append((subject, body))
        return technical_run.Deps(
            fetch=self.web.fetch, get=self.web.get, sleep=self.slept.append, now=lambda: self.now,
            final=lambda run: date(2026, 11, 10), page_rows=lambda run, last: self.rows,
            sitemaps_api=lambda run: self.api if not isinstance(self.api, Exception) else (_ for _ in ()).throw(self.api),
            mail=send if mail else None)

    def go(self, mail=True, mail_error=None, runs=None, advance=True, deps=None):
        """One daily run (the next day, unless told otherwise). Returns the lines the run prints for its summary."""
        self.mails.clear()
        lines = technical_run.run_technical(runs or self.runs, deps or self.deps(mail, mail_error), self.logs.append,
                                            on_error=lambda r, e: self.errors.append((r.name, repr(e))))
        self.assertEqual(self.errors, [])
        if advance:
            self.now += timedelta(days=1)
        return lines

    def file_id(self, label="Example"):
        return self.drive.id_of(f"[{label}] Technical")

    def tab(self, name, label="Example"):
        return self.fs.books[self.file_id(label)][name].data

    def log_results(self, label="Example"):
        return [(r[2], r[3]) for r in self.tab("Health Log", label)[1:]]

    def state(self, label="Example"):
        return technical.TechState(self.tab("_state", label))


class HealthyDay(Base):

    def test_everything_fine_nothing_to_tell(self):
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(self.slept, [])                                      # nothing failed: no second check, no wait
        rows = self.tab("Health Log")[1:]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(r[3] == "ok" for r in rows))
        overview = self.tab("Overview")
        self.assertEqual(overview[0], technical_run.OVERVIEW_HEADER)
        self.assertEqual(overview[1][:2], ["Site health", "OK"])
        self.assertEqual(self.state().open(), {})
        self.assertEqual(len(self.tab("Robots Versions")), 2)                 # the header and the first version
        self.assertNotIn("example.test", " ".join(self.logs))                 # the log says counts only

    def test_the_tabs_and_the_index(self):
        self.go()
        book = self.fs.books[self.file_id()]
        self.assertEqual(list(book), ["Overview", "Health Log", "Robots Versions", "_state"])
        index_id = self.drive.id_of(provision.INDEX_NAME)
        header = self.fs.books[index_id][provision.INDEX_TAB].data[0]
        self.assertEqual(header[7], "technical_id")
        self.assertEqual(self.fs.books[index_id][provision.INDEX_TAB].data[1][7], self.file_id())

    def test_the_sheet_is_found_again_not_made_twice(self):
        self.go()
        first = self.file_id()
        self.go()
        self.assertEqual(self.drive.names(provision.SHEET).count("[Example] Technical"), 1)
        self.assertEqual(self.file_id(), first)
        again, created = provision.technical_file(self.drive, self.fs, self.site, "Example")
        self.assertEqual((again, created), (first, False))

    def test_the_reports_sheet_is_not_touched(self):
        before = [list(r) for r in self.fs.books["REP"]["Dashboard"].data]
        self.go()
        self.assertEqual(self.fs.books["REP"]["Dashboard"].data, before)
        self.assertFalse([1 for book, _ in self.fs.requests if book == "REP"])

    def test_key_pages_are_the_top_twenty_by_clicks_and_the_configured_ones(self):
        site = make_sites(("site-x", "Example", {"key_pages_extra": ["/extra", f"{HOME}/a/p3"]}))[0]
        urls, clicks = technical_run.key_page_set(site, HOME, self.rows)
        self.assertEqual(urls[:20], [p(i) for i in range(1, 21)])
        self.assertEqual(urls[20:], [f"{HOME}/extra"])                         # p3 is already there
        self.assertEqual(clicks[HOME + "/a/p1"], 199)

    def test_a_site_without_clicks_still_gets_its_checks(self):
        self.rows = []
        self.go()
        self.assertEqual(self.mails, [])
        self.assertTrue(any(name == "Key pages answer" and res == "skipped" for name, res in self.log_results()))


class KeyPageRedirects(Base):

    def test_a_slash_redirect_leads_to_the_same_page_which_is_the_one_checked(self):
        self.web.pages[p(1)] = Fetched(301, {"location": p(1) + "/"}, "", 0.1)
        self.web.pages[p(1) + "/"] = Fetched(200, {}, page_html(url=p(1) + "/"), 0.2)
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(self.slept, [])

    def test_the_page_a_slash_redirect_leads_to_is_checked_too(self):
        self.web.pages[p(1)] = Fetched(301, {"location": p(1) + "/"}, "", 0.1)
        self.web.pages[p(1) + "/"] = Fetched(200, {}, page_html(noindex=True, url=p(1) + "/"), 0.2)
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages set to noindex")

    def test_https_and_www_moves_are_the_same_page(self):
        moved = Fetched(301, {}, "", 0.1)
        moved.location = "https://www.example.test/a/p2"
        self.web.pages[p(2)] = moved
        self.web.pages["https://www.example.test/a/p2"] = Fetched(200, {}, page_html(url=p(2)), 0.2)
        self.go()
        self.assertEqual(self.mails, [])

    def test_a_redirect_to_another_page_is_still_a_problem(self):
        self.web.pages[p(1)] = Fetched(301, {"location": f"{HOME}/a/elsewhere"}, "", 0.1)
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages not answering 200")
        self.assertIn("301 x1", self.mails[0][1])

    def test_a_redirect_that_goes_round_in_circles_is_a_problem(self):
        self.web.pages[p(1)] = Fetched(301, {"location": p(1)}, "", 0.1)
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages not answering 200")

    def test_addresses_with_a_query_string_are_not_key_pages(self):
        rows = [{"keys": [f"{HOME}/a/p1?x=1"], "clicks": 900}, {"keys": [p(1)], "clicks": 5}, {"keys": [p(2)], "clicks": 4}]
        urls, clicks = technical_run.key_page_set(self.site, HOME, rows)
        self.assertEqual(urls, [p(1), p(2)])
        self.assertEqual(clicks[HOME + "/a/p1"], 905)                  # its clicks still count for the page


class PlantedProblems(Base):

    def plant(self):
        self.web.files[HOME + "/robots.txt"] = (200, f"User-agent: *\nDisallow: /\nSitemap: {ROOT}\n".encode())
        self.web.files[ROOT] = (404, b"")
        self.web.set_page(1, noindex=True)
        self.web.set_page(2, status=500)
        self.web.set_page(3, canonical=f"{HOME}/a/somewhere-else")

    def test_every_problem_is_found_scored_and_placed(self):
        self.plant()
        self.go()
        self.assertEqual(self.slept, [1800])                                   # one wait for the second check
        confirmed = {name for name, res in self.log_results() if res == "confirmed problem"}
        self.assertEqual(confirmed, {"robots.txt blocking", "Sitemap", "Key pages answer", "Key pages noindex",
                                     "Key pages canonical"})
        first = {name for name, res in self.log_results() if res == "failed (first check)"}
        self.assertEqual(first, confirmed)
        overview = self.tab("Overview")
        self.assertEqual(overview[1][:2], ["Site health", "5 open problems"])
        self.assertEqual(sum(1 for r in overview if len(r) > 1 and r[1] == "PROBLEM"), 5)
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · 5 problems")
        self.assertIn("Clicks on those pages in the last 28 days: 199", body)     # the noindex page had 199 clicks
        self.assertIn("/a/p1", body)
        self.assertIn("Disallow: /", body)
        self.assertIn("First thing to check:", body)
        self.assertNotIn("PulseO", body)
        opened = self.state().open()
        self.assertEqual(len(opened), 5)
        self.assertTrue(all(rec["alerted"] == "2026-11-12" for rec in opened.values()))

    def test_one_failed_request_alerts_nobody(self):
        self.web.flaky[p(5)] = [503]
        self.go()
        self.assertEqual(self.mails, [])
        results = self.log_results()
        self.assertIn(("Key pages answer", "failed (first check)"), results)
        self.assertIn(("Key pages answer", "not confirmed"), results)
        self.assertNotIn("confirmed problem", [res for _, res in results])
        self.assertEqual(self.state().open(), {})
        self.assertEqual(self.slept, [1800])

    def test_a_problem_is_told_once_and_the_fix_once(self):
        self.web.set_page(1, noindex=True)
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages set to noindex")
        self.go()                                                              # the next day: the same problem
        self.assertEqual(self.mails, [])
        self.go()
        self.assertEqual(self.mails, [])
        self.web.set_page(1)                                                   # fixed
        self.go()
        self.assertEqual(self.mails[0][0], "Fixed · Example · Key pages set to noindex")
        self.assertIn("Fixed", self.mails[0][1])
        self.go()
        self.assertEqual(self.mails, [])

    def test_a_problem_that_gets_worse_is_told_again(self):
        self.web.set_page(1, noindex=True)
        self.go()
        self.web.set_page(2, noindex=True)
        self.web.set_page(3, noindex=True)
        self.go()
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · Example · Key pages set to noindex")
        self.assertIn("got worse", body)
        self.go()
        self.assertEqual(self.mails, [])                                       # and not again

    def test_a_problem_that_got_better_but_is_not_gone_says_nothing(self):
        for i in (1, 2, 3):
            self.web.set_page(i, noindex=True)
        self.go()
        self.web.set_page(1)
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(self.state().open()["key_noindex"]["count"], 2)

    def test_a_large_change_is_one_alert(self):
        self.go()                                                              # a healthy day: the statuses are remembered
        for i in range(1, 8):                                                  # 7 of 20 key pages (35%) change at once
            self.web.pages[p(i)] = Fetched(301, {"location": f"{HOME}/new/{i}"}, "", 0.1)
        self.go()
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · Example · Large change detected")
        self.assertIn("7 of 20 key pages changed", body)
        self.assertNotIn("Key pages not answering 200", body)                  # not one by one
        self.go()                                                              # the same the next day: nothing new
        self.assertEqual(self.mails, [])

    def test_a_small_change_is_not_a_large_one(self):
        self.go()
        for i in range(1, 4):                                                  # 3 of 20 (15%)
            self.web.pages[p(i)] = Fetched(404, {}, "", 0.1)
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages not answering 200")

    def test_many_server_errors_are_a_problem_of_their_own(self):
        for i in range(1, 7):                                                  # 6 of 20 (30%) fail with a server error
            self.web.pages[p(i)] = Fetched(503, {}, "", 0.1)
        self.go()
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · 2 problems")
        self.assertIn("Server errors on key pages", body)

    def test_no_answer_counts_as_a_server_error(self):
        for i in range(1, 7):
            self.web.pages[p(i)] = Fetched(0, {}, "", 20)
        self.go()
        self.assertIn("Server errors on key pages", self.mails[0][1])
        self.assertIn("no answer", self.mails[0][1])

    def test_a_slow_home_page_is_a_problem(self):
        self.web.pages[HOME + "/"] = Fetched(200, {}, page_html(), 14.0)
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Home page not answering")
        self.assertIn("took 14.0s", self.mails[0][1])

    def test_a_home_page_that_is_down(self):
        self.web.pages[HOME + "/"] = Fetched(502, {}, "", 0.1)
        self.go()
        self.assertIn("answered 502", self.mails[0][1])

    def test_a_noindex_in_the_header_counts(self):
        self.web.set_page(4, headers={"x-robots-tag": "noindex"})
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages set to noindex")

    def test_a_missing_robots_file_is_a_problem_but_blocks_nothing_else(self):
        self.web.files[HOME + "/robots.txt"] = (404, b"")
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · robots.txt not available")
        self.assertIn("no rules", self.mails[0][1])
        self.assertIn(("robots.txt blocking", "skipped"), self.log_results())


class Sitemaps(Base):

    def test_a_sitemap_that_loses_pages_is_a_problem_until_it_is_back(self):
        self.web.files[ROOT] = (200, urlset(100))
        self.go()
        self.assertEqual(self.mails, [])
        self.web.files[ROOT] = (200, urlset(60))
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Sitemap lost many pages")
        self.assertIn("60 addresses, it had 100", self.mails[0][1])
        self.assertEqual(json.loads(self.state().get("sitemaps", "counts"))[ROOT], 100)    # measured against the last good size
        self.web.files[ROOT] = (200, urlset(100))
        self.go()
        self.assertEqual(self.mails[0][0], "Fixed · Example · Sitemap lost many pages")

    def test_a_small_loss_is_not_one(self):
        self.web.files[ROOT] = (200, urlset(100))
        self.go()
        self.web.files[ROOT] = (200, urlset(90))
        self.go()
        self.assertEqual(self.mails, [])

    def test_a_sitemap_that_is_not_xml(self):
        self.web.files[ROOT] = (200, b"<html>not a sitemap</html>")
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Sitemap not readable")
        self.assertIn("not a valid sitemap", self.mails[0][1])

    def test_a_sitemap_index_counts_its_children_and_a_broken_child_is_named(self):
        child_a, child_b = f"{HOME}/s-a.xml", f"{HOME}/s-b.xml"
        index = (f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>{child_a}</loc></sitemap>'
                 f'<sitemap><loc>{child_b}</loc></sitemap></sitemapindex>').encode()
        self.web.files[ROOT] = (200, index)
        self.web.files[child_a] = (200, urlset(10))
        self.web.files[child_b] = (500, b"")
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Sitemap not readable")
        self.assertIn("/s-b.xml", self.mails[0][1])
        self.assertNotIn(ROOT, json.loads(self.state().get("sitemaps", "counts")))       # no size from a partial read

    def test_the_configured_extra_sitemap_is_checked(self):
        site = make_sites(("site-x", "Example", None))[0]
        site.reports_sheet_id = "REP"
        site.content.extra_sitemaps = [f"{HOME}/fa/sitemap.xml"]
        self.runs = [self.make_run(site)]
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Sitemap not readable")

    def test_an_error_in_the_search_console_list_is_a_problem(self):
        self.api = [{"path": ROOT, "errors": "3", "warnings": "0"}, {"path": f"{HOME}/other.xml", "errors": "0"}]
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Sitemap error in Search Console")
        self.assertIn("3 in all", self.mails[0][1])

    def test_a_search_console_list_that_cannot_be_read_concludes_nothing(self):
        self.api = RuntimeError("boom")
        self.go()
        self.assertEqual(self.mails, [])
        self.assertIn(("Sitemaps in Search Console", "skipped"), self.log_results())


class Robots(Base):

    def test_every_version_is_kept_and_a_change_is_a_finding_not_an_alert(self):
        self.go()
        self.web.files[HOME + "/robots.txt"] = (200, (ROBOTS_OK + "Disallow: /new-area\n").encode())
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(len(self.tab("Robots Versions")), 3)
        self.assertIn(("robots.txt changes", "changed"), self.log_results())
        detail = [r[4] for r in self.tab("Health Log") if r[2] == "robots.txt changes"][0]
        self.assertIn("1 lines added", detail)
        self.assertIn("Disallow: /new-area", detail)
        self.go()
        self.assertEqual(len(self.tab("Robots Versions")), 3)                  # no change: no new version

    def test_a_new_block_of_everything_is_a_problem(self):
        self.go()
        self.web.files[HOME + "/robots.txt"] = (200, f"User-agent: *\nDisallow: /\nSitemap: {ROOT}\n".encode())
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · robots.txt blocks the whole site")


class Mail(Base):

    def test_a_run_started_by_hand_tells_nothing_and_the_next_scheduled_run_tells_it(self):
        self.web.set_page(1, noindex=True)
        self.go(mail=False)
        self.assertEqual(self.mails, [])
        self.assertIn("stays untold", " ".join(self.logs))
        self.assertEqual(self.state().open()["key_noindex"]["alerted"], "")
        self.go()
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · Example · Key pages set to noindex")
        self.assertIn("Since: 12 Nov 2026", body)                               # since the first look, not since the email
        self.go()
        self.assertEqual(self.mails, [])

    def test_a_problem_that_was_never_told_and_is_fixed_stays_quiet(self):
        self.web.set_page(1, noindex=True)
        self.go(mail=False)
        self.web.set_page(1)
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(self.state().open(), {})

    def test_an_email_that_fails_is_tried_again_next_time(self):
        self.web.set_page(1, noindex=True)
        self.go(mail_error=OSError("down"))
        self.assertIn("could not be sent", " ".join(self.logs))
        self.assertEqual(self.state().open()["key_noindex"]["alerted"], "")
        self.go()
        self.assertEqual(len(self.mails), 1)

    def test_one_email_covers_all_sites(self):
        a, b = make_sites(("site-a", "Alpha", None), ("site-b", "Beta", None))
        a.reports_sheet_id = b.reports_sheet_id = "REP"
        self.runs = [self.make_run(a), self.make_run(b)]
        self.web.set_page(1, noindex=True)
        self.go()
        self.assertEqual(len(self.mails), 1)
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · 2 problems")
        self.assertIn("Alpha · Key pages set to noindex", body)
        self.assertIn("Beta · Key pages set to noindex", body)
        self.assertEqual(self.slept, [1800])                                   # one wait for every site

    def test_a_site_that_fails_does_not_stop_the_others(self):
        a, b = make_sites(("site-a", "Alpha", None), ("site-b", "Beta", None))
        a.reports_sheet_id = b.reports_sheet_id = "REP"
        self.runs = [self.make_run(a), self.make_run(b)]
        deps = self.deps()
        real = deps.final
        deps.final = lambda run: (_ for _ in ()).throw(RuntimeError("boom")) if run.name == "site-a" else real(run)
        self.mails.clear()
        lines = technical_run.run_technical(self.runs, deps, self.logs.append,
                                            on_error=lambda r, e: self.errors.append(r.name))
        self.assertEqual(self.errors, ["site-a"])
        self.assertTrue(any(line.startswith("site-a: failed") for line in lines))
        self.assertTrue(self.tab("Health Log", "Beta")[1:])

    def test_a_site_with_the_module_off_is_left_alone(self):
        off = make_sites(("site-x", "Example", {"enabled": False}))[0]
        self.runs = [self.make_run(off)]
        self.go()
        self.assertEqual(self.drive.names(provision.SHEET), [])
        health_off = make_sites(("site-x", "Example", {"checks": {"health": False}}))[0]
        self.runs = [self.make_run(health_off)]
        self.go()
        self.assertEqual(self.drive.names(provision.SHEET), [])


class Parsing(unittest.TestCase):

    def test_robots_blocking(self):
        blocks = technical.robots_blocks_all
        self.assertTrue(blocks("User-agent: *\nDisallow: /\n"))
        self.assertTrue(blocks("User-agent: googlebot\nDisallow: /\n"))
        self.assertTrue(blocks("User-agent: *\nUser-agent: Googlebot\nDisallow: /\n"))
        self.assertTrue(blocks("USER-AGENT: *\r\nDISALLOW: /\r\n"))
        self.assertFalse(blocks("User-agent: *\nDisallow: /\nAllow: /\n"))
        self.assertFalse(blocks("User-agent: *\nDisallow: /private\n"))
        self.assertFalse(blocks("User-agent: *\n# Disallow: /\n"))
        self.assertFalse(blocks("User-agent: bingbot\nDisallow: /\n"))
        self.assertFalse(blocks("User-agent: googlebot\nAllow: /\n\nUser-agent: *\nDisallow: /\n"))     # Googlebot's own rules win
        self.assertFalse(blocks(""))

    def test_robots_sitemaps_and_diff(self):
        self.assertEqual(technical.robots_sitemaps("Sitemap: https://x.test/a.xml\nsitemap: https://x.test/a.xml\nSitemap: https://x.test/b.xml"),
                         ["https://x.test/a.xml", "https://x.test/b.xml"])
        added, removed = technical.robots_diff("User-agent: *\nDisallow: /a\n# note\n", "User-agent: *\nDisallow: /b\n")
        self.assertEqual((added, removed), (["Disallow: /b"], ["Disallow: /a"]))

    def test_the_header_noindex(self):
        yes = technical.header_noindex
        self.assertTrue(yes("noindex"))
        self.assertTrue(yes("noindex, nofollow"))
        self.assertTrue(yes("googlebot: noindex"))
        self.assertTrue(yes("none"))
        self.assertTrue(yes("bingbot: nofollow\nnoindex"))
        self.assertFalse(yes("bingbot: noindex"))
        self.assertFalse(yes("unavailable_after: 25 Jun 2030 15:00:00 PST"))
        self.assertFalse(yes("nofollow"))
        self.assertFalse(yes(""))

    def test_the_page_facts(self):
        def facts(body, headers=None, url=f"{HOME}/a/p1"):
            return technical.page_facts(Fetched(200, headers or {}, body, 0.1), url)

        self.assertTrue(facts("<meta name='robots' content='NOINDEX,nofollow'>").noindex)
        self.assertTrue(facts('<meta content="noindex" name="robots">').noindex)
        self.assertTrue(facts('<meta name="googlebot" content="noindex">').noindex)
        self.assertFalse(facts('<meta name="bingbot" content="noindex">').noindex)
        self.assertFalse(facts('<meta name="description" content="how to noindex a page">').noindex)
        self.assertFalse(facts('<meta name="robots" content="index, follow">').noindex)
        self.assertTrue(facts("<p>x</p>", {"x-robots-tag": "noindex"}).noindex)
        self.assertEqual(facts('<link rel="canonical" href="/a/other">').canonical, f"{HOME}/a/other")
        self.assertEqual(facts('<link href="https://o.test/x" rel="canonical">').canonical, "https://o.test/x")
        self.assertEqual(facts('<link rel="alternate" href="https://o.test/x">').canonical, "")

    def test_large_change(self):
        prev = {f"u{i}": 200 for i in range(10)}
        now = dict(prev, u0=301, u1=301, u2=301)
        self.assertIsNone(technical.large_change(prev, now, 0.3))              # 30% exactly is not more than 30%
        now["u3"] = 404
        self.assertEqual(technical.large_change(prev, now, 0.3)["changed"], 4)
        self.assertIsNone(technical.large_change({"a": 200, "b": 200}, {"a": 404, "b": 404}, 0.3))      # too few pages to tell
        self.assertIsNone(technical.large_change({}, now, 0.3))

    def test_the_subjects(self):
        alert = lambda title: technical.Alert("key_noindex", title, "d", "2026-11-12", [], 0)
        one = technical.Decision(alerts=[alert("A problem")])
        two = technical.Decision(alerts=[alert("A problem"), alert("Another")])
        fixed = technical.Decision(fixed=[("A problem", "2026-11-01")])
        self.assertIsNone(technical.build_email([("S", technical.Decision())]))
        self.assertEqual(technical.build_email([("S", one)])[0], "Site alert · S · A problem")
        self.assertEqual(technical.build_email([("S", two)])[0], "Site alert · 2 problems")
        self.assertEqual(technical.build_email([("S", one), ("T", one)])[0], "Site alert · 2 problems")
        self.assertEqual(technical.build_email([("S", fixed)])[0], "Fixed · S · A problem")
        self.assertEqual(technical.build_email([("S", fixed), ("T", fixed)])[0], "Fixed · 2 problems")


class Config(unittest.TestCase):

    def parse(self, value):
        return make_sites(("site-x", "Example", value))[0].technical

    def test_defaults_are_the_spec(self):
        t = self.parse(None)
        self.assertEqual((t.enabled, t.inspection_daily_max, t.sitemap_drop_alert, t.confirm_minutes),
                         (True, 1500, 0.2, 30))
        self.assertEqual(t.checks, {"health": True, "inspection": True, "cwv": True, "crawl": True, "exports": True})

    def test_values(self):
        t = self.parse({"key_pages_extra": ["/a"], "money_pages": ["/m"], "confirm_minutes": 5,
                        "checks": {"cwv": False}, "sitemap_drop_alert": 0.5})
        self.assertEqual((t.key_pages_extra, t.money_pages, t.confirm_minutes, t.sitemap_drop_alert),
                         (["/a"], ["/m"], 5, 0.5))
        self.assertFalse(t.checks["cwv"])
        self.assertTrue(t.checks["health"])

    def test_bad_values_are_refused(self):
        for bad in ({"enabled": "yes"}, {"colour": 1}, {"confirm_minutes": -1}, {"confirm_minutes": 2.5},
                    {"inspection_daily_max": 2000}, {"sitemap_drop_alert": 0}, {"key_pages_extra": "x"},
                    {"key_pages_extra": [1]}, {"checks": {"other": True}}, {"checks": {"health": "yes"}}, []):
            with self.assertRaises(sites.SitesConfigError, msg=str(bad)):
                self.parse(bad)

    def test_the_configured_pages_are_kept_out_of_logs(self):
        site = make_sites(("site-x", "Example", {"key_pages_extra": ["/private-page"], "money_pages": ["/money"]}))[0]
        self.assertIn("/private-page", site.sensitive_values())
        self.assertIn("/money", site.sensitive_values())


class RealRequests(unittest.TestCase):
    """The default request function against a tiny server on this computer."""

    @classmethod
    def setUpClass(cls):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/ok":
                    body = b'<html><head><link rel="canonical" href="/ok"></head></html>'
                    self.send_response(200)
                    self.send_header("X-Robots-Tag", "noindex")
                    self.send_header("X-Robots-Tag", "googlebot: nofollow")
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/moved":
                    self.send_response(301)
                    self.send_header("Location", "/ok")
                    self.end_headers()
                elif self.path == "/ua":
                    self.send_response(200 if "Mozilla" in self.headers.get("User-Agent", "") else 403)
                    self.end_headers()
                else:
                    self.send_response(500)
                    self.end_headers()

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_answer_with_its_headers(self):
        f = technical.http_fetch(self.url("/ok"))
        self.assertEqual(f.status, 200)
        self.assertEqual(f.headers["x-robots-tag"], "noindex\ngooglebot: nofollow")      # a header sent twice is kept whole
        self.assertTrue(technical.page_facts(f, self.url("/ok")).noindex)
        self.assertEqual(technical.page_facts(f, self.url("/ok")).canonical, self.url("/ok"))

    def test_a_redirect_is_seen_not_followed_unless_asked(self):
        f = technical.http_fetch(self.url("/moved"))
        self.assertEqual((f.status, f.location), (301, "/ok"))
        self.assertEqual(technical.http_fetch(self.url("/moved"), follow=True).status, 200)

    def test_errors_and_no_answer(self):
        self.assertEqual(technical.http_fetch(self.url("/boom")).status, 500)
        server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
        port = server.server_address[1]
        server.server_close()                                                   # nothing listens there any more
        self.assertEqual(technical.http_fetch(f"http://127.0.0.1:{port}/").status, 0)

    def test_it_looks_like_a_browser(self):
        self.assertEqual(technical.http_fetch(self.url("/ua")).status, 200)


if __name__ == "__main__":
    unittest.main()
