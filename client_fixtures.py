"""
Invented report data for the client deck (tests and previews). Everything here is made up:
generic words, round numbers, the site is called "Example". No real site, URL, client or keyword
appears in this file, and none may be added.
"""

import deck_data as dd
import opportunities
import reports
import resolved

MONTHS = [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]   # 15 months
ALL_CLICKS = [900, 880, 950, 1010, 1100, 1400, 1250, 1000, 950, 980, 1100, 1180, 1210, 1260, 1300]
ALL_IMPR = [30000, 29000, 31000, 32500, 35000, 41000, 38000, 31000, 30000, 31500, 34000, 36000,
            36500, 37000, 36000]
POSITION = [7.8, 7.7, 7.6, 7.5, 7.4, 7.2, 7.1, 7.0, 6.9, 6.8, 6.7, 6.6, 6.5, 6.4, 6.2]
SEGMENT_CLICKS = {
    "Guides": [450, 440, 470, 500, 560, 700, 640, 520, 500, 520, 590, 650, 680, 720, 800],
    "Shop": [250, 250, 260, 270, 280, 330, 300, 250, 240, 240, 250, 230, 200, 190, 170],
    "Blog & other": [300, 300, 330, 350, 380, 480, 400, 320, 300, 320, 350, 370, 390, 400, 430],
}

PERSIAN = ["آموزش برنامه نویسی", "بهترین کتاب رمان", "قیمت لپ تاپ", "راهنمای خرید موبایل"]
ENGLISH = ["blue running shoes", "garden tools guide", "best budget laptop", "summer travel tips",
           "home office desk", "healthy breakfast ideas", "city bike prices", "winter coat sizes"]


def _pct(cur, prev):
    return round((cur - prev) / prev * 100, 1) if prev else ""


def _row(i: int, segment: str, clicks: list, impr_scale: float = 1.0) -> dict:
    row = {h: "" for h in reports.MONTHLY_HEADER}
    c = clicks[i]
    n = round(ALL_IMPR[i] * c / ALL_CLICKS[i] * impr_scale) if segment != "All" else ALL_IMPR[i]
    row.update({"Period": MONTHS[i], "Segment": segment, "Clicks": c, "Impressions": n,
                "CTR (%)": round(c / n * 100, 2), "Avg. Position": POSITION[i]})
    if i > 0:
        pc = clicks[i - 1]
        pn = round(ALL_IMPR[i - 1] * pc / ALL_CLICKS[i - 1] * impr_scale) if segment != "All" else ALL_IMPR[i - 1]
        row.update({"Clicks MoM": c - pc, "Clicks MoM %": _pct(c, pc), "Impr MoM": n - pn,
                    "Impr MoM %": _pct(n, pn)})
    if i >= 12:
        yc = clicks[i - 12]
        yn = round(ALL_IMPR[i - 12] * yc / ALL_CLICKS[i - 12] * impr_scale) if segment != "All" else ALL_IMPR[i - 12]
        row.update({"Clicks YoY": c - yc, "Clicks YoY %": _pct(c, yc), "Impr YoY": n - yn,
                    "Impr YoY %": _pct(n, yn)})
    return row


def _kw(kind, query, clicks, clicks_prev, impr, impr_prev, pos, pos_prev):
    return dict(zip(reports.KEYWORDS_HEADER, [MONTHS[-1], kind, query, clicks, clicks_prev,
                                              clicks - clicks_prev, impr, impr_prev, impr - impr_prev,
                                              pos, pos_prev, round(pos - pos_prev, 1)]))


def _pg(kind, path, clicks, clicks_prev, impr, impr_prev, pos, pos_prev):
    return dict(zip(reports.PAGES_HEADER, [MONTHS[-1], kind, "https://example.test" + path, "", clicks,
                                           clicks_prev, clicks - clicks_prev, impr, impr_prev,
                                           impr - impr_prev, pos, pos_prev, round(pos - pos_prev, 1),
                                           path]))


