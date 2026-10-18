"""
Tests for v2.3: sites that run with the owner's own Google account (`"auth": "me"`).
Search Console, Sheets, Drive and SMTP are in-memory fakes; the OAuth refresh is replaced, so no
credentials or network are needed.
"""

import contextlib
import io
import json
import os
import re
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

from google.auth.exceptions import RefreshError

import auth
import gsc_pulse
import mailer
import page_discovery
import provision
import sites
from test_history import FakeSheets, Req, http_error, patch_time
from test_reports import FakeSA, build_records

CLIENT_ID, CLIENT_SECRET, REFRESH = "cid-123.apps.test", "csecret-456", "rtoken-789"
OAUTH = json.dumps({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH})
OWNER, SMTP_PASSWORD = "owner@example.test", "smtp-password-xyz"
SMTP_ENV = {"SMTP_HOST": "smtp.test", "SMTP_PORT": "587", "SMTP_USER": OWNER,
            "SMTP_PASSWORD": SMTP_PASSWORD}

S1 = {"name": "site-1", "site_url": "sc-domain:alpha.test", "sheet_id": "S1-MAIN",
      "history_sheet_id": "S1-HIST", "reports_sheet_id": "S1-REPORTS"}
S2_URL = "sc-domain:kalameh.test"
S2 = {"name": "site-2", "site_url": S2_URL, "auth": "me", "deck": {"enabled": False}}


class FakeDrive:
    """Just enough of Drive v3: files().list(q=...) and files().create(); a created
    spreadsheet also becomes a book of the fake Sheets."""

    def __init__(self, sheets):
        self.sheets, self.files_, self.n = sheets, {}, 0

    def files(self):
        return self

    def list(self, q, spaces=None, fields=None, pageSize=None, orderBy=None):
        def run():
            name = re.search(r"name = '((?:[^'\\]|\\.)*)'", q).group(1)
            name = name.replace("\\'", "'").replace("\\\\", "\\")
            mime = re.search(r"mimeType = '([^']+)'", q).group(1)
            parent = re.search(r"'([^']+)' in parents", q)
            return {"files": [
                {"id": i, "name": f["name"]} for i, f in self.files_.items()
                if f["name"] == name and f["mimeType"] == mime and not f["trashed"]
                and (not parent or parent.group(1) in f["parents"])]}
        return Req(run)

    def create(self, body, fields=None):
        def run():
            self.n += 1
            fid = f"DRIVE-{self.n}"
            self.files_[fid] = {"name": body["name"], "mimeType": body["mimeType"],
                                "parents": body.get("parents", []), "trashed": False}
            if body["mimeType"] == provision.SHEET:
                self.sheets.add_book(fid)
            return {"id": fid}
        return Req(run)

    def id_of(self, name):
        return next(i for i, f in self.files_.items() if f["name"] == name)

    def names(self, mime):
        return sorted(f["name"] for f in self.files_.values() if f["mimeType"] == mime)


class MeGsc(FakeSA):
    """Search Console as the owner: the data of FakeSA plus sites().list()."""

    def __init__(self, records, entries):
        super().__init__(records)
        self.entries, self.site_urls = entries, []

    def query(self, siteUrl, body):
        self.site_urls.append(siteUrl)
        return super().query(siteUrl, body)

    def sites(self):
        return self

    def list(self):
        return Req(lambda: {"siteEntry": self.entries})


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def owner_entry(url=S2_URL, level="siteOwner"):
    return {"siteUrl": url, "permissionLevel": level}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

