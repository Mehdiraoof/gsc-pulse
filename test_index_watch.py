"""The index watch (phase 2): what Google says about the pages, which pages are asked about today, the Index Status tab, the
findings, the second look before an alert, the quota, and the Index Status column of the content dashboard. Google, the
site, Sheets, Drive and email are fakes; every address is invented."""

import json
import threading
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import bing_data
import content
import index_watch
import technical
import technical_run
from index_watch import Inspection
from test_bing_data import FilterSheets
from test_content_run import make_site as make_content_site
from test_history import FakeSheets, Req, http_error, patch_time
from test_own_access import FakeDrive
from test_technical import Base, DAY0, HOME, ROOT, Web, gsc_rows, make_sites, p
from googleapiclient.errors import HttpError
import httplib2

GOOD = "Submitted and indexed"
NOT_INDEXED = "Crawled - currently not indexed"


def ins(url, **over) -> Inspection:
    """What Google says about a page that is in the index; `over` changes fields."""
    base = dict(url=url, ok=True, verdict="PASS", coverage=GOOD, indexing="INDEXING_ALLOWED", robots="ALLOWED",
                fetch="SUCCESSFUL", last_crawl="2026-11-10T04:00:00Z", google_canonical=url, user_canonical=url, rich={})
    base.update(over)
    return Inspection(**base)


def not_indexed(url, coverage=NOT_INDEXED, **over) -> Inspection:
    return ins(url, verdict="NEUTRAL", coverage=coverage, google_canonical="", **over)


class FakeInspector:
    """Stands in for index_watch.Inspector: answers from the test's table, records what was asked."""

    def __init__(self, case):
        self.case, self.used, self.stopped, self.lock = case, 0, "", threading.Lock()

    def inspect(self, url) -> Inspection:
        with self.lock:
            if self.stopped:
                return Inspection(url, ok=False, error=self.stopped)
            if self.case.limit is not None and self.used >= self.case.limit:
                self.stopped = "quota reached"
                return Inspection(url, ok=False, error=self.stopped)
            self.used += 1
            self.case.clock_time[0] += self.case.tick                          # time the questions take
            self.case.asked.append(url)
            answer = self.case.answers.get(content.url_key(url))
            if isinstance(answer, list):
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
            return answer or ins(url)


class IndexBase(Base):

    def setUp(self):
        super().setUp()
        self.asked, self.answers, self.limit, self.pages, self.rows_before = [], {}, None, [], []
        self.fill_calls = []
        self.clock_time, self.tick = [0.0], 0
        patcher = mock.patch.object(index_watch, "WORKERS", 1)           # one at a time: the order of the questions is the plan
        patcher.start()
        self.addCleanup(patcher.stop)

    def deps(self, mail=True, mail_error=None, daily=False):
        d = super().deps(mail, mail_error, daily)
        d.inspector = lambda run: FakeInspector(self)
        d.content_pages = lambda run: list(self.pages)
        d.page_rows = lambda run, last: self.rows if last == date(2026, 11, 10) else self.rows_before
        d.fill_dashboard = lambda run, table: self.fill_calls.append(table) or 0
        d.clock = lambda: self.clock_time[0]
        return d

    def index_rows(self, label="Example"):
        return [dict(zip(index_watch.HEADER, r)) for r in self.tab("Index Status", label)[1:]]

    def overview(self):
        return {r[0]: r for r in self.tab("Overview")[4:] if len(r) > 1}


