"""The Bing AI export folder: month and kind of a file from its name, the file formats, the folder next to the site's other
sheets and its sharing with the service account, and reading it as the service account. Files, folders and accounts are
fakes; every address is invented."""

import io
import json
import os
import unittest
import zipfile
from datetime import date
from types import SimpleNamespace
from unittest import mock

import bing_ai
import gsc_pulse
import provision
from test_history import FakeSheets, Req, patch_time
from test_own_access import FakeDrive

PAGES = "URL,Citations\nhttps://example.test/a,12\nhttps://example.test/b,3\n"


def make_xlsx(shared, rows_xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                   + rows_xml + "</sheetData></worksheet>")
    return buf.getvalue()


class Names(unittest.TestCase):

    def test_the_month_comes_from_the_name(self):
        modified = date(2026, 11, 5)
        for name, month in (("2026-10 cited pages.csv", "2026-10"), ("bing_ai_2026_09_pages.csv", "2026-09"),
                            ("2026.10.csv", "2026-10"), ("Oct 2026 queries.csv", "2026-10"), ("october-2026.csv", "2026-10"),
                            ("2026 Sep export.csv", "2026-09"), ("export-2026-1.csv", "2026-01")):
            self.assertEqual(bing_ai.month_of(name, modified), (month, False), name)

    def test_a_name_without_a_month_means_the_month_before_the_change(self):
        self.assertEqual(bing_ai.month_of("export.csv", date(2026, 11, 5)), ("2026-10", True))
        self.assertEqual(bing_ai.month_of("export.csv", date(2026, 1, 2)), ("2025-12", True))
        self.assertEqual(bing_ai.month_of("report 2026-13.csv", date(2026, 3, 2)), ("2026-02", True))      # not a month

    def test_the_kind_comes_from_the_name_or_the_content(self):
        pages = [["Item", "Citations"], ["https://example.test/a", 1]]
        queries = [["Item", "Citations"], ["how to plant", 1]]
        self.assertEqual(bing_ai.kind_of("grounding queries 2026-10.csv", pages), "Queries")
        self.assertEqual(bing_ai.kind_of("cited pages 2026-10.csv", queries), "Pages")
        self.assertEqual(bing_ai.kind_of("export.csv", pages), "Pages")
        self.assertEqual(bing_ai.kind_of("export.csv", queries), "Queries")


class Formats(unittest.TestCase):

    def test_csv_with_commas_semicolons_tabs_and_a_byte_order_mark(self):
        want = [["URL", "Citations"], ["https://example.test/a", "12"]]
        self.assertEqual(bing_ai.csv_rows(b"URL,Citations\nhttps://example.test/a,12\n"), want)
        self.assertEqual(bing_ai.csv_rows(b"URL;Citations\r\nhttps://example.test/a;12\r\n"), want)
        self.assertEqual(bing_ai.csv_rows(b"URL\tCitations\nhttps://example.test/a\t12\n"), want)
        self.assertEqual(bing_ai.csv_rows(b"\xef\xbb\xbfURL,Citations\nhttps://example.test/a,12\n"), want)
        self.assertEqual(bing_ai.csv_rows("URL\tCitations\nhttps://example.test/a\t12\n".encode("utf-16")), want)
        self.assertEqual(bing_ai.csv_rows(b'"a, b",3\n')[0], ["a, b", "3"])

    def test_a_quoted_query_with_a_persian_word(self):
        self.assertEqual(bing_ai.csv_rows("Query,Citations\nباغ گیاه,4\n".encode("utf-8-sig"))[1], ["باغ گیاه", "4"])

    def test_xlsx_shared_inline_and_number_cells(self):
        raw = make_xlsx(["URL", "Citations", "https://example.test/a"],
                        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>12</v></c></row>'
                        '<row r="3"><c r="A3" t="inlineStr"><is><t>https://example.test/b</t></is></c><c r="C3"><v>5</v></c></row>')
        self.assertEqual(bing_ai.xlsx_rows(raw), [["URL", "Citations"], ["https://example.test/a", "12"],
                                                  ["https://example.test/b", "", "5"]])


