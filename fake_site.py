"""
PulseO: a fake site to test content creation on (invented data, step 1 of the work order)
=========================================================================================
Content creation changes real dashboards and writes real documents, so it is tried first on a world
that is made up. `build()` creates it in the owner's Drive, inside a folder called "PulseO Test":

    PulseO Test /
        PulseO Test · Index            (which file is which: the only place ids are written)
        Samples /                      (documents that are right and documents that are wrong, to prove
                                        the checker finds every kind of mistake)
        Fake Persian Site /            Persian, has an SOP
            [Fake Persian Site] Content Dashboard, SOP, Briefs /, Drafts /
        Fake English Site /            English, has an SOP
        Fake No SOP Site /             Persian, no SOP: briefs but no drafts

Everything in it is invented: the sites do not exist (their addresses end in .test), and the keywords
are made up, with the awkward cases of real Persian text on purpose: Arabic ي and ك, a half space,
a tatweel, Persian and Arabic digits, English words and Latin digits inside Persian, extra spaces, a
number in the title, two addresses that differ only by a half space, percent-encoded addresses.

Nothing here touches a real site's files. The module only talks to Drive and Sheets through the clients
it is given, so the tests run it against in-memory fakes.
"""

import io
import re
from dataclasses import dataclass, field

from googleapiclient.http import MediaIoBaseUpload

import content
import content_check as cc
import content_sheet as cs
import history
import provision
import sites
import tabs

TEST_ROOT = "PulseO Test"
INDEX_NAME = "PulseO Test · Index"
INDEX_TAB = "Sites"
INDEX_HEADER = ["name", "label", "language", "dashboard_id", "sop_id", "briefs_id", "drafts_id", "folder_id"]
SAMPLES = "Samples"
DOC = "application/vnd.google-apps.document"
ZWNJ, TATWEEL = "‌", "ـ"
ARABIC_KAF, ARABIC_YEH = "ك", "ي"

# --------------------------------------------------------------------------- #
# Documents in Drive
# --------------------------------------------------------------------------- #


def doc_link(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def create_doc(drive, name: str, html: str, parent: str) -> str:
    """A Google Doc made from HTML (Drive converts it); `drive.file` is enough to do this."""
    media = MediaIoBaseUpload(io.BytesIO(html.encode("utf-8")), mimetype="text/html", resumable=False)
    return drive.files().create(body={"name": name, "mimeType": DOC, "parents": [parent]},
                                media_body=media, fields="id").execute(num_retries=provision.RETRIES)["id"]


def ensure_doc(drive, name: str, html: str, parent: str) -> str:
    return provision.find(drive, name, DOC, parent) or create_doc(drive, name, html, parent)


def export_html(drive, doc_id: str) -> str:
    data = drive.files().export(fileId=doc_id, mimeType="text/html").execute(num_retries=provision.RETRIES)
    return data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)


def docs_in(drive, folder_id: str) -> dict:
    """{doc id: name} of the documents in a folder."""
    resp = drive.files().list(q=f"'{folder_id}' in parents and trashed = false", spaces="drive",
                              fields="files(id,name,mimeType)", pageSize=200).execute(num_retries=provision.RETRIES)
    return {f["id"]: f["name"] for f in resp.get("files", []) if f.get("mimeType", DOC) == DOC}


# --------------------------------------------------------------------------- #
# The three fake sites
# --------------------------------------------------------------------------- #


@dataclass
class FakeSite:
    name: str
    label: str
    language: str            # "fa" or "en"
    domain: str
    sop: bool
    extra_rules: str = ""


FAKE_SITES = (
    FakeSite("fake-fa", "Fake Persian Site", "fa", "https://fa.fake-site.test", True,
             "Never recommend a brand by name without a reason."),
    FakeSite("fake-en", "Fake English Site", "en", "https://en.fake-site.test", True, ""),
    FakeSite("fake-nosop", "Fake No SOP Site", "fa", "https://nosop.fake-site.test", False, ""),
)

