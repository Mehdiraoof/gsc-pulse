"""
PulseO: what goes into the client-ready monthly deck (v2.4)
===========================================================
Turns a deck_data.Report (the Reports sheet) plus the Opportunities tab into the content of each
slide: plain dicts with the texts already written, so the renderer (client_deck.py) only draws
and the numbers can be tested without PowerPoint. Progress first, real keyword and page names,
calm wording, concrete recommendations with an estimated gain.

Every slide function returns None when it has no data, and the deck simply skips it (the one
exception is "Our work this month", the placeholder Mehdi fills in).

Anything on the Resolved list (Events tab) is left out everywhere: pages, keywords, segments and
months. A decline that comes from fewer searches is grey, not red; red is only for rankings that
really fell (average position worse by 0.5 or more).
"""

import re
import statistics

import client_i18n
import deck_data as dd
import i18n
import periods
import resolved as resolved_mod
import tabs

GOOD, GREY, RED = "good", "grey", "red"
UP, DOWN = "↑", "↓"
MIN_CLIMB_IMPRESSIONS = 50
MIN_CLIMB = 1.0                     # position steps
STEADY_YOY = 2.0                    # percent
WATCH_SEGMENT_DROP = -20.0          # percent year on year
WATCH_GONE_MIN = 5
LOW_MONTH_SHARE = 0.5               # a month under half the median is "low"
MAX_WATCH = 5


def num(row, key):
    return dd.num(row.get(key)) if row else None


def f1(x) -> str:
    return f"{x:.1f}"


def pct_abs(x, decimals: int = 0) -> str:
    return f"{abs(x):.{decimals}f}%"


def signed(x, decimals: int = 0) -> str:
    return i18n.fmt_signed(x, decimals, pct=True)