class ConfigTests(unittest.TestCase):

    def parse(self, *entries):
        return sites.parse_sites_json(json.dumps(list(entries)))

    def test_auth_defaults_to_the_robot(self):
        self.assertEqual(self.parse(S1)[0].auth, "robot")

    def test_a_me_site_needs_no_sheet_ids(self):
        site = self.parse(S1, S2)[1]
        self.assertEqual((site.auth, site.sheet_id, site.history_sheet_id, site.reports_sheet_id),
                         ("me", "", "", ""))

    def test_a_robot_site_still_needs_its_sheet(self):
        with self.assertRaises(sites.SitesConfigError):
            self.parse({"name": "site-1", "site_url": "sc-domain:alpha.test"})
        with self.assertRaises(sites.SitesConfigError):
            self.parse({"name": "site-1", "site_url": "sc-domain:alpha.test", "auth": "robot"})

    def test_auth_must_be_robot_or_me_and_the_message_does_not_echo_it(self):
        with self.assertRaises(sites.SitesConfigError) as cm:
            self.parse(dict(S2, auth="secret-mode"))
        self.assertNotIn("secret-mode", str(cm.exception))

    def test_empty_recipients_mean_the_owner_only(self):
        site = self.parse(dict(S2, deck={"enabled": True, "site_label": "Kalameh", "recipients": []}))[0]
        self.assertEqual(site.deck.recipients, ["env:SMTP_USER"])
        self.assertEqual(mailer.resolve_recipients(site.deck.recipients, {"SMTP_USER": OWNER}), [OWNER])
        self.assertEqual(site.deck.sensitive_values(), ["Kalameh"])           # the address stays out

    def test_explicit_recipients_are_still_supported(self):
        site = self.parse(dict(S2, deck={"enabled": True, "site_label": "K",
                                         "recipients": ["a@b.test"]}))[0]
        self.assertEqual(site.deck.recipients, ["a@b.test"])


# --------------------------------------------------------------------------- #
# auth.py
# --------------------------------------------------------------------------- #

class AuthTests(unittest.TestCase):

    def test_exactly_two_permissions_are_asked_for(self):
        creds = auth.me_credentials({"GSC_OAUTH_JSON": OAUTH})
        self.assertEqual(sorted(creds.scopes), sorted([
            "https://www.googleapis.com/auth/webmasters.readonly",
            "https://www.googleapis.com/auth/drive.file"]))
        self.assertEqual(creds.token_uri, "https://oauth2.googleapis.com/token")
        self.assertEqual((creds.client_id, creds.refresh_token), (CLIENT_ID, REFRESH))

    def test_a_missing_or_broken_secret_is_explained_without_echoing_it(self):
        for value in (None, "", "not json " + REFRESH, json.dumps({"client_id": CLIENT_ID}),
                      json.dumps([CLIENT_SECRET])):
            with self.assertRaises(auth.AuthError) as cm:
                auth.me_credentials({"GSC_OAUTH_JSON": value} if value is not None else {})
            for secret in (CLIENT_ID, CLIENT_SECRET, REFRESH):
                self.assertNotIn(secret, str(cm.exception))
            self.assertIn("GSC_OAUTH_JSON", str(cm.exception))

    def test_secret_values_are_listed_for_the_scrubber(self):
        self.assertEqual(sorted(auth.secret_values({"GSC_OAUTH_JSON": OAUTH})),
                         sorted([CLIENT_ID, CLIENT_SECRET, REFRESH]))
        self.assertEqual(auth.secret_values({}), [])
        self.assertEqual(auth.secret_values({"GSC_OAUTH_JSON": "{broken"}), [])

    def test_a_revoked_token_is_access_lost_and_other_refusals_are_not(self):
        creds = auth.me_credentials({"GSC_OAUTH_JSON": OAUTH})
        with mock.patch.object(auth.Credentials, "refresh",
                               side_effect=RefreshError("invalid_grant: Token has been revoked")):
            with self.assertRaises(auth.AccessLost) as cm:
                auth.check_access(creds)
        self.assertNotIn(REFRESH, str(cm.exception))
        with mock.patch.object(auth.Credentials, "refresh",
                               side_effect=RefreshError("invalid_client: Unauthorized")):
            with self.assertRaises(auth.AuthError) as cm:
                auth.check_access(creds)
        self.assertNotIsInstance(cm.exception, auth.AccessLost)
        with mock.patch.object(auth.Credentials, "refresh", side_effect=OSError("down")):
            with self.assertRaises(auth.AuthError):
                auth.check_access(creds)

    def test_the_property_must_be_listed_with_more_than_unverified_user(self):
        gsc = MeGsc([], [owner_entry("sc-domain:other.test"), owner_entry(S2_URL, "siteFullUser")])
        self.assertTrue(auth.access_to_property(gsc, S2_URL))
        self.assertFalse(auth.access_to_property(gsc, "sc-domain:missing.test"))
        gsc.entries = [owner_entry(S2_URL, "siteUnverifiedUser")]
        self.assertFalse(auth.access_to_property(gsc, S2_URL))
        gsc.entries = [owner_entry("https://" + S2_URL.split(":")[1] + "/")]        # not the exact name
        self.assertFalse(auth.access_to_property(gsc, S2_URL))
        gsc.entries = []
        self.assertFalse(auth.access_to_property(gsc, S2_URL))