TABS = [sites.ContentTab("Articles", r"^https://[^/]+/article/.+", description="Fake articles."),
        sites.ContentTab("Landing Pages", r"^https://[^/]+/[^/?#.]+$", description="Fake main pages.",
                         landing=True)]


def _row(**cells) -> dict:
    row = {h: "" for h in content.HEADERS}
    row.update(cells)
    return row


def _idea(keyword, todo="Brief", status="Inboxed", **more):
    return _row(**{"Primary Keyword": keyword, "Status": status, "To Do": todo, "Intent": "Informational",
                   "Keyword Type": "General", "Content Type": "Cluster", **more})


def _page(domain, slug, keyword, todo="Keep", clicks=40, **more):
    url = f"{domain}/article/{slug}"
    return _row(**{"Primary Keyword": keyword, "Published URL": url, "Status": "Published", "To Do": todo,
                   "Priority": "Moderate", "Score": 50, "Clicks (90d)": clicks, "Impressions (90d)": clicks * 30,
                   "CTR": 0.03, "Avg. Position": 8.4, "Intent": "Informational", "Keyword Type": "General",
                   "Content Type": "Cluster", "Slug": f"/article/{slug}", **more})


def persian_articles(domain: str, brief_links: dict) -> list:
    """The Articles rows of a Persian site, awkward cases first. `brief_links`: {keyword: link} of
    the briefs that already exist."""
    k_draft, k_done = "راهنمای مراقبت از پوست خشک", "روتین مراقبت شبانه"
    return [
        _idea("بهترین شامپو برای موهای چرب", **{"Secondary Keywords": "شامپو موی چرب، شامپو ضد چربی"}),
        _idea(ARABIC_KAF + "رم ضد آفتاب"),                                           # Arabic kaf
        _idea("۱۰ ایده برای دکوراسیون اتاق خواب"),                                   # a number in the title
        _idea("می" + ZWNJ + "خواهم موهایم بلند شود"),                                # a half space
        _idea("قیمت گوشی سامسونگ galaxy s24 در ایران"),                              # Latin words and digits
        _page(domain, "رنگ-مو", "مقایسه رنگ مو", todo="Brief",                       # an update of a live page
              **{"Notes/Checklist": "نسخه قبلی سال ۱۴۰۲ نوشته شده است."}),
        _page(domain, "می" + ZWNJ + "خواهم-مو", "می" + ZWNJ + "خواهم مو"),            # a pair that differs by a half space
        _page(domain, "میخواهم-مو", "میخواهم مو"),
        _idea(k_draft, todo="Draft", status="Briefed", Attachments=brief_links.get(k_draft, "")),
        _idea("چای سبز و لاغری", todo="Draft"),                                       # Draft without a brief
        _idea(k_done, todo="Brief", status="Briefed", Attachments=brief_links.get(k_done, ""),
              **{"Briefed Date": "2026-10-20"}),                                      # already briefed
        _page(domain, "روغن-آرگان", "روغن آرگان برای مو"),
        _page(domain, "ماسک-مو", "ماسک مو در خانه"),
        _page(domain, "رنگ" + ZWNJ + "کردن-مو", "رنگ" + ZWNJ + "کردن مو در خانه"),
        _page(domain, "درمان-ریزش-مو", "درمان ریزش مو"),
        _page(domain, "مراقبت-از-پوست-در-زمستان", "مراقبت از پوست در زمستان"),
        _page(domain, "کرم-مرطوب-کننده", "بهترین کرم مرطوب" + ZWNJ + "کننده", todo="Brief",
              **{"Meta Title": "کرم مرطوب‌کننده مناسب هر پوست", "Slug": "/article/کرم-مرطوب-کننده"}),  # cells already filled
        _idea("٣ روش برای شست" + ZWNJ + "وشوی مو"),                                  # Arabic digit
        _idea("  کرم  دور چشم "),                                                      # extra spaces
        _idea("م" + TATWEEL * 3 + "راقبت از مو"),                                      # a tatweel
    ]


