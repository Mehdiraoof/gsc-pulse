"""Tests for looking at the live site: sitemaps and the status of a page (v2.7).
No real site is touched: the sitemaps are in-memory answers and the page requests go to a small
server on this computer."""

import gzip
import http.server
import threading
import unittest
from xml.sax.saxutils import escape

import site_check as sc
from site_check import Probe

PERSIAN_PATH = "/کتاب"                  # /کتاب
ENCODED_PATH = "/%DA%A9%D8%AA%D8%A7%D8%A8"


def xml(kind, *locs):
    ns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
    locs = [escape(u) for u in locs]
    if kind == "index":
        body = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in locs)
        return f"<?xml version='1.0'?><sitemapindex {ns}>{body}</sitemapindex>".encode()
    body = "".join(f"<url><loc>{u}</loc></url>" for u in locs)
    return f"<?xml version='1.0'?><urlset {ns}>{body}</urlset>".encode()


def getter(table):
    """A fake `get`: {address: (status, bytes)}; anything else is a 404."""
    calls = []

    def get(url):
        calls.append(url)
        return table.get(url, (404, b""))
    get.calls = calls
    return get


class AddressTests(unittest.TestCase):

    def test_the_home_address_of_a_property(self):
        self.assertEqual(sc.origin_of("sc-domain:example.test"), "https://example.test")
        self.assertEqual(sc.origin_of("https://www.example.test/blog/"), "https://www.example.test")
        self.assertEqual(sc.origin_of("not a property"), "")
        self.assertEqual(sc.origin_of(""), "")

    def test_an_address_a_request_can_be_made_with(self):
        self.assertEqual(sc.ascii_url("https://example.test" + PERSIAN_PATH), "https://example.test" + ENCODED_PATH)
        self.assertEqual(sc.ascii_url("https://example.test" + ENCODED_PATH), "https://example.test" + ENCODED_PATH)
        self.assertEqual(sc.ascii_url("http://127.0.0.1:8080/a?b=ک#x"), "http://127.0.0.1:8080/a?b=%DA%A9")
        self.assertEqual(sc.ascii_url("https://bücher.test/x"), "https://xn--bcher-kva.test/x")


class ClassifyTests(unittest.TestCase):

    def test_what_a_status_means(self):
        self.assertEqual(Probe(200).kind, sc.OK)
        self.assertEqual(Probe(404).kind, sc.GONE_KIND)
        self.assertEqual(Probe(410).kind, sc.GONE_KIND)
        for code in (301, 302, 307, 308):
            self.assertEqual(Probe(code, "/x").kind, sc.REDIRECT)
        for code in (0, 401, 403, 429, 500, 503, 304):
            self.assertEqual(Probe(code, "/x").kind, sc.UNKNOWN)
        self.assertEqual(Probe(301, "").kind, sc.UNKNOWN)             # a redirect that says nowhere

    def test_a_redirect_to_the_same_page_is_not_a_move(self):
        url = "https://example.test/a"
        self.assertEqual(sc.classify(Probe(301, "/a/"), url), (sc.UNKNOWN, ""))                # a slash
        self.assertEqual(sc.classify(Probe(301, "https://www.example.test/a"), url), (sc.UNKNOWN, ""))   # www
        self.assertEqual(sc.classify(Probe(301, "/b"), url), (sc.REDIRECT, "https://example.test/b"))
        self.assertEqual(sc.classify(Probe(302, "https://other.test/x?y=1"), url),
                         (sc.REDIRECT, "https://other.test/x"))
        self.assertEqual(sc.classify(Probe(404), url), (sc.GONE_KIND, ""))
        self.assertEqual(sc.classify(Probe(200), url), (sc.OK, ""))


class CheckAllTests(unittest.TestCase):

    def test_every_address_is_answered_once(self):
        asked = []
        out = sc.check_all(["a", "b", "a", "c"], lambda u: asked.append(u) or Probe(200, "", u))
        self.assertEqual(sorted(asked), ["a", "b", "c"])
        self.assertEqual({u: p.title for u, p in out.items()}, {"a": "a", "b": "b", "c": "c"})

    def test_when_the_time_is_up_the_rest_is_unknown(self):
        urls = [f"u{i}" for i in range(100)]
        times = iter([0, 0, 10 ** 9, 10 ** 9])
        out = sc.check_all(urls, lambda u: Probe(200), budget_s=60, clock=lambda: next(times))
        self.assertEqual(sum(1 for p in out.values() if p.status == 200), sc.BATCH)
        self.assertEqual(sum(1 for p in out.values() if p.status == 0), 100 - sc.BATCH)


