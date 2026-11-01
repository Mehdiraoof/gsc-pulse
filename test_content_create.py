"""Tests for content creation: the brief step and the draft step, end to end on the fake test site, with an AI
that is made up here (it follows the answer layout of the prompts). The "done" checklist is tested with the
same verifier that tests the real run (content_accept.py)."""

import re
import unittest
from datetime import date
from unittest import mock

import ai_client
import content
import content_accept as acc
import content_check as cc
import content_create as ccr
import content_docs as cd
import content_sheet as cs
import fake_site as fs
import gdocs
import tabs
from test_history import FakeSheets, patch_time
from test_own_access import FakeDrive

TODAY = date(2026, 11, 2)
OTHER_SITE = "https://other.test/x"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def field(prompt: str, name: str) -> str:
    m = re.search(rf"^{name}: (.*)$", prompt, re.MULTILINE)
    return m.group(1).strip() if m else ""


def listed(prompt: str, header: str) -> list:
    """[(keyword, address)] of the '- keyword: address' lines under a header line."""
    out, on = [], False
    for line in prompt.splitlines():
        if line.startswith(header):
            on = True
            continue
        if on:
            m = re.match(r"^- (.*): (https?://\S+)$", line)
            if not m:
                break
            out.append((m.group(1), m.group(2)))
    return out


def number_in(text: str) -> int:
    m = re.search(r"(?<![\d.,])(\d{1,3})(?![\d.,])\s+\S+", text.translate(_DIGITS))
    return int(m.group(1)) if m else 0