class ClientReport:
    """The slides of one monthly deck, as data."""

    def __init__(self, rep: dd.Report, lang: str, label: str):
        self.rep, self.lang, self.label = rep, lang, label
        self.T = {**i18n.table(lang), **client_i18n.table(lang)}
        self.res = rep.resolved or resolved_mod.Resolved()
        self.cur = rep.row()
        self.prev = rep.row(rep.prev_period)
        self.year = rep.row(rep.year_period) if rep.year_period else None
        self.month = i18n.month_name(rep.period, lang)
        self.month_short = i18n.month_name(rep.period, lang, short=True)
        self.prev_short = i18n.month_name(rep.prev_period, lang, short=True)
        self.year_n = int(rep.period[:4]) - 1
        first = periods.parse_month(rep.period)
        self.next_period = periods.month_label(periods.add_months(first, 1))
        self.next_month = i18n.month_name(self.next_period, lang)
        self.series = self._series()
        self.segments = [s for s in rep.segments if s != "All" and not self.res.segment(s)
                         and rep.row(segment=s)]
        self.pos_worse = self._pos_worse()
        self.gone = [g for g in rep.gone if not self.res.page(g["Page"])]
        self.opps = [o for o in rep.opps if not self._opp_resolved(o)]

    # ---- small helpers ----------------------------------------------------------------
    def _series(self) -> list:
        """[(label, clicks, impressions, ctr, position)] of the months up to this one, oldest first."""
        out = []
        for label in self.rep.labels:
            row = self.rep.row(label)
            if row:
                out.append((label, num(row, "Clicks"), num(row, "Impressions"), num(row, "CTR (%)"),
                            num(row, "Avg. Position")))
        return out

    def _pos_worse(self) -> float:
        pos, prev = num(self.cur, "Avg. Position"), num(self.prev, "Avg. Position")
        return (pos - prev) if (pos is not None and prev is not None) else 0.0

    def _opp_resolved(self, o: dict) -> bool:
        return (self.res.keyword(o.get("keyword", "")) or self.res.page(o.get("page", ""))
                or self.res.segment(o.get("segment", "")))

    def label_of_page(self, url: str) -> str:
        return tabs.path_of(url)

    def decline_tone(self) -> str:
        """A drop is grey; red only when rankings really fell (position worse by 0.5 or more)."""
        return RED if self.pos_worse >= 0.5 else GREY

    def opps_of(self, *kinds) -> list:
        rows = [o for o in self.opps if o["type"] in kinds]
        return sorted(rows, key=lambda o: -(dd.num(o["potential_gain_month"]) or 0))

    def lead_segment(self):
        """(segment, month-on-month %, year-on-year %) of the segment with the biggest rise."""
        best = None
        for s in self.segments:
            row = self.rep.row(segment=s)
            mom, yoy = num(row, "Clicks MoM %"), num(row, "Clicks YoY %")
            if mom is not None and mom > 0 and (best is None or mom > best[1]):
                best = (s, mom, yoy)
        return best

    def quick_items(self, limit: int = 5) -> list:
        """The top quick wins and click-rate fixes (one row per keyword, biggest gain first)."""
        seen, out = set(), []
        for o in self.opps_of("quick_win", "ctr_fix"):
            if o["keyword"] in seen or (dd.num(o["potential_gain_month"]) or 0) <= 0:
                continue
            seen.add(o["keyword"])
            out.append(o)
        return out[:limit]


    # ---- slide 2 -------------------------------------------------------------------------
    def headline(self):
        T, cur = self.T, self.cur
        if not cur:
            return None
        pos, ctr = num(cur, "Avg. Position"), num(cur, "CTR (%)")
        prev_pos, prev_ctr = num(self.prev, "Avg. Position"), num(self.prev, "CTR (%)")
        earlier = [p for (_, _, _, _, p) in self.series[-12:-1] if p is not None]
        best = pos is not None and len(earlier) >= 6 and pos <= min(earlier)
        yoy = num(cur, "Clicks YoY %")
        ctr_up = ctr is not None and prev_ctr is not None and ctr > prev_ctr
        lead = self.lead_segment()
        if best:
            head = T["h_pos"].format(site=self.label)
        elif yoy is not None and yoy >= 10:
            head = T["h_yoy"].format(pct=pct_abs(yoy))
        elif ctr_up:
            head = T["h_ctr"]
        elif lead:
            head = T["h_seg"].format(segment=lead[0])
        else:
            head = T["h_flat"].format(site=self.label)

        a = None
        if pos is not None:
            improved = prev_pos is not None and pos < prev_pos
            a = T["sup_pos_up" if improved else "sup_pos"].format(pos=f1(pos))
        b = T["sup_ctr"].format(ctr=f"{ctr:.2f}%") if ctr_up else None
        sentences = []
        if a and b:
            sentences.append(T["sup_and"].format(a=a, b=b))
        elif a or b:
            sentences.append(T["sup_one"].format(a=a or b))
        if lead and lead[2] is not None:
            sentences.append(T["sup_seg"].format(segment=lead[0], mom=signed(lead[1]),
                                                 yoy=signed(lead[2]), month=self.month))
        cards = []
        if pos is not None:
            prev_s = f1(prev_pos) if prev_pos is not None else None
            year_pos = num(self.year, "Avg. Position")
            year_s = f1(year_pos) if year_pos is not None else None
            improved = prev_pos is not None and pos < prev_pos
            stem = "c_pos_body_up" if improved else "c_pos_body"
            if prev_s and year_s:
                body = T[stem].format(pos=f1(pos), prev=prev_s, yoy=year_s)
            elif prev_s:
                body = T[stem + "_prev"].format(pos=f1(pos), prev=prev_s)
            elif year_s:
                body = T[stem + "_yoy"].format(pos=f1(pos), yoy=year_s)
            else:
                body = T["c_pos_body_now"].format(pos=f1(pos))
            cards.append({"title": T["c_best_pos" if best else "c_pos_title"], "body": body,
                          "tone": "green"})
        clicks = num(cur, "Clicks")
        if yoy is not None and clicks is not None:
            before = clicks - (num(cur, "Clicks YoY") or 0)
            impr_yoy = num(cur, "Impr YoY %")
            if abs(yoy) < STEADY_YOY:
                cards.append({"title": T["c_clicks_steady_t"], "tone": "periwinkle",
                              "body": T["c_clicks_steady_b"].format(
                                  clicks=i18n.fmt_int(clicks), pct=f"{max(1, round(abs(yoy)))}%",
                                  month=self.month,
                                  fewer=T["c_fewer"] if (impr_yoy is not None and impr_yoy < 0) else "")})
            elif yoy > 0:
                cards.append({"title": T["c_clicks_up_t"].format(pct=pct_abs(yoy)), "tone": "periwinkle",
                              "body": T["c_clicks_up_b"].format(clicks=i18n.fmt_int(clicks),
                                                                before=i18n.fmt_int(before),
                                                                month=self.month)})
            else:
                cards.append({"title": T["c_clicks_down_t"], "tone": "periwinkle",
                              "body": T["c_clicks_down_b"].format(clicks=i18n.fmt_int(clicks),
                                                                  before=i18n.fmt_int(before),
                                                                  month=self.month, pct=signed(yoy))})
        wins = self.quick_items()
        if wins:
            gain = int(sum(dd.num(o["potential_gain_month"]) or 0 for o in wins))
            cards.append({"title": T["c_room_t"], "tone": "amber",
                          "body": T["c_room_b1" if len(wins) == 1 else "c_room_b"].format(
                              n=len(wins), gain=i18n.fmt_int(gain))})
        return {"headline": head, "support": " ".join(sentences), "cards": cards}

    # ---- slide 3 -------------------------------------------------------------------------
    def scorecard(self):
        T, cur, prev, year = self.T, self.cur, self.prev, self.year
        if not cur:
            return None
        down = self.decline_tone()
        impr_mom, clicks_mom = num(cur, "Impr MoM %"), num(cur, "Clicks MoM %")
        clicks_yoy = num(cur, "Clicks YoY %")
        pos, prev_pos = num(cur, "Avg. Position"), num(prev, "Avg. Position")
        ctr, prev_ctr = num(cur, "CTR (%)"), num(prev, "CTR (%)")

        def pct_line(label, value):
            if value is None:
                return None
            return {"label": label, "text": signed(value, 1), "tone": GOOD if value > 0 else down}

        def pt_line(label, now, before):
            if now is None or before is None:
                return None
            d = now - before
            return {"label": label, "text": f"{i18n.fmt_signed(d, 2)} {T['c_pt']}",
                    "tone": GOOD if d > 0.004 else down}

        def pos_line(label, now, before):
            if now is None or before is None:
                return None
            d = before - now                       # positive: a better (lower) position
            if abs(d) < 0.05:
                return {"label": label, "text": f"= {f1(now)}", "tone": GREY}
            tone = GOOD if d > 0 else (RED if -d >= 0.5 else GREY)
            return {"label": label, "text": f"{UP if d > 0 else DOWN} {f1(abs(d))}", "tone": tone}

        vs_prev, vs_year = T["c_vs"].format(m=self.prev_short), T["c_vs_year"]
        yoy_text = (T["c_yoy_flat"] if clicks_yoy is None or abs(clicks_yoy) < STEADY_YOY
                    else T["c_yoy_up" if clicks_yoy > 0 else "c_yoy_down"].format(pct=pct_abs(clicks_yoy)))

        # the one sentence under each number
        if clicks_mom is not None and clicks_mom > 0:
            n_clicks = T["c_n_clicks_up"].format(yoy=yoy_text)
        elif self.pos_worse >= 0.5:
            n_clicks = T["c_n_clicks_slip"].format(yoy=yoy_text)
        else:
            n_clicks = T["c_n_clicks_down"].format(yoy=yoy_text)
        if impr_mom is not None and impr_mom > 0:
            n_impr = T["c_n_impr_up"]
        elif len(self.gone) >= WATCH_GONE_MIN:
            n_impr = T["c_n_impr_gone"].format(n=len(self.gone))
        else:
            n_impr = T["c_n_impr_down"]
        n_ctr = T["c_n_ctr_up" if (ctr is not None and prev_ctr is not None and ctr > prev_ctr)
                  else "c_n_ctr_down"]
        n_pos = (T["c_n_pos_up"] if (pos is not None and prev_pos is not None and pos < prev_pos)
                 else T["c_n_pos_red" if self.pos_worse >= 0.5 else "c_n_pos_down"])

        cards = [
            {"label": T["sc_clicks"], "value": i18n.fmt_int(num(cur, "Clicks") or 0),
             "lines": [l for l in (pct_line(vs_prev, clicks_mom), pct_line(vs_year, clicks_yoy)) if l],
             "note": n_clicks},
            {"label": T["sc_impressions"], "value": i18n.fmt_int(num(cur, "Impressions") or 0),
             "lines": [l for l in (pct_line(vs_prev, impr_mom),
                                   pct_line(vs_year, num(cur, "Impr YoY %"))) if l],
             "note": n_impr},
            {"label": T["sc_ctr"], "value": f"{ctr:.2f}%" if ctr is not None else "—",
             "lines": [l for l in (pt_line(vs_prev, ctr, prev_ctr),
                                   pt_line(vs_year, ctr, num(year, "CTR (%)"))) if l],
             "note": n_ctr},
            {"label": T["sc_position"], "value": f1(pos) if pos is not None else "—",
             "lines": [l for l in (pos_line(vs_prev, pos, prev_pos),
                                   pos_line(vs_year, pos, num(year, "Avg. Position"))) if l],
             "note": n_pos},
        ]
        aio = num(cur, "AIO Impressions")
        if aio is not None:
            cards.append({"label": T["sc_aio"], "value": i18n.fmt_int(aio),
                          "lines": [l for l in (pct_line(vs_prev, num(cur, "AIO MoM %")),) if l],
                          "note": T["c_n_aio"]})
        year_month = f"{self.month_short} {self.year_n}"
        return {"kicker": T["c_k_score"].format(month=f"{self.month} {self.rep.period[:4]}",
                                                prev=self.prev_short, year=year_month),
                "cards": cards, "footnote": T["c_foot_grey"]}

    # ---- slide 4 -------------------------------------------------------------------------
    def climbs(self):
        T = self.T
        cands = []
        for r in self.rep.keywords:
            if r["Type"] in ("Growing", "Declining") and not self.res.keyword(r["Query"]):
                cands.append(("keyword", r["Query"], r))
        for r in self.rep.pages:
            if r["Type"] in ("Growing", "Declining") and not self.res.page(r["Page"]):
                cands.append(("page", self.label_of_page(r["Page"]), r))
        rows = []
        for kind, label, r in cands:
            impr, impr_prev = dd.num(r["Impressions"]), dd.num(r["Impr prev"])
            pos, prev = dd.num(r["Position"]), dd.num(r["Position prev"])
            if None in (impr, impr_prev, pos, prev):
                continue
            better = prev - pos
            if impr >= MIN_CLIMB_IMPRESSIONS and impr_prev >= MIN_CLIMB_IMPRESSIONS and better >= MIN_CLIMB:
                rows.append({"label": label, "prev": prev, "now": pos, "better": better,
                             "score": better * impr})
        if not rows:
            return None
        rows.sort(key=lambda x: -x["score"])
        shown = rows[:7]
        onto = [x for x in rows if x["prev"] > 10 and x["now"] <= 10]
        foot = ""
        if onto:
            names = [x["label"] for x in onto[:2]]
            joined = names[0] if len(names) == 1 else T["c_and"].format(a=names[0], b=names[1])
            foot = T["c_climb_foot_one" if len(onto) == 1 else "c_climb_foot"].format(
                n=len(onto), names=joined)
        pos, year_pos = num(self.cur, "Avg. Position"), num(self.year, "Avg. Position")
        card = None
        if pos is not None and year_pos is not None and pos < year_pos:
            impr_yoy, clicks_yoy = num(self.cur, "Impr YoY %"), num(self.cur, "Clicks YoY %")
            if impr_yoy is not None and impr_yoy < 0 and clicks_yoy is not None and clicks_yoy >= -3:
                body = T["c_climb_year"].format(impr=pct_abs(impr_yoy))
            else:
                body = T["c_climb_generic"].format(a=f1(year_pos), b=f1(pos))
            card = {"title": T["c_avg_pos"], "from": f1(year_pos), "to": f1(pos), "body": body,
                    "span": f"{self.month_short} {self.year_n} → {self.month_short} {self.year_n + 1}"}
        table = []
        for x in shown:
            table.append({"label": x["label"], "prev": f1(x["prev"]), "now": f1(x["now"]),
                          "change": f"{UP} {f1(x['better'])}"})
        return {"card": card, "header": [T["c_col_kw_page"], self.prev_short, self.month_short, ""],
                "rows": table, "footnote": foot}

    # ---- slide 5 -------------------------------------------------------------------------
    def grew(self):
        T = self.T
        kws = [r for r in self.rep.keywords if r["Type"] == "Growing"
               and not self.res.keyword(r["Query"]) and (dd.num(r["Clicks diff"]) or 0) > 0]
        pgs = [r for r in self.rep.pages if r["Type"] == "Growing"
               and not self.res.page(r["Page"]) and (dd.num(r["Clicks diff"]) or 0) > 0]
        kws.sort(key=lambda r: -(dd.num(r["Clicks diff"]) or 0))
        pgs.sort(key=lambda r: -(dd.num(r["Clicks diff"]) or 0))
        if not kws and not pgs:
            return None

        def rows(items, label):
            return [{"label": label(r), "clicks": i18n.fmt_int(dd.num(r["Clicks"]) or 0),
                     "change": i18n.fmt_signed(dd.num(r["Clicks diff"]) or 0)} for r in items[:5]]

        seg_best = None
        for s in self.segments:
            yoy = num(self.rep.row(segment=s), "Clicks YoY %")
            if yoy is not None and yoy > 0 and (seg_best is None or yoy > seg_best[1]):
                seg_best = (s, yoy)
        yoy_pages = [r for r in self.rep.pages if r["Type"] == "YoY Growing" and not self.res.page(r["Page"])]
        yoy_pages.sort(key=lambda r: -(dd.num(r["Clicks diff"]) or 0))
        top = yoy_pages[0] if yoy_pages and (dd.num(yoy_pages[0]["Clicks diff"]) or 0) > 0 else None
        fact = None
        if seg_best and top:
            fact = T["c_yoy_both"].format(segment=seg_best[0], pct=pct_abs(seg_best[1]),
                                          page=self.label_of_page(top["Page"]),
                                          n=i18n.fmt_int(dd.num(top["Clicks diff"])))
        elif seg_best:
            fact = T["c_yoy_seg"].format(segment=seg_best[0], pct=pct_abs(seg_best[1]))
        elif top:
            fact = T["c_yoy_page"].format(page=self.label_of_page(top["Page"]),
                                          n=i18n.fmt_int(dd.num(top["Clicks diff"])))
        return {"left_title": T["c_h_kw_gain"], "right_title": T["c_h_pg_gain"],
                "keywords": rows(kws, lambda r: r["Query"]),
                "pages": rows(pgs, lambda r: self.label_of_page(r["Page"])),
                "fact": fact, "fact_title": T["c_yoy_best"]}

    # ---- slide 6 -------------------------------------------------------------------------
    def new_searches(self):
        T = self.T
        new = [r for r in self.rep.keywords if r["Type"] == "New" and not self.res.keyword(r["Query"])]
        new.sort(key=lambda r: -(dd.num(r["Impressions"]) or 0))
        if not new:
            return None
        cards = []
        for r in new[:3]:
            pos = dd.num(r["Position"])
            cards.append({"keyword": r["Query"],
                          "pill": T["c_pill_impr"].format(n=i18n.fmt_int(dd.num(r["Impressions"]) or 0)),
                          "position": T["c_new_pos"].format(pos=f1(pos)) if pos else ""})
        return {"intro": T["c_new_intro"].format(site=self.label), "cards": cards}

    # ---- slide 7 -------------------------------------------------------------------------
    def work(self):
        T = self.T
        return {"fill": T["c_fill"], "prompts": [p.format(site=self.label) for p in T["c_prompts"]]}

    # ---- slide 8 -------------------------------------------------------------------------
    def trend(self):
        T = self.T
        data = [(l, c) for (l, c, *_rest) in self.series[-13:] if c is not None]
        if len(data) < 3:
            return None
        labels = [i18n.short_month(l, self.lang) for l, _ in data]
        values = [c for _, c in data]
        notes = []
        peak_i = max(range(len(values)), key=lambda i: values[i])
        month_of = lambda l: i18n.month_name(l, self.lang)                  # noqa: E731
        notes.append({"title": month_of(data[peak_i][0]), "tone": "green",
                      "body": T["c_peak_b"].format(n=i18n.fmt_int(values[peak_i]))})
        median = statistics.median(values)
        low_i = min(range(len(values)), key=lambda i: values[i])
        if low_i != peak_i and values[low_i] < LOW_MONTH_SHARE * median:
            known = self.res.month(data[low_i][0])
            notes.append({"title": month_of(data[low_i][0]), "tone": "grey" if known else "red",
                          "body": T["c_low_known" if known else "c_low_b"].format(
                              n=i18n.fmt_int(values[low_i]))})
        last_l, last_c = data[-1]
        mom = num(self.cur, "Clicks MoM %")
        notes.append({"title": T["c_now"], "tone": "periwinkle",
                      "body": T["c_now_b"].format(n=i18n.fmt_int(last_c), month=month_of(last_l),
                                                  pct=signed(mom, 1) if mom is not None else "—",
                                                  prev=i18n.month_name(self.rep.prev_period, self.lang))})
        nxt = self._next3()
        if nxt is not None:
            notes.append({"title": T["c_next3_t"], "tone": "green" if nxt >= 0 else "grey",
                          "body": T["c_next3_b"].format(pct=signed(nxt, 1), month=self.month)})
        return {"title": T["c_t_trend"].format(n=len(data)), "chart_title": T["c_chart_clicks"],
                "labels": labels, "values": values, "notes": notes[:4]}

    def _next3(self):
        """How the next three months did on average, last year, against this month then."""
        first = periods.parse_month(self.rep.period)
        base = self.rep.row(periods.month_label(periods.add_months(first, -12)))
        base_clicks = num(base, "Clicks")
        nxt = [num(self.rep.row(periods.month_label(periods.add_months(first, -12 + k))), "Clicks")
               for k in (1, 2, 3)]
        if not base_clicks or any(v is None for v in nxt):
            return None
        return (sum(nxt) / 3 - base_clicks) / base_clicks * 100

    # ---- slide 9 -------------------------------------------------------------------------
    def sections(self):
        T = self.T
        segs = [s for s in self.segments if num(self.rep.row(segment=s), "Clicks") is not None]
        if len(segs) < 2:
            return None
        segs.sort(key=lambda s: -(num(self.rep.row(segment=s), "Clicks") or 0))
        rows, total = [], 0
        for s in segs:
            row = self.rep.row(segment=s)
            clicks = num(row, "Clicks") or 0
            total += clicks
            mom, yoy = num(row, "Clicks MoM %"), num(row, "Clicks YoY %")
            rows.append({"label": s, "clicks": clicks,
                         "mom": signed(mom, 1) if mom is not None else "—",
                         "mom_tone": GOOD if (mom or 0) > 0 else GREY,
                         "yoy": signed(yoy, 1) if yoy is not None else "—",
                         "yoy_tone": GOOD if (yoy or 0) > 0 else GREY})
        site_total = num(self.cur, "Clicks") or 0
        over = (total - site_total) / site_total * 100 if site_total else 0
        foot = (T["c_foot_overlap"].format(pct=f"{over:.0f}%") if over >= 1.5 else T["c_foot_exact"])
        return {"chart_title": T["c_chart_month"].format(month=self.month),
                "header": [T["c_col_section"], T["c_vs"].format(m=self.prev_short), T["c_vs_year"]],
                "rows": rows, "footnote": foot}

    # ---- slide 10 ------------------------------------------------------------------------
    def quick_wins(self):
        T = self.T
        items = self.quick_items()
        if not items:
            return None
        rows, total = [], 0
        for o in items:
            gain = int(dd.num(o["potential_gain_month"]) or 0)
            total += gain
            rows.append({"keyword": o["keyword"],
                         "searches": i18n.fmt_int((dd.num(o["impressions_90d"]) or 0) / 3),
                         "position": f1(dd.num(o["position"]) or 0), "gain": f"+{i18n.fmt_int(gain)}"})
        return {"intro": T["c_intro_wins"].format(site=self.label), "header": T["c_col_wins"],
                "rows": rows, "card": T["c_wins_card"].format(gain=i18n.fmt_int(total)),
                "card_note": T["c_wins_note"], "total": total, "count": len(rows)}

    # ---- slide 11 ------------------------------------------------------------------------
    def pages_to_update(self):
        T = self.T
        items = self.opps_of("update_page", "decliner")[:5]
        if not items:
            return None
        rows = []
        for o in items:
            ctr = dd.num(o["ctr"])
            rows.append({"page": self.label_of_page(o["page"]),
                         "impr": i18n.fmt_int(dd.num(o["impressions_90d"]) or 0),
                         "position": f1(dd.num(o["position"])) if dd.num(o["position"]) else "—",
                         "ctr": f"{ctr * 100:.2f}%" if ctr else "—",
                         "text": f"{o['diagnosis']} {o['action']}".strip()})
        return {"intro": T["c_intro_pages"], "header": T["c_col_pages"], "rows": rows}

    # ---- slide 12 ------------------------------------------------------------------------
    def ideas(self):
        T = self.T
        questions = self.opps_of("question")[:6]
        cannibal = self.opps_of("cannibal")[:3]
        if not questions and not cannibal:
            return None
        cards = []
        for o in questions:
            pos, ctr = dd.num(o["position"]) or 0, dd.num(o["ctr"]) or 0
            impr = i18n.fmt_int(dd.num(o["impressions_90d"]) or 0)
            sub = (T["c_q_pos"].format(n=impr, pos=f"{pos:.0f}") if pos > 5
                   else T["c_q_ctr"].format(n=impr, ctr=f"{ctr * 100:.2f}%"))
            cards.append({"keyword": o["keyword"], "sub": sub})
        merge = None
        if cannibal:
            counts = [int(_first_int(o["diagnosis"]) or 2) for o in cannibal]
            names = [o["keyword"] for o in cannibal]
            sep = "، " if self.lang == "fa" else ", "
            joined = (names[0] if len(names) == 1
                      else T["c_and"].format(a=sep.join(names[:-1]), b=names[-1]))
            merge = {"title": T["c_cannibal"],
                     "body": T["c_cannibal_b"].format(names=joined, n=min(counts))}
        return {"intro": T["c_intro_q"].format(site=self.label), "cards": cards, "merge": merge}

    # ---- slide 13 ------------------------------------------------------------------------
    def next_month_slide(self, nums: dict = None):
        T = self.T
        first = periods.parse_month(self.rep.period)
        a = self.rep.row(periods.month_label(periods.add_months(first, -12)))
        b = self.rep.row(periods.month_label(periods.add_months(first, -11)))
        clicks_a, clicks_b = num(a, "Clicks"), num(b, "Clicks")
        if not clicks_a or clicks_b is None:
            return None
        cards = []
        whole = (clicks_b - clicks_a) / clicks_a * 100
        impr_a, impr_b = num(a, "Impressions"), num(b, "Impressions")
        sub = ""
        if impr_a and impr_b is not None:
            sub = T["c_plus_impr"].format(pct=signed((impr_b - impr_a) / impr_a * 100, 1))
        cards.append({"label": T["c_whole"], "value": signed(whole, 1 if abs(whole) < 100 else 0),
                      "word": T["c_clicks_word"], "sub": sub, "tone": GOOD if whole >= 0 else GREY})
        growth = []
        for s in self.segments:
            ra = self.rep.row(periods.month_label(periods.add_months(first, -12)), s)
            rb = self.rep.row(periods.month_label(periods.add_months(first, -11)), s)
            ca, cb = num(ra, "Clicks"), num(rb, "Clicks")
            if ca and cb is not None:
                growth.append((s, (cb - ca) / ca * 100, num(self.rep.row(segment=s), "Clicks") or 0))
        for s, g, _ in sorted(growth, key=lambda x: -x[2])[:4]:
            cards.append({"label": s, "value": signed(g, 0), "word": T["c_clicks_word"], "sub": "",
                          "tone": GOOD if g >= 0 else GREY})
        bullets = []
        slide_ref = (nums or {}).get("pages")
        for s, g, _ in sorted((x for x in growth if x[1] > 0), key=lambda x: -x[1])[:2]:
            bullets.append(T["c_do_seg_slide" if slide_ref else "c_do_seg"].format(
                segment=s, n=slide_ref))
        seasonal = [o for o in self.opps_of("seasonal")]
        seasonal.sort(key=lambda o: -(dd.num(o["potential_gain_month"]) or 0))
        for o in seasonal[:3]:
            pct = _first_pct(o["diagnosis"])
            if pct is not None:
                bullets.append(T["c_do_page"].format(page=self.label_of_page(o["page"]),
                                                     month=self.next_month, pct=signed(pct, 0)))
        return {"title": T["c_t_next"].format(month=self.next_month),
                "intro": T["c_next_intro"].format(m1=self.month, m2=self.next_month),
                "cards": cards, "do_title": T["c_do_now"], "bullets": bullets}

    # ---- slide 14 ------------------------------------------------------------------------
    def watching(self):
        T = self.T
        items = []
        for s in self.segments:                                   # segment drops year on year
            row = self.rep.row(segment=s)
            yoy, mom = num(row, "Clicks YoY %"), num(row, "Clicks MoM %")
            if yoy is not None and yoy <= WATCH_SEGMENT_DROP:
                mom_part = (T["c_w_mom"].format(mom=signed(mom)) if (mom is not None and mom > 0) else "")
                items.append({"title": s, "body": T["c_w_seg_b"].format(yoy=signed(yoy), mom=mom_part),
                              "weight": yoy})
        items.sort(key=lambda x: x["weight"])
        items = items[:2]
        if len(self.gone) >= WATCH_GONE_MIN:
            items.append({"title": T["c_w_old"], "body": T["c_w_old_b"].format(n=len(self.gone))})
        by_url = {r["Page"]: r for r in self.rep.pages if r["Type"] == "Declining"}
        for o in self.opps_of("decliner")[:2]:
            r = by_url.get(o["page"])
            if r:
                items.append({"title": self.label_of_page(o["page"]),
                              "body": T["c_w_dec_b"].format(
                                  a=i18n.fmt_int(dd.num(r["Clicks prev"]) or 0),
                                  b=i18n.fmt_int(dd.num(r["Clicks"]) or 0), diag=o["diagnosis"])})
        for o in self.opps_of("check_page")[:1]:
            drop = _first_pct(o["diagnosis"])
            items.append({"title": self.label_of_page(o["page"]),
                          "body": T["c_w_check_b"].format(drop=signed(-abs(drop)) if drop is not None else "")})
        items = items[:MAX_WATCH]
        if not items:
            return None
        return {"items": [{"title": i["title"], "body": i["body"]} for i in items]}

    # ---- slide 15 ------------------------------------------------------------------------
    def focus(self, nums: dict = None):
        T, nums = self.T, nums or {}
        cards = []
        wins = self.quick_wins()
        if wins:
            s = nums.get("wins")
            cards.append({"title": T["c_f_wins_t"].format(n=wins["count"]),
                          "body": T["c_f_wins_b" if s else "c_f_wins_b0"].format(
                              s=s, gain=i18n.fmt_int(wins["total"])), "tone": "amber"})
        cannibal = self.opps_of("cannibal")
        if cannibal:
            cards.append({"title": T["c_f_merge_t"],
                          "body": T["c_f_merge_b"].format(n=min(len(cannibal), 3)), "tone": "tint"})
        questions = self.opps_of("question")
        if questions:
            s = nums.get("ideas")
            cards.append({"title": T["c_f_q_t"], "body": T["c_f_q_b" if s else "c_f_q_b0"].format(s=s),
                          "tone": "tint"})
        if self.opps_of("seasonal"):
            cards.append({"title": T["c_f_season_t"].format(month=self.next_month),
                          "body": T["c_f_season_b"].format(month=self.next_month), "tone": "tint"})
        return {"cards": cards[:4]} if cards else None

    # ---- the whole deck ------------------------------------------------------------------
    def slides(self) -> list:
        """[(id, payload)] in deck order; slides without data are left out."""
        base = [("headline", self.headline()), ("scorecard", self.scorecard()),
                ("climbs", self.climbs()), ("grew", self.grew()), ("new", self.new_searches()),
                ("work", self.work()), ("trend", self.trend()), ("sections", self.sections()),
                ("wins", self.quick_wins()), ("pages", self.pages_to_update()),
                ("ideas", self.ideas()), ("next", self.next_month_slide()),
                ("watch", self.watching()), ("focus", self.focus())]
        present = [sid for sid, payload in base if payload]
        # slide numbers (the title is slide 1) for the texts that point at other slides
        nums = {sid: i + 2 for i, sid in enumerate(present)}
        out = []
        for sid, payload in base:
            if not payload:
                continue
            if sid == "next":
                payload = self.next_month_slide(nums)
            elif sid == "focus":
                payload = self.focus(nums)
            out.append((sid, payload))
        return out


def _first_int(text: str):
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def _first_pct(text: str):
    m = re.search(r"([+\-−]?\d+(?:\.\d+)?)%", text or "")
    if not m:
        return None
    return float(m.group(1).replace("−", "-"))