def english_articles(domain: str, brief_links: dict) -> list:
    k_draft, k_done = "how to start a vegetable garden", "morning stretching routine"
    return [
        _idea("best moisturizer for oily skin"),
        _idea("10 ideas for a small kitchen"),
        _idea("best budget laptops 2026"),                                           # a comparison with prices
        _idea("how to wash a wool sweater"),
        _page(domain, "coffee-stain", "how to remove a coffee stain", todo="Brief"),  # an update
        _idea(k_draft, todo="Draft", status="Briefed", Attachments=brief_links.get(k_draft, "")),
        _idea("indoor plants for low light", todo="Draft"),                          # Draft without a brief
        _idea(k_done, todo="Brief", status="Briefed", Attachments=brief_links.get(k_done, ""),
              **{"Briefed Date": "2026-10-20"}),
        _page(domain, "compost-basics", "compost basics"),
        _page(domain, "herb-garden", "herb garden for beginners"),
        _page(domain, "stain-removal-tips", "stain removal tips"),
        _page(domain, "kitchen-storage", "kitchen storage ideas"),
        _page(domain, "laptop-battery", "how to save laptop battery"),
    ]


def nosop_articles(domain: str, brief_links: dict) -> list:
    k_draft = "مراقبت از گیاهان آپارتمانی"
    return [
        _idea("آب‌دادن به کاکتوس"), _idea("خاک مناسب گل شمعدانی"), _idea("بهترین گلدان برای بالکن"),
        _idea(k_draft, todo="Draft", status="Briefed", Attachments=brief_links.get(k_draft, "")),
        _page(domain, "کود-گیاه", "کود مناسب گیاهان"),
        _page(domain, "نور-گیاه", "نور مورد نیاز گیاهان"),
        _page(domain, "گیاه-سایه-دوست", "گیاهان سایه‌دوست"),
        _page(domain, "تکثیر-گیاه", "تکثیر گیاه با قلمه"),
    ]


def landing_rows(domain: str, language: str) -> list:
    names = ([("about", "درباره ما"), ("contact", "تماس با ما")] if language == "fa"
             else [("about", "about us"), ("contact", "contact us")])
    return [_row(**{"Primary Keyword": kw, "Published URL": f"{domain}/{slug}", "Status": "Published",
                    "To Do": "Keep", "Intent": "Navigational", "Content Type": "Pillar", "Slug": f"/{slug}"})
            for slug, kw in names]


def articles_for(site: FakeSite, brief_links: dict) -> list:
    make = {"fake-fa": persian_articles, "fake-en": english_articles, "fake-nosop": nosop_articles}[site.name]
    return [{k: v for k, v in row.items() if k in content.HEADERS} for row in make(site.domain, brief_links)]


def brief_keywords(site: FakeSite) -> list:
    """The keywords whose rows already have a brief when the world is made."""
    return {"fake-fa": ["راهنمای مراقبت از پوست خشک", "روتین مراقبت شبانه"],
            "fake-en": ["how to start a vegetable garden", "morning stretching routine"],
            "fake-nosop": ["مراقبت از گیاهان آپارتمانی"]}[site.name]


# --------------------------------------------------------------------------- #
# Text of the invented documents
# --------------------------------------------------------------------------- #