class FakeAI:
    """Answers the three prompts of content creation in the layout they ask for. `first` and `edit` switch on
    the mistakes of the first pass and of the editor pass of a draft."""

    provider = "fake"

    def __init__(self, first=None, edit=None, brief_arabic=False, quota_after=None, fail_for=None):
        self.calls, self.steps, self.prompts = 0, [], []
        self.first, self.edit = first or {}, edit or {}
        self.brief_arabic, self.quota_after, self.fail_for = brief_arabic, quota_after, fail_for

    def generate(self, system, prompt, search=False, **kw):
        if self.quota_after is not None and self.calls >= self.quota_after:
            raise ai_client.QuotaExhausted("the fake AI's limit is reached")
        self.calls += 1
        step = re.search(r"STEP: (\w+)", prompt).group(1)
        self.steps.append(step)
        self.prompts.append(prompt)
        if self.fail_for and self.fail_for in prompt:
            raise ai_client.AiError("the fake AI failed")
        lang = "fa" if "Persian (Farsi)" in prompt else "en"
        if step == "BRIEF":
            return self.brief(prompt, lang)
        return self.draft(prompt, lang, edited=(step == "EDIT"))

    # ---- a brief
    def brief(self, prompt, lang):
        t = fs.T[lang]
        kw = field(prompt, "Keyword")
        links = listed(prompt, "PAGES OF THE SAME SITE")
        update = "Page: update of" in prompt
        rows = "\n".join(f"| this article | {u} | {k} |" for k, u in links[:4])
        parts = {
            1: ("New page or update, guide, goal: help the reader." if lang == "en" else "صفحه جدید، راهنما، هدف کمک به خواننده."),
            2: f"Target: {kw}",
            3: f"Title tag: {kw} | Fake\nMeta description: A short description for {kw}.\nH1: {kw}\nSlug: slug-{self.calls}",
            4: "- H2: " + t["h2_ideas"] + "\n- H3: " + t["h2_compare"] + "\nTables planned: a comparison table of the options",
            5: "Sources: example.org (official), example.com (expert).",
            6: "- Q1\n- Q2\n- Q3\n- Q4",
            7: "| From | To | Anchor text |\n|---|---|---|\n" + rows,
            8: "Word count: 1800\nFormat: headings, lists, one table. Tone: friendly.",
            9: "Keep the intro, rewrite the rest." if update else "Not applicable",
            10: "- [ ] one H1\n- [ ] a table\n- [ ] 3 to 5 links",
        }
        if self.brief_arabic:
            parts[1] = "كتاب علي در 2026 — مهم"
        return "\n".join(f"=== {n} ===\n{text}" for n, text in parts.items())

    # ---- an article
    def md(self, lang, kw, links, *, items=None, table=True, touches=3, second_h1=False, many_links=False,
           arabic=False, latin=False, dash=False, tool=False, images=3) -> str:
        t, h = fs.T[lang], cd.DRAFT_HEADINGS[lang]
        n = number_in(kw)
        count = items if items is not None else (n if n >= 2 else 4)
        lines = [f"# {kw}", "", t["intro"], ""]
        if second_h1:
            lines += [f"# {kw} again", ""]
        for x in t["touches"][:touches]:
            lines += [x, ""]
        lines += [f"## {t['h2_ideas']}", ""] + [f"- {t['items'][i % 10]}" for i in range(count)] + [""]
        if table:
            lines += [f"## {t['h2_compare']}", "", "| " + " | ".join(t["table_head"]) + " |", "|---|---|---|"]
            lines += ["| " + " | ".join(r) + " |" for r in t["table_rows"]] + [""]
        for _ in range(images):
            lines += [t["img"], ""]
        chosen = [(k, u) for k, u in links[:4]]
        if many_links:
            chosen = links[:6] + links[:1] + [("outside", OTHER_SITE)]
        lines += [t["links_lead"] + " " + " ".join(f"[{k}]({u})" for k, u in chosen), ""]
        if arabic:
            lines += ["كتاب علي", ""]
        if latin:
            lines += ["In 2026 prices changed." if lang == "en" else "در سال 2026 قیمت شد.", ""]
        if dash:
            lines += ["A — B – C", ""]
        if tool:
            lines += ["Written with AI.", ""]
        lines += [f"## {h['faq']}", "", f"### {t['q']}", "", t["a"], "", f"## {h['summary']}", "", t["summary_text"]]
        return "\n".join(lines)

    def draft(self, prompt, lang, edited):
        kw = field(prompt, "Keyword") or getattr(self, "keyword", "")
        self.keyword = kw
        links = listed(prompt, "ALLOWED ADDRESSES")
        flags = dict(self.edit if edited else self.first)
        if edited and "Add a Markdown table" in prompt:
            flags.pop("table", None)
        if edited and "Include 2 to 4 human touch markers" in prompt:
            flags.pop("touches", None)
        if edited and "contains a number" in prompt:
            flags.pop("items", None)
        article = self.md(lang, kw, links, **flags)
        meta = (f"=== META ===\nMeta title: {kw} | Fake\nMeta description: About {kw}\nSlug: draft-{self.calls}\n"
                f"Article type: Guide\nKeywords: {kw}\nWord count: 1500\n")
        out = meta + "=== ARTICLE ===\n" + article + "\n"
        if edited:
            t = fs.T[lang]
            verify = "برای بررسی:\n- هر قیمت" if lang == "fa" else "To verify:\n- every price"
            out += "=== QUALITY ===\n" + "\n".join(f"- {c}" for c in t["checks"]) + "\n" + verify + "\n"
        return out


class Base(unittest.TestCase):

    def setUp(self):
        patch_time(self)
        self.sheets = FakeSheets()
        self.drive = FakeDrive(self.sheets)
        self.world = fs.build(self.drive, self.sheets, lambda s: None)
        self.page = lambda url: (200, b"<html><body><h1>Old page</h1><p>Old text of the page.</p></body></html>")

    def cfg(self, name, **kw):
        w = self.world.sites[name]
        base = dict(label=w.site.label, language=w.site.language, domain=w.site.domain,
                    tabs=["Articles", "Landing Pages"], sop_doc_id=w.sop_id, briefs_folder_id=w.briefs_id,
                    drafts_folder_id=w.drafts_id, extra_rules=w.site.extra_rules)
        base.update(kw)
        return ccr.CreateConfig(**base)

    def run_site(self, name, ai=None, mode="both", **kw):
        return ccr.run(self.drive, self.sheets, self.world.sites[name].dashboard_id, self.cfg(name, **kw),
                       ai or FakeAI(), mode, TODAY, http_get=self.page, log=lambda s: None)

    def run_all(self, ai=None, mode="both", **kw):
        ai = ai or FakeAI()
        results, emails = {}, []
        for name in self.world.sites:
            results[name] = self.run_site(name, ai, mode, **kw)
            mail = ccr.email_text(self.world.sites[name].site.label, results[name])
            if mail:
                emails.append((name, *mail))
        return results, emails

    def rows(self, name, tab="Articles"):
        return cs.table_of(cs.read_all(self.sheets, self.world.sites[name].dashboard_id, tab))[1]

    def row(self, name, keyword):
        return next(r for r in self.rows(name) if r["Primary Keyword"].split() == keyword.split())


