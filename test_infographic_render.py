"""The real rendering of infographics: the font for Persian cards, the size limit, the two sample cards, and putting a file into a
Drive folder. The browser itself is not started here (the workflow does that); a fake renderer stands in."""

import io
import unittest
from unittest import mock

import drive_files
import infographic
from test_history import FakeSheets
from test_own_access import FakeDrive
from test_wp_publish import png


def busy_png(width=1200, height=300) -> bytes:
    """A picture with a lot of colours (noise): a big PNG."""
    import os
    from PIL import Image
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Cards(unittest.TestCase):

    def test_a_persian_card_loads_vazirmatn_and_an_english_one_does_not(self):
        fa = infographic.build_html(dict(infographic.SAMPLES["fa"]), infographic.Brand(language="fa"))
        en = infographic.build_html(dict(infographic.SAMPLES["en"]), infographic.Brand(language="en"))
        self.assertIn(infographic.VAZIRMATN_CSS, fa)
        self.assertNotIn("vazirmatn@", en.lower())
        self.assertIn('dir="rtl"', fa)
        self.assertIn("چگونه یک بذر بکاریم", fa)

    def text_of(self, page):
        import re
        return re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", page, flags=re.S))

    def test_a_persian_card_has_persian_digits_everywhere_and_an_english_card_keeps_its_own(self):
        spec = dict(infographic.SAMPLES["fa"], title="۳ روش (3 ways) 2026", numbers="4 مرحله; 120 تومان",
                    points=["قدم 1", "قدم 2", "قدم 3"])
        for template in ("steps", "takeaways", "facts", "price_table"):
            fa = infographic.build_html(dict(spec, template=template), infographic.Brand(name="نام 7", language="fa"))
            self.assertNotRegex(self.text_of(fa), r"[0-9]", template)
        steps = infographic.build_html(dict(spec, template="steps"), infographic.Brand(language="fa"))
        for digit in "۱۲۳":
            self.assertIn(f'<div class="n">{digit}</div>', steps)
        en = infographic.build_html(dict(infographic.SAMPLES["en"], title="3 ways"), infographic.Brand(language="en"))
        self.assertIn('<div class="n">1</div>', en)
        self.assertIn("3 ways", en)

    def test_the_brand_name_is_shown_once_in_the_footer_only(self):
        for language in ("en", "fa"):
            for logo in ("", "data:image/png;base64,AAAA"):
                page = infographic.build_html(dict(infographic.SAMPLES[language]),
                                              infographic.Brand(name="Zebra Brand", language=language, logo_data_uri=logo))
                self.assertEqual(self.text_of(page).count("Zebra Brand"), 1, (language, bool(logo)))
                self.assertIn('<div class="foot">Zebra Brand</div>', page)
        plain = infographic.build_html(dict(infographic.SAMPLES["en"]), infographic.Brand(name="Zebra Brand"))
        self.assertNotIn('<div class="top">', plain)                 # no logo: no bar at the top

    def test_the_two_samples_can_be_drawn(self):
        for language, spec in infographic.SAMPLES.items():
            self.assertEqual(infographic.valid(spec), "", language)
            self.assertEqual(infographic.template_name(spec["template"]), "steps")

    def test_a_picture_with_fewer_colours_is_smaller_and_stays_a_png_of_the_same_width(self):
        big = busy_png()
        small = infographic.shrink_png(big, colours=16)
        self.assertEqual(infographic.png_size(small)[0], 1200)
        self.assertLess(len(small), len(big))

    def test_render_card_gives_back_what_the_renderer_made_when_it_is_within_the_limit(self):
        class Fake:
            def __init__(self, data):
                self.data, self.calls = data, 0

            def render(self, page, width):
                self.calls += 1
                return self.data
        ok = Fake(png(size=20_000))
        self.assertEqual(infographic.render_card(ok, "<html>"), ok.data)
        self.assertEqual(ok.calls, 1)

    def test_without_pillow_the_picture_is_returned_as_it_is(self):
        with mock.patch.dict("sys.modules", {"PIL": None}):
            self.assertEqual(infographic.shrink_png(b"abc"), b"abc")


class DriveFiles(unittest.TestCase):

    def test_a_file_goes_into_the_folder_and_its_link_is_given(self):
        drive = FakeDrive(FakeSheets())
        folder = drive.files().create(body={"name": "Drafts", "mimeType": "application/vnd.google-apps.folder"}).execute()["id"]
        file_id, link = drive_files.upload_file(drive, folder, "sample-infographic-en.png", png())
        self.assertEqual(link, f"https://drive.google.com/file/d/{file_id}/view")
        self.assertEqual(drive.files_[file_id]["parents"], [folder])
        self.assertEqual(drive.files_[file_id]["content"][:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(drive_files.files_named(drive, folder, "sample-infographic-en.png"), [file_id])
        self.assertEqual(drive_files.files_named(drive, folder, "other.png"), [])


if __name__ == "__main__":
    unittest.main()
