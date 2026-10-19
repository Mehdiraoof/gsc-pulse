"""
PulseO: texts of the client-ready monthly deck (v2.4)
=====================================================
English and Persian. They are merged over the general deck texts (i18n.py) when the monthly
deck is built, so the footer, month names and column labels are shared. A text with {placeholders}
is filled by client_data; numbers and names are never part of the translation.
"""

EN = {
    "footer": "{label} · {period}",
    "c_subtitle": "{period} · Google Search performance, progress and next steps",
    "c_prepared": "Prepared by Mehdi Raoof · built with PulseO",

    # slide 2
    "c_k_headline": "Headline", "c_t_headline": "This month in one line",
    "h_pos": "{site} ranks higher than it has all year.",
    "h_yoy": "Clicks are up {pct} on last year.",
    "h_ctr": "More searchers clicked through.",
    "h_seg": "{segment} led the growth.",
    "h_flat": "{site} held its ground this month.",
    "sup_pos_up": "Average position improved to {pos}", "sup_pos": "Average position is {pos}",
    "sup_ctr": "more searchers clicked through (CTR {ctr})",
    "sup_and": "{a} and {b}.",
    "sup_one": "{a}.",
    "sup_seg": "{segment} led the growth: {mom} this month and {yoy} vs last {month}.",
    "c_best_pos": "Best ranking of the year", "c_pos_title": "Average position",
    "c_pos_body_up": "Average position {pos}, up from {prev} last month and {yoy} a year ago.",
    "c_pos_body_up_prev": "Average position {pos}, up from {prev} last month.",
    "c_pos_body_up_yoy": "Average position {pos}, up from {yoy} a year ago.",
    "c_pos_body": "Average position {pos}, against {prev} last month and {yoy} a year ago.",
    "c_pos_body_prev": "Average position {pos}, against {prev} last month.",
    "c_pos_body_yoy": "Average position {pos}, against {yoy} a year ago.",
    "c_pos_body_now": "Average position {pos}.",
    "c_clicks_steady_t": "Clicks held steady vs last year",
    "c_clicks_steady_b": "{clicks} clicks, within {pct} of last {month}{fewer}.",
    "c_fewer": " even with fewer searches",
    "c_clicks_up_t": "Clicks up {pct} on last year",
    "c_clicks_up_b": "{clicks} clicks against {before} in {month} last year.",
    "c_clicks_down_t": "Clicks vs last year",
    "c_clicks_down_b": "{clicks} clicks against {before} in {month} last year ({pct}).",
    "c_room_t": "Big room to grow",
    "c_room_b": "{n} keywords already close to the top could bring about {gain} extra clicks a month.",
    "c_room_b1": "1 keyword already close to the top could bring about {gain} extra clicks a month.",

    # slide 3
    "c_k_score": "{month} vs {prev} and vs {year}", "c_t_score": "Scorecard",
    "c_vs": "vs {m}", "c_vs_year": "vs last year", "c_pt": "pt",
    "c_yoy_flat": "almost flat", "c_yoy_up": "up {pct}", "c_yoy_down": "down {pct}",
    "c_n_clicks_down": "Fewer searches this month; year on year {yoy}.",
    "c_n_clicks_up": "More clicks than last month; year on year {yoy}.",
    "c_n_clicks_slip": "Clicks fell as rankings slipped; year on year {yoy}.",
    "c_n_impr_down": "Fewer searches this month.",
    "c_n_impr_gone": "Fewer searches this month, and {n} old URLs no longer show in Google "
                     "(expected after redirects).",
    "c_n_impr_up": "More people saw us in search.",
    "c_n_ctr_up": "More of the people who see us click.",
    "c_n_ctr_down": "Slightly fewer clicks per search.",
    "c_n_pos_up": "Rankings improved (lower is better).",
    "c_n_pos_down": "Rankings slipped a little (lower is better).",
    "c_n_pos_red": "Rankings slipped (lower is better).",
    "c_n_aio": "How often an AI Overview showed our pages.",
    "c_foot_grey": "Drops are shown in grey, not red, when they come from fewer searches "
                   "rather than lost rankings.",

    # slide 4
    "c_k_progress": "Progress", "c_t_climb": "Rankings that climbed",
    "c_avg_pos": "Average position",
    "c_climb_year": "Every step up page 1 brings more clicks for the same number of searches. "
                    "That is why clicks stayed level with last year while impressions fell {impr}.",
    "c_climb_generic": "Average position moved from {a} to {b}.",
    "c_col_kw_page": "Keyword / page", "c_top_spot": "top spot", "c_top3": "top 3",
    "c_climb_foot_one": "One ranking moved onto page 1 this month: {names}.",
    "c_climb_foot": "{n} rankings moved onto page 1 this month: {names}.",
    "c_and": "{a} and {b}",

    # slide 5
    "c_t_grew": "What grew this month",
    "c_h_kw_gain": "Keywords gaining clicks", "c_h_pg_gain": "Pages gaining clicks",
    "c_yoy_best": "Best year-on-year result.",
    "c_yoy_both": "Year on year, {segment} is up {pct} and {page} gained {n} clicks.",
    "c_yoy_seg": "Year on year, {segment} is up {pct}.",
    "c_yoy_page": "Year on year, {page} gained {n} clicks.",

    # slide 6
    "c_t_new": "New searches finding {site}",
    "c_new_intro": "Searches that showed {site} for the first time this month. They are early "
                   "signals of what people are starting to look for.",
    "c_pill_impr": "{n} impressions", "c_new_pos": "position {pos}",

    # slide 7
    "c_k_did": "What we did", "c_t_work": "Our work this month",
    "c_fill": "Mehdi fills this slide before sending.",
    "c_prompts": ["Fixes shipped", "Content published or updated", "Technical work",
                  "Anything waiting on the {site} team"],

    # slide 8
    "c_k_big": "The bigger picture", "c_t_trend": "{n}-month trend",
    "c_chart_clicks": "Clicks per month",
    "c_peak_b": "Record month: {n} clicks.", "c_low_b": "Lowest month: {n} clicks.",
    "c_low_known": "Known and fixed: {n} clicks.",
    "c_now": "Now", "c_now_b": "{n} clicks in {month}, {pct} vs {prev}.",
    "c_next3_t": "Last year, the next 3 months",
    "c_next3_b": "Clicks changed by {pct} on average compared with {month}.",

    # slide 9
    "c_k_types": "Page types", "c_t_sections": "Performance by section",
    "c_chart_month": "Clicks in {month}", "c_col_section": "Section",
    "c_foot_overlap": "Sections overlap slightly, so they add up to about {pct} more than the site "
                      "total. That is normal in Search Console.",
    "c_foot_exact": "The sections add up to the site total.",

    # slides 10 to 12
    "c_k_opp": "Opportunities",
    "c_t_wins": "Quick wins: almost on top",
    "c_intro_wins": "Keywords where {site} already ranks on page 1 or close to it, but below the "
                    "top 3. Moving them up is the fastest growth we can get.",
    "c_col_wins": ["Keyword", "Searches / month", "Position now", "Extra clicks / mo if top 3"],
    "c_wins_card": "About +{gain} clicks a month together.",
    "c_wins_note": " Estimate based on typical top-3 click rates; real results vary.",
    "c_t_pages": "Pages to update",
    "c_intro_pages": "Pages with lots of visibility but a small share of clicks, or that are down "
                     "on last year. Each one gets a short diagnosis.",
    "c_col_pages": ["Page", "Impr. (90 days)", "Position", "CTR", "Diagnosis and action"],
    "c_t_ideas": "New content ideas",
    "c_intro_q": "People ask these questions and {site} appears, but there is no page that answers "
                 "them directly.",
    "c_q_pos": "{n} impr. · position {pos}", "c_q_ctr": "{n} impr. · CTR {ctr}",
    "c_cannibal": "Also: one page per topic.",
    "c_cannibal_b": "{names} each have {n} pages competing for the same search. Google cannot "
                    "tell which one to rank, so none reaches the top. Merging each set into one "
                    "page usually lifts it.",

    # slide 13
    "c_k_season": "Seasonality", "c_t_next": "Get ready for {month}",
    "c_next_intro": "What happened from {m1} to {m2} last year:",
    "c_whole": "Whole site", "c_clicks_word": "clicks", "c_plus_impr": "{pct} impressions",
    "c_do_now": "What we do now",
    "c_do_seg": "{segment} grows most next month: refresh the top pages first.",
    "c_do_seg_slide": "{segment} grows most next month: refresh the top pages from slide {n} first.",
    "c_do_page": "Update {page} before {month}: it grew {pct} into {month} last year.",

    # slide 14
    "c_k_watch": "No action needed yet", "c_t_watch": "What we're watching",
    "c_w_old": "Old URLs",
    "c_w_old_b": "{n} old URLs no longer show in Google (expected after redirects); we check they "
                 "pass traffic on.",
    "c_w_seg_b": "{yoy} vs last year{mom}.", "c_w_mom": ", but {mom} this month",
    "c_w_dec_b": "Clicks {a} → {b}. {diag}",
    "c_w_check_b": "Impressions {drop} on last month. We check indexing and recent changes.",

    # slide 15
    "c_k_plan": "Plan", "c_t_focus": "Next month's focus",
    "c_f_wins_t": "Lift the {n} quick wins",
    "c_f_wins_b": "The keywords on slide {s}. About +{gain} clicks a month at stake.",
    "c_f_wins_b0": "The keywords listed. About +{gain} clicks a month at stake.",
    "c_f_merge_t": "Merge competing pages",
    "c_f_merge_b": "One page each for the {n} topics that split across several pages.",
    "c_f_q_t": "Answer the questions",
    "c_f_q_b": "Short explainer pages for the question searches on slide {s}.",
    "c_f_q_b0": "Short explainer pages for the question searches we found.",
    "c_f_season_t": "Prepare for {month}",
    "c_f_season_b": "Refresh the pages that grow most into {month} before it starts.",

    # the weekly deck's extra slide
    "c_wk_gone": "Pages that disappeared",
    "c_wk_gone_intro": "Pages that had impressions last week and none this week. Check that their "
                       "redirects pass the traffic.",
    "c_col_clicks_before": "Clicks last week", "c_col_impr_before": "Impr. last week",
    "c_wk_gone_none": "No pages disappeared this week.",
}