FA = {
    "style": "body{font-family:'Vazirmatn',Tahoma,sans-serif;line-height:1.9}",
    "box": [("عنوان متا", "۱۰ ایده برای دکوراسیون اتاق خواب | فروشگاه نمونه"), ("توضیح متا", "ایده‌های ساده برای اتاق خواب"),
            ("اسلاگ", "10-ideas-bedroom"), ("نوع مقاله", "راهنما"), ("تعداد کلمات", "1500")],
    "h1": "۱۰ ایده برای دکوراسیون اتاق خواب",
    "intro": "برای یک اتاق خواب آرام، رنگ روشن، نور گرم و چیدمان ساده بهترین شروع است.",
    "items": ["استفاده از رنگ‌های روشن", "پرده‌های سبک", "آینه بزرگ", "نور گرم", "فرش ساده", "گیاه آپارتمانی",
              "قفسه دیواری", "تخت چوبی", "مرتب نگه داشتن", "بالش و روتختی هماهنگ"],
    "h2_ideas": "ایده‌ها",
    "h2_compare": "مقایسه گزینه‌ها",
    "table_head": ["گزینه", "هزینه", "زمان اجرا"],
    "table_rows": [["رنگ‌آمیزی", "متوسط", "دو روز"], ["پرده", "کم", "یک روز"], ["فرش", "زیاد", "یک روز"]],
    "links_lead": "بیشتر بخوانید:",
    "img": "[عکس: اتاق خواب روشن | اتاق خواب با رنگ روشن | bedroom-light.jpg]",
    "faq": "پرسش‌های متداول", "q": "چه رنگی برای اتاق خواب مناسب است؟",
    "a": "رنگ‌های روشن و خنثی مناسب‌تر هستند.",
    "summary": "جمع‌بندی", "summary_text": "با چند تغییر کوچک می‌توانید اتاق خواب را آرام‌تر کنید. از یک ایده شروع کنید.",
    "quality": "بررسی کیفیت", "checks": ["✓ یک عنوان اصلی", "✓ جدول مقایسه", "✓ سه تا پنج پیوند داخلی"],
    "link_texts": ["رنگ مو", "روغن آرگان", "ماسک مو", "درمان ریزش مو", "مراقبت از پوست", "کرم مرطوب‌کننده"],
    "link_slugs": ["رنگ-مو", "روغن-آرگان", "ماسک-مو", "درمان-ریزش-مو", "مراقبت-از-پوست-در-زمستان",
                   "کرم-مرطوب-کننده"],
    "extra": "این یک جمله ساده است.",
    "latin": "در سال 2026 قیمت‌ها تغییر کرد.",
    "arabic_digit": "در سال ٢٠٢٦ قیمت‌ها تغییر کرد.",
    "arabic_letters": "\u0643\u062a\u0627\u0628 \u062e\u0648\u0628 \u0648 \u0639\u0644\u064a",     # Arabic kaf and yeh
}
EN = {
    "style": "body{font-family:Arial,sans-serif;line-height:1.6}",
    "box": [("Meta title", "10 ideas for a small kitchen | Sample Store"), ("Meta description", "Simple ideas for a small kitchen"),
            ("Slug", "10-ideas-small-kitchen"), ("Article type", "Guide"), ("Word count", "1500")],
    "h1": "10 ideas for a small kitchen",
    "intro": "For a small kitchen, light colours, open shelves and a clear counter are the best start.",
    "items": ["Light paint", "Open shelves", "A narrow cart", "Hanging pots", "Under-cabinet lights", "A fold-down table",
              "Slim bins", "Mirror tiles", "One strong colour", "Clear counters"],
    "h2_ideas": "The ideas",
    "h2_compare": "Comparing the options",
    "table_head": ["Option", "Cost", "Time"],
    "table_rows": [["Paint", "Medium", "Two days"], ["Shelves", "Low", "One day"], ["Lights", "Low", "One day"]],
    "links_lead": "Read more:",
    "img": "[Image: bright kitchen | bright small kitchen | kitchen-bright.jpg]",
    "faq": "Frequently asked questions", "q": "Which colour suits a small kitchen?",
    "a": "Light and neutral colours work best.",
    "summary": "Summary", "summary_text": "A few small changes make a small kitchen calmer. Start with one idea.",
    "quality": "Quality check", "checks": ["✓ one main heading", "✓ comparison table", "✓ three to five internal links"],
    "link_texts": ["coffee stains", "compost", "herb garden", "stain removal", "kitchen storage", "laptop battery"],
    "link_slugs": ["coffee-stain", "compost-basics", "herb-garden", "stain-removal-tips", "kitchen-storage",
                   "laptop-battery"],
    "extra": "This is a plain sentence.",
    "latin": "",
    "arabic_digit": "",
    "arabic_letters": "",
}
T = {"fa": FA, "en": EN}


