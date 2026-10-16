"""
Tests for site configuration loading (sites.py): legacy single-site settings,
SITES_JSON, validation, and that error messages never echo the secret.
"""

import json
import unittest

import sites


def site(name="site-1", **kw):
    d = {"name": name, "site_url": f"sc-domain:{name}.test", "sheet_id": f"{name}-main"}
    d.update(kw)
    return d


def dump(*entries):
    return json.dumps(list(entries))


class LoadSitesTests(unittest.TestCase):

    def test_legacy_single_site(self):
        got = sites.load_sites(None, "sc-domain:x.test", "MAIN", "HIST")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0].name, got[0].site_url, got[0].sheet_id,
                          got[0].history_sheet_id, got[0].enabled),
                         ("site-1", "sc-domain:x.test", "MAIN", "HIST", True))

    def test_legacy_without_history(self):
        got = sites.load_sites("", "sc-domain:x.test", "MAIN", None)
        self.assertEqual(got[0].history_sheet_id, "")

    def test_blank_sites_json_falls_back_to_legacy(self):
        got = sites.load_sites("   ", "sc-domain:x.test", "MAIN", None)
        self.assertEqual(got[0].sheet_id, "MAIN")

    def test_nothing_configured(self):
        with self.assertRaises(sites.SitesConfigError):
            sites.load_sites(None, None, None, None)

    def test_sites_json_wins_over_legacy_settings(self):
        got = sites.load_sites(dump(site("site-a")), "sc-domain:old.test", "OLD", "OLDH")
        self.assertEqual([s.name for s in got], ["site-a"])
        self.assertEqual(got[0].sheet_id, "site-a-main")

    def test_command_line_site_wins_over_sites_json(self):
        got = sites.load_sites(dump(site("site-a")), "sc-domain:cli.test", "CLI", None,
                               force_single=True)
        self.assertEqual([s.sheet_id for s in got], ["CLI"])

    def test_two_sites_with_optional_history_and_enabled(self):
        got = sites.parse_sites_json(dump(
            site("site-a", history_sheet_id="a-hist"),
            site("site-b", enabled=False),
            site("client_c", history_sheet_id=""),
        ))
        self.assertEqual([(s.name, s.enabled, s.history_sheet_id) for s in got],
                         [("site-a", True, "a-hist"), ("site-b", False, ""),
                          ("client_c", True, "")])

    def test_disabled_site_may_omit_urls(self):
        got = sites.parse_sites_json(dump(site("site-a"), {"name": "site-b", "enabled": False}))
        self.assertFalse(got[1].enabled)

    def test_sensitive_values_cover_every_id(self):
        s = sites.parse_sites_json(dump(site("site-a", history_sheet_id="a-hist")))[0]
        self.assertEqual(set(s.sensitive_values()), {"sc-domain:site-a.test", "site-a-main", "a-hist"})


    def test_reports_sheet_is_optional_and_counts_as_sensitive(self):
        got = sites.parse_sites_json(dump(site("site-a", reports_sheet_id="a-reports"),
                                          site("site-b")))
        self.assertEqual(got[0].reports_sheet_id, "a-reports")
        self.assertEqual(got[1].reports_sheet_id, "")
        self.assertIn("a-reports", got[0].sensitive_values())

    def test_reports_sheet_must_be_its_own_sheet(self):
        with self.assertRaises(sites.SitesConfigError):
            sites.parse_sites_json(dump(site("site-a", reports_sheet_id="site-a-main")))
        with self.assertRaises(sites.SitesConfigError):
            sites.parse_sites_json(dump(site("site-a", history_sheet_id="x", reports_sheet_id="x")))
        with self.assertRaises(sites.SitesConfigError):
            sites.parse_sites_json(dump(site("site-a", reports_sheet_id="r"),
                                        site("site-b", reports_sheet_id="r")))