class Parsing(unittest.TestCase):

    def test_an_answer_of_the_api(self):
        response = {"inspectionResult": {
            "inspectionResultLink": "https://example.test/x",
            "indexStatusResult": {"verdict": "NEUTRAL", "coverageState": NOT_INDEXED, "robotsTxtState": "ALLOWED",
                                  "indexingState": "INDEXING_ALLOWED", "pageFetchState": "SUCCESSFUL",
                                  "lastCrawlTime": "2026-11-10T04:05:06Z", "userCanonical": "https://example.test/a",
                                  "googleCanonical": "https://example.test/b", "crawledAs": "MOBILE"},
            "richResultsResult": {"verdict": "FAIL", "detectedItems": [
                {"richResultType": "FAQ", "items": [{"name": "x", "issues": [{"issueMessage": "m", "severity": "ERROR"},
                                                                              {"issueMessage": "m", "severity": "WARNING"}]}]},
                {"richResultType": "Breadcrumbs", "items": [{"name": "y"}]}]}}}
        got = index_watch.parse_inspection("https://example.test/a", response)
        self.assertEqual((got.ok, got.verdict, got.coverage, got.robots, got.indexing, got.fetch),
                         (True, "NEUTRAL", NOT_INDEXED, "ALLOWED", "INDEXING_ALLOWED", "SUCCESSFUL"))
        self.assertEqual((got.last_crawl, got.user_canonical, got.google_canonical),
                         ("2026-11-10T04:05:06Z", "https://example.test/a", "https://example.test/b"))
        self.assertEqual(got.rich, {"FAQ": (1, 1), "Breadcrumbs": (0, 0)})

    def test_an_answer_with_nothing_in_it_is_unknown_not_a_failure(self):
        got = index_watch.parse_inspection("https://example.test/a", {"inspectionResult": {}})
        self.assertEqual((got.ok, got.verdict, got.rich), (True, "", {}))
        self.assertFalse(index_watch.is_known({index_watch.VERDICT: got.verdict}))

    def test_the_rich_results_text_goes_there_and_back(self):
        text = index_watch.rich_text({"FAQ": (2, 1), "Breadcrumbs": (0, 0), "Review": (0, 3)})
        self.assertEqual(text, "Breadcrumbs: valid; FAQ: 2 errors, 1 warning; Review: 3 warnings")
        self.assertEqual(index_watch.rich_of(text), {"Breadcrumbs": (0, 0), "FAQ": (2, 1), "Review": (0, 3)})
        self.assertEqual(index_watch.rich_of(""), {})

    def test_the_text_of_the_dashboard_column(self):
        self.assertEqual(index_watch.status_text(None), "Not checked yet")
        self.assertEqual(index_watch.status_text({index_watch.VERDICT: ""}), "Not checked yet")
        self.assertEqual(index_watch.status_text({index_watch.VERDICT: "PASS"}), "Indexed")
        self.assertEqual(index_watch.status_text({index_watch.VERDICT: "NEUTRAL", index_watch.COVERAGE: NOT_INDEXED}),
                         f"Not indexed: {NOT_INDEXED}")
        self.assertEqual(index_watch.status_text({index_watch.VERDICT: "FAIL"}), "Not indexed: FAIL")