BLOCK_TAG = re.compile(r"<(p|h[1-6]|li|td|th)>")


def inline_style(html: str, lang: str, rtl: bool = True) -> str:
    """Puts the font (and, for a right-to-left document, the direction) on every paragraph, heading,
    list item and cell: Google keeps what is written on the element but does not always keep what is
    written once for the whole page."""
    font = "font-family:'Vazirmatn',Tahoma,sans-serif" if lang == "fa" else "font-family:Arial,sans-serif"
    attrs = f' style="{font}"' + (' dir="rtl"' if (lang == "fa" and rtl) else "")
    return BLOCK_TAG.sub(lambda m: f"<{m.group(1)}{attrs}>", html)


def _page_start(lang: str, rtl: bool = True) -> str:
    t = T[lang]
    attrs = ' lang="fa" dir="rtl"' if (lang == "fa" and rtl) else (' lang="fa"' if lang == "fa" else ' lang="en"')
    return f"<html{attrs}><head><meta charset=\"utf-8\"><style>{t['style']}</style></head><body>"


def draft_html(lang: str, domain: str, *, h1s: int = 1, faq_level: int = 2, summary_level: int = 2,
               table: bool = True, links: int = 4, repeat_link: bool = False, images: bool = True,
               quality: bool = True, header_box: bool = True, rtl: bool = True, items: int = 10,
               words: str = "", arabic_letters: bool = False, latin_digits: bool = False,
               arabic_digits: bool = False) -> str:
    """A draft that follows every rule, or breaks the one the flags ask for."""
    t = T[lang]
    out = [_page_start(lang, rtl)]
    if header_box:
        rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in t["box"])
        out.append(f'<table id="header-box">{rows}</table>')
    out.append("".join(f"<h1>{t['h1']}</h1>" for _ in range(h1s)) or "")
    out.append(f"<p>{t['intro']}</p>")
    out.append(f"<h2>{t['h2_ideas']}</h2><ol>" + "".join(f"<li>{x}</li>" for x in t["items"][:items]) + "</ol>")
    if table:
        head = "".join(f"<th>{h}</th>" for h in t["table_head"])
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in t["table_rows"])
        out.append(f"<h2>{t['h2_compare']}</h2><table><tr>{head}</tr>{body}</table>")
    if images:
        out.append(f"<p>{t['img']}</p>")
    slugs = list(t["link_slugs"][:links])
    if repeat_link and slugs:
        slugs[-1] = slugs[0]
    anchors = " ".join(f'<a href="{domain}/article/{s}">{t["link_texts"][i % len(t["link_texts"])]}</a>'
                       for i, s in enumerate(slugs))
    out.append(f"<p>{t['links_lead']} {anchors}</p>")
    if arabic_letters and t["arabic_letters"]:
        out.append(f"<p>{t['arabic_letters']}</p>")
    if latin_digits and t["latin"]:
        out.append(f"<p>{t['latin']}</p>")
    if arabic_digits and t["arabic_digit"]:
        out.append(f"<p>{t['arabic_digit']}</p>")
    if words:
        out.append(f"<p>{t['extra']} {words}</p>")
    out.append(f"<h{faq_level}>{t['faq']}</h{faq_level}><h3>{t['q']}</h3><p>{t['a']}</p>")
    out.append(f"<h{summary_level}>{t['summary']}</h{summary_level}><p>{t['summary_text']}</p>")
    if quality:
        out.append(f"<h2>{t['quality']}</h2><ul>" + "".join(f"<li>{c}</li>" for c in t["checks"]) + "</ul>")
    out.append("</body></html>")
    return inline_style("".join(out), lang, rtl)