class ValidationTests(unittest.TestCase):

    def fails(self, raw, *must_not_contain):
        with self.assertRaises(sites.SitesConfigError) as cm:
            sites.parse_sites_json(raw)
        for text in must_not_contain:
            self.assertNotIn(text, str(cm.exception))
        return str(cm.exception)

    def test_bad_json_does_not_echo_content(self):
        msg = self.fails('[{"name": "site-a", "site_url": "TOP-SECRET-VALUE",', "TOP-SECRET-VALUE")
        self.assertIn("not valid JSON", msg)

    def test_not_a_list(self):
        self.fails('{"name": "site-a"}')

    def test_empty_list(self):
        self.fails("[]")

    def test_entry_not_an_object(self):
        self.fails('["TOP-SECRET-VALUE"]', "TOP-SECRET-VALUE")

    def test_unknown_field_does_not_echo_name_or_value(self):
        self.fails(dump(site("site-a", sitee_url="TOP-SECRET-VALUE")),
                   "TOP-SECRET-VALUE", "sitee_url")

    def test_missing_required_fields_for_enabled_site(self):
        self.fails(dump({"name": "site-a", "sheet_id": "x"}))
        self.fails(dump({"name": "site-a", "site_url": "sc-domain:a.test"}))

    def test_value_of_wrong_type_does_not_echo(self):
        self.fails(dump(site("site-a", sheet_id=12345678)), "12345678")

    def test_enabled_must_be_boolean(self):
        self.fails(dump(site("site-a", enabled="false")))

    def test_name_must_be_a_neutral_label_not_a_domain(self):
        self.fails(dump(site("real-domain.com")), "real-domain.com")
        self.fails(dump(site("a/b")))
        self.fails(dump({"site_url": "sc-domain:a.test", "sheet_id": "x"}))

    def test_duplicate_names(self):
        self.fails(dump(site("site-a"), site("Site-A", sheet_id="other")))

    def test_no_enabled_site(self):
        self.fails(dump(site("site-a", enabled=False)))

    def test_history_must_differ_from_main_sheet(self):
        self.fails(dump(site("site-a", history_sheet_id="site-a-main")))

    def test_nothing_shared_between_sites(self):
        self.fails(dump(site("site-a"), site("site-b", sheet_id="site-a-main")))
        self.fails(dump(site("site-a", history_sheet_id="h"),
                        site("site-b", history_sheet_id="h")))
        self.fails(dump(site("site-a"), site("site-b", history_sheet_id="site-a-main")))



class ReportConfigTests(unittest.TestCase):

    def parse(self, **kw):
        return sites.parse_sites_json(dump(site("site-a", **kw)))[0]

    def fails(self, *secret, **kw):
        with self.assertRaises(sites.SitesConfigError) as cm:
            self.parse(**kw)
        for text in secret:
            self.assertNotIn(text, str(cm.exception))

    def test_defaults_when_nothing_is_configured(self):
        s = self.parse()
        self.assertEqual((s.page_types, s.thresholds, s.weekly_segments), ([], {}, False))
        self.assertEqual(s.threshold("lost_new_min_impressions"), 10)
        self.assertEqual(s.threshold("movers_top_n"), 100)

    def test_page_types_thresholds_and_weekly_flag(self):
        s = self.parse(page_types=[{"name": "English", "regex": "/en/"},
                                   {"name": "Shop", "regex": "^https://shop\.example\.com/"},
                                   {"name": "مقاله", "regex": "/article/"}],
                       thresholds={"lost_new_min_impressions": 25, "movers_top_n": 50},
                       weekly_segments=True)
        self.assertEqual([t["name"] for t in s.page_types], ["English", "Shop", "مقاله"])
        self.assertEqual(s.threshold("lost_new_min_impressions"), 25)
        self.assertEqual(s.threshold("movers_top_n"), 50)
        self.assertEqual(s.threshold("alarm_aio_drop_pct"), 30)            # untouched default
        self.assertTrue(s.weekly_segments)

    def test_regexes_are_sensitive_values(self):
        s = self.parse(page_types=[{"name": "English", "regex": "/en-secret/"}],
                       reports_sheet_id="rep")
        self.assertIn("/en-secret/", s.sensitive_values())
        self.assertIn("rep", s.sensitive_values())

    def test_invalid_regex_is_refused_without_echoing_it(self):
        self.fails("(unclosed-secret", page_types=[{"name": "English", "regex": "(unclosed-secret"}])

    def test_reserved_duplicate_and_malformed_page_types(self):
        for bad in ([{"name": "All", "regex": "/a/"}], [{"name": "other", "regex": "/a/"}],
                    [{"name": "A", "regex": "/a/"}, {"name": "a", "regex": "/b/"}],
                    [{"name": "A"}], [{"name": "A", "regex": ""}], [{"name": "A", "regex": 5}],
                    [{"name": "A", "regex": "/a/", "extra": 1}], ["x"], "x", [{"name": "bad/name", "regex": "/a/"}],
                    [{"name": "T%d" % i, "regex": "/%d/" % i} for i in range(21)]):
            self.fails(page_types=bad)

    def test_unknown_or_bad_thresholds(self):
        self.fails("secret-setting", thresholds={"secret-setting": 1})
        self.fails(thresholds={"movers_top_n": 0})
        self.fails(thresholds={"movers_top_n": 2.5})
        self.fails(thresholds={"lost_new_min_impressions": -1})
        self.fails(thresholds={"lost_new_min_impressions": "10"})
        self.fails(thresholds={"lost_new_min_impressions": True})
        self.fails(thresholds=[1])

    def test_weekly_segments_must_be_boolean(self):
        self.fails(weekly_segments="yes")


if __name__ == "__main__":
    unittest.main()