class SitemapTests(unittest.TestCase):

    O = "https://example.test"

    def test_robots_names_an_index_and_a_broken_file_only_costs_its_own_pages(self):
        get = getter({
            f"{self.O}/robots.txt": (200, b"User-agent: *\nSitemap: https://example.test/index.xml\nsitemap: https://example.test/index.xml\n"),
            f"{self.O}/index.xml": (200, xml("index", f"{self.O}/s1.xml.gz", f"{self.O}/s2.xml", f"{self.O}/s3.xml")),
            f"{self.O}/s1.xml.gz": (200, gzip.compress(xml("urls", f"{self.O}/a", f"{self.O}/b?x=1&y=2"))),
            f"{self.O}/s2.xml": (500, b""),
            f"{self.O}/s3.xml": (200, b"this is not xml"),
        })
        urls, ok = sc.read_sitemaps(self.O, get)
        self.assertTrue(ok)
        self.assertEqual(urls, [f"{self.O}/a", f"{self.O}/b?x=1&y=2"])

    def test_without_robots_the_default_address_is_tried(self):
        get = getter({f"{self.O}/sitemap.xml": (200, xml("urls", f"{self.O}/a", f"{self.O}/a", f"{self.O}/b"))})
        self.assertEqual(sc.read_sitemaps(self.O, get), ([f"{self.O}/a", f"{self.O}/b"], True))

    def test_a_sitemap_named_in_robots_that_cannot_be_read_falls_back_to_the_default(self):
        get = getter({f"{self.O}/robots.txt": (200, b"Sitemap: https://example.test/gone.xml"),
                      f"{self.O}/sitemap.xml": (200, xml("urls", f"{self.O}/a"))})
        self.assertEqual(sc.read_sitemaps(self.O, get), ([f"{self.O}/a"], True))

    def test_a_sitemap_that_cannot_be_read_is_not_ok(self):
        self.assertEqual(sc.read_sitemaps(self.O, getter({})), ([], False))
        self.assertEqual(sc.read_sitemaps(self.O, getter({f"{self.O}/sitemap.xml": (200, b"<html></html>")})),
                         ([], False))
        self.assertEqual(sc.read_sitemaps("", getter({})), ([], False))

    def test_a_sitemap_that_lists_itself_is_read_once(self):
        get = getter({f"{self.O}/sitemap.xml": (200, xml("index", f"{self.O}/sitemap.xml", f"{self.O}/c.xml")),
                      f"{self.O}/c.xml": (200, xml("urls", f"{self.O}/a"))})
        self.assertEqual(sc.read_sitemaps(self.O, get), ([f"{self.O}/a"], True))
        self.assertEqual(get.calls.count(f"{self.O}/sitemap.xml"), 1)


class RequestTests(unittest.TestCase):
    """fetch_probe against a server on this computer."""

    @classmethod
    def setUpClass(cls):
        cls.hits, cls.agents = [], []
        outer = cls

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, headers=(), body=b""):
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.hits.append(self.path)
                outer.agents.append(self.headers.get("User-Agent", ""))
                title = "<html><head><title>  A کتاب &amp; more\n</title></head></html>"
                if self.path in ("/ok", ENCODED_PATH):
                    self.reply(200, [("Content-Type", "text/html; charset=utf-8")], title.encode("utf-8"))
                elif self.path == "/gone":
                    self.reply(404)
                elif self.path == "/old":
                    self.reply(410)
                elif self.path == "/moved":
                    self.reply(301, [("Location", "/ok")])
                elif self.path == "/found":
                    self.reply(302, [("Location", f"http://127.0.0.1:{self.server.server_port}/ok")])
                else:
                    self.reply(503)

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.hits.clear()
        self.agents.clear()

    def test_a_page_that_answers_gives_its_title(self):
        p = sc.fetch_probe(self.base + "/ok")
        self.assertEqual((p.status, p.kind), (200, sc.OK))
        self.assertEqual(p.title, "A کتاب & more")

    def test_gone_pages(self):
        self.assertEqual(sc.fetch_probe(self.base + "/gone").kind, sc.GONE_KIND)
        self.assertEqual(sc.fetch_probe(self.base + "/old").status, 410)

    def test_redirects_are_not_followed(self):
        p = sc.fetch_probe(self.base + "/moved")
        self.assertEqual((p.status, p.location, p.kind), (301, "/ok", sc.REDIRECT))
        self.assertEqual(self.hits, ["/moved"])                         # one request, nothing after it
        self.assertEqual(sc.classify(p, self.base + "/moved"), (sc.REDIRECT, self.base + "/ok"))
        q = sc.fetch_probe(self.base + "/found")
        self.assertEqual(q.status, 302)
        self.assertEqual(self.hits, ["/moved", "/found"])

    def test_an_error_or_no_answer_is_unknown(self):
        self.assertEqual(sc.fetch_probe(self.base + "/broken").kind, sc.UNKNOWN)
        self.assertEqual(sc.fetch_probe("http://127.0.0.1:1/x", timeout=2).status, 0)

    def test_a_persian_address_is_sent_encoded_with_a_browser_agent(self):
        p = sc.fetch_probe(self.base + PERSIAN_PATH)
        self.assertEqual(p.status, 200)
        self.assertEqual(self.hits, [ENCODED_PATH])
        self.assertTrue(self.agents[0].startswith("Mozilla/5.0"))


if __name__ == "__main__":
    unittest.main()