class InspectorTests(unittest.TestCase):

    class Service:
        def __init__(self, answers):
            self.answers, self.bodies = list(answers), []

        def urlInspection(self):
            return self

        def index(self):
            return self

        def inspect(self, body):
            self.bodies.append(body)
            reply = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

            def run(num_retries=0):
                if isinstance(reply, Exception):
                    raise reply
                return reply
            return SimpleNamespace(execute=run)

    OK = {"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}}

    def make(self, answers, **kw):
        self.slept, self.now = [], [0.0]

        def sleep(seconds):
            self.slept.append(round(seconds, 6))
            self.now[0] += seconds                                              # time passes while it waits
        self.service = self.Service(answers)
        return index_watch.Inspector(self.service, "sc-domain:example.test", sleep=sleep, clock=lambda: self.now[0], **kw)

    def test_the_request_and_the_spacing(self):
        insp = self.make([self.OK])
        for i in range(3):
            insp.inspect(f"https://example.test/{i}")
        self.assertEqual(self.service.bodies[0], {"inspectionUrl": "https://example.test/0", "siteUrl": "sc-domain:example.test",
                                                  "languageCode": "en-US"})
        self.assertEqual(insp.used, 3)
        self.assertEqual(self.slept, [index_watch.INTERVAL_S, index_watch.INTERVAL_S])     # 300 a minute at most
        self.assertLessEqual(60 / index_watch.INTERVAL_S, 0.75 * index_watch.QUOTA_PER_MINUTE)

    def test_a_quota_answer_stops_everything_after_it(self):
        quota = HttpError(httplib2.Response({"status": "429"}), b'{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}')
        insp = self.make([self.OK, quota, self.OK])
        self.assertTrue(insp.inspect("https://example.test/1").ok)
        self.assertFalse(insp.inspect("https://example.test/2").ok)
        self.assertEqual(insp.stopped, "quota reached")
        last = insp.inspect("https://example.test/3")
        self.assertEqual((last.ok, last.error), (False, "quota reached"))
        self.assertEqual(len(self.service.bodies), 2)                              # nothing was asked after the stop

    def test_a_daily_limit_answer_is_a_quota_answer_too(self):
        quota = HttpError(httplib2.Response({"status": "403"}), b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
        insp = self.make([quota])
        insp.inspect("https://example.test/1")
        self.assertEqual(insp.stopped, "quota reached")

    def test_repeated_errors_stop_it_but_one_error_does_not(self):
        boom = http_error(500)
        insp = self.make([boom, self.OK, boom, boom, boom, self.OK])
        self.assertFalse(insp.inspect("https://example.test/1").ok)
        self.assertTrue(insp.inspect("https://example.test/2").ok)                  # the count starts again
        for i in (3, 4, 5):
            insp.inspect(f"https://example.test/{i}")
        self.assertEqual(insp.stopped, "repeated errors")
        self.assertEqual(insp.failed, 4)

    def test_every_thread_gets_a_client_of_its_own(self):
        made = []

        def factory():
            made.append(threading.get_ident())
            return self.Service([self.OK])
        insp = index_watch.Inspector(None, "sc-domain:example.test", sleep=lambda s: None, clock=lambda: 0.0, factory=factory)
        insp.inspect("https://example.test/1")
        insp.inspect("https://example.test/2")                                   # the same thread: the same client
        self.assertEqual(len(made), 1)
        threads = [threading.Thread(target=insp.inspect, args=(f"https://example.test/t{i}",)) for i in range(3)]
        for t in threads:
            t.start()
            t.join()
        self.assertEqual(len(made), 4)                                           # one client per thread, however many questions

    def test_a_client_that_cannot_be_made_counts_as_an_error(self):
        def factory():
            raise RuntimeError("no client")
        insp = index_watch.Inspector(None, "sc-domain:example.test", sleep=lambda s: None, clock=lambda: 0.0, factory=factory)
        self.assertEqual(insp.inspect("https://example.test/1").error, "RuntimeError")

    def test_an_error_says_what_but_not_the_address(self):
        insp = self.make([http_error(500)])
        got = insp.inspect("https://example.test/secret-page")
        self.assertEqual(got.error, "HTTP 500")
        self.assertNotIn("example.test", got.error)


class PlanTests(unittest.TestCase):

    TODAY = date(2026, 11, 12)

    def table(self, **checked):
        t = index_watch.Table([])
        for url, day in checked.items():
            t.put(f"https://example.test/{url}", {index_watch.ADDRESS: f"https://example.test/{url}", index_watch.VERDICT: "PASS",
                                                  index_watch.CHECKED: day})
        return t

    def test_the_order_the_dedupe_and_the_budget(self):
        u = lambda n: f"https://example.test/{n}"
        pages = [index_watch.ContentPage(u("new1"), "Published", self.TODAY - timedelta(days=3)),
                 index_watch.ContentPage(u("new2"), "Draft", self.TODAY - timedelta(days=9)),
                 index_watch.ContentPage(u("old"), "Published", self.TODAY - timedelta(days=200)),
                 index_watch.ContentPage(u("k1"), "Published", self.TODAY - timedelta(days=1))]
        got = index_watch.plan([u("k1"), u("k2"), u("blocked")], [u("blocked")], pages, [u("drop"), u("new2")],
                               [u("s1"), u("s2"), u("k2")], self.table(), self.TODAY, 100)
        self.assertEqual(got, [(u("blocked"), index_watch.WHY_BLOCKED), (u("k1"), index_watch.WHY_KEY), (u("k2"), index_watch.WHY_KEY),
                               (u("new1"), index_watch.WHY_NEW), (u("new2"), index_watch.WHY_NEW),
                               (u("old"), index_watch.WHY_PUBLISHED), (u("drop"), index_watch.WHY_DROP),
                               (u("s1"), index_watch.WHY_ROTATION), (u("s2"), index_watch.WHY_ROTATION)])
        self.assertEqual(len(index_watch.plan([u("k1"), u("k2")], [], pages, [], [], self.table(), self.TODAY, 1)), 1)
        self.assertEqual(index_watch.plan([u("k1")], [], pages, [], [u("s1")], self.table(), self.TODAY, 0), [])

    def test_a_published_page_already_known_to_be_indexed_is_not_asked_about_each_time(self):
        u = lambda n: f"https://example.test/{n}"
        pages = [index_watch.ContentPage(u("old"), "Published", None)]
        self.assertEqual(index_watch.plan([], [], pages, [], [], self.table(old="2026-11-01"), self.TODAY, 10), [])

    def test_the_rotation_takes_the_page_looked_at_longest_ago_first_and_never_asked_ones_before_them(self):
        u = lambda n: f"https://example.test/{n}"
        got = index_watch.plan([], [], [], [], [u("a"), u("b"), u("c"), u("d")],
                               self.table(a="2026-11-05", b="2026-10-01", c="2026-11-10"), self.TODAY, 3)
        self.assertEqual([url for url, _ in got], [u("d"), u("b"), u("a")])

    def test_pages_that_lost_more_than_half_of_their_impressions(self):
        row = lambda url, n: {"keys": [url], "impressions": n}
        now = [row("https://example.test/a", 40), row("https://example.test/b", 90), row("https://example.test/c?x=1", 1),
               row("https://example.test/d", 5)]
        before = [row("https://example.test/a", 200), row("https://example.test/b", 100), row("https://example.test/c?x=1", 900),
                  row("https://example.test/d", 50), row("https://example.test/gone", 500)]
        self.assertEqual(index_watch.lost_impressions(now, before), ["https://example.test/gone", "https://example.test/a"])


class FirstRun(IndexBase):

    def test_the_key_pages_and_the_sitemap_are_inspected_and_the_tab_is_filled(self):
        self.go()
        self.assertEqual(self.tab("Index Status")[0], index_watch.HEADER)
        rows = self.index_rows()
        self.assertEqual(len(rows), 50)                                         # 20 key pages and 30 sitemap pages
        self.assertEqual(self.asked[:20], [p(i) for i in range(1, 21)])         # the key pages first
        first = rows[0]
        self.assertEqual((first["Address"], first["Verdict"], first["Coverage"], first["Why checked"]),
                         (p(1), "PASS", GOOD, "Key page"))
        self.assertEqual((first["First seen"], first["Last checked"], first["Last changed"], first["Clicks (28 days)"]),
                         ("2026-11-12", "2026-11-12", "2026-11-12", 199))
        self.assertEqual(first["Last crawl"], "2026-11-10")
        self.assertEqual(self.mails, [])
        self.assertEqual(self.slept, [])
        row = self.overview()["URL Inspection use"]
        self.assertEqual(row[1], "Info")
        self.assertIn("50 of 50 planned pages inspected", row[2])
        self.assertIn("50 of the 1500 allowed today used", row[2])
        self.assertNotIn("example.test", " ".join(self.logs))

    def test_a_second_day_changes_nothing_that_did_not_change(self):
        self.go()
        self.go()
        rows = {r["Address"]: r for r in self.index_rows()}
        self.assertEqual(len(rows), 50)
        self.assertEqual((rows[p(1)]["First seen"], rows[p(1)]["Last checked"], rows[p(1)]["Last changed"]),
                         ("2026-11-12", "2026-11-13", "2026-11-12"))
        self.answers[content.url_key(p(1))] = not_indexed(p(1))
        self.go()
        rows = {r["Address"]: r for r in self.index_rows()}
        self.assertEqual((rows[p(1)]["Verdict"], rows[p(1)]["First seen"], rows[p(1)]["Last changed"]),
                         ("NEUTRAL", "2026-11-12", "2026-11-14"))

    def test_the_content_dashboard_is_asked_to_fill_its_column(self):
        self.go()
        self.assertEqual(len(self.fill_calls), 1)
        self.assertEqual(len(self.fill_calls[0].recs), 50)

    def test_the_reports_sheet_is_still_not_touched(self):
        before = [list(r) for r in self.fs.books["REP"]["Dashboard"].data]
        self.go()
        self.assertEqual(self.fs.books["REP"]["Dashboard"].data, before)
        self.assertFalse([1 for book, _ in self.fs.requests if book == "REP"])


class Quota(IndexBase):

    def make_site_with(self, config):
        site = make_sites(("site-x", "Example", config))[0]
        site.reports_sheet_id = "REP"
        self.runs = [self.make_run(site)]

    def test_never_above_the_daily_maximum_and_the_key_pages_come_first_every_day(self):
        self.make_site_with({"inspection_daily_max": 30})
        self.go()
        self.assertEqual(len(self.asked), 30)
        self.assertEqual(self.asked[:20], [p(i) for i in range(1, 21)])
        self.assertEqual(set(self.asked[20:]), {f"{HOME}/s/{i}" for i in range(10)})
        self.assertEqual(self.state().get("quota", "2026-11-12"), "30")
        self.asked.clear()
        self.go()                                                               # the next day: the key pages, then the next ten
        self.assertEqual(self.asked[:20], [p(i) for i in range(1, 21)])
        self.assertEqual(set(self.asked[20:]), {f"{HOME}/s/{i}" for i in range(10, 20)})
        self.assertEqual(self.state().get("quota", "2026-11-13"), "30")

    def test_a_second_run_on_the_same_day_only_has_what_is_left(self):
        self.make_site_with({"inspection_daily_max": 30})
        self.go(advance=False)
        self.asked.clear()
        self.go(advance=False)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.state().get("quota", "2026-11-12"), "30")

    def test_what_the_quota_answer_stops_is_said_and_the_rest_goes_on(self):
        self.limit = 25
        self.go()
        self.assertEqual(len(self.asked), 25)
        row = self.overview()["URL Inspection use"]
        self.assertEqual(row[1], "Stopped")
        self.assertIn("stopped: quota reached", row[2])
        self.assertEqual(len(self.index_rows()), 25)
        self.assertEqual(self.mails, [])
        self.assertTrue(self.tab("Health Log")[1:])

    def test_old_days_are_forgotten(self):
        self.go()
        for i in range(8):
            self.go()
        self.assertNotIn("2026-11-12", self.state().of("quota"))
        self.assertIn("2026-11-20", self.state().of("quota"))


class KeyPageFindings(IndexBase):

    def test_a_key_page_that_is_not_in_the_index_is_told_after_a_second_look(self):
        self.answers[content.url_key(p(3))] = not_indexed(p(3))
        self.go()
        self.assertEqual(self.slept, [1800])
        subject, body = self.mails[0]
        self.assertEqual(subject, "Site alert · Example · Key pages not in Google's index")
        self.assertIn(NOT_INDEXED, body)
        self.assertIn("/a/p3", body)
        self.assertIn("Clicks on those pages in the last 28 days: 197", body)
        self.assertEqual(self.asked.count(p(3)), 2)                              # asked again after the wait
        results = self.log_results()
        self.assertIn(("Key pages in Google's index", "failed (first check)"), results)
        self.assertIn(("Key pages in Google's index", "confirmed problem"), results)
        self.assertEqual(self.state().open()["idx_key_index"]["alerted"], "2026-11-12")

    def test_the_rest_of_the_watch_is_done_while_the_second_check_waits(self):
        self.tick = 1                                                           # every question takes a second
        self.answers[content.url_key(p(3))] = not_indexed(p(3))
        self.go()
        # 20 key pages come before the wait; the 30 sitemap pages after it begins are part of the wait
        self.assertEqual(self.slept, [1800 - 30])
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages not in Google's index")

    def test_the_key_pages_are_asked_before_the_rest(self):
        self.go()
        self.assertEqual(self.asked[:20], [p(i) for i in range(1, 21)])
        self.assertEqual(len(self.asked), 50)

    def test_a_page_that_is_in_the_index_the_second_time_alerts_nobody(self):
        self.answers[content.url_key(p(3))] = [not_indexed(p(3)), ins(p(3))]
        self.go()
        self.assertEqual(self.mails, [])
        self.assertIn(("Key pages in Google's index", "not confirmed"), self.log_results())
        self.assertEqual(self.state().open(), {})

    def test_a_page_that_dropped_out_of_the_index_says_so(self):
        self.go()
        self.answers[content.url_key(p(3))] = not_indexed(p(3), "Duplicate without user-selected canonical")
        self.go()
        self.assertIn("1 of them were in the index before", self.mails[0][1])

    def test_it_is_told_once_and_the_fix_once(self):
        self.answers[content.url_key(p(3))] = not_indexed(p(3))
        self.go()
        self.go()
        self.assertEqual(self.mails, [])
        del self.answers[content.url_key(p(3))]
        self.go()
        self.assertEqual(self.mails[0][0], "Fixed · Example · Key pages not in Google's index")

    def test_google_taking_another_address_is_its_own_finding(self):
        self.answers[content.url_key(p(4))] = ins(p(4), google_canonical=f"{HOME}/a/elsewhere", user_canonical=p(4))
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Google took another address as the main one for key pages")
        self.assertNotIn("Key pages not in Google's index", self.mails[0][0])

    def test_rich_result_errors_are_told_and_so_is_a_rich_result_that_went_away(self):
        self.answers[content.url_key(p(5))] = ins(p(5), rich={"FAQ": (2, 0)})
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Rich result problem on key pages")
        self.assertIn("FAQ x1", self.mails[0][1])

    def test_a_rich_result_that_disappeared(self):
        self.answers[content.url_key(p(6))] = [ins(p(6), rich={"Breadcrumbs": (0, 0)}), ins(p(6))]
        self.go()
        self.assertEqual(self.mails, [])                                         # valid rich results are not a problem
        self.answers[content.url_key(p(6))] = ins(p(6))
        self.go()
        self.assertEqual(self.mails[0][0], "Site alert · Example · Rich result problem on key pages")
        self.assertIn("went away", self.mails[0][1])

    def test_a_key_page_that_only_redirects_is_not_an_index_problem(self):
        self.answers[content.url_key(p(7))] = not_indexed(p(7), "Page with redirect")
        self.go()
        self.assertEqual(self.mails, [])

    def test_unknown_to_google_is_not_an_answer_to_trust_for_a_page_with_clicks(self):
        self.answers[content.url_key(p(9))] = ins(p(9), verdict="NEUTRAL", coverage="URL is unknown to Google",
                                                  indexing="INDEXING_STATE_UNSPECIFIED", google_canonical="")
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(self.state().open(), {})
        self.assertEqual(index_watch.status_text({index_watch.VERDICT: "NEUTRAL", index_watch.COVERAGE: "URL is unknown to Google"}),
                         "Unknown to Google (not confirmed)")

    def test_the_quota_line_counts_the_requests_really_made(self):
        self.limit = 25
        self.go()
        self.assertIn("25 of the 1500 allowed today used", self.overview()["URL Inspection use"][2])

    def test_a_page_google_could_not_say_anything_about_is_not_a_problem(self):
        self.answers[content.url_key(p(8))] = Inspection(p(8), ok=True)
        self.go()
        self.assertEqual(self.mails, [])


class OwnerFindings(IndexBase):
    """Findings that are for the owner to read: in the Overview and the Health Log, never an email, never an open problem."""

    def test_sitemap_pages_that_are_not_indexable_and_new_content_that_is_not_indexed(self):
        s = lambda n: f"{HOME}/s/{n}"
        self.answers[content.url_key(s(3))] = ins(s(3), indexing="BLOCKED_BY_META_TAG", verdict="NEUTRAL", coverage="Excluded by noindex tag")
        self.answers[content.url_key(s(4))] = not_indexed(s(4), "Not found (404)", fetch="NOT_FOUND")
        self.answers[content.url_key(s(5))] = ins(s(5), fetch="REDIRECT_ERROR", verdict="NEUTRAL", coverage="Page with redirect")
        self.answers[content.url_key(s(6))] = ins(s(6), google_canonical=s(7), user_canonical=s(6))
        self.answers[content.url_key(s(1))] = not_indexed(s(1))
        self.pages = [index_watch.ContentPage(s(1), "Published", date(2026, 10, 20)),          # 23 days ago: past the 14 days
                      index_watch.ContentPage(s(2), "Published", date(2026, 11, 8))]           # 4 days ago: too early to say
        self.go()
        self.assertEqual(self.mails, [])
        self.assertEqual(self.state().open(), {})
        over = self.overview()
        self.assertEqual(over["Sitemap pages not indexable"][1], "Finding")
        self.assertIn("4 inspected sitemap pages are not indexable", over["Sitemap pages not indexable"][2])
        self.assertIn("noindex x1", over["Sitemap pages not indexable"][2])
        self.assertEqual(over["New content not indexed"][1], "Finding")
        self.assertIn("1 pages with new or updated content", over["New content not indexed"][2])
        self.assertEqual(self.tab("Overview")[1][:2], ["Site health", "OK"])
        self.assertIn(("Sitemap pages not indexable", "finding"), self.log_results())
        self.assertEqual(over["Key pages in Google's index"][1], "OK")

    def test_a_page_that_dropped_out_of_the_index_is_a_finding_for_pages_that_are_not_key_pages(self):
        s = f"{HOME}/s/9"
        self.go()
        self.answers[content.url_key(s)] = not_indexed(s)
        self.go()
        self.assertEqual(self.mails, [])
        over = self.overview()
        self.assertEqual(over["Pages dropped from the index"][1], "Finding")
        self.assertIn("1 pages that were in the index are not any more", over["Pages dropped from the index"][2])

    def test_rich_result_errors_on_other_pages(self):
        s = f"{HOME}/s/2"
        self.answers[content.url_key(s)] = ins(s, rich={"Product": (3, 1)})
        self.go()
        self.assertEqual(self.mails, [])
        self.assertIn("Product x1", self.overview()["Rich result errors (other pages)"][2])

    def test_pages_that_lost_their_impressions_are_asked_about(self):
        self.rows_before = [{"keys": [f"{HOME}/lost"], "impressions": 800, "clicks": 3}]
        self.go()
        row = next(r for r in self.index_rows() if r["Address"] == f"{HOME}/lost")
        self.assertEqual(row["Why checked"], "Lost impressions")


class BlockedPages(IndexBase):

    def test_pages_the_checker_cannot_reach_are_inspected_and_the_overview_says_what_google_says(self):
        self.go()
        tab = self.fs.books[self.file_id()]["Checker Blocked"]
        self.fs.write_cells(tab, 2, 1, [[p(3), "refuses the checker"], [p(4), "refuses the checker"]])
        self.web.pages[p(3)] = technical.Fetched(403, {}, "", 0.1)
        self.web.pages[p(4)] = technical.Fetched(403, {}, "", 0.1)
        self.answers[content.url_key(p(4))] = not_indexed(p(4))
        before = len(self.asked)
        self.go()
        row = self.overview()["Key pages blocked for checker"]
        self.assertIn("1 indexed, 1 not indexed, 0 not checked yet", row[2])
        rows = {r["Address"]: r for r in self.index_rows()}
        self.assertEqual(rows[p(3)]["Why checked"], "Key page (blocked for the checker)")
        self.assertEqual(self.asked[before:].count(p(3)), 1)
        # the one Google does not index is a key page like any other for the index finding
        self.assertEqual(self.mails[0][0], "Site alert · Example · Key pages not in Google's index")


class Switches(IndexBase):

    def test_the_index_watch_can_be_switched_off(self):
        site = make_sites(("site-x", "Example", {"checks": {"inspection": False}}))[0]
        site.reports_sheet_id = "REP"
        self.runs = [self.make_run(site)]
        self.go()
        self.assertEqual(self.asked, [])
        self.assertEqual(self.tab("Index Status"), [index_watch.HEADER])
        self.assertNotIn("URL Inspection use", self.overview())
        self.assertTrue(self.tab("Health Log")[1:])

    def test_the_small_daily_check_never_inspects(self):
        self.go(daily=True)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.fill_calls, [])

    def test_a_run_that_cannot_ask_google_still_makes_the_health_checks(self):
        deps = self.deps()
        deps.inspector = lambda run: (_ for _ in ()).throw(RuntimeError("no service"))
        self.go(deps=deps)
        self.assertIn("the index watch could not be made", " ".join(self.logs))
        self.assertTrue(self.tab("Health Log")[1:])
        self.assertEqual(self.mails, [])

    def test_no_inspector_in_this_run_leaves_the_tab_alone(self):
        deps = self.deps()
        deps.inspector = None
        self.go(deps=deps)
        self.assertEqual(self.tab("Index Status"), [index_watch.HEADER])

    def test_a_dashboard_that_cannot_be_filled_never_stops_the_rest(self):
        deps = self.deps()
        deps.fill_dashboard = lambda run, table: (_ for _ in ()).throw(RuntimeError("no"))
        self.go(deps=deps)
        self.assertIn("could not be written", " ".join(self.logs))
        self.assertEqual(len(self.index_rows()), 50)                            # the tab was written before the column

    def test_the_budget_of_the_site_can_be_zero(self):
        site = make_sites(("site-x", "Example", {"inspection_daily_max": 0}))[0]
        site.reports_sheet_id = "REP"
        self.runs = [self.make_run(site)]
        self.go()
        self.assertEqual(self.asked, [])