FA = {
    "footer": "{label} · {period}",
    "c_subtitle": "{period} · عملکرد در جستجوی گوگل، پیشرفت و گام‌های بعدی",
    "c_prepared": "تهیه‌شده توسط مهدی رئوف · ساخته‌شده با PulseO",

    "c_k_headline": "خلاصه", "c_t_headline": "این ماه در یک جمله",
    "h_pos": "{site} بالاتر از تمام سال گذشته رتبه گرفته است.",
    "h_yoy": "کلیک‌ها نسبت به سال قبل {pct} بیشتر شده است.",
    "h_ctr": "کاربران بیشتری روی نتایج کلیک کردند.",
    "h_seg": "{segment} پیشتاز رشد بود.",
    "h_flat": "{site} این ماه جایگاه خود را حفظ کرد.",
    "sup_pos_up": "میانگین جایگاه به {pos} بهتر شد", "sup_pos": "میانگین جایگاه {pos} است",
    "sup_ctr": "کاربران بیشتری کلیک کردند (نرخ کلیک {ctr})",
    "sup_and": "{a} و {b}.",
    "sup_one": "{a}.",
    "sup_seg": "{segment} پیشتاز رشد بود: {mom} نسبت به ماه قبل و {yoy} نسبت به {month} سال قبل.",
    "c_best_pos": "بهترین جایگاه سال", "c_pos_title": "میانگین جایگاه",
    "c_pos_body_up": "میانگین جایگاه {pos}؛ ماه قبل {prev} و یک سال پیش {yoy} بود.",
    "c_pos_body_up_prev": "میانگین جایگاه {pos}؛ ماه قبل {prev} بود.",
    "c_pos_body_up_yoy": "میانگین جایگاه {pos}؛ یک سال پیش {yoy} بود.",
    "c_pos_body": "میانگین جایگاه {pos}؛ ماه قبل {prev} و یک سال پیش {yoy} بود.",
    "c_pos_body_prev": "میانگین جایگاه {pos}؛ ماه قبل {prev} بود.",
    "c_pos_body_yoy": "میانگین جایگاه {pos}؛ یک سال پیش {yoy} بود.",
    "c_pos_body_now": "میانگین جایگاه {pos}.",
    "c_clicks_steady_t": "کلیک‌ها نسبت به سال قبل ثابت ماند",
    "c_clicks_steady_b": "{clicks} کلیک، در حد {pct} از {month} سال قبل{fewer}.",
    "c_fewer": " با وجود جستجوی کمتر",
    "c_clicks_up_t": "کلیک‌ها نسبت به سال قبل {pct} بیشتر",
    "c_clicks_up_b": "{clicks} کلیک، در برابر {before} در {month} سال قبل.",
    "c_clicks_down_t": "کلیک‌ها نسبت به سال قبل",
    "c_clicks_down_b": "{clicks} کلیک، در برابر {before} در {month} سال قبل ({pct}).",
    "c_room_t": "جای رشد زیاد",
    "c_room_b": "{n} کلمه کلیدی که نزدیک صدر هستند می‌توانند ماهی حدود {gain} کلیک اضافه بیاورند.",
    "c_room_b1": "یک کلمه کلیدی که نزدیک صدر است می‌تواند ماهی حدود {gain} کلیک اضافه بیاورد.",

    "c_k_score": "{month} در برابر {prev} و {year}", "c_t_score": "کارنامه",
    "c_vs": "نسبت به {m}", "c_vs_year": "نسبت به سال قبل", "c_pt": "واحد",
    "c_yoy_flat": "تقریباً ثابت", "c_yoy_up": "{pct} بیشتر", "c_yoy_down": "{pct} کمتر",
    "c_n_clicks_down": "جستجوی کمتر در این ماه؛ نسبت به سال قبل {yoy}.",
    "c_n_clicks_up": "کلیک بیشتر از ماه قبل؛ نسبت به سال قبل {yoy}.",
    "c_n_clicks_slip": "کلیک‌ها با افت جایگاه کم شد؛ نسبت به سال قبل {yoy}.",
    "c_n_impr_down": "جستجوی کمتر در این ماه.",
    "c_n_impr_gone": "جستجوی کمتر در این ماه و {n} نشانی قدیمی دیگر در گوگل دیده نمی‌شود "
                     "(بعد از ریدایرکت‌ها طبیعی است).",
    "c_n_impr_up": "افراد بیشتری ما را در جستجو دیدند.",
    "c_n_ctr_up": "از کسانی که ما را می‌بینند، بیشتر کلیک می‌کنند.",
    "c_n_ctr_down": "کلیک به‌ازای هر جستجو کمی کمتر شد.",
    "c_n_pos_up": "جایگاه‌ها بهتر شد (عدد کمتر یعنی بهتر).",
    "c_n_pos_down": "جایگاه‌ها کمی افت کرد (عدد کمتر یعنی بهتر).",
    "c_n_pos_red": "جایگاه‌ها افت کرد (عدد کمتر یعنی بهتر).",
    "c_n_aio": "تعداد دفعاتی که AI Overview صفحه‌های ما را نشان داد.",
    "c_foot_grey": "وقتی افت از کمتر شدن جستجوها باشد و نه از دست رفتن جایگاه، با رنگ خاکستری "
                   "نشان داده می‌شود، نه قرمز.",

    "c_k_progress": "پیشرفت", "c_t_climb": "جایگاه‌هایی که بالا رفتند",
    "c_avg_pos": "میانگین جایگاه",
    "c_climb_year": "هر پله بالاتر در صفحه اول، با همان تعداد جستجو کلیک بیشتری می‌آورد. برای همین "
                    "کلیک‌ها هم‌سطح سال قبل ماند، در حالی که نمایش‌ها {impr} کم شد.",
    "c_climb_generic": "میانگین جایگاه از {a} به {b} رسید.",
    "c_col_kw_page": "کلمه کلیدی / صفحه", "c_top_spot": "جایگاه اول", "c_top3": "سه نتیجه برتر",
    "c_climb_foot_one": "یک جایگاه این ماه به صفحه اول رسید: {names}.",
    "c_climb_foot": "{n} جایگاه این ماه به صفحه اول رسیدند: {names}.",
    "c_and": "{a} و {b}",

    "c_t_grew": "آنچه این ماه رشد کرد",
    "c_h_kw_gain": "کلمه‌های کلیدی با کلیک بیشتر", "c_h_pg_gain": "صفحه‌های با کلیک بیشتر",
    "c_yoy_best": "بهترین نتیجه نسبت به سال قبل.",
    "c_yoy_both": "نسبت به سال قبل، {segment} {pct} رشد کرده و {page} {n} کلیک بیشتر گرفته است.",
    "c_yoy_seg": "نسبت به سال قبل، {segment} {pct} رشد کرده است.",
    "c_yoy_page": "نسبت به سال قبل، {page} {n} کلیک بیشتر گرفته است.",

    "c_t_new": "جستجوهای تازه‌ای که {site} را پیدا می‌کنند",
    "c_new_intro": "جستجوهایی که این ماه برای اولین بار {site} را نشان دادند. نشانه‌های زودهنگام "
                   "از چیزی که مردم تازه شروع به جستجویش کرده‌اند.",
    "c_pill_impr": "{n} نمایش", "c_new_pos": "جایگاه {pos}",

    "c_k_did": "کارهای انجام‌شده", "c_t_work": "کار ما در این ماه",
    "c_fill": "مهدی پیش از ارسال این اسلاید را پر می‌کند.",
    "c_prompts": ["اصلاحات انجام‌شده", "محتوای منتشرشده یا به‌روزشده", "کار فنی",
                  "هر چیزی که منتظر تیم {site} است"],

    "c_k_big": "تصویر بزرگ‌تر", "c_t_trend": "روند {n} ماهه",
    "c_chart_clicks": "کلیک در هر ماه",
    "c_peak_b": "رکورد ماه: {n} کلیک.", "c_low_b": "کم‌ترین ماه: {n} کلیک.",
    "c_low_known": "شناخته‌شده و رفع‌شده: {n} کلیک.",
    "c_now": "اکنون", "c_now_b": "{n} کلیک در {month}، {pct} نسبت به {prev}.",
    "c_next3_t": "سال قبل، سه ماه بعد",
    "c_next3_b": "کلیک‌ها به‌طور میانگین {pct} نسبت به {month} تغییر کرد.",

    "c_k_types": "نوع صفحه", "c_t_sections": "عملکرد بخش‌ها",
    "c_chart_month": "کلیک در {month}", "c_col_section": "بخش",
    "c_foot_overlap": "بخش‌ها کمی هم‌پوشانی دارند و جمعشان حدود {pct} بیشتر از کل سایت می‌شود. "
                      "این در سرچ کنسول عادی است.",
    "c_foot_exact": "جمع بخش‌ها برابر کل سایت است.",

    "c_k_opp": "فرصت‌ها",
    "c_t_wins": "برنده‌های سریع: نزدیک صدر",
    "c_intro_wins": "کلمه‌هایی که {site} در آن‌ها در صفحه اول یا نزدیک آن است، اما زیر سه نتیجه "
                    "برتر. بالا بردن آن‌ها سریع‌ترین رشدی است که می‌توانیم بگیریم.",
    "c_col_wins": ["کلمه کلیدی", "جستجو در ماه", "جایگاه فعلی", "کلیک اضافه در ماه اگر سه‌تای برتر"],
    "c_wins_card": "روی هم حدود {gain}+ کلیک در ماه.",
    "c_wins_note": " برآورد بر پایه نرخ کلیک معمول سه نتیجه برتر است؛ نتیجه واقعی می‌تواند فرق کند.",
    "c_t_pages": "صفحه‌هایی که باید به‌روز شوند",
    "c_intro_pages": "صفحه‌هایی که دیده‌شدن زیادی دارند اما سهم کلیکشان کم است، یا نسبت به سال قبل "
                     "افت کرده‌اند. برای هر کدام یک تشخیص کوتاه آمده است.",
    "c_col_pages": ["صفحه", "نمایش (۹۰ روز)", "جایگاه", "نرخ کلیک", "تشخیص و اقدام"],
    "c_t_ideas": "ایده‌های محتوای تازه",
    "c_intro_q": "مردم این سؤال‌ها را می‌پرسند و {site} نمایش داده می‌شود، اما صفحه‌ای نیست که "
                 "مستقیم جواب بدهد.",
    "c_q_pos": "{n} نمایش · جایگاه {pos}", "c_q_ctr": "{n} نمایش · نرخ کلیک {ctr}",
    "c_cannibal": "همچنین: یک صفحه برای هر موضوع.",
    "c_cannibal_b": "{names} هرکدام {n} صفحه دارند که بر سر یک جستجو با هم رقابت می‌کنند. گوگل "
                    "نمی‌داند کدام را بالا بیاورد، پس هیچ‌کدام به صدر نمی‌رسد. ادغام هر مجموعه در "
                    "یک صفحه معمولاً آن را بالا می‌برد.",

    "c_k_season": "فصلی بودن", "c_t_next": "آماده شویم برای {month}",
    "c_next_intro": "سال قبل از {m1} تا {m2} چه شد:",
    "c_whole": "کل سایت", "c_clicks_word": "کلیک", "c_plus_impr": "{pct} نمایش",
    "c_do_now": "الان چه می‌کنیم",
    "c_do_seg": "{segment} ماه بعد بیشترین رشد را دارد: ابتدا صفحه‌های برتر را به‌روز کنیم.",
    "c_do_seg_slide": "{segment} ماه بعد بیشترین رشد را دارد: ابتدا صفحه‌های برتر اسلاید {n} را "
                      "به‌روز کنیم.",
    "c_do_page": "{page} را پیش از {month} به‌روز کنیم: سال قبل به {month} {pct} رشد کرد.",

    "c_k_watch": "فعلاً اقدامی لازم نیست", "c_t_watch": "آنچه زیر نظر داریم",
    "c_w_old": "نشانی‌های قدیمی",
    "c_w_old_b": "{n} نشانی قدیمی دیگر در گوگل دیده نمی‌شود (بعد از ریدایرکت‌ها طبیعی است)؛ "
                 "بررسی می‌کنیم که ترافیک را به صفحه جدید برسانند.",
    "c_w_seg_b": "{yoy} نسبت به سال قبل{mom}.", "c_w_mom": "، اما {mom} نسبت به ماه قبل",
    "c_w_dec_b": "کلیک {a} → {b}. {diag}",
    "c_w_check_b": "نمایش‌ها {drop} نسبت به ماه قبل. وضعیت ایندکس و تغییرات اخیر را بررسی می‌کنیم.",

    "c_k_plan": "برنامه", "c_t_focus": "تمرکز ماه بعد",
    "c_f_wins_t": "بالا بردن {n} برنده سریع",
    "c_f_wins_b": "کلمه‌های اسلاید {s}. حدود {gain}+ کلیک در ماه در گرو آن است.",
    "c_f_wins_b0": "کلمه‌های فهرست‌شده. حدود {gain}+ کلیک در ماه در گرو آن است.",
    "c_f_merge_t": "ادغام صفحه‌های رقیب",
    "c_f_merge_b": "برای هر یک از {n} موضوعی که میان چند صفحه پخش شده، یک صفحه.",
    "c_f_q_t": "جواب دادن به سؤال‌ها",
    "c_f_q_b": "صفحه‌های توضیحی کوتاه برای جستجوهای پرسشی اسلاید {s}.",
    "c_f_q_b0": "صفحه‌های توضیحی کوتاه برای جستجوهای پرسشی که پیدا کردیم.",
    "c_f_season_t": "آماده‌سازی برای {month}",
    "c_f_season_b": "صفحه‌هایی را که بیشترین رشد را به {month} دارند پیش از شروع آن به‌روز کنیم.",

    "c_wk_gone": "صفحه‌هایی که ناپدید شدند",
    "c_wk_gone_intro": "صفحه‌هایی که هفته قبل نمایش داشتند و این هفته هیچ. بررسی کنید که ریدایرکت "
                       "آن‌ها ترافیک را منتقل می‌کند.",
    "c_col_clicks_before": "کلیک هفته قبل", "c_col_impr_before": "نمایش هفته قبل",
    "c_wk_gone_none": "این هفته صفحه‌ای ناپدید نشد.",
}

TABLES = {"en": EN, "fa": FA}


def table(lang: str) -> dict:
    return TABLES[lang]