BRIEF_HEADINGS = {
    "en": ["Task and article type", "Keywords and reader", "Title tag", "Meta description", "H1 and slug", "Outline",
           "Entities and facts", "FAQ from real searches", "Internal links", "Length and format",
           "Update notes", "Writer checklist"],
    "fa": ["وظیفه و نوع مقاله", "کلمات کلیدی و مخاطب", "تگ عنوان", "متا دیسکریپشن", "اچ یک و اسلاگ", "ساختار مقاله",
           "موجودیت‌ها و واقعیت‌ها", "پرسش‌های متداول از جستجوهای واقعی", "لینک‌های داخلی", "طول و قالب",
           "یادداشت‌های به‌روزرسانی", "چک‌لیست نویسنده"],
}


def brief_html(lang: str, keyword: str, *, drop: str = "", h1s: int = 1, update: bool = False,
               words: str = "") -> str:
    """A brief with every section, or without the section named in `drop` (a part of its heading)."""
    heads = [h for h in BRIEF_HEADINGS[lang] if (update or h not in (BRIEF_HEADINGS[lang][10],))]
    if drop:
        heads = [h for h in heads if drop.lower() not in h.lower()]
    out = [_page_start(lang), "".join(f"<h1>{keyword}</h1>" for _ in range(h1s))]
    for h in heads:
        out.append(f"<h2>{h}</h2><p>{'ok' if lang == 'en' else 'تایید'}</p>")
    if words:
        out.append(f"<p>{words}</p>")
    out.append("</body></html>")
    return inline_style("".join(out), lang)


def sop_html(site: FakeSite) -> str:
    t = T[site.language]
    items = ("<li>یک عنوان اصلی</li><li>پاسخ مستقیم در ابتدا</li><li>جدول برای هر مقایسه</li>"
             "<li>سه تا پنج پیوند داخلی</li><li>بخش پرسش‌های متداول و جمع‌بندی</li>"
             if site.language == "fa" else
             "<li>One main heading</li><li>Direct answer first</li><li>A table for every comparison</li>"
             "<li>Three to five internal links</li><li>An FAQ and a summary</li>")
    title = f"{site.label} " + ("روش نوشتن" if site.language == "fa" else "writing rules")
    return (_page_start(site.language) + f"<h1>{title}</h1><p>{t['intro']}</p><h2>Checklist</h2><ul>{items}</ul>"
            + (f"<p>{site.extra_rules}</p>" if site.extra_rules else "") + "</body></html>")


# --------------------------------------------------------------------------- #
# Samples: documents that are right and documents that are wrong
# --------------------------------------------------------------------------- #

@dataclass
class Sample:
    name: str
    html: str
    kind: str            # "draft" or "brief"
    language: str
    domain: str
    expected: set        # codes the checker must report; empty: the document is right
    update: bool = False


