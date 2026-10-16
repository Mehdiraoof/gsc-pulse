"""
Invented report data for testing and previewing decks. Everything here is fake: generic
words, made-up numbers. No real site, URL, client or keyword appears in this file.
"""

from datetime import date

import deck_data as dd
import periods
import reports

MONTHS = [f"2025-{m:02d}" for m in range(9, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]
CLICKS = [3400, 3000, 2550, 2000, 3550, 1330, 16, 1700, 1840, 770, 595, 481, 325]
IMPR = [187000, 140000, 118000, 123000, 171000, 94000, 3000, 92000, 152000, 118000, 114000, 100000, 69600]
CTR = [1.8, 2.1, 2.2, 1.6, 2.1, 1.4, 0.5, 1.8, 1.2, 0.65, 0.52, 0.48, 0.47]
POS = [6.7, 6.0, 4.0, 3.9, 3.8, 3.9, 13.7, 4.5, 4.3, 3.1, 2.9, 3.4, 3.7]

PERSIAN = ["آموزش برنامه نویسی", "بهترین کتاب رمان", "قیمت لپ تاپ", "دستور پخت قورمه سبزی",
           "راهنمای خرید موبایل", "تعمیر یخچال", "آموزش زبان انگلیسی", "مقایسه گوشی ها",
           "بهترین فیلم ها", "نحوه ثبت نام", "کلاس آنلاین", "خرید بلیط"]
ENGLISH = ["blue running shoes", "how to bake bread", "best budget laptop", "garden tools guide",
           "summer travel tips", "home office desk", "healthy breakfast ideas", "city bike prices"]


def _pct(cur, prev):
    return round((cur - prev) / prev * 100, 1) if prev else ""


def _row(kind: str, labels: list, i: int, segment="All", scale=1.0, aio=None):
    pre = "MoM" if kind == "monthly" else "WoW"
    c, n = round(CLICKS[i] * scale), round(IMPR[i] * scale)
    row = {h: "" for h in (reports.MONTHLY_HEADER if kind == "monthly" else reports.WEEKLY_HEADER)}
    row.update({"Period": labels[i], "Segment": segment, "Clicks": c, "Impressions": n,
                "CTR (%)": CTR[i], "Avg. Position": POS[i]})
    if i > 0:
        pc, pn = round(CLICKS[i - 1] * scale), round(IMPR[i - 1] * scale)
        row.update({f"Clicks {pre}": c - pc, f"Clicks {pre} %": _pct(c, pc),
                    f"Impr {pre}": n - pn, f"Impr {pre} %": _pct(n, pn)})
    if aio is not None:
        row["AIO Impressions"] = aio[i]
        if i > 0 and aio[i - 1]:
            row[f"AIO {pre}"] = aio[i] - aio[i - 1]
            row[f"AIO {pre} %"] = _pct(aio[i], aio[i - 1])
    if i >= 4 and kind == "monthly":
        row.update({"Clicks YoY": c - 3000, "Clicks YoY %": _pct(c, 3000),
                    "Impr YoY": n - 150000, "Impr YoY %": _pct(n, 150000)})
    return row


def monthly_report(segments: bool = False, aio: bool = False, persian_rows: bool = True) -> dd.Report:
    rep = dd.Report("monthly", MONTHS[-1], MONTHS[-2], "2025-09")
    aio_values = [0, 0, 0, 0, 0, 0, 0, 0, 0, 5200, 4800, 4400, 3100] if aio else None
    names = ["All"] + (["English", "Article", "Other"] if segments else [])
    shares = {"All": 1.0, "English": 0.45, "Article": 0.3, "Other": 0.35}
    for seg in names:
        for i, label in enumerate(MONTHS):
            a = ([v for v in aio_values] if aio and seg == "All" else None)
            rep.rows[(label, seg)] = _row("monthly", MONTHS, i, seg, shares[seg], a)
    if aio:
        for i, label in enumerate(MONTHS[:9]):
            rep.rows[(label, "All")]["AIO Impressions"] = ""
    rep.labels = list(MONTHS)
    rep.segments = names
    rep.page_types = names[1:-1] if segments else []
    pool = (PERSIAN if persian_rows else ENGLISH) + ENGLISH

    def kw(kind, n, base_impr):
        return [dict(zip(reports.KEYWORDS_HEADER, [MONTHS[-1], kind, pool[i % len(pool)] + ("" if i < len(pool) else f" {i}"),
                                                    max(0, 10 - i), 12 - i, (10 - i) - (12 - i),
                                                    base_impr - i * 3, base_impr - i * 2, -i, 3.1, 3.4, -0.3]))
                for i in range(n)]

    lost = kw("Lost", 12, 90)
    for r in lost:
        r.update({"Impr prev": r["Impressions"] + 20, "Impressions": 0, "Clicks": 0})
    new = kw("New", 8, 50)
    for r in new:
        r.update({"Impr prev": 0})
    grow = kw("Growing", 10, 40)
    for i, r in enumerate(grow):
        r.update({"Clicks": 12 - i, "Clicks diff": 11 - i})
    fall = kw("Declining", 10, 40)
    for i, r in enumerate(fall):
        r.update({"Clicks": i, "Clicks diff": -(14 - i)})
    rep.keywords = lost + new + grow + fall

    def pg(kind, n, sign):
        paths = ["/guide/" + (pool[i % len(pool)]).replace(" ", "-") + "/" for i in range(n)]
        return [dict(zip(reports.PAGES_HEADER, [MONTHS[-1], kind, "https://example.test" + paths[i], "",
                                                (20 - i) if sign > 0 else i, 10, sign * (30 - 3 * i),
                                                400, 380, 20 * sign, 3.2, 3.5, -0.3])) for i in range(n)]

    rep.pages = pg("Growing", 8, 1) + pg("Declining", 10, -1) + pg("YoY Growing", 5, 1) + pg("YoY Declining", 10, -1)
    if aio:
        rep.aio_pages = [dict(zip(reports.AIO_PAGES_HEADER, [MONTHS[-1], "Growing" if i < 5 else "Declining",
                                                             "https://example.test/page-" + str(i), 100 + i,
                                                             80, (20 + i) * (1 if i < 5 else -1)])) for i in range(10)]
    rep.alarms = [
        {"severity": "high", "area": "Segment clicks", "impact": 156,
         "message": "Clicks for All fell by 32.4% compared with 2026-08 (481 to 325)."},
        {"severity": "medium", "area": "Page loss", "impact": 82,
         "message": "The page https://example.test/guide/blue-running-shoes/ lost 59.9% of its clicks "
                    "compared with 2026-08 (137 to 55)."},
        {"severity": "medium", "area": "Page loss", "impact": 70,
         "message": "and 4 more pages lost 50%+ of their clicks"},
        {"severity": "medium", "area": "Lost keyword", "impact": 26,
         "message": "The keyword \"best budget laptop\" brought 26 clicks in 2026-08 and is not in "
                    "Search Console's data in 2026-09."},
    ]
    rep.notes = [("Data lag", "Search Console data is final about two to three days after a day ends."),
                 ("Complete periods only", "Months are calendar months. A period is reported only after its "
                                           "last day has final data."),
                 ("Anonymized queries", "Query-level data leaves out anonymized queries."),
                 ("Page types", "None.")]
    return rep


def weekly_report() -> dd.Report:
    labels = [f"2026-W{w:02d}" for w in range(30, 42)]
    rep = dd.Report("weekly", labels[-1], labels[-2], "2025-W41")
    rep.monday, rep.sunday = date(2026, 10, 5), date(2026, 10, 11)
    clicks = [120, 130, 118, 140, 150, 135, 128, 110, 105, 98, 90, 64]
    impr = [9000, 9500, 9100, 9800, 10100, 9700, 9400, 8800, 8600, 8200, 7900, 5600]
    for i, label in enumerate(labels):
        row = {h: "" for h in reports.WEEKLY_HEADER}
        row.update({"Period": label, "Segment": "All", "Clicks": clicks[i], "Impressions": impr[i],
                    "CTR (%)": round(clicks[i] / impr[i] * 100, 2), "Avg. Position": 3.5})
        if i:
            row.update({"Clicks WoW": clicks[i] - clicks[i - 1], "Clicks WoW %": _pct(clicks[i], clicks[i - 1]),
                        "Impr WoW": impr[i] - impr[i - 1], "Impr WoW %": _pct(impr[i], impr[i - 1])})
        rep.rows[(label, "All")] = row
    rep.labels, rep.segments = labels, ["All"]
    rep.movers = ([dict(zip(reports.KEYWORDS_HEADER, [labels[-1], "Growing", q, 6 - i, 2, 4 - i, 90, 50, 40, 3, 4, -1]))
                   for i, q in enumerate(ENGLISH[:5])]
                  + [dict(zip(reports.KEYWORDS_HEADER, [labels[-1], "Declining", q, i, 8, -(8 - i), 40, 90, -50, 5, 4, 1]))
                     for i, q in enumerate(PERSIAN[:5])])
    rep.alarms = dd.weekly_alarms(rep)
    rep.notes = [("Data lag", "Search Console data is final about two to three days after a day ends.")]
    return rep