class ChecklistTests(Base):
    """The whole 'done' list, on the three fake sites, the way the real test run does it."""

    def test_the_checklist_passes_and_a_second_run_changes_nothing(self):
        before = acc.snapshot(self.sheets, self.world)
        ai = FakeAI()
        results, emails = self.run_all(ai)
        after = acc.snapshot(self.sheets, self.world)
        report = acc.verify(self.drive, self.world, before, after, results, emails, TODAY)
        self.assertEqual(report.text(), "all checks passed")
        docs_after_first, calls = acc.doc_sets(self.drive, self.world), ai.calls
        again, emails2 = self.run_all(ai)
        self.assertEqual(ai.calls, calls)                                          # nothing to do, nothing asked
        repeat = acc.verify_repeat(self.drive, self.world, docs_after_first, after,
                                   acc.snapshot(self.sheets, self.world), again, emails2)
        self.assertTrue(repeat.ok, repeat.text())
        self.assertEqual(emails2, [])                                              # no email when nothing was done

    def test_what_each_site_made(self):
        results, emails = self.run_all()
        self.assertEqual({n: (len(r.briefs), len(r.drafts)) for n, r in results.items()},
                         {"fake-fa": (10, 1), "fake-en": (5, 1), "fake-nosop": (3, 0)})
        self.assertEqual({n: len(r.no_brief) for n, r in results.items()}, {"fake-fa": 1, "fake-en": 1, "fake-nosop": 0})
        self.assertEqual([e[0] for e in emails], ["fake-fa", "fake-en", "fake-nosop"])      # one email per site
        for r in results.values():
            self.assertEqual((r.waiting, r.stopped), (0, ""))

    def test_the_request_count(self):
        ai = FakeAI()
        self.run_all(ai)
        self.assertEqual(ai.steps.count("BRIEF"), 18)
        self.assertEqual((ai.steps.count("DRAFT"), ai.steps.count("EDIT")), (2, 2))         # two passes per draft

    def test_briefs_only_and_drafts_only(self):
        ai = FakeAI()
        results, _ = self.run_all(ai, mode="briefs")
        self.assertEqual(set(ai.steps), {"BRIEF"})
        self.assertTrue(all(not r.drafts for r in results.values()))
        ai2 = FakeAI()
        results, _ = self.run_all(ai2, mode="drafts")
        self.assertEqual(set(ai2.steps), {"DRAFT", "EDIT"})
        self.assertEqual([len(r.drafts) for r in results.values()], [1, 1, 0])


