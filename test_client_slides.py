"""The owner's plan chooses the slides of the client's monthly deck (AI Notes slot client_slides). All data invented."""

import io
import re
import unittest

from pptx import Presentation

import ai_notes
import client_data as cd
import client_deck
import client_fixtures as fx
import client_slides
import deck_runner
import test_our_work as tow
import work_items as wi

FULL = ["headline", "scorecard", "climbs", "grew", "new", "trend", "sections", "wins", "pages", "ideas", "next",
        "watch", "focus"]
WITH_WORK = ["headline", "scorecard", "climbs", "grew", "new", "work", "trend", "sections", "wins", "pages", "ideas",
             "next", "watch", "focus"]


def client(work=None):
    return cd.ClientReport(fx.monthly_report(), "en", "Example", work, "")


def titles(prs):
    out = []
    for slide in prs.slides:
        first = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        out.append(first)
    return out


def build(choice="", work=None, log=None):
    data = client_deck.build_monthly(fx.monthly_report(), fx.Cfg("en"), deck_runner.TEMPLATE_DIR, None, log,
                                     work=work, slide_choice=choice)
    return Presentation(io.BytesIO(data))


class ParseTests(unittest.TestCase):

    def test_lines_in_the_asked_format_and_the_ways_an_answer_may_drift(self):
        text = ("headline | The one message of the month\n"
                "- scorecard: Clear numbers\n"
                "3. `wins` - Easy to act on\n"
                "WINS | listed twice, the second is ignored\n"
                "nonsense | not a slide\n"
                "trend – long view\n"
                "\n"
                "focus | Ends with a plan")
        self.assertEqual(client_slides.parse(text, FULL),
                         [("headline", "The one message of the month"), ("scorecard", "Clear numbers"),
                          ("wins", "Easy to act on"), ("trend", "long view"), ("focus", "Ends with a plan")])

    def test_json_is_understood_too(self):
        text = 'Here: {"slides": [{"id": "wins", "reason": "a"}, {"id": "bad", "reason": "b"}, {"id": "headline"}]}'
        self.assertEqual(client_slides.parse(text, FULL), [("wins", "a"), ("headline", "")])
        self.assertEqual(client_slides.parse('["trend", "wins"]', FULL), [("trend", ""), ("wins", "")])

    def test_nothing_usable_gives_nothing(self):
        for text in ("", "I would keep everything.", "x | y", None):
            self.assertEqual(client_slides.parse(text, FULL), [])


class ChooseTests(unittest.TestCase):

    def test_the_order_given_is_the_order_of_the_deck(self):
        logs = []
        order = client_slides.choose("focus | a\nheadline | b\nwins | c", FULL, logs.append)
        self.assertEqual(order, ["focus", "headline", "wins"])
        self.assertIn("3 of 13", logs[0])

    def test_the_work_slides_are_never_dropped_and_stay_where_they_were_in_the_deck(self):
        order = client_slides.choose("headline | a\ngrew | b\ntrend | c\nfocus | d", WITH_WORK + [])
        self.assertEqual(order, ["headline", "grew", "work", "trend", "focus"])      # right after the slide before it
        two = WITH_WORK[:6] + ["work2"] + WITH_WORK[6:]
        order = client_slides.choose("work2 | x\nheadline | a\nwins | b", two)
        self.assertEqual(order, ["work2", "headline", "work", "wins"])
        self.assertIn("work", order)
        self.assertIn("work2", order)
        self.assertEqual(client_slides.choose("work | up front\nheadline | a\nwins | b", WITH_WORK)[0], "work")

    def test_fewer_than_three_usable_ids_gives_the_full_deck(self):
        logs = []
        self.assertEqual(client_slides.choose("headline | a\nwins | b", FULL, logs.append), [])
        self.assertIn("not used", logs[0])
        self.assertEqual(client_slides.choose("", FULL, logs.append), [])


class InputTests(unittest.TestCase):

    def test_the_input_lists_every_slide_with_id_title_and_data_and_says_what_is_kept(self):
        c = client(tow.wi.for_client(tow.all_items(), tow.TODAY))
        slides = c.slides()
        text = client_slides.input_text("Example", "October 2026", "en", slides)
        for sid, payload in slides:
            self.assertRegex(text, rf"(?m)^{sid} \| [^|]+ \| ")
        self.assertIn("work | Our work and its results | ", text)
        self.assertIn("ALWAYS KEPT", text)
        self.assertIn("id | one-line reason", text)
        self.assertNotIn("PulseO", text)
        for line in text.splitlines():
            if re.match(r"^[a-z0-9_]+ \| ", line):
                self.assertLessEqual(len(line.split(" | ", 2)[2]), client_slides.SUMMARY_LENGTH)

    def test_the_slot_belongs_to_the_monthly_deck_only(self):
        self.assertIn(ai_notes.CLIENT_SLIDES, ai_notes.SLOTS["monthly"])
        self.assertNotIn(ai_notes.CLIENT_SLIDES, ai_notes.SLOTS["weekly"])


class DeckTests(unittest.TestCase):

    def test_a_waiting_slot_or_an_unusable_answer_gives_todays_full_deck(self):
        self.assertEqual(len(build("").slides), 14)
        logs = []
        self.assertEqual(len(build("only one | slide", log=logs.append).slides), 14)
        self.assertEqual(len(build("headline | a\nwins | b", log=logs.append).slides), 14)
        self.assertTrue(any("not used" in line for line in logs))

    def test_the_deck_has_the_cover_and_exactly_the_chosen_slides_in_the_given_order(self):
        prs = build("wins | easy\nheadline | message\nscorecard | numbers")
        self.assertEqual(len(prs.slides), 4)
        texts = ["\n".join(t) for t in titles(prs)]
        self.assertIn("Quick wins", texts[1])
        self.assertIn("This month in one line", texts[2])
        self.assertIn("Scorecard", texts[3])
        self.assertNotIn("What grew this month", "\n".join(texts))

    def test_the_work_slide_stays_even_when_it_is_not_in_the_answer(self):
        items = wi.for_client(tow.all_items(), tow.TODAY)
        prs = build("headline | a\nscorecard | b\nwins | c", work=items)
        all_text = "\n".join("\n".join(t) for t in titles(prs))
        self.assertIn("Our work and its results", all_text)
        self.assertEqual(len(prs.slides), 5)                                       # cover, 3 chosen, the work slide
        self.assertIn("Our work", "\n".join(titles(prs)[3]))                        # right after the slide before it

    def test_slide_numbers_in_the_texts_follow_the_deck_that_is_built(self):
        c = client()
        full = dict(c.slides())
        picked = dict(c.slides(only=["focus", "next", "wins"]))
        self.assertEqual(list(picked), ["focus", "next", "wins"])
        self.assertEqual([sid for sid, _ in c.slides()], FULL)                      # asking again gives the full deck

    def test_an_id_that_no_longer_exists_is_ignored(self):
        self.assertEqual([sid for sid, _ in client().slides(only=["wins", "work", "headline"])], ["wins", "headline"])


if __name__ == "__main__":
    unittest.main()
