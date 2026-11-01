"""
PulseO: wording of the decks and emails (English and Persian)
=============================================================
Every label that appears on a slide or in an email comes from this table, so a
language is just another dictionary. Numbers keep Latin digits in both languages.

Persian text is right-to-left. Numbers, %, + and minus signs inside it are wrapped
in LRM (U+200E) so they keep their order (see `bidi`).
"""

import re
from datetime import date

LRM = "‎"
MINUS = "−"

EN = {
    # report names, footer
    "monthly_report": "Monthly SEO Report",
    "weekly_report": "Weekly SEO Report",
    "monthly_report_lc": "monthly SEO report",
    "weekly_report_lc": "weekly SEO report",
    "week": "Week {n}",
    "footer": "{label} · {report} · {period}",
    # summary
    "summary_placeholder": "Summary not generated automatically",
    "summary_hint": "To get a written summary, copy this slide's speaker notes into Claude "
                    "and ask for a plain-language summary for non-SEO readers.",
    "next_steps": "Recommended next steps",
    "notes_heading": "Report data for the summary",
    # action titles
    "clicks": "Clicks",
    "title_same": "Clicks {dir} {m} {when}, {y} vs last year",
    "title_mixed": "Clicks {dir} {m} {when}, {dir2} {y} vs last year",
    "title_mom_only": "Clicks {dir} {m} {when}",
    "title_flat": "Clicks are steady {when}",
    "dir_down": "down", "dir_up": "up",
    "this_month": "this month", "this_week": "this week",
    # alarms
    "alarms_title": "{n} alarms {when}, {mix}",
    "alarms_title_one": "1 alarm {when}, {mix}",
    "alarms_all": "all {sev}",
    "alarms_mix": "{high} high, {medium} medium",
    "alarms_none_title": "No major changes {when}",
    "alarms_none": "No major changes this month.",
    "alarms_none_week": "No major changes this week.",
    "sev_high": "HIGH", "sev_medium": "MEDIUM", "sev_low": "LOW",
    "sev_word_high": "high", "sev_word_medium": "medium", "sev_word_low": "low",
    "area_Segment clicks": "Traffic",
    "area_Lost keyword": "Lost keyword",
    "area_Page loss": "Page loss",
    "area_AI Overviews": "AI search",
    "area_New URL pattern": "New URL pattern",
    "area_AIO import": "AI data import",
    "area_Traffic": "Traffic",
    "clicks_all": "Clicks", "clicks_seg": "Clicks for {seg}",
    "impressions_all": "Impressions", "impressions_seg": "Impressions for {seg}",
    "msg_clicks_up": "{metric} rose {pct} vs {prev} ({a} to {b}).",
    "msg_clicks_down": "{metric} fell {pct} vs {prev} ({a} to {b}).",
    "msg_lost_kw": "The keyword \u201c{kw}\u201d brought {n} clicks in {prev} and is not in Search "
                   "Console's data in {period}.",
    "msg_page_loss": "The page {page} lost {pct} of its clicks vs {prev} ({a} to {b}).",
    "msg_more": "and {n} more pages lost {pct} of their clicks",
    "msg_aio_drop": "AI Overview impressions fell {pct} vs {prev} ({a} to {b}).",
    "weekly_fell": "{metric} fell {pct} vs the previous week ({a} to {b}).",
    # scorecard
    "sc_clicks": "Clicks", "sc_impressions": "Impressions", "sc_ctr": "CTR",
    "sc_position": "Avg. position", "sc_aio": "AIO impressions",
    "mom": "MoM", "wow": "WoW", "yoy": "YoY", "pt": "pt",
    "better": "better", "worse": "worse", "not_imported": "Not imported",
    "what_it_means": "What it means",
    "means_position_up_clicks_down": "Rankings look better than last year (position {a} to {b}) "
                                     "while clicks fell. Ranking higher but getting clicked less "
                                     "usually means the site appears in a SERP feature, such as an "
                                     "image block, instead of a normal result.",
    "means_both_down": "Clicks and impressions both fell: Google is showing the site less often, "
                       "not only getting clicked less. Check indexing and recent ranking changes.",
    "means_both_up": "Clicks and impressions grew together: more visibility is turning into "
                     "more visits.",
    # trend
    "trend_title_peak": "Last {n} {unit}: clicks peaked in {peak}, now {pct} below",
    "trend_title_high": "Last {n} {unit}: clicks are at their highest",
    "trend_title_plain": "Clicks and impressions, last {n} {unit}",
    "units_months": "months", "units_weeks": "weeks",
    "trend_caption": "Highest: {hi_label} ({hi} clicks). Lowest: {lo_label} ({lo} clicks).",
    "axis_clicks": "Clicks", "axis_impressions": "Impressions",
    "series_clicks": "Clicks", "series_impressions": "Impressions",
    # segments
    "segments_title": "Clicks by segment",
    "segment_all": "Whole site",
    "col_segment": "Segment", "col_clicks": "Clicks", "col_impr": "Impr.",
    "col_mom": "MoM", "col_yoy": "YoY", "col_aio": "AIO",
    # keywords and pages
    "col_query": "Query", "col_page": "Page", "col_change": "Change",
    "col_impr_month": "Impr. {m}",
    "kw_title": "{lost} keywords lost, {new} new",
    "kw_title_one": "{lost} keyword lost, {new} new",
    "h_lost": "Lost (top 10 by last month's impressions)",
    "h_new": "New (top 10 by impressions)",
    "no_lost": "No lost keywords this month",
    "no_new": "No new keywords this month",
    "movers_title": "Keyword movers: {up} clicks gained, {down} lost",
    "movers_title_plain": "Growing and declining keywords",
    "h_kw_up": "Growing keywords", "h_kw_down": "Declining keywords",
    "no_kw_up": "No growing keywords this month", "no_kw_down": "No declining keywords this month",
    "pages_title_loss": "The page {page} lost {n} clicks",
    "pages_title_loss_generic": "The top declining page lost {n} clicks",
    "pages_title_plain": "Growing and declining pages",
    "h_pg_up": "Growing pages", "h_pg_down": "Declining pages",
    "no_pg_up": "No growing pages this month", "no_pg_down": "No declining pages this month",
    "yoy_title": "Year over year: {up} clicks gained, {down} lost",
    "yoy_title_none": "Year over year: not enough history yet",
    "h_yoy_up": "Growing vs {period}", "h_yoy_down": "Declining vs {period}",
    "no_yoy_up": "No growing pages vs last year", "no_yoy_down": "No declining pages vs last year",
    "no_yoy": "Search Console does not hold data for the same month last year, so a "
              "year-over-year comparison is not possible yet.",
    # weekly movers
    "wk_movers_title": "Top keyword movers this week",
    "wk_h_up": "Growing keywords (vs previous week)", "wk_h_down": "Declining keywords (vs previous week)",
    "no_wk_movers": "No keyword data for this week",
    # AIO
    "aio_title": "AI search: {n} impressions in {month}",
    "aio_title_none": "AI search: no data imported for {month}",
    "aio_pages_title": "AI search: pages that gained and lost impressions",
    "aio_chart_caption": "AI Overview impressions per month, from the Search Console Generative AI report.",
    "h_aio_up": "Growing in AI search", "h_aio_down": "Declining in AI search",
    "col_aio_impr": "AIO impr.",
    "no_aio_up": "No growing pages", "no_aio_down": "No declining pages",
    "aio_howto_heading": "How to add AI Overview data (2 minutes a month)",
    "aio_step1": "In Search Console, open the Generative AI report, pick last month, and export it "
                 "to Google Sheets",
    "aio_step2": "Copy the Pages and Dates tabs into this site's Reports sheet",
    "aio_step3": "Rename them “AIO {ym} Pages” and “AIO {ym} Dates”. The next run adds "
                 "AI search to this slide",
    "aio_manual": "Search Console has no API for AI Overview data yet, so this one step stays manual.",
    # about
    "about_title": "About this report",
    "about_source": "Source: Google Search Console.",
    "about_complete": "Complete periods only: data is final 2 to 3 days after each day ends, so "
                      "partial months and weeks are never shown.",
    "about_changes": "MoM compares with the previous month, WoW with the previous week, YoY with the "
                     "same period last year.",
    "about_keywords": "Lost and new keywords: queries above {n} impressions that disappeared or "
                      "appeared. Anonymized queries are not included.",
    "about_types_none": "Page types: none for this site, so all numbers cover the whole site.",
    "about_types": "Page types in use: {names}. Types can overlap.",
    "about_history": "History beyond Google's 16 months is kept in the site's history sheet.",
    # email
    "report_name": "SEO Report",
    "prepared": "Prepared by Mehdi Raoof",
    "mail_subject": "{label} {report_name} · {period}",
    "mail_greeting": "Hi,",
    "mail_intro": "Here is the SEO report for {label}, {period}. It is attached as a PowerPoint file "
                  "and also opens in Google Slides.",
    "mail_clicks": "{label} got {clicks} clicks from Google{change}.",
    "mail_up": ", up {pct} on {prev}", "mail_down": ", down {pct} on {prev}",
    "mail_flat": ", about the same as {prev}",
    "mail_impr": "It showed up {impr} times in search, and the average position was {pos}{pos_prev}.",
    "mail_pos_prev": " ({prev_pos} in {prev})",
    "mail_prev_week": "last week",
    "mail_sign": "Mehdi",
    "months": ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"],
    "months_short": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "jalali_months": [],
}