class RowTests(Base):

    def setUp(self):
        super().setUp()
        self.results, self.emails = self.run_all()

    def test_a_new_page_brief(self):
        r = self.row("fake-fa", "بهترین شامپو برای موهای چرب")
        self.assertEqual((r["Status"], r["To Do"], r["Briefed Date"]), ("Briefed", "Write", "2026-11-02"))
        ids = gdocs.doc_ids_in(r["Attachments"])
        self.assertEqual(len(ids), 1)
        self.assertIn(ids[0], gdocs.docs_in(self.drive, self.world.sites["fake-fa"].briefs_id))
        self.assertTrue(r["Meta Title"] and r["Slug"] and r["Outlines"] and r["Related URLs"])
        self.assertEqual(str(r["Words Count (Suggested)"]), "1800")
        self.assertTrue(all(u.startswith("https://fa.fake-site.test/") for u in r["Related URLs"].split("\n")))

    def test_an_update_brief_keeps_the_notes_and_the_cells_already_filled(self):
        r = self.row("fake-fa", "مقایسه رنگ مو")
        self.assertEqual((r["Status"], r["To Do"]), ("Briefed", "Update"))
        self.assertEqual(r["Notes/Checklist"], "نسخه قبلی سال ۱۴۰۲ نوشته شده است.")        # a brief adds no note
        filled = self.row("fake-fa", "بهترین کرم مرطوب‌کننده")
        self.assertEqual(filled["Meta Title"], "کرم مرطوب‌کننده مناسب هر پوست")              # not overwritten
        self.assertEqual(filled["Slug"], "/article/کرم-مرطوب-کننده")
        self.assertTrue(filled["Outlines"] and filled["Words Count (Suggested)"])         # the empty ones were filled

    def test_a_draft_with_a_brief(self):
        r = self.row("fake-fa", "راهنمای مراقبت از پوست خشک")
        self.assertEqual((r["Status"], r["To Do"]), ("Drafted", "Review"))
        ids = gdocs.doc_ids_in(r["Attachments"])
        self.assertEqual(len(ids), 2)                                                      # the brief and the draft
        self.assertIn(ids[1], gdocs.docs_in(self.drive, self.world.sites["fake-fa"].drafts_id))
        self.assertTrue(r["Notes/Checklist"].startswith("Draft ready"))

    def test_a_draft_without_a_brief_is_skipped_with_one_note(self):
        r = self.row("fake-fa", "چای سبز و لاغری")
        self.assertEqual((r["Status"], r["To Do"], r["Attachments"], r["Notes/Checklist"]),
                         ("Inboxed", "Draft", "", ccr.NOTE_NO_BRIEF))
        self.run_all()
        self.assertEqual(self.row("fake-fa", "چای سبز و لاغری")["Notes/Checklist"], ccr.NOTE_NO_BRIEF)   # not twice

    def test_an_already_briefed_row_is_not_touched(self):
        r = self.row("fake-fa", "روتین مراقبت شبانه")
        self.assertEqual((r["Status"], r["To Do"], r["Briefed Date"]), ("Briefed", "Brief", "2026-10-20"))
        self.assertEqual(len(gdocs.doc_ids_in(r["Attachments"])), 1)

    def test_the_site_without_an_sop_made_briefs_and_no_drafts(self):
        w = self.world.sites["fake-nosop"]
        self.assertEqual(gdocs.docs_in(self.drive, w.drafts_id), {})
        r = self.row("fake-nosop", "مراقبت از گیاهان آپارتمانی")
        self.assertEqual((r["Status"], r["To Do"], r["Notes/Checklist"]), ("Briefed", "Draft", ""))      # left as it was

    def test_the_documents(self):
        brief = gdocs.doc_ids_in(self.row("fake-en", "10 ideas for a small kitchen")["Attachments"])[0]
        html = gdocs.export_html(self.drive, brief)
        doc = cc.Doc(html)
        self.assertEqual(len(doc.headings(1)), 1)
        self.assertEqual([b.text for b in doc.headings(2)], cd.BRIEF_HEADINGS["en"][:8] + cd.BRIEF_HEADINGS["en"][9:])
        draft = gdocs.doc_ids_in(self.row("fake-en", "how to start a vegetable garden")["Attachments"])[1]
        d = cc.Doc(gdocs.export_html(self.drive, draft))
        self.assertEqual(len(d.headings(1)), 1)
        self.assertGreaterEqual(d.tables, 1)

    def test_the_email_has_the_links(self):
        subject, body = self.emails[0][1], self.emails[0][2]
        self.assertTrue(subject.startswith("New briefs and drafts"))
        for kw, link in self.results["fake-fa"].briefs + self.results["fake-fa"].drafts:
            self.assertIn(link, body)
        self.assertIn("rows set to Draft have no brief yet", body)
        self.assertNotIn("PulseO", subject + body)


