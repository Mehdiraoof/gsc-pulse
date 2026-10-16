"""
GSC Pulse: alarms
=================
Deterministic rules that turn the report numbers into a short list of things
worth looking at. Messages are plain English; the first slide of a client deck
can be built from them. Every function returns rows for the `R Alarms` tab:
[Period, Severity, Area, Message].

All thresholds come from the site's configuration (sites.THRESHOLD_DEFAULTS).
"""

from urllib.parse import unquote, urlsplit

HEADER = ["Period", "Severity", "Area", "Message"]


def _n(value) -> str:
    """12345 -> '12,345'"""
    return f"{int(round(value)):,}"


def _pct(change: float) -> str:
    return f"{abs(change):.1f}%"


def segment_change(period: str, prev_period: str, segment: str, clicks: int, prev_clicks: int,
                   th: dict) -> list:
    """A segment's clicks moved by the configured percentage month on month."""
    if prev_clicks is None or prev_clicks <= 0 or prev_clicks < th["alarm_min_clicks"]:
        return []
    change = (clicks - prev_clicks) / prev_clicks * 100
    if abs(change) < th["alarm_clicks_change_pct"]:
        return []
    severity = "high" if abs(change) >= th["alarm_clicks_high_pct"] else "medium"
    direction = "rose" if change > 0 else "fell"
    return [[period, severity, "Segment clicks",
             f"Clicks for {segment} {direction} by {_pct(change)} compared with {prev_period} "
             f"({_n(prev_clicks)} to {_n(clicks)})."]]


def lost_keywords(period: str, prev_period: str, lost: list, th: dict) -> list:
    """lost: [(query, clicks_prev)] for keywords that dropped out of the data this month."""
    out = []
    for query, prev_clicks in lost:
        if prev_clicks >= th["alarm_lost_keyword_clicks"]:
            out.append([period, "medium", "Lost keyword",
                        f"The keyword \"{query}\" brought {_n(prev_clicks)} clicks in "
                        f"{prev_period} and is not in Search Console's data in {period}."])
    return out


def page_losses(period: str, prev_period: str, pages: list, th: dict) -> list:
    """pages: [(url, clicks, clicks_prev)]. Pages that lost most of their clicks."""
    out = []
    for url, clicks, prev_clicks in pages:
        if prev_clicks < th["alarm_page_loss_min_clicks"] or prev_clicks <= 0:
            continue
        loss = (prev_clicks - clicks) / prev_clicks * 100
        if loss >= th["alarm_page_loss_pct"]:
            out.append([period, "medium", "Page loss",
                        f"The page {unquote(url)} lost {_pct(loss)} of its clicks compared with "
                        f"{prev_period} ({_n(prev_clicks)} to {_n(clicks)})."])
    return out


def aio_drop(period: str, prev_period: str, aio: int, prev_aio: int, th: dict) -> list:
    """AI Overview impressions fell by the configured percentage month on month."""
    if aio is None or prev_aio is None or prev_aio <= 0:
        return []
    drop = (prev_aio - aio) / prev_aio * 100
    if drop < th["alarm_aio_drop_pct"]:
        return []
    return [[period, "medium", "AI Overviews",
             f"AI Overview impressions fell by {_pct(drop)} compared with {prev_period} "
             f"({_n(prev_aio)} to {_n(aio)})."]]


def _groups(urls: list) -> dict:
    """Groups URLs by host and by host + first path segment."""
    groups = {}
    for url in urls:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}/"
        groups.setdefault(("host", host), []).append(url)
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2:                # a lone slug is a page, not a section
            groups.setdefault(("folder", f"{host}{segments[0]}/"), []).append(url)
    return groups


def new_url_patterns(period: str, other_urls: list, prev_other_urls: list, th: dict) -> list:
    """
    Pages that match no page type but share a host or a first path segment: probably a
    section that deserves its own page type. Only reported when the group is new, i.e.
    it was below the threshold in the previous month. The site's main host is not a
    'new pattern'.
    """
    need = th["alarm_new_pattern_pages"]
    now, before = _groups(other_urls), _groups(prev_other_urls)
    hosts = {}
    for (kind, key), urls in _groups(other_urls + prev_other_urls).items():
        if kind == "host":
            hosts[key] = len(urls)
    main_host = max(hosts, key=hosts.get) if hosts else None
    out = []
    for (kind, key), urls in sorted(now.items()):
        if len(urls) < need or len(before.get((kind, key), [])) >= need:
            continue
        if kind == "host" and key == main_host:
            continue
        what = "subdomain" if kind == "host" else "first folder"
        out.append([period, "low", "New URL pattern",
                    f"{len(urls)} pages that match no page type share the same {what} "
                    f"({unquote(key)}). This may be a new section: consider a page-type rule."])
    return out
