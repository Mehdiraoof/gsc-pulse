"""The WordPress read-only check: secret names, GET requests only, what is read, what is printed (counts only, never a
password, a user or an address). WordPress is a fake that answers from a table; every address is invented."""

import base64
import json
import unittest
import urllib.parse
from datetime import date

import sites
import wp_check as wp
from test_technical import make_sites

TODAY = date(2026, 11, 16)
USER, PASSWORD = "pulseo-editor", "abcd efgh ijkl mnop"
ENV = {"WP_SITE1_USER": USER, "WP_SITE1_APP_PASSWORD": PASSWORD}


class FakeWp:
    """Answers GET requests of one install from a table {route: (status, total, json)}; records every request."""

    def __init__(self, routes=None, pretty=True, status_root=200):
        self.requests, self.pretty = [], pretty
        self.routes = {
            "/": (status_root, None, {"namespaces": ["oembed/1.0", "wp/v2"]}),
            "/wp/v2/users/me": (200, None, {"roles": ["editor"], "capabilities": {"edit_posts": True, "upload_files": True,
                                                                                     "publish_posts": True, "edit_others_posts": True}}),
            "/wp/v2/posts": (200, 7, []), "/wp/v2/media": (200, 340, []), "/wp/v2/categories": (200, 12, [])}
        self.routes.update(routes or {})

    def __call__(self, url, headers):
        self.requests.append((url, headers))
        parts = urllib.parse.urlsplit(url)
        if parts.path.startswith("/wp-json"):
            if not self.pretty:
                return 404, {}, b"not found"
            route = parts.path[len("/wp-json"):] or "/"
            query = urllib.parse.parse_qs(parts.query)
        else:
            query = urllib.parse.parse_qs(parts.query)
            route = (query.get("rest_route") or [""])[0]
            if not self.pretty and not route:
                return 404, {}, b""
        if route == "/wp/v2/posts":                                       # drafts, published, published lately: told by the query
            status = (query.get("status") or [""])[0]
            total = {"draft": 4, "publish": 220}.get(status, 0) if "after" not in query else 9
            status_code, _, body = self.routes[route]
            return status_code, {"x-wp-total": str(total)}, json.dumps(body).encode()
        if route in self.routes:
            code, total, body = self.routes[route]
            return code, ({"x-wp-total": str(total)} if total is not None else {}), json.dumps(body).encode()
        return 404, {}, b"{}"


class Secrets(unittest.TestCase):

    def test_names_from_the_site_and_the_language(self):
        self.assertEqual(wp.secret_prefix("site-1"), "WP_SITE1")
        self.assertEqual(wp.secret_prefix("site-4", "en"), "WP_SITE4_EN")
        self.assertEqual(wp.secret_prefix("Site 4", "FA"), "WP_SITE4_FA")

    def test_one_install_for_a_site_and_one_for_each_language_of_a_two_language_site(self):
        one = make_sites(("site-1", "Alpha", None))[0]
        installs = wp.installs_for(one, ENV)
        self.assertEqual([(i.label, i.base, i.prefix) for i in installs], [("", "https://example.test", "WP_SITE1")])
        self.assertEqual((installs[0].user, installs[0].password), (USER, "abcdefghijklmnop"))        # the spaces of the password go
        two = make_sites(("site-4", "Beta", None))[0]
        two.content.languages = {"en": "", "fa": "/fa"}
        installs = wp.installs_for(two, {"WP_SITE4_EN_USER": "u1", "WP_SITE4_FA_USER": "u2"})
        self.assertEqual([(i.label, i.base, i.prefix, i.user) for i in installs],
                         [("EN", "https://example.test", "WP_SITE4_EN", "u1"), ("FA", "https://example.test/fa", "WP_SITE4_FA", "u2")])
        self.assertEqual(installs[1].secrets, ("WP_SITE4_FA_USER", "WP_SITE4_FA_APP_PASSWORD"))