class ChangedCellsTests(Base):

    def test_only_the_allowed_columns_can_be_written(self):
        item = ccr.load_items(self.sheets, self.world.sites["fake-en"].dashboard_id, ["Articles"])[0]
        with self.assertRaises(ValueError):
            ccr.write_row(self.sheets, self.world.sites["fake-en"].dashboard_id, item, {"Score": 99})
        with self.assertRaises(ValueError):
            ccr.write_row(self.sheets, self.world.sites["fake-en"].dashboard_id, item, {"Primary Keyword": "x"})

    def test_no_other_cell_changes_and_no_row_moves(self):
        before = acc.snapshot(self.sheets, self.world)
        results, _ = self.run_all()
        after = acc.snapshot(self.sheets, self.world)
        for name in self.world.sites:
            for tab in before[name]:
                failures = cc.diff_dashboard(before[name][tab], after[name][tab], results[name].processed_keys)
                self.assertEqual(failures, [], (name, tab))

    def put(self, name, sheet_row, column, value):
        """Writes one cell of the Articles tab of a fake site, the way an owner would."""
        w = self.world.sites[name]
        header = ccr.load_items(self.sheets, w.dashboard_id, ["Articles"])[0].header
        tabs.patch_cells(self.sheets, w.dashboard_id, [(f"'Articles'!{cs.letters(cs.col_of(header, column))}{sheet_row}", [[value]])])

    def test_the_checker_would_notice_a_stray_change(self):
        before = acc.snapshot(self.sheets, self.world)
        results, _ = self.run_all()
        self.put("fake-en", 10, "Score", 1)                         # a row the run did not work on
        after = acc.snapshot(self.sheets, self.world)
        failures = cc.diff_dashboard(before["fake-en"]["Articles"], after["fake-en"]["Articles"], results["fake-en"].processed_keys)
        self.assertEqual({f.code for f in failures}, {"other-row-changed"})

    def test_the_brief_cells_are_appended_not_replaced(self):
        self.put("fake-en", 2, "Attachments", "https://docs.google.com/spreadsheets/d/old/edit")
        self.put("fake-en", 2, "Notes/Checklist", "my note")
        self.run_site("fake-en", mode="briefs")
        r = self.row("fake-en", "best moisturizer for oily skin")
        self.assertTrue(r["Attachments"].startswith("https://docs.google.com/spreadsheets/d/old/edit\nhttps://docs.google.com/document/d/"))
        self.assertEqual(r["Notes/Checklist"], "my note")


class LimitTests(Base):

    def test_a_limit_stops_cleanly_and_leaves_the_rest_for_next_time(self):
        before = acc.snapshot(self.sheets, self.world)
        ai = FakeAI(quota_after=3)
        results, emails = self.run_all(ai, mode="briefs")
        r = results["fake-fa"]
        self.assertEqual((len(r.briefs), r.waiting, bool(r.stopped)), (3, 7, True))
        after = acc.snapshot(self.sheets, self.world)
        report = acc.verify(self.drive, self.world, before, after, results, emails, TODAY, "briefs")
        report.add(acc.verify_limited(emails, results, after, before).failures)
        self.assertTrue(report.ok, report.text())
        mail = next(b for n, s, b in emails if n == "fake-fa")
        self.assertIn("stopped early", mail)
        self.assertIn("7 rows wait for the next run", mail)
        # the next run, with the AI back, does the rest and never repeats the first three
        docs = set(gdocs.docs_in(self.drive, self.world.sites["fake-fa"].briefs_id))
        later = FakeAI()
        results2, _ = self.run_all(later, mode="briefs")
        self.assertEqual(len(results2["fake-fa"].briefs), 7)
        self.assertEqual(len(gdocs.docs_in(self.drive, self.world.sites["fake-fa"].briefs_id)), len(docs) + 7)

    def test_the_runs_own_limit_of_requests(self):
        results, _ = self.run_all(mode="briefs", max_requests=2)
        self.assertEqual({n: len(r.briefs) for n, r in results.items()}, {"fake-fa": 2, "fake-en": 2, "fake-nosop": 2})
        self.assertTrue(all("own limit" in r.stopped for r in results.values()))

    def test_the_days_limit_counts_earlier_runs(self):
        self.run_site("fake-en", mode="briefs", daily_requests=3, max_requests=2)          # 2 requests today
        r = self.run_site("fake-en", mode="briefs", daily_requests=3, max_requests=5)
        self.assertEqual(len(r.briefs), 1)                                                  # only 1 left of 3
        self.assertIn("day", r.stopped)

    def test_a_row_whose_draft_cannot_be_finished_is_left_alone(self):
        ai = FakeAI(quota_after=1)                       # the first pass answers, the editor pass hits the limit
        r = self.run_site("fake-en", ai, mode="drafts")
        self.assertEqual(r.drafts, [])
        row = self.row("fake-en", "how to start a vegetable garden")
        self.assertEqual((row["Status"], row["To Do"]), ("Briefed", "Draft"))
        self.assertEqual(gdocs.docs_in(self.drive, self.world.sites["fake-en"].drafts_id), {})

    def test_the_ai_failing_three_times_in_a_row_stops_the_run(self):
        ai = FakeAI(fail_for="Keyword:")
        r = self.run_site("fake-fa", ai, mode="briefs")
        self.assertEqual(r.briefs, [])
        self.assertIn("three times", r.stopped)
        self.assertEqual(ai.calls, 3)

    def test_one_failure_skips_only_that_row(self):
        ai = FakeAI(fail_for="Keyword: how to wash a wool sweater")
        r = self.run_site("fake-en", ai, mode="briefs")
        self.assertEqual(len(r.briefs), 4)
        self.assertTrue(any("could not be written" in p for p in r.problems))
        self.assertEqual(self.row("fake-en", "how to wash a wool sweater")["Status"], "Inboxed")