class DashboardColumn(unittest.TestCase):
    """The Index Status column of the content dashboard, on a fake dashboard."""

    HOME = "https://example.test"

    def setUp(self):
        patch_time(self)
        self.fs = FilterSheets()
        self.fs.add_book("DASH", {"Articles": (50, 42), "Landing Pages": (50, 42)})
        for tab in ("Articles", "Landing Pages"):
            self.fs.write_cells(self.fs.books["DASH"][tab], 1, 1, [content.HEADERS])
        self.table = index_watch.Table([])
        self.put(f"{self.HOME}/article/a", ins(f"{self.HOME}/article/a"))
        self.put(f"{self.HOME}/article/b", not_indexed(f"{self.HOME}/article/b"))

    def put(self, url, inspection):
        self.table.put(url, index_watch.new_record(url, inspection, None, "Key page", 0, date(2026, 11, 12)))

    def add(self, cells, tab="Articles"):
        t = self.fs.books["DASH"][tab]
        t.data.append([cells.get(h, "") for h in content.HEADERS])
        t.row_count = max(t.row_count, len(t.data))

    def rows(self, tab="Articles"):
        data = self.fs.books["DASH"][tab].data
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(data[0])} for r in data[1:]], data[0]

    def fill(self):
        return index_watch.fill_dashboard(self.fs, "DASH", ["Articles", "Landing Pages", "Missing Tab"], self.table, lambda *_: None)

    def test_the_column_is_after_the_other_data_columns_and_filled_by_address(self):
        self.add({content.KEY: "kw a", content.URL: f"{self.HOME}/article/a/", content.STATUS: "Published", "Notes/Checklist": "mine"})
        self.add({content.KEY: "kw b", content.URL: f"{self.HOME}/article/b", content.STATUS: "Published"})
        self.add({content.KEY: "kw c", content.URL: f"{self.HOME}/article/c"})
        self.add({content.KEY: "topic with no page"})
        self.add({content.KEY: "old", content.URL: f"{self.HOME}/article/old", content.STATUS: "Removed"})
        before = self.rows()[0]
        self.assertEqual(self.fill(), 2)
        rows, header = self.rows()
        at = content.HEADERS.index("Ranking Queries") + 1
        self.assertEqual(header[at], index_watch.COL_INDEX)
        self.assertEqual([r[index_watch.COL_INDEX] for r in rows[:3]],
                         ["Indexed", f"Not indexed: {NOT_INDEXED}", "Not checked yet"])
        self.assertEqual(rows[3][index_watch.COL_INDEX], "")                    # no address: the cell keeps what it held
        self.assertEqual(rows[4][index_watch.COL_INDEX], "")                    # a settled row too
        for old, new in zip(before, rows):                                      # the owner's columns are not touched
            for name in content.HEADERS:
                self.assertEqual(new[name], old[name], name)

    def test_the_column_goes_after_the_bing_columns_when_they_are_there(self):
        header = list(content.HEADERS)
        at = header.index("Ranking Queries") + 1
        header[at:at] = [bing_data.COL_CLICKS, bing_data.COL_IMPR]
        self.fs.books["DASH"]["Articles"].data[0] = header
        self.add_with(header, {content.URL: f"{self.HOME}/article/a", content.KEY: "kw"})
        self.fill()
        _, got = self.rows()
        self.assertEqual(got[at:at + 3], [bing_data.COL_CLICKS, bing_data.COL_IMPR, index_watch.COL_INDEX])

    def add_with(self, header, cells):
        t = self.fs.books["DASH"]["Articles"]
        t.data.append([cells.get(h, "") for h in header])

    def test_running_it_again_changes_nothing(self):
        self.add({content.KEY: "kw a", content.URL: f"{self.HOME}/article/a"})
        self.fill()
        first = [list(r) for r in self.fs.books["DASH"]["Articles"].data]
        self.fill()
        self.assertEqual(self.fs.books["DASH"]["Articles"].data, first)

    def test_the_pages_of_the_dashboard_with_their_status_and_date(self):
        serial = (date(2026, 11, 1) - date(1899, 12, 30)).days
        self.add({content.KEY: "a", content.URL: f"{self.HOME}/article/a", content.STATUS: "Published", content.PUBLISHED_DATE: serial})
        self.add({content.KEY: "b", content.URL: f"{self.HOME}/article/b", content.STATUS: "Draft", content.PUBLISHED_DATE: "2026-10-15"})
        self.add({content.KEY: "c", content.URL: f"{self.HOME}/article/c", content.STATUS: "Removed"})
        self.add({content.KEY: "d"})
        got = index_watch.read_content_pages(self.fs, "DASH", ["Articles", "Nope"])
        self.assertEqual([(g.url, g.status, g.day) for g in got],
                         [(f"{self.HOME}/article/a", "Published", date(2026, 11, 1)),
                          (f"{self.HOME}/article/b", "Draft", date(2026, 10, 15))])


