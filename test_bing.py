"""Bing Webmaster access: the key never shows, retries are polite, sites are matched by domain, the read-only check
reports counts only, and another way of getting access (a fake OAuth one) works with the same data code. Fake answers only."""

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from types import SimpleNamespace

import bing

KEY = "k3y-SECRET-0123456789"


def answer(data, status=200):
    return status, json.dumps({"d": data} if status == 200 else data).encode()


class FakeBing:
    """A fake of the Bing API: methods -> answers; records the addresses it was asked for."""

    def __init__(self, replies):
        self.replies, self.urls = replies, []

    def __call__(self, url):
        self.urls.append(url)
        method = url.split("/json/")[1].split("?")[0]
        reply = self.replies.get(method)
        if callable(reply):
            reply = reply()
        return reply if reply is not None else (404, b"")


SITES = [{"Url": "https://www.example.test/", "IsVerified": True}, {"Url": "http://example.test/", "IsVerified": True},
         {"Url": "https://other.test/", "IsVerified": False}]
TRAFFIC = [{"Date": "/Date(1793000000000)/", "Clicks": 4, "Impressions": 90}, {"Date": "/Date(1792900000000)/", "Clicks": 1, "Impressions": 10}]


def good():
    return {"GetUserSites": answer(SITES), "GetRankAndTrafficStats": answer(TRAFFIC),
            "GetQueryStats": answer([{"Query": "a"}, {"Query": "b"}, {"Query": "c"}]), "GetPageStats": answer([{"Query": "p"}]),
            "GetCrawlStats": answer([{"Date": "x"}] * 5), "GetCrawlIssues": answer([{"Url": "u"}] * 2)}


def site(name, url):
    return SimpleNamespace(name=name, site_url=url)


class AccessTests(unittest.TestCase):

    def test_the_key_is_sent_but_never_shown_in_an_error(self):
        fake = FakeBing({"GetUserSites": answer({"ErrorCode": 3, "Message": f"InvalidApiKey {KEY}"}, 400)})
        access = bing.ApiKeyAccess(KEY, fake, sleep=lambda s: None)
        with self.assertRaises(bing.BingError) as ctx:
            access.get("GetUserSites")
        self.assertIn(f"apikey={KEY}", fake.urls[0])
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIn("InvalidApiKey", str(ctx.exception))

    def test_a_network_problem_message_has_no_address(self):
        access = bing.ApiKeyAccess(KEY, lambda url: (0, b""), sleep=lambda s: None, attempts=2)
        with self.assertRaises(bing.BingError) as ctx:
            access.get("GetQueryStats", siteUrl="https://www.example.test/")
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertNotIn("example.test", str(ctx.exception))

    def test_a_rate_limit_is_retried_after_a_pause_then_succeeds(self):
        calls, pauses = [], []

        def opener(url):
            calls.append(url)
            return (429, b"") if len(calls) < 3 else answer([{"Clicks": 1}])
        got = bing.ApiKeyAccess(KEY, opener, sleep=pauses.append).get("GetQueryStats", siteUrl="x")
        self.assertEqual(got, [{"Clicks": 1}])
        self.assertEqual(pauses, [2, 4])

    def test_a_wrong_key_is_not_retried(self):
        fake = FakeBing({"GetUserSites": answer({"ErrorCode": 3, "Message": "InvalidApiKey"}, 400)})
        with self.assertRaises(bing.BingError):
            bing.ApiKeyAccess(KEY, fake, sleep=lambda s: self.fail("no pause for a wrong key")).get("GetUserSites")
        self.assertEqual(len(fake.urls), 1)

    def test_the_provider_is_chosen_by_the_config_and_needs_its_credentials(self):
        self.assertIsInstance(bing.access_for("api_key", {"BING_API_KEY": KEY}), bing.ApiKeyAccess)
        with self.assertRaises(bing.BingError):
            bing.access_for("api_key", {})
        with self.assertRaises(bing.BingError):
            bing.access_for("oauth", {})