class SettingsTests(Base):

    def test_a_site_that_stops_at_the_brief(self):
        ai = FakeAI()
        results, _ = self.run_all(ai, ai_drafts=False)
        self.assertEqual(set(ai.steps), {"BRIEF"})
        self.assertTrue(all(not r.drafts and not r.no_brief for r in results.values()))
        for name, kw in (("fake-fa", "چای سبز و لاغری"), ("fake-fa", "راهنمای مراقبت از پوست خشک"), ("fake-en", "indoor plants for low light")):
            r = self.row(name, kw)
            self.assertEqual(r["To Do"], "Draft")                                          # a Draft row is left untouched
            self.assertEqual(r["Notes/Checklist"], "")
        self.assertEqual(gdocs.docs_in(self.drive, self.world.sites["fake-fa"].drafts_id), {})

    def test_the_language_of_the_site_decides_the_language_of_the_prompts_output(self):
        ai = FakeAI()
        self.run_site("fake-en", ai, mode="briefs")
        self.run_site("fake-fa", ai, mode="briefs")
        self.assertTrue(any("Write the brief in English" in p for p in ai.prompts))
        self.assertTrue(any("Write the brief in Persian (Farsi)" in p for p in ai.prompts))

    def test_the_prompts_are_in_english_and_carry_the_rules(self):
        ai = FakeAI()
        self.run_site("fake-fa", ai, mode="both")
        brief = next(p for p in ai.prompts if p.startswith("STEP: BRIEF"))
        self.assertIn("Research in English", brief)
        self.assertIn("Persian sources only for local facts", brief)
        self.assertIn("ONE short Persian search", brief)
        draft = next(p for p in ai.prompts if p.startswith("STEP: DRAFT"))
        self.assertIn("[تجربه شخصی: ...] and [نظر متخصص: ...]", draft)
        self.assertIn("[needs checking]", draft)
        self.assertIn("Never invent a quote", draft)
        self.assertIn(self.world.sites["fake-fa"].site.extra_rules, brief)                 # the site's own rules

    def test_the_sop_goes_into_the_prompts_and_the_page_text_for_an_update(self):
        ai = FakeAI()
        self.run_site("fake-fa", ai, mode="briefs")
        update = next(p for p in ai.prompts if "Page: update of" in p)
        self.assertIn("Old text of the page.", update)
        self.assertIn("پاسخ مستقیم در ابتدا", update)

    def test_crash_recovery_reuses_a_doc_that_is_already_there(self):
        w = self.world.sites["fake-en"]
        existing = gdocs.create_doc(self.drive, "Brief · best moisturizer for oily skin", cd.wrap("en", "<h1>x</h1>"), w.briefs_id)
        ai = FakeAI()
        r = self.run_site("fake-en", ai, mode="briefs")
        self.assertEqual(len(r.briefs), 5)
        self.assertEqual(ai.calls, 4)                                                       # none for the row that had its doc
        row = self.row("fake-en", "best moisturizer for oily skin")
        self.assertIn(existing, gdocs.doc_ids_in(row["Attachments"]))
        self.assertEqual(len(gdocs.docs_in(self.drive, w.briefs_id)), 2 + 5)                # the 2 from the start + 5, no double