class TheDashboardInTheRun(Base):
    """The whole run on a site with a content dashboard: the pages come from it and the column goes back into it."""

    def setUp(self):
        patch_time(self)
        self.fs = FilterSheets()
        self.drive = FakeDrive(self.fs)
        self.fs.add_book("REP", {"Dashboard": (5, 5)})
        self.fs.add_book("DASH", {"Articles": (50, 42), "Landing Pages": (50, 42)})
        for tab in ("Articles", "Landing Pages"):
            self.fs.write_cells(self.fs.books["DASH"][tab], 1, 1, [content.HEADERS])
        for url, status, day in ((p(1), "Published", ""), (f"{HOME}/new-post", "Published", "2026-11-05"),
                                 (f"{HOME}/s/2", "Draft", "")):
            row = {content.KEY: "kw", content.URL: url, content.STATUS: status, content.PUBLISHED_DATE: day, "Notes/Checklist": "mine"}
            t = self.fs.books["DASH"]["Articles"]
            t.data.append([row.get(h, "") for h in content.HEADERS])
        self.web = Web()
        self.now = DAY0
        self.mails, self.logs, self.slept, self.errors = [], [], [], []
        self.api, self.rows = [], gsc_rows()
        self.site = make_content_site(sheet_id="DASH")
        self.site.reports_sheet_id = "REP"
        self.runs = [self.make_run(self.site)]
        self.asked, self.answers, self.limit = [], {}, None
        self.clock_time, self.tick = [0.0], 0

    def deps(self, mail=True, mail_error=None, daily=False):
        d = super().deps(mail, mail_error, daily)
        d.inspector = lambda run: FakeInspector(self)
        d.page_rows = lambda run, last: self.rows if last == date(2026, 11, 10) else []
        return d

    def test_new_content_is_asked_about_and_the_column_is_filled(self):
        self.answers[content.url_key(f"{HOME}/new-post")] = not_indexed(f"{HOME}/new-post")
        self.go()
        self.assertIn(f"{HOME}/new-post", self.asked)                            # a page that is not in the sitemap
        data = self.fs.books["DASH"]["Articles"].data
        header = data[0]
        col = header.index(index_watch.COL_INDEX)
        self.assertEqual(col, header.index("Ranking Queries") + 1)
        by_url = {r[header.index(content.URL)]: r[col] for r in data[1:]}
        self.assertEqual(by_url[p(1)], "Indexed")
        self.assertEqual(by_url[f"{HOME}/new-post"], f"Not indexed: {NOT_INDEXED}")
        self.assertEqual(by_url[f"{HOME}/s/2"], "Indexed")                      # a sitemap page, asked about in the rotation
        self.assertEqual([r[header.index("Notes/Checklist")] for r in data[1:]], ["mine"] * 3)
        self.assertIn("dashboard column on 2 tabs", " ".join(self.logs))

    def test_a_dashboard_with_a_page_that_is_not_listed_anywhere_says_not_checked_yet(self):
        t = self.fs.books["DASH"]["Articles"]
        t.data.append([{content.KEY: "z", content.URL: f"{HOME}/nowhere"}.get(h, "") for h in content.HEADERS])
        self.go()
        data = self.fs.books["DASH"]["Articles"].data
        header = data[0]
        got = {r[header.index(content.URL)]: r[header.index(index_watch.COL_INDEX)] for r in data[1:]}
        self.assertEqual(got[f"{HOME}/nowhere"], "Not checked yet")


if __name__ == "__main__":
    unittest.main()