class SiteTests(unittest.TestCase):

    def test_sites_are_matched_by_domain_https_and_verified_first(self):
        self.assertEqual(bing.match_site(SITES, "sc-domain:example.test")["Url"], "https://www.example.test/")
        self.assertEqual(bing.match_site(SITES, "https://example.test/blog/")["Url"], "https://www.example.test/")
        self.assertEqual(bing.match_site(SITES, "sc-domain:missing.test"), {})
        self.assertEqual(bing.host_of("https://www.Example.test:8080/x"), "example.test")

    def test_every_entry_of_a_site_is_listed_the_likeliest_first(self):
        found = bing.matching_sites(SITES, "sc-domain:example.test")
        self.assertEqual([s["Url"] for s in found], ["https://www.example.test/", "http://example.test/"])
        self.assertEqual(bing.matching_sites(SITES, "sc-domain:missing.test"), [])

    def test_how_an_address_is_written_and_whether_two_are_the_same(self):
        self.assertEqual(bing.address_form("https://www.example.test/"), "https, with www")
        self.assertEqual(bing.address_form("http://example.test"), "http, without www")
        self.assertEqual(bing.address_form("example.test"), "no scheme, without www")
        self.assertTrue(bing.same_address("https://www.example.test/", "https://WWW.example.test"))
        self.assertFalse(bing.same_address("https://www.example.test/", "https://example.test/"))
        self.assertFalse(bing.same_address("http://example.test/", "https://example.test/"))

    def test_the_newest_day_of_an_answer(self):
        rows = [{"Date": "/Date(1793000000000)/", "Clicks": 0, "Impressions": 0},
                {"Date": "/Date(1792000000000)/", "Clicks": 2, "Impressions": 9}]
        self.assertEqual(bing.last_day(rows), date(2026, 10, 26))
        self.assertEqual(bing.last_day(rows, positive=True), date(2026, 10, 14))
        self.assertIsNone(bing.last_day([]))
        self.assertIsNone(bing.last_day([{"Query": "no date"}]))

    def test_bing_dates(self):
        self.assertEqual(bing.parse_date("/Date(1316156400000-0700)/"), date(2011, 9, 16))
        self.assertEqual(bing.parse_date("2026-10-03T00:00:00-07:00"), date(2026, 10, 3))
        self.assertIsNone(bing.parse_date("soon"))


class CheckTests(unittest.TestCase):
    TODAY = date(2026, 10, 30)

    def lines(self, replies, sites=None, access=None):
        access = access or bing.ApiKeyAccess(KEY, FakeBing(replies), sleep=lambda s: None)
        return bing.check_lines(access, sites or [site("site-1", "sc-domain:example.test"), site("site-2", "sc-domain:missing.test")],
                                today=self.TODAY)

    def test_counts_only_for_each_site(self):
        lines = self.lines(good())
        self.assertEqual(lines[0], "the account lists 3 sites, 2 verified")
        self.assertEqual(lines[1], "site-1: 2 addresses of this site in the account")
        self.assertIn("site-1 address 1: https, with www, verified", lines[2])
        self.assertIn("site-1 address 2: http, without www, verified", lines[3])
        self.assertIn("queries: 3 rows", lines[2])
        self.assertIn("pages: 1 rows", lines[2])
        self.assertIn("crawl stats: 5 rows", lines[2])
        self.assertIn("crawl issues: 2 rows", lines[2])
        self.assertIn("newest day with clicks or impressions 2026-", lines[2])
        self.assertEqual(lines[4], "site-2: not in the Bing account")
        text = "\n".join(lines)
        for secret in ("example.test", KEY, "https://"):
            self.assertNotIn(secret, text)

    def test_a_failed_call_is_said_not_raised(self):
        replies = good()
        replies["GetQueryStats"] = answer({"ErrorCode": 5, "Message": "Quota"}, 400)
        line = self.lines(replies)[2]
        self.assertIn("queries: failed (GetQueryStats: HTTP 400 (Quota 5))", line)
        self.assertIn("pages: 1 rows", line)

    def test_a_wrong_key_stops_at_the_site_list(self):
        lines = self.lines({"GetUserSites": answer({"ErrorCode": 3, "Message": "InvalidApiKey"}, 400)})
        self.assertEqual(len(lines), 1)
        self.assertIn("the site list could not be read", lines[0])

    def test_another_way_of_getting_access_works_with_the_same_data_code(self):
        class FakeOAuth(bing.Access):
            def get(self, method, **params):
                return {"GetUserSites": SITES}.get(method, [{"Query": "q"}])
        lines = self.lines({}, access=FakeOAuth())
        self.assertIn("site-1 address 1: https, with www, verified", lines[2])
        self.assertIn("queries: 1 rows", lines[2])


class LogTests(unittest.TestCase):

    def test_the_key_and_the_addresses_never_reach_the_log(self):
        fake = FakeBing(good())
        out = io.StringIO()
        with redirect_stdout(out):
            for line in bing.check_lines(bing.ApiKeyAccess(KEY, fake, sleep=lambda s: None), [site("site-1", "sc-domain:example.test")]):
                print(line)
        self.assertNotIn(KEY, out.getvalue())
        self.assertNotIn("apikey", out.getvalue())
        self.assertTrue(fake.urls)


if __name__ == "__main__":
    unittest.main()