def _opp(kind, keyword, page, impr, clicks, pos, ctr, gain, diagnosis, action, segment=""):
    return dict(zip(opportunities.HEADER, [kind, keyword, page, impr, clicks, pos, ctr,
                                           round(opportunities.expected_ctr(pos), 4), gain, diagnosis,
                                           action, segment]))


def monthly_report(segments: bool = True, opps: bool = True, gone: int = 6, resolved_list=None,
                   persian: bool = True) -> dd.Report:
    rep = dd.Report("monthly", MONTHS[-1], MONTHS[-2], MONTHS[-13])
    names = ["All"] + (list(SEGMENT_CLICKS) if segments else [])
    for i in range(len(MONTHS)):
        rep.rows[(MONTHS[i], "All")] = _row(i, "All", ALL_CLICKS)
        if segments:
            for name, series in SEGMENT_CLICKS.items():
                rep.rows[(MONTHS[i], name)] = _row(i, name, series)
    rep.labels, rep.segments = list(MONTHS), names
    rep.page_types = names[1:-1] if segments else []
    kw = (PERSIAN[:2] if persian else []) + ENGLISH
    rep.keywords = [
        # moved up: (clicks, prev clicks, impressions, prev impressions, position, previous position)
        _kw("Growing", kw[0], 34, 17, 900, 700, 3.8, 6.8),
        _kw("Growing", kw[1], 20, 8, 400, 380, 7.6, 10.5),
        _kw("Growing", kw[2], 14, 9, 300, 250, 5.6, 7.7),
        _kw("Growing", kw[3], 12, 5, 70, 60, 4.6, 5.9),                 # only 1.3 steps: counts
        _kw("Growing", kw[4], 9, 5, 40, 90, 3.0, 5.5),                  # too few impressions now
        _kw("Declining", kw[5], 3, 14, 500, 800, 9.0, 6.0),
        _kw("Declining", kw[6], 2, 9, 200, 300, 6.5, 6.4),
        _kw("New", ENGLISH[3] + " new", 0, 0, 276, 0, 8.4, 0),
        _kw("New", ENGLISH[4] + " new", 1, 0, 120, 0, 12.0, 0),
        _kw("New", ENGLISH[5] + " new", 0, 0, 90, 0, 15.2, 0),
        _kw("New", ENGLISH[6] + " new", 0, 0, 40, 0, 18.0, 0),
        _kw("Lost", "old forgotten words", 0, 10, 0, 97, 0, 5.0),
    ]
    rep.pages = [
        _pg("Growing", "/", 794, 389, 9000, 7000, 3.2, 3.9),
        _pg("Growing", "/guide/blue-shoes/", 120, 70, 2000, 1500, 5.9, 8.0),
        _pg("Growing", "/shop/hat/", 60, 40, 800, 700, 4.0, 4.4),
        _pg("Declining", "/guide/old-tips/", 55, 137, 4000, 5000, 6.0, 5.0),
        _pg("Declining", "/guide/steady/", 30, 52, 2500, 2400, 7.0, 7.0),
        _pg("YoY Growing", "/guide/blue-shoes/", 120, 40, 2000, 1000, 5.9, 8.5),
        _pg("YoY Declining", "/guide/old-tips/", 55, 200, 4000, 6000, 6.0, 4.0),
        _pg("YoY Declining", "/guide/steady/", 30, 80, 2500, 3500, 7.0, 6.5),
    ]
    rep.gone = [dict(zip(reports.GONE_HEADER, [MONTHS[-1], "Gone", f"https://example.test/old/p{i}",
                                               50 - i, 600 - i, 30, 400, f"/old/p{i}"]))
                for i in range(gone)]
    rep.notes = [("Data lag", "Search Console data is final about two to three days after a day ends.")]
    if opps:
        rep.opps = [
            _opp("quick_win", kw[0] if persian else ENGLISH[0], "https://example.test/guide/a/", 5475, 120,
                 9.4, 0.022, 145, "Ranks on page 1 but below the top 3.", "Improve the page.", "Guides"),
            _opp("quick_win", ENGLISH[0], "https://example.test/guide/b/", 4227, 90, 7.9, 0.021, 100,
                 "Ranks on page 1 but below the top 3.", "Improve the page.", "Guides"),
            _opp("quick_win", ENGLISH[1], "https://example.test/guide/c/", 4032, 80, 6.1, 0.02, 90,
                 "Ranks on page 1 but below the top 3.", "Improve the page.", "Guides"),
            _opp("ctr_fix", ENGLISH[1], "https://example.test/guide/c/", 4032, 20, 3.0, 0.005, 95,
                 "Click rate 0.5% where about 10.0% is usual.", "Rewrite title and meta.", "Guides"),
            _opp("quick_win", ENGLISH[2], "https://example.test/shop/d/", 3675, 60, 9.2, 0.016, 90,
                 "Ranks on page 1 but below the top 3.", "Improve the page.", "Shop"),
            _opp("quick_win", ENGLISH[3], "https://example.test/guide/e/", 2181, 40, 4.4, 0.018, 45,
                 "Ranks on page 1 but below the top 3.", "Improve the page.", "Guides"),
            _opp("quick_win", ENGLISH[4], "https://example.test/guide/f/", 1200, 10, 12.0, 0.008, 25,
                 "Ranks on page 2 but below the top 3.", "Improve the page.", "Guides"),
            _opp("update_page", ENGLISH[0], "https://example.test/guide/g/", 13610, 143, 7.5, 0.0105, 195,
                 "Ranks well, title doesn't win the click.", "Rewrite title + meta.", "Guides"),
            _opp("update_page", ENGLISH[1], "https://example.test/guide/h/", 12523, 120, 6.7, 0.0096, 190,
                 "Split with 2 other pages for the same keyword.", "Merge into one strong page.", "Guides"),
            _opp("decliner", ENGLISH[5], "https://example.test/guide/old-tips/", 4000, 55, 6.0, 0.014, 82,
                 "Ranking fell.", "Update content.", "Guides"),
            _opp("question", "what is a trail shoe", "https://example.test/guide/i/", 1756, 11, 8.0,
                 0.0063, 40, "Question search ranking at position 8.0.", "Write a short explainer page.",
                 "Guides"),
            _opp("question", "how to clean a bike", "", 931, 0, 9.0, 0.0, 30,
                 "Question search ranking at position 9.0.", "Write a short explainer page.", ""),
            _opp("question", "why do boots leak", "", 625, 6, 3.0, 0.009, 20,
                 "Question search ranking at position 3.0.", "Write a short explainer page.", ""),
            _opp("cannibal", ENGLISH[0], "https://example.test/guide/b/", 4227, 90, 7.9, 0.021, 100,
                 "3 pages compete for this search.", "Merge into one page.", "Guides"),
            _opp("cannibal", ENGLISH[1], "https://example.test/guide/c/", 4032, 80, 6.1, 0.02, 90,
                 "3 pages compete for this search.", "Merge into one page.", "Guides"),
            _opp("check_page", "", "https://example.test/guide/j/", 300, 5, 12.0, 0.016, 12,
                 "Impressions down 85% on last month.", "Check this page.", "Guides"),
            _opp("seasonal", ENGLISH[0], "https://example.test/guide/k/", 2000, 40, 5.0, 0.02, 60,
                 "Grew +50% into October last year.", "Update before October.", "Guides"),
        ]
    rep.resolved = resolved.parse(resolved_list or [])
    rep.lost_new_floor = 10
    return rep


class Cfg:
    """Just what the client deck reads of a DeckConfig."""

    def __init__(self, language="en", site_label="Example"):
        self.language, self.site_label = language, site_label
        self.accent_color = self.brand_color = self.logo_url = ""