def samples() -> list:
    """The documents the checker is tried on. Right ones must pass; each wrong one breaks one rule and the
    checker must name exactly that rule."""
    out = []
    for lang, domain in (("fa", "https://fa.fake-site.test"), ("en", "https://en.fake-site.test")):
        def d(name, expected, **kw):
            out.append(Sample(f"{lang} draft · {name}", draft_html(lang, domain, **kw), "draft", lang, domain,
                              set(expected)))
        d("right", [])
        d("two H1", ["h1-count"], h1s=2)
        d("no H1", ["h1-count"], h1s=0)
        d("FAQ is H3", ["faq-not-h2"], faq_level=3)
        d("summary is H3", ["summary-not-h2"], summary_level=3)
        d("no table", ["no-table"], table=False)
        d("two links", ["link-count"], links=2)
        d("six links", ["link-count"], links=6)
        d("a link twice", ["link-repeated"], repeat_link=True)
        d("no image slot", ["no-image-slot"], images=False)
        d("no quality check", ["no-quality-check"], quality=False)
        d("no header box", ["no-header-box"], header_box=False)
        d("tool name", ["tool-name"], words="PulseO")
        d("the word AI", ["tool-name"], words="AI")
        d("em dash", ["dash"], words="—")
        d("en dash", ["dash"], words="–")
        d("title says ten, seven listed", ["count-mismatch"], items=7)
        if lang == "fa":
            d("Arabic letters", ["arabic-letter"], arabic_letters=True)
            d("Latin digits", ["latin-digit"], latin_digits=True)
            d("Arabic digits", ["arabic-digit"], arabic_digits=True)
            d("not right to left", ["not-rtl"], rtl=False)
        out.append(Sample(f"{lang} brief · right", brief_html(lang, "kw"), "brief", lang, domain, set()))
        out.append(Sample(f"{lang} brief · right, an update", brief_html(lang, "kw", update=True), "brief", lang,
                          domain, set(), update=True))
        out.append(Sample(f"{lang} brief · no FAQ section", brief_html(lang, "kw", drop="faq" if lang == "en" else "پرسش"),
                          "brief", lang, domain, {"missing-section"}))
        out.append(Sample(f"{lang} brief · no update notes", brief_html(lang, "kw"), "brief", lang, domain,
                          {"missing-section"}, update=True))
        out.append(Sample(f"{lang} brief · two H1", brief_html(lang, "kw", h1s=2), "brief", lang, domain, {"two-h1"}))
        out.append(Sample(f"{lang} brief · tool name", brief_html(lang, "kw", words="PulseO"), "brief", lang, domain,
                          {"tool-name"}))
    return out


def check_sample(sample: Sample, html: str = None) -> set:
    """The codes the checker reports for a sample (for its own HTML, or the HTML Google exported)."""
    html = sample.html if html is None else html
    if sample.kind == "brief":
        found = cc.check_brief(html, update=sample.update)
    else:
        found = cc.check_draft(html, language=sample.language, domain=sample.domain, has_comparison=True)
    return {f.code for f in found}


def selftest(items: list) -> list:
    """[(name, expected, found)] of the samples the checker got wrong. `items`: [(Sample, html)]; an
    empty result means every right document passed and every wrong one failed on the right rule."""
    wrong = []
    for sample, html in items:
        found = check_sample(sample, html)
        if found != sample.expected:
            wrong.append((sample.name, sample.expected, found))
    return wrong


# --------------------------------------------------------------------------- #
# Building the world in Drive
# --------------------------------------------------------------------------- #

@dataclass
class SiteWorld:
    site: FakeSite
    folder_id: str = ""
    dashboard_id: str = ""
    sop_id: str = ""
    briefs_id: str = ""
    drafts_id: str = ""
    brief_ids: dict = field(default_factory=dict)     # keyword -> id of the brief that exists from the start


@dataclass
class World:
    root_id: str = ""
    index_id: str = ""
    samples_id: str = ""
    sites: dict = field(default_factory=dict)         # name -> SiteWorld
    sample_ids: dict = field(default_factory=dict)    # sample name -> doc id

    def settings(self, name: str) -> dict:
        """What content creation reads from a site's configuration."""
        w = self.sites[name]
        return {"sop_doc_id": w.sop_id, "briefs_folder_id": w.briefs_id, "drafts_folder_id": w.drafts_id,
                "language": w.site.language, "extra_rules": w.site.extra_rules}


def _rows_matrix(rows: list) -> list:
    return [[row.get(h, "") for h in content.HEADERS] for row in rows]