class RepairTests(Base):
    """Mistakes of the AI that code (or the editor pass) must put right: the checklist still passes."""

    def finished(self, ai, name="fake-fa"):
        before = acc.snapshot(self.sheets, self.world)
        r = self.run_site(name, ai, mode="drafts")
        after = acc.snapshot(self.sheets, self.world)
        self.assertEqual(len(r.drafts), 1)
        report = cc.Report()
        for tab in before[name]:
            report.add(cc.diff_dashboard(before[name][tab], after[name][tab], r.processed_keys))
        link = r.drafts[0][1]
        w = self.world.sites[name]
        brief = gdocs.doc_ids_in(self.row(name, r.drafts[0][0])["Attachments"])[0]
        report.add(cc.check_draft(gdocs.export_html(self.drive, gdocs.doc_ids_in(link)[0]), language=w.site.language,
                                  domain=w.site.domain, has_comparison=ccr.tables_planned(ccr.read_text(self.drive, brief))))
        return r, report

    def test_arabic_letters_latin_digits_and_dashes_are_fixed_in_code(self):
        ai = FakeAI(first=dict(arabic=True, latin=True, dash=True), edit=dict(arabic=True, latin=True, dash=True))
        r, report = self.finished(ai)
        self.assertTrue(report.ok, report.text())
        self.assertEqual(r.problems, [])

    def test_wrong_links_are_repaired(self):
        for name in ("fake-fa", "fake-en"):
            r, report = self.finished(FakeAI(first=dict(many_links=True), edit=dict(many_links=True)), name)
            self.assertTrue(report.ok, (name, report.text()))

    def test_two_h1_are_made_one(self):
        r, report = self.finished(FakeAI(first=dict(second_h1=True), edit=dict(second_h1=True)))
        self.assertTrue(report.ok, report.text())

    def test_the_editor_pass_is_told_what_is_missing_and_fixes_it(self):
        w = self.world.sites["fake-fa"]                     # a brief that plans a table
        gdocs.replace_doc(self.drive, w.brief_ids["راهنمای مراقبت از پوست خشک"],
                          cd.wrap("fa", "<h1>x</h1><p>Tables planned: a price table</p>"))
        ai = FakeAI(first=dict(table=False, touches=0))
        r, report = self.finished(ai)
        edit = next(p for p in ai.prompts if p.startswith("STEP: EDIT"))
        for needed in ("Add a Markdown table", "Include 2 to 4 human touch markers"):
            self.assertIn(needed, edit)
        self.assertTrue(report.ok, report.text())
        self.assertEqual(r.problems, [])

    def test_a_problem_nobody_fixes_is_saved_and_noted(self):
        ai = FakeAI(first=dict(tool=True), edit=dict(tool=True))
        r, report = self.finished(ai)
        self.assertIn("tool-name", report.text())
        self.assertTrue(any("still fails" in p for p in r.problems))
        row = self.row("fake-fa", "راهنمای مراقبت از پوست خشک")
        self.assertIn("automatic checks still fail (tool-name)", row["Notes/Checklist"])
        self.assertEqual((row["Status"], row["To Do"]), ("Drafted", "Review"))

    def test_arabic_letters_in_a_brief_are_fixed_too(self):
        r = self.run_site("fake-fa", FakeAI(brief_arabic=True), mode="briefs")
        link = r.briefs[0][1]
        html = gdocs.export_html(self.drive, gdocs.doc_ids_in(link)[0])
        self.assertEqual(cc.check_brief(html), [])
        self.assertEqual(cc.check_persian(cc.Doc(html)), [])

    def test_a_persian_doc_whose_font_google_dropped_is_written_again(self):
        real = gdocs.export_html
        seen = {}

        def flaky(drive, doc_id):
            html = real(drive, doc_id)
            seen[doc_id] = seen.get(doc_id, 0) + 1
            return html.replace("Vazirmatn", "Arial") if seen[doc_id] == 1 else html
        with mock.patch.object(gdocs, "export_html", flaky):
            r = self.run_site("fake-fa", FakeAI(), mode="briefs", max_briefs=2)
        self.assertEqual(r.font_rewrites, 2)
        for kw, link in r.briefs:
            self.assertTrue(gdocs.font_kept(real(self.drive, gdocs.doc_ids_in(link)[0])))