class Drive(FakeDrive):
    """FakeDrive with the sharing calls of Drive."""

    def __init__(self, sheets):
        super().__init__(sheets)
        self.shared, self.fail_share = [], False

    def permissions(self):
        outer = self

        class P:
            def list(self, fileId, fields=None):
                return Req(lambda: {"permissions": [{"emailAddress": e} for f, e in outer.shared if f == fileId]})

            def create(self, fileId, body, sendNotificationEmail=None, fields=None):
                def run():
                    if outer.fail_share:
                        raise RuntimeError("no")
                    assert sendNotificationEmail is False
                    outer.shared.append((fileId, body["emailAddress"]))
                    return {"id": "P1"}
                return Req(run)
        return P()


def make_site(**over):
    site = SimpleNamespace(name="site-x", sheet_id="", drive_folder_id="", auth="me",
                           deck=SimpleNamespace(site_label="Example"), content=SimpleNamespace(sheet_id=""))
    site.__dict__.update(over)
    return site


class Folder(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.drive = Drive(self.fs)

    def test_the_folder_is_made_next_to_the_sites_other_files_and_found_again(self):
        site = make_site()
        first, created = bing_ai.folder_for(self.drive, site, "Example")
        again, created_again = bing_ai.folder_for(self.drive, site, "Example")
        self.assertTrue(created)
        self.assertEqual((again, created_again), (first, False))
        self.assertEqual(self.drive.names(provision.FOLDER), ["Bing AI export", "Example", "PulseO"])
        self.assertEqual(self.drive.files_[first]["parents"], [self.drive.id_of("Example")])

    def test_asking_without_making(self):
        self.assertEqual(bing_ai.folder_for(self.drive, make_site(), "Example", create=False), ("", False))
        self.assertEqual(self.drive.names(provision.FOLDER), ["Example", "PulseO"])       # the site's folder is the parent

    def test_the_folder_is_shared_once_read_only_with_the_service_account(self):
        folder, _ = bing_ai.folder_for(self.drive, make_site(), "Example")
        self.assertTrue(bing_ai.share_with(self.drive, folder, "robot@example.test"))
        self.assertTrue(bing_ai.share_with(self.drive, folder, "Robot@Example.test"))
        self.assertEqual(self.drive.shared, [(folder, "robot@example.test")])

    def test_a_sharing_that_fails_is_a_false_not_an_error(self):
        self.drive.fail_share = True
        self.assertFalse(bing_ai.share_with(self.drive, "F", "robot@example.test"))
        self.assertFalse(bing_ai.share_with(self.drive, "", "robot@example.test"))
        self.assertFalse(bing_ai.share_with(self.drive, "F", ""))


class ReaderDrive:
    """The service account's view of Drive: the files of one folder and their bytes."""

    def __init__(self, files, contents):
        self.files_, self.contents, self.asked = files, contents, []

    def files(self):
        return self

    def list(self, q, fields=None, pageSize=None, orderBy=None):
        self.asked.append(q)
        return Req(lambda: {"files": self.files_})

    def get_media(self, fileId):
        def run():
            if isinstance(self.contents[fileId], Exception):
                raise self.contents[fileId]
            return self.contents[fileId]
        return Req(run)


def listed(fid, name, mime="text/csv", modified="2026-11-03T08:00:00.000Z", size=100):
    return {"id": fid, "name": name, "mimeType": mime, "modifiedTime": modified, "size": str(size)}


class Reading(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.logs = []

    def read(self, files, contents):
        return bing_ai.read_folder(ReaderDrive(files, contents), self.fs, "FOLDER", self.logs.append)

    def test_csv_xlsx_and_a_google_sheet_are_read(self):
        self.fs.add_book("GS")
        self.fs.write_cells(self.fs.books["GS"]["Sheet1"], 1, 1, [["Grounding query", "Citations"], ["how to plant", 7]])
        xlsx = make_xlsx(["URL", "Citations", "https://example.test/c"],
                              '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                              '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>2</v></c></row>')
        got = self.read(
            [listed("1", "2026-10 pages.csv"), listed("2", "2026-09 pages.xlsx", "application/octet-stream"),
             listed("GS", "2026-10 queries", bing_ai.GOOGLE_SHEET)],
            {"1": PAGES.encode(), "2": xlsx})
        by = {e.name: e for e in got}
        self.assertEqual(by["2026-10 pages.csv"].values[1], ["https://example.test/a", "12"])
        self.assertEqual(by["2026-09 pages.xlsx"].values[1], ["https://example.test/c", "2"])
        self.assertEqual(by["2026-10 queries"].values[1], ["how to plant", "7"])
        self.assertEqual(by["2026-10 pages.csv"].modified, date(2026, 11, 3))

    def test_only_the_folder_is_asked_for(self):
        drive = ReaderDrive([], {})
        bing_ai.read_folder(drive, self.fs, "FOLDER-1", self.logs.append)
        self.assertEqual(drive.asked, ["'FOLDER-1' in parents and trashed = false"])
        self.assertEqual(bing_ai.read_folder(drive, self.fs, "", self.logs.append), [])
        self.assertEqual(len(drive.asked), 1)

    def test_a_file_that_is_not_a_table_or_too_big_is_counted_not_named(self):
        got = self.read([listed("1", "2026-10 pages.csv"), listed("2", "photo.png", "image/png"),
                         listed("3", "huge.csv", size=bing_ai.MAX_BYTES + 1), listed("4", "gone.csv")],
                        {"1": PAGES.encode(), "2": b"\x89PNG....", "3": b"", "4": OSError("x")})
        self.assertEqual([e.name for e in got], ["2026-10 pages.csv"])
        self.assertEqual(self.logs, ["  bing AI: 3 of 4 files in the folder could not be read as a table"])


class Wiring(unittest.TestCase):
    """gsc_pulse.bing_export_reader: the folder, its sharing, the service account that reads it."""

    EMAIL = "robot@example.test"

    def setUp(self):
        patch_time(self)
        self.fs = FakeSheets()
        self.drive = Drive(self.fs)
        self.run = SimpleNamespace(site=make_site(), drive=self.drive, name="site-x")
        self.before = list(gsc_pulse._SENSITIVE)
        self.addCleanup(lambda: gsc_pulse._SENSITIVE.__setitem__(slice(None), self.before))

    def reader(self, env, built=None):
        built = built if built is not None else []
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(gsc_pulse.service_account.Credentials, "from_service_account_info", return_value="CREDS"), \
                mock.patch.object(gsc_pulse, "build", side_effect=lambda api, version, credentials: built.append(api) or api):
            return gsc_pulse.bing_export_reader(self.run)

    def test_the_folder_is_made_shared_and_the_account_and_folder_are_kept_out_of_logs(self):
        built = []
        where, read = self.reader({"GSC_SERVICE_ACCOUNT_JSON": json.dumps({"client_email": self.EMAIL})}, built)
        folder = self.drive.id_of("Bing AI export")
        self.assertEqual(where, f"https://drive.google.com/drive/folders/{folder}")
        self.assertEqual(self.drive.shared, [(folder, self.EMAIL)])
        self.assertEqual(built, ["drive", "sheets"])
        self.assertTrue(callable(read))
        self.assertIn(self.EMAIL, gsc_pulse._SENSITIVE)
        self.assertIn(folder, gsc_pulse._SENSITIVE)

    def test_without_a_service_account_the_folder_is_made_and_nothing_is_read(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GSC_SERVICE_ACCOUNT_JSON", None)
            where, read = gsc_pulse.bing_export_reader(self.run)
        self.assertIn("not read: no service account", where)
        self.assertIsNone(read)
        self.assertEqual(self.drive.names(provision.FOLDER).count("Bing AI export"), 1)

    def test_a_site_with_the_service_account_only_has_no_folder(self):
        self.run.site = make_site(auth="robot")
        self.run.drive = None
        self.assertEqual(gsc_pulse.bing_export_reader(self.run), ("", None))

    def test_a_sharing_that_fails_is_said_in_the_log(self):
        self.drive.fail_share = True
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.reader({"GSC_SERVICE_ACCOUNT_JSON": json.dumps({"client_email": self.EMAIL})})
        self.assertIn("could not be shared", out.getvalue())
        self.assertNotIn(self.EMAIL, out.getvalue())


if __name__ == "__main__":
    unittest.main()