def write_dashboard(sheets, file_id: str, articles: list, landing: list):
    """The two tabs' data rows, from row 2 down, replacing whatever the invented dashboard held."""
    last = cs.letters(len(content.HEADERS) - 1)
    for tab, rows in (("Articles", articles), ("Landing Pages", landing)):
        history._write(sheets.spreadsheets().values().clear(spreadsheetId=file_id, range=f"'{tab}'!A2:{last}", body={}))
        tabs.patch_cells(sheets, file_id, [(f"'{tab}'!A2:{last}{len(rows) + 1}", _rows_matrix(rows))])


def build(drive, sheets, log=print, reset: bool = False) -> World:
    """Makes (or finds) the whole fake world in Drive. Running it again changes nothing, unless
    `reset`: then the two tabs of every fake dashboard are put back to their starting rows. Only
    files and folders made here, inside the "PulseO Test" folder, are ever touched. No id is printed."""
    world = World()
    world.root_id = provision.ensure_folder(drive, TEST_ROOT)
    world.index_id = provision.find(drive, INDEX_NAME, provision.SHEET, world.root_id) or \
        provision.create(drive, INDEX_NAME, provision.SHEET, world.root_id)
    for site in FAKE_SITES:
        w = SiteWorld(site)
        w.folder_id = provision.ensure_folder(drive, site.label, world.root_id)
        w.briefs_id = provision.ensure_folder(drive, "Briefs", w.folder_id)
        w.drafts_id = provision.ensure_folder(drive, "Drafts", w.folder_id)
        if site.sop:
            w.sop_id = ensure_doc(drive, f"{site.label} · SOP", sop_html(site), w.folder_id)
        for kw in brief_keywords(site):
            w.brief_ids[kw] = ensure_doc(drive, f"Brief · {kw}", brief_html(site.language, kw), w.briefs_id)
        name = f"[{site.label}] Content Dashboard"
        w.dashboard_id = provision.find(drive, name, provision.SHEET, w.folder_id)
        fresh = w.dashboard_id is None
        if fresh:
            w.dashboard_id = provision.create(drive, name, provision.SHEET, w.folder_id)
            cs.initial_setup(sheets, w.dashboard_id, site.label, TABS)
        if fresh or reset:
            links = {kw: doc_link(i) for kw, i in w.brief_ids.items()}
            write_dashboard(sheets, w.dashboard_id, articles_for(site, links), landing_rows(site.domain, site.language))
        world.sites[site.name] = w
        log(f"  fake site: {site.name} {'made' if fresh else 'found'}")
    world.samples_id = provision.ensure_folder(drive, SAMPLES, world.root_id)
    for s in samples():
        world.sample_ids[s.name] = ensure_doc(drive, s.name, s.html, world.samples_id)
    rows = [[w.site.name, w.site.label, w.site.language, w.dashboard_id, w.sop_id, w.briefs_id, w.drafts_id,
             w.folder_id] for w in world.sites.values()]
    _write_index(sheets, world.index_id, rows)
    return world


def _write_index(sheets, file_id: str, rows: list):
    titles, _ = history.grid_info(sheets, file_id)
    if INDEX_TAB not in titles:
        first = next(iter(titles))
        history._write(sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [{
            "updateSheetProperties": {"properties": {"sheetId": titles[first], "title": INDEX_TAB,
                                                     "gridProperties": {"frozenRowCount": 1}},
                                      "fields": "title,gridProperties.frozenRowCount"}}]}))
    last = cs.letters(len(INDEX_HEADER) - 1)
    tabs.patch_cells(sheets, file_id, [(f"'{INDEX_TAB}'!A1:{last}{len(rows) + 1}", [INDEX_HEADER] + rows)])


def check_samples_in_drive(drive, world: World) -> list:
    """The self-test on the real round trip: each sample is read back from Drive (as Google exports
    it) and checked. Returns what the checker got wrong, as selftest does."""
    by_name = {s.name: s for s in samples()}
    items = [(by_name[name], export_html(drive, doc_id)) for name, doc_id in world.sample_ids.items()]
    return selftest(items)