class EmailTests(unittest.TestCase):

    def test_nothing_done_no_email(self):
        self.assertIsNone(ccr.email_text("Site", ccr.CreateResult()))

    def test_the_text(self):
        r = ccr.CreateResult(briefs=[("kw1", "https://docs.google.com/document/d/a/edit")],
                             drafts=[("kw2", "https://docs.google.com/document/d/b/edit")],
                             no_brief=["kw3"], waiting=4, stopped="a request limit of the AI is reached",
                             problems=["a draft still fails 1 automatic checks"])
        subject, body = ccr.email_text("Site", r)
        self.assertEqual(subject, "New briefs and drafts · Site")
        for needle in ("New briefs (1):", "- kw1: https://docs.google.com/document/d/a/edit", "New drafts (1):",
                       "1 rows set to Draft have no brief yet", "The run stopped early", "4 rows wait", "Please look at"):
            self.assertIn(needle, body)
        only_limit = ccr.email_text("Site", ccr.CreateResult(stopped="limit", waiting=2))
        self.assertEqual(only_limit[0], "Briefs and drafts · Site")
        for banned in ("PulseO", "Claude", "AI "):
            self.assertNotIn(banned, subject + body)


class PromptHelpersTests(unittest.TestCase):

    def test_tables_planned(self):
        self.assertTrue(ccr.tables_planned("x\nTables planned: a price table\ny"))
        for none in ("Tables planned: none", "Tables planned: None.", "tables planned: no", "Tables planned: n/a", ""):
            self.assertFalse(ccr.tables_planned(none), none)

    def test_headings_are_fixed(self):
        md = "# A\n\n# B\n\n### Frequently asked questions\n\n### Summary\n\n## Other"
        out = ccr.fix_headings(md, "T", "en")
        self.assertEqual([ln for ln in out.splitlines() if ln.startswith("#")],
                         ["# A", "## B", "## Frequently asked questions", "## Summary", "## Other"])
        self.assertTrue(ccr.fix_headings("text", "Title", "en").startswith("# Title\n\n"))

    def test_the_quality_section_the_model_wrote_is_cut(self):
        self.assertEqual(ccr.cut_quality("# A\n\ntext\n\n## Quality check\n\n- x"), "# A\n\ntext")
        self.assertEqual(ccr.cut_quality("# A\n\ntext"), "# A\n\ntext")

    def test_at_most_four_touch_markers(self):
        md = " ".join(f"[Expert quote: {i}]" for i in range(6))
        out = ccr.limit_touches(md, "en")
        self.assertEqual(len(cc.HUMAN_TOUCH["en"].findall(out)), 4)

    def test_link_candidates_prefer_the_same_topic(self):
        items = [ccr.Item("A", 2, {"Primary Keyword": "blue shoes", "Published URL": "https://s.test/article/blue-shoes"}, []),
                 ccr.Item("A", 3, {"Primary Keyword": "red hats", "Published URL": "https://s.test/article/red-hats"}, []),
                 ccr.Item("A", 4, {"Primary Keyword": "buy blue shoes online", "Published URL": ""}, [])]
        out = ccr.link_candidates(items, items[2])
        self.assertEqual([u for _, u in out], ["https://s.test/article/blue-shoes", "https://s.test/article/red-hats"])


if __name__ == "__main__":
    unittest.main()