# --------------------------------------------------------------------------- #
# provision.py
# --------------------------------------------------------------------------- #

class ProvisionTests(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.sheets = FakeSheets()
        self.drive = FakeDrive(self.sheets)
        self.mails = []
        self.notify = lambda subject, body: self.mails.append((subject, body))

    def site(self, label="Kalameh", **kw):
        deck = sites.DeckConfig(enabled=bool(label), site_label=label)
        return sites.Site(name="site-2", site_url=S2_URL, auth="me", deck=deck, **kw)

    def index_rows(self):
        index = self.sheets.books[self.drive.id_of(provision.INDEX_NAME)]
        return index[provision.INDEX_TAB].data

    def test_first_run_makes_the_folders_the_index_and_three_sheets(self):
        site = self.site()
        made = provision.provision_site(self.drive, self.sheets, site, self.notify)
        self.assertEqual(made.created, 3)
        self.assertEqual(self.drive.names(provision.FOLDER), ["Kalameh", "PulseO"])
        self.assertEqual(self.drive.names(provision.SHEET), [
            "Kalameh · PulseO Data", "Kalameh · PulseO History", "Kalameh · PulseO Reports",
            "PulseO · Index"])
        folders = {f["name"]: i for i, f in self.drive.files_.items() if f["mimeType"] == provision.FOLDER}
        by_name = {f["name"]: f for f in self.drive.files_.values()}
        self.assertEqual(by_name["Kalameh"]["parents"], [folders["PulseO"]])
        self.assertEqual(by_name["PulseO · Index"]["parents"], [folders["PulseO"]])
        self.assertEqual(by_name["Kalameh · PulseO Data"]["parents"], [folders["Kalameh"]])
        self.assertTrue(site.sheet_id and site.history_sheet_id and site.reports_sheet_id)
        self.assertEqual(len({site.sheet_id, site.history_sheet_id, site.reports_sheet_id}), 3)

    def test_the_index_remembers_the_ids(self):
        site = self.site()
        provision.provision_site(self.drive, self.sheets, site, self.notify)
        rows = self.index_rows()
        self.assertEqual(rows[0], provision.INDEX_HEADER)
        self.assertEqual(rows[1][:4], ["site-2", site.sheet_id, site.history_sheet_id, site.reports_sheet_id])
        self.assertTrue(rows[1][4])                                            # created_at
        self.assertTrue(rows[1][5])                                            # emailed_at
        self.assertEqual(len(rows), 2)

    def test_the_second_run_reuses_everything(self):
        first = self.site()
        provision.provision_site(self.drive, self.sheets, first, self.notify)
        files = dict(self.drive.files_)
        second = self.site()
        made = provision.provision_site(self.drive, self.sheets, second, self.notify)
        self.assertEqual(made.created, 0)
        self.assertEqual(self.drive.files_, files)
        self.assertEqual((second.sheet_id, second.history_sheet_id, second.reports_sheet_id),
                         (first.sheet_id, first.history_sheet_id, first.reports_sheet_id))
        self.assertEqual(len(self.mails), 1)                                   # told only once
        self.assertEqual(len(self.index_rows()), 2)

    def test_the_sheets_ready_email_has_the_links_and_is_built_once(self):
        site = self.site()
        provision.provision_site(self.drive, self.sheets, site, self.notify)
        (subject, body), = self.mails
        self.assertEqual(subject, "PulseO · sheets ready · Kalameh")
        for sheet_id in (site.sheet_id, site.history_sheet_id, site.reports_sheet_id,
                         self.drive.id_of(provision.INDEX_NAME)):
            self.assertIn(f"https://docs.google.com/spreadsheets/d/{sheet_id}", body)

    def test_a_failed_email_is_tried_again_on_the_next_run(self):
        def broken(subject, body):
            raise mailer.MailError("down")
        logs = []
        provision.provision_site(self.drive, self.sheets, self.site(), broken, logs.append)
        self.assertEqual(self.index_rows()[1][5], "")
        self.assertTrue(any("could not send" in l for l in logs))
        provision.provision_site(self.drive, self.sheets, self.site(), self.notify)
        self.assertEqual(len(self.mails), 1)
        self.assertTrue(self.index_rows()[1][5])

    def test_without_a_label_the_neutral_name_is_used(self):
        provision.provision_site(self.drive, self.sheets, self.site(label=""), self.notify)
        self.assertIn("site-2 · PulseO Reports", self.drive.names(provision.SHEET))

    def test_ids_in_the_configuration_win_over_the_index(self):
        first = self.site()
        provision.provision_site(self.drive, self.sheets, first, self.notify)
        self.sheets.add_book("MINE-HISTORY")
        mine = "MINE-HISTORY"
        second = self.site(history_sheet_id=mine)
        made = provision.provision_site(self.drive, self.sheets, second, self.notify)
        self.assertEqual(second.history_sheet_id, mine)
        self.assertEqual(second.sheet_id, first.sheet_id)
        self.assertEqual(made.created, 0)

    def test_only_the_missing_sheets_are_created(self):
        site = self.site(sheet_id="GIVEN-DATA")
        made = provision.provision_site(self.drive, self.sheets, site, self.notify)
        self.assertEqual(made.created, 2)
        self.assertEqual(site.sheet_id, "GIVEN-DATA")
        self.assertNotIn("Kalameh · PulseO Data", self.drive.names(provision.SHEET))

    def test_all_three_ids_in_the_configuration_need_no_drive_at_all(self):
        site = self.site(sheet_id="A", history_sheet_id="B", reports_sheet_id="C")
        made = provision.provision_site(self.drive, self.sheets, site, self.notify)
        self.assertEqual((made.created, self.drive.files_, self.mails), (0, {}, []))

    def test_two_sites_share_one_index(self):
        provision.provision_site(self.drive, self.sheets, self.site(), self.notify)
        other = sites.Site(name="site-3", site_url="https://x.test/", auth="me",
                           deck=sites.DeckConfig(enabled=True, site_label="Dubai Telescope"))
        provision.provision_site(self.drive, self.sheets, other, self.notify)
        self.assertEqual(self.drive.names(provision.SHEET).count(provision.INDEX_NAME), 1)
        self.assertEqual([r[0] for r in self.index_rows()[1:]], ["site-2", "site-3"])
        self.assertEqual(len(self.mails), 2)
        self.assertEqual(self.drive.names(provision.FOLDER).count("PulseO"), 1)

    def test_a_quote_in_a_name_cannot_break_the_search(self):
        site = self.site(label="Kal'ameh")
        provision.provision_site(self.drive, self.sheets, site, self.notify)
        again = self.site(label="Kal'ameh")
        self.assertEqual(provision.provision_site(self.drive, self.sheets, again, self.notify).created, 0)


# --------------------------------------------------------------------------- #
# The whole run
# --------------------------------------------------------------------------- #

class MainTests(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        today = date.today()
        self.records = build_records(start=today - timedelta(days=520), end=today)
        self.robot = MeGsc(self.records, [])                       # the robot never lists sites
        self.me = MeGsc(self.records, [owner_entry()])
        self.sheets = FakeSheets()
        for book in ("S1-MAIN", "S1-HIST", "S1-REPORTS"):
            self.sheets.add_book(book)
        self.drive = FakeDrive(self.sheets)
        self.refresh = None
        FakeSMTP.sent = []

    def build(self, name, version, credentials=None):
        mine = isinstance(credentials, auth.Credentials)
        if name == "webmasters":
            return self.me if mine else self.robot
        return self.drive if name == "drive" else self.sheets

    def run_main(self, entries=(S1, S2), env=None, oauth=OAUTH, args=()):
        keys = ("SITES_JSON", "SITE_URL", "SHEET_ID", "HISTORY_SHEET_ID", "DAYS", "GITHUB_ACTIONS",
                "DISCOVER_SITE", "REBUILD_VIEWS", "REBUILD_ALARMS", "REBUILD_DECKS", "GSC_OAUTH_JSON",
                "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD")
        clean = {k: v for k, v in os.environ.items() if k not in keys}
        clean.update({"SITES_JSON": json.dumps(list(entries)), "GITHUB_ACTIONS": "true",
                      **SMTP_ENV, **(env or {})})
        if oauth is not None:
            clean["GSC_OAUTH_JSON"] = oauth
        out, error = io.StringIO(), None
        refresh = self.refresh or (lambda creds, request: None)
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(sys, "argv", ["gsc_pulse.py", "--config", "none.yaml", *args]), \
             mock.patch.object(gsc_pulse, "build", self.build), \
             mock.patch.object(gsc_pulse, "get_credentials", lambda f: object()), \
             mock.patch.object(gsc_pulse, "PUBLIC_LOGS", True), \
             mock.patch.object(gsc_pulse, "_SENSITIVE", []), \
             mock.patch.object(auth.Credentials, "refresh", refresh), \
             mock.patch("smtplib.SMTP", FakeSMTP), \
             contextlib.redirect_stdout(out):
            try:
                gsc_pulse.main()
            except (Exception, SystemExit) as e:        # noqa: BLE001
                error = e
        return out.getvalue(), error

    def summary_line(self, out, name):
        return [l for l in out[out.index("Summary"):].splitlines() if l.strip().startswith(name)][0]

    def subjects(self):
        return [m["Subject"] for m in FakeSMTP.sent]

    def book(self, name):
        return self.sheets.books[self.drive.id_of(name)]

    # ---- the happy path -----------------------------------------------------------------
    def test_each_site_is_read_with_its_own_auth(self):
        out, err = self.run_main()
        self.assertIsNone(err, out)
        self.assertIn("site-1: ok", self.summary_line(out, "site-1"))
        self.assertIn("site-2: ok", self.summary_line(out, "site-2"))
        self.assertEqual(set(self.robot.site_urls), {"sc-domain:alpha.test"})
        self.assertEqual(set(self.me.site_urls), {S2_URL})

    def test_the_robot_site_is_unaffected(self):
        out, err = self.run_main()
        self.assertIsNone(err, out)
        self.assertIn("Dashboard", self.sheets.books["S1-REPORTS"])
        self.assertGreater(len(self.sheets.books["S1-MAIN"]["Queries"].data), 1)
        self.assertEqual(self.subjects().count("PulseO · sheets ready · site-2"), 1)
        self.assertFalse(any("site-1" in s for s in self.subjects()))

    def test_a_site_with_its_own_access_gets_all_its_sheets_and_a_full_sync(self):
        out, err = self.run_main()
        self.assertIsNone(err, out)
        self.assertIn("site-2: 3 sheets created", out)
        data, hist = self.book("site-2 · PulseO Data"), self.book("site-2 · PulseO History")
        reports_book = self.book("site-2 · PulseO Reports")
        self.assertGreater(len(data["Queries"].data), 1)
        self.assertGreater(len(hist["H Daily"].data), 1)
        self.assertGreater(len(reports_book["R Monthly"].data), 1)
        self.assertIn("Dashboard", reports_book)
        self.assertNotIn("Sheet1", reports_book)
        line = self.summary_line(out, "site-2")
        self.assertIn("views: built", line)

    def test_the_second_run_creates_nothing_and_sends_nothing(self):
        self.run_main()
        files, sent = dict(self.drive.files_), len(FakeSMTP.sent)
        out, err = self.run_main()
        self.assertIsNone(err, out)
        self.assertEqual(self.drive.files_, files)
        self.assertIn("site-2: sheets from the Index", out)
        self.assertEqual(len(FakeSMTP.sent), sent)

    def test_weekly_deck_of_a_site_with_empty_recipients_goes_to_the_owner(self):
        site = dict(S2, deck={"enabled": True, "language": "en", "site_label": "Kalameh",
                              "recipients": [], "weekly": True, "monthly": False})
        out, err = self.run_main((S1, site))
        self.assertIsNone(err, out)
        decks = [m for m in FakeSMTP.sent if "Weekly" in m["Subject"]]
        self.assertEqual(len(decks), 1)
        self.assertEqual(decks[0]["To"], OWNER)
        self.assertIn("Kalameh · PulseO Data", self.drive.names(provision.SHEET))

    # ---- nothing secret in the log -------------------------------------------------------
    def test_no_credential_and_no_link_reaches_the_log(self):
        out, err = self.run_main()
        self.assertIsNone(err, out)
        for secret in (CLIENT_ID, CLIENT_SECRET, REFRESH, SMTP_PASSWORD, OWNER, "kalameh", "alpha.test",
                       "DRIVE-", "docs.google.com", "S1-MAIN", "S1-REPORTS"):
            self.assertNotIn(secret, out)

    def test_credentials_are_scrubbed_even_from_an_unexpected_error(self):
        self.me.query = lambda siteUrl, body: (_ for _ in ()).throw(
            RuntimeError(f"boom {REFRESH} {CLIENT_SECRET} {self.drive.n}"))
        out, err = self.run_main()
        self.assertIn("site-2: failed", out)
        self.assertNotIn(REFRESH, out)
        self.assertNotIn(CLIENT_SECRET, out)

    # ---- losing access -------------------------------------------------------------------
    def test_a_revoked_token_skips_the_me_sites_but_not_the_robot_ones(self):
        def revoked(creds, request):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")
        self.refresh = revoked
        out, err = self.run_main()
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("site-1: ok", self.summary_line(out, "site-1"))
        self.assertIn("site-2: failed", self.summary_line(out, "site-2"))
        self.assertEqual(self.drive.files_, {})                               # nothing created
        self.assertEqual(self.me.site_urls, [])
        self.assertEqual(self.subjects(), [gsc_pulse.LOST_ACCESS_SUBJECT])    # one email, no more
        self.assertIn("re-authorize", FakeSMTP.sent[0].get_content())
        self.assertNotIn(REFRESH, out)

    def test_several_me_sites_still_send_one_email(self):
        def revoked(creds, request):
            raise RefreshError("invalid_grant")
        self.refresh = revoked
        third = dict(S2, name="site-3", site_url="https://other.test/")
        out, err = self.run_main((S1, S2, third))
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertEqual(self.subjects(), [gsc_pulse.LOST_ACCESS_SUBJECT])

    def test_a_missing_secret_fails_only_the_me_sites(self):
        out, err = self.run_main(oauth=None)
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("GSC_OAUTH_JSON", out)
        self.assertIn("site-1: ok", self.summary_line(out, "site-1"))
        self.assertIn("site-2: failed", self.summary_line(out, "site-2"))

    def test_a_property_without_access_is_skipped_and_the_owner_is_told(self):
        for entries in ([], [owner_entry(level="siteUnverifiedUser")], [owner_entry("sc-domain:x.test")]):
            FakeSMTP.sent = []
            self.me.entries, self.me.site_urls = entries, []
            self.drive.files_.clear()
            out, err = self.run_main()
            self.assertIsInstance(err, gsc_pulse.GscPulseError)
            self.assertIn('site-2: failed (GscPulseError: no Search Console access with "me" auth)', out)
            self.assertNotIn("kalameh", out)
            self.assertIn("site-1: ok", self.summary_line(out, "site-1"))
            self.assertEqual(self.me.site_urls, [])
            self.assertEqual(self.drive.files_, {})                           # no sheets for a site we cannot read
            self.assertEqual(self.subjects(), ["PulseO · no Search Console access"])
            self.assertIn(S2_URL, FakeSMTP.sent[0].get_content())

    def test_the_property_list_is_never_logged(self):
        self.me.entries = [owner_entry(), owner_entry("sc-domain:private-client.test")]
        out, err = self.run_main()
        self.assertNotIn("private-client", out)

    def test_a_sheet_from_the_configuration_that_pulseo_did_not_create_is_refused(self):
        real_get = self.sheets.get

        def get(spreadsheetId, fields=None):
            if spreadsheetId == "FOREIGN-SHEET":
                def refuse():
                    raise http_error(404, "Requested entity was not found. FOREIGN-SHEET")
                return Req(refuse)
            return real_get(spreadsheetId, fields)

        with mock.patch.object(self.sheets, "get", get):
            out, err = self.run_main((S1, dict(S2, sheet_id="FOREIGN-SHEET")))
        self.assertIsInstance(err, gsc_pulse.GscPulseError)
        self.assertIn("was not created by PulseO", out)
        self.assertNotIn("FOREIGN-SHEET", out)
        self.assertIn("site-1: ok", self.summary_line(out, "site-1"))

    # ---- discovery -----------------------------------------------------------------------
    def test_page_type_discovery_works_for_a_me_site(self):
        out, err = self.run_main(env={"DISCOVER_SITE": "site-2"})
        self.assertIsNone(err, out)
        reports_book = self.book("site-2 · PulseO Reports")
        self.assertIn(page_discovery.TAB_NAME, reports_book)
        self.assertEqual(set(self.robot.site_urls), set())                    # the robot was not used
        self.assertNotIn("Queries", self.book("site-2 · PulseO Data"))   # nothing else ran

    def test_discovery_for_a_me_site_with_no_access_stops(self):
        self.me.entries = []
        out, err = self.run_main(env={"DISCOVER_SITE": "site-2"})
        self.assertIsInstance(err, gsc_pulse.GscPulseError)


if __name__ == "__main__":
    unittest.main()