FA = {
    "monthly_report": "گزارش ماهانه سئو",
    "weekly_report": "گزارش هفتگی سئو",
    "monthly_report_lc": "گزارش ماهانه سئو",
    "weekly_report_lc": "گزارش هفتگی سئو",
    "week": "هفته {n}",
    "footer": "{label} · {report} · {period}",
    "summary_placeholder": "خلاصه به‌صورت خودکار تولید نشده است",
    "summary_hint": "برای دریافت خلاصه، یادداشت‌های این اسلاید را در Claude بچسبانید و یک خلاصه "
                    "ساده برای افراد غیر متخصص سئو بخواهید.",
    "next_steps": "گام‌های پیشنهادی",
    "notes_heading": "داده‌های گزارش برای خلاصه",
    "clicks": "کلیک‌ها",
    "title_same": "کلیک‌ها {m} {dir} {when}، {y} {dir} نسبت به سال قبل",
    "title_mixed": "کلیک‌ها {m} {dir} {when}، {y} {dir2} نسبت به سال قبل",
    "title_mom_only": "کلیک‌ها {m} {dir} {when}",
    "title_flat": "کلیک‌ها {when} ثابت ماند",
    "dir_down": "کمتر شد", "dir_up": "بیشتر شد",
    "this_month": "نسبت به ماه قبل", "this_week": "نسبت به هفته قبل",
    "alarms_title": "{n} هشدار {when}، {mix}",
    "alarms_title_one": "۱ هشدار {when}، {mix}",
    "alarms_all": "همه {sev}",
    "alarms_mix": "{high} مهم، {medium} متوسط",
    "alarms_none_title": "تغییر مهمی نبود",
    "alarms_none": "این ماه تغییر مهمی نبود.",
    "alarms_none_week": "این هفته تغییر مهمی نبود.",
    "sev_high": "مهم", "sev_medium": "متوسط", "sev_low": "کم",
    "sev_word_high": "مهم", "sev_word_medium": "متوسط", "sev_word_low": "کم",
    "area_Segment clicks": "ترافیک",
    "area_Lost keyword": "کلمه از دست رفته",
    "area_Page loss": "افت صفحه",
    "area_AI Overviews": "جستجوی هوش مصنوعی",
    "area_New URL pattern": "الگوی جدید آدرس",
    "area_AIO import": "ورود داده هوش مصنوعی",
    "area_Traffic": "ترافیک",
    "clicks_all": "کلیک‌ها", "clicks_seg": "کلیک‌های {seg}",
    "impressions_all": "نمایش‌ها", "impressions_seg": "نمایش‌های {seg}",
    "msg_clicks_up": "{metric} نسبت به {prev} {pct} بیشتر شد ({a} به {b}).",
    "msg_clicks_down": "{metric} نسبت به {prev} {pct} کمتر شد ({a} به {b}).",
    "msg_lost_kw": "کلمه «{kw}» در {prev} {n} کلیک آورد و در داده سرچ کنسول {period} دیده نمی‌شود.",
    "msg_page_loss": "صفحه {page} نسبت به {prev} {pct} از کلیک‌های خود را از دست داد ({a} به {b}).",
    "msg_more": "و {n} صفحه دیگر {pct} از کلیک‌های خود را از دست دادند",
    "msg_aio_drop": "نمایش‌های AI Overview نسبت به {prev} {pct} کمتر شد ({a} به {b}).",
    "weekly_fell": "{metric} نسبت به هفته قبل {pct} کم شد ({a} به {b}).",
    "sc_clicks": "کلیک‌ها", "sc_impressions": "نمایش‌ها", "sc_ctr": "نرخ کلیک",
    "sc_position": "میانگین جایگاه", "sc_aio": "نمایش در AI Overview",
    "mom": "ماه قبل", "wow": "هفته قبل", "yoy": "سال قبل", "pt": "واحد",
    "better": "بهتر", "worse": "بدتر", "not_imported": "وارد نشده",
    "what_it_means": "معنی این اعداد",
    "means_position_up_clicks_down": "جایگاه سایت نسبت به پارسال بهتر شده (از {a} به {b}) اما "
                                     "کلیک‌ها به‌شدت کم شده. بالاتر بودن جایگاه همراه با کلیک کمتر "
                                     "معمولاً یعنی سایت در یک بخش ویژه نتایج، مثل بلوک تصاویر، دیده "
                                     "می‌شود و نه به‌عنوان یک نتیجه معمولی.",
    "means_both_down": "کلیک‌ها و نمایش‌ها هر دو کم شده‌اند: گوگل سایت را کمتر نشان می‌دهد، نه "
                       "فقط اینکه کلیک کمتری می‌گیرد. ایندکس و تغییرات اخیر رتبه را بررسی کنید.",
    "means_both_up": "کلیک‌ها و نمایش‌ها با هم رشد کرده‌اند: دیده شدن بیشتر به بازدید بیشتر تبدیل می‌شود.",
    "trend_title_peak": "{n} {unit} گذشته: اوج کلیک‌ها در {peak} بود و اکنون {pct} پایین‌تر است",
    "trend_title_high": "{n} {unit} گذشته: کلیک‌ها در بالاترین سطح هستند",
    "trend_title_plain": "کلیک‌ها و نمایش‌ها، {n} {unit} گذشته",
    "units_months": "ماه", "units_weeks": "هفته",
    "trend_caption": "بیشترین: {hi_label} ({hi} کلیک). کمترین: {lo_label} ({lo} کلیک).",
    "axis_clicks": "کلیک", "axis_impressions": "نمایش",
    "series_clicks": "کلیک‌ها", "series_impressions": "نمایش‌ها",
    "segments_title": "کلیک‌ها به تفکیک بخش",
    "segment_all": "کل سایت",
    "col_segment": "بخش", "col_clicks": "کلیک", "col_impr": "نمایش",
    "col_mom": "ماه قبل", "col_yoy": "سال قبل", "col_aio": "AIO",
    "col_query": "کلمه کلیدی", "col_page": "صفحه", "col_change": "تغییر",
    "col_impr_month": "نمایش {m}",
    "kw_title": "{lost} کلمه از دست رفت، {new} کلمه جدید",
    "kw_title_one": "{lost} کلمه از دست رفت، {new} کلمه جدید",
    "h_lost": "کلمات از دست رفته",
    "h_new": "کلمات جدید",
    "no_lost": "این ماه کلمه‌ای از دست نرفت",
    "no_new": "این ماه کلمه جدیدی نبود",
    "movers_title": "تغییر کلمات: {up} کلیک بیشتر، {down} کلیک کمتر",
    "movers_title_plain": "کلمات در حال رشد و افت",
    "h_kw_up": "کلمات در حال رشد", "h_kw_down": "کلمات در حال افت",
    "no_kw_up": "این ماه کلمه‌ای رشد نکرد", "no_kw_down": "این ماه کلمه‌ای افت نکرد",
    "pages_title_loss": "صفحه {page} {n} کلیک از دست داد",
    "pages_title_loss_generic": "صفحه‌ای که بیشترین افت را داشت {n} کلیک از دست داد",
    "pages_title_plain": "صفحات در حال رشد و افت",
    "h_pg_up": "صفحات در حال رشد", "h_pg_down": "صفحات در حال افت",
    "no_pg_up": "این ماه صفحه‌ای رشد نکرد", "no_pg_down": "این ماه صفحه‌ای افت نکرد",
    "yoy_title": "نسبت به سال قبل: {up} کلیک بیشتر، {down} کلیک کمتر",
    "yoy_title_none": "نسبت به سال قبل: هنوز داده کافی نیست",
    "h_yoy_up": "رشد نسبت به {period}", "h_yoy_down": "افت نسبت به {period}",
    "no_yoy_up": "صفحه‌ای نسبت به سال قبل رشد نکرد", "no_yoy_down": "صفحه‌ای نسبت به سال قبل افت نکرد",
    "no_yoy": "سرچ کنسول داده همین ماه سال قبل را ندارد، پس مقایسه با سال قبل هنوز ممکن نیست.",
    "wk_movers_title": "بیشترین تغییر کلمات این هفته",
    "wk_h_up": "کلمات در حال رشد (نسبت به هفته قبل)", "wk_h_down": "کلمات در حال افت (نسبت به هفته قبل)",
    "no_wk_movers": "برای این هفته داده کلمات وجود ندارد",
    "aio_title": "جستجوی هوش مصنوعی: {n} نمایش در {month}",
    "aio_title_none": "جستجوی هوش مصنوعی: داده {month} وارد نشده",
    "aio_pages_title": "جستجوی هوش مصنوعی: صفحاتی که نمایش گرفتند و از دست دادند",
    "aio_chart_caption": "نمایش‌های AI Overview در هر ماه، از گزارش Generative AI سرچ کنسول.",
    "h_aio_up": "رشد در جستجوی هوش مصنوعی", "h_aio_down": "افت در جستجوی هوش مصنوعی",
    "col_aio_impr": "نمایش AIO",
    "no_aio_up": "صفحه‌ای رشد نکرد", "no_aio_down": "صفحه‌ای افت نکرد",
    "aio_howto_heading": "افزودن داده AI Overview (ماهی ۲ دقیقه)",
    "aio_step1": "در سرچ کنسول گزارش Generative AI را باز کنید، ماه گذشته را انتخاب کنید و به "
                 "Google Sheets خروجی بگیرید",
    "aio_step2": "تب‌های Pages و Dates را به شیت گزارش‌های این سایت کپی کنید",
    "aio_step3": "نام آن‌ها را «AIO {ym} Pages» و «AIO {ym} Dates» بگذارید. اجرای بعدی جستجوی "
                 "هوش مصنوعی را به این اسلاید اضافه می‌کند",
    "aio_manual": "سرچ کنسول هنوز API برای داده AI Overview ندارد، پس همین یک مرحله دستی می‌ماند.",
    "about_title": "درباره این گزارش",
    "about_source": "منبع: Google Search Console.",
    "about_complete": "فقط دوره‌های کامل: داده ۲ تا ۳ روز پس از پایان هر روز نهایی می‌شود، پس ماه "
                      "یا هفته ناقص هرگز نشان داده نمی‌شود.",
    "about_changes": "ماه قبل با ماه پیش، هفته قبل با هفته پیش و سال قبل با همان دوره سال گذشته "
                     "مقایسه می‌شود.",
    "about_keywords": "کلمات از دست رفته و جدید: عبارت‌هایی با بیش از {n} نمایش که ناپدید یا ظاهر شدند. "
                      "عبارت‌های ناشناس در داده نیستند.",
    "about_types_none": "نوع صفحه: برای این سایت تعریف نشده، پس همه اعداد کل سایت را پوشش می‌دهند.",
    "about_types": "نوع‌های صفحه: {names}. نوع‌ها می‌توانند هم‌پوشانی داشته باشند.",
    "about_history": "تاریخچه بیش از ۱۶ ماه گوگل در شیت تاریخچه سایت نگهداری می‌شود.",
    "report_name": "گزارش سئو",
    "prepared": "تهیه‌شده توسط مهدی رئوف",
    "mail_subject": "{report_name} {label} · {period}",
    "mail_greeting": "سلام،",
    "mail_intro": "گزارش سئوی {label} برای {period} آماده است. به‌صورت فایل پاورپوینت پیوست شده و در "
                  "Google Slides هم باز می‌شود.",
    "mail_clicks": "{label} {clicks} کلیک از گوگل گرفت{change}.",
    "mail_up": "؛ {pct} بیشتر از {prev}", "mail_down": "؛ {pct} کمتر از {prev}",
    "mail_flat": "؛ تقریباً برابر با {prev}",
    "mail_impr": "{impr} بار در نتایج جستجو دیده شد و میانگین جایگاه {pos} بود{pos_prev}.",
    "mail_pos_prev": " ({prev_pos} در {prev})",
    "mail_prev_week": "هفته قبل",
    "mail_sign": "مهدی",
    "months": ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر",
              "اکتبر", "نوامبر", "دسامبر"],
    "months_short": ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر",
                     "اکتبر", "نوامبر", "دسامبر"],
    "jalali_months": ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان",
                      "آذر", "دی", "بهمن", "اسفند"],
}