class Checks(unittest.TestCase):

    def install(self):
        return wp.installs_for(make_sites(("site-1", "Alpha", None))[0], ENV)[0]

    def test_a_good_install_reports_counts_only(self):
        fake = FakeWp()
        lines = wp.check_install(self.install(), fake, TODAY)
        self.assertEqual(lines, [
            "site-1: reachable, REST API wp/v2 answers",
            "site-1: login ok, role editor; edit_posts yes, upload_files yes, publish_posts yes, edit_others_posts yes",
            "site-1: drafts 4", "site-1: media files 340", "site-1: published posts 220",
            "site-1: published in the last 30 days 9", "site-1: categories 12"])
        text = "\n".join(lines)
        for private in (USER, PASSWORD, "abcdefghijklmnop", "example.test", "http"):
            self.assertNotIn(private, text)

    def test_every_request_is_a_get_and_the_login_is_only_in_the_header(self):
        fake = FakeWp()
        wp.check_install(self.install(), fake, TODAY)
        self.assertGreaterEqual(len(fake.requests), 7)
        token = "Basic " + base64.b64encode(f"{USER}:abcdefghijklmnop".encode()).decode()
        for url, headers in fake.requests:
            self.assertNotIn("abcdefghijklmnop", url)
            self.assertNotIn(USER, url)
            if url.endswith("/wp-json/") or url.endswith("/wp-json"):
                self.assertNotIn("Authorization", headers)                       # the first look needs no login
            else:
                self.assertEqual(headers["Authorization"], token)
        self.assertTrue(all(h["Accept"] == "application/json" for _, h in fake.requests))

    def test_a_site_without_pretty_links_is_asked_through_rest_route(self):
        fake = FakeWp(pretty=False)
        lines = wp.check_install(self.install(), fake, TODAY)
        self.assertIn("site-1: drafts 4", lines)
        self.assertTrue(any("rest_route=" in url for url, _ in fake.requests))

    def test_a_server_that_does_not_answer(self):
        lines = wp.check_install(self.install(), lambda url, headers: (0, {}, b""), TODAY)
        self.assertEqual(lines, ["site-1: no answer (the server may block requests from outside its country)"])

    def test_an_address_that_is_not_wordpress(self):
        lines = wp.check_install(self.install(), FakeWp(routes={"/": (200, None, {"name": "something else"})}), TODAY)
        self.assertEqual(lines, ["site-1: the REST API did not answer as WordPress does (HTTP 200)"])

    def test_a_login_that_is_refused(self):
        lines = wp.check_install(self.install(), FakeWp(routes={"/wp/v2/users/me": (401, None, {"code": "rest_forbidden"})}), TODAY)
        self.assertEqual(len(lines), 2)
        self.assertIn("the login was refused (HTTP 401)", lines[1])

    def test_a_user_who_cannot_upload_is_said_so(self):
        me = {"roles": ["author"], "capabilities": {"edit_posts": True}}
        lines = wp.check_install(self.install(), FakeWp(routes={"/wp/v2/users/me": (200, None, me)}), TODAY)
        self.assertIn("role author; edit_posts yes, upload_files no, publish_posts no, edit_others_posts no", lines[1])

    def test_a_list_that_cannot_be_read_is_said_without_stopping_the_others(self):
        lines = wp.check_install(self.install(), FakeWp(routes={"/wp/v2/media": (403, None, {})}), TODAY)
        self.assertIn("site-1: media files could not be read (HTTP 403)", lines)
        self.assertIn("site-1: categories 12", lines)

    def test_no_login_yet_says_which_secrets_to_create(self):
        install = wp.installs_for(make_sites(("site-4", "Beta", None))[0], {})[0]
        self.assertEqual(wp.check_install(install, FakeWp(), TODAY),
                         ["site-4: no login yet: create the secrets WP_SITE4_USER and WP_SITE4_APP_PASSWORD"])


class Everything(unittest.TestCase):

    def test_only_the_named_sites_are_looked_at_and_missing_secrets_are_listed(self):
        a, b, c = make_sites(("site-1", "Alpha", None), ("site-2", "Beta", None), ("site-4", "Gamma", None))
        c.content.languages = {"en": "", "fa": "/fa"}
        env = dict(ENV, WORDPRESS_SITES="site-1,site-4", WP_SITE4_EN_USER="u", WP_SITE4_EN_APP_PASSWORD="p")
        lines = wp.check_lines([a, b, c], env, FakeWp(), TODAY)
        self.assertTrue(any(l.startswith("site-1: login ok") for l in lines))
        self.assertTrue(any(l.startswith("site-4 EN: login ok") for l in lines))
        self.assertIn("site-4 FA: no login yet: create the secrets WP_SITE4_FA_USER and WP_SITE4_FA_APP_PASSWORD", lines)
        self.assertFalse(any(l.startswith("site-2") for l in lines))

    def test_without_a_list_only_installs_with_a_login_are_checked(self):
        a, b = make_sites(("site-1", "Alpha", None), ("site-2", "Beta", None))
        lines = wp.check_lines([a, b], ENV, FakeWp(), TODAY)
        self.assertTrue(lines and all(l.startswith("site-1") for l in lines))


if __name__ == "__main__":
    unittest.main()