LANGUAGES = {"en": EN, "fa": FA}

_NUMBER = re.compile(r"(?<![\w.])([+\-−]?)(\d[\d,.]*%?)")   # a sign counts only at the start of a word
_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


def table(lang: str) -> dict:
    return LANGUAGES[lang]


def is_rtl(lang: str) -> bool:
    return lang == "fa"


def has_arabic_script(text: str) -> bool:
    return _ARABIC.search(text) is not None


def bidi(text: str, lang: str) -> str:
    """
    In Persian text, wraps numbers, % and signs in LRM so they keep their order. The sign gets
    its own LRM between it and the digits: with only one LRM around the whole number a minus
    sign resolves to the right-to-left direction and lands on the wrong side of the digits.
    """
    if lang != "fa":
        return text

    def wrap(m):
        sign, number = m.group(1), m.group(2)
        return f"{LRM}{sign}{LRM}{number}{LRM}" if sign else f"{LRM}{number}{LRM}"

    return _NUMBER.sub(wrap, text)

def fmt_int(n) -> str:
    return f"{int(round(n)):,}"


def fmt_signed(n, decimals: int = 0, pct: bool = False) -> str:
    """+11 / −14 / +3.4% with a real minus sign."""
    text = f"{abs(n):,.{decimals}f}"
    sign = "+" if n > 0 else (MINUS if n < 0 else "")
    return f"{sign}{text}{'%' if pct else ''}"


def month_name(label: str, lang: str, short: bool = False) -> str:
    """'2026-09' -> 'September' (or 'Sep')."""
    names = table(lang)["months_short" if short else "months"]
    return names[int(label[5:7]) - 1]


def month_title(label: str, lang: str) -> str:
    """'2026-09' -> 'September 2026' (Persian: with the Jalali month too)."""
    base = f"{month_name(label, lang)} {label[:4]}"
    if lang != "fa":
        return base
    jy, jm, _ = gregorian_to_jalali(int(label[:4]), int(label[5:7]), 15)
    return f"{base} ({table(lang)['jalali_months'][jm - 1]} {jy})"


def short_month(label: str, lang: str) -> str:
    """'2026-09' -> 'Sep 26' for chart axes."""
    return f"{month_name(label, lang, short=True)} {label[2:4]}"


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple:
    """Gregorian date to the Solar Hijri (Jalali) calendar used in Iran."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def week_dates(monday: date, sunday: date, lang: str) -> str:
    """'5 Oct to 11 Oct 2026'"""
    names = table(lang)["months_short"]
    return (f"{monday.day} {names[monday.month - 1]} – "
            f"{sunday.day} {names[sunday.month - 1]} {sunday.year}")
