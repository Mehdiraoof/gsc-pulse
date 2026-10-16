"""
PulseO: the deck summary
========================
With `ai_summary: true` and an ANTHROPIC_API_KEY, the Summary slide is written by Claude
from *aggregated* report numbers only (scorecard, segments, alarms, top movers, AIO):
no credentials, IDs or raw tables are sent. On any error, or when it is off, the slide
says "Summary not generated automatically" and its speaker notes hold the same aggregated
text, ready to paste into Claude by hand.
"""

import json
import re
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"          # override with the AI_SUMMARY_MODEL variable
MAX_TOKENS = 700

LANGUAGE_NAMES = {"en": "English", "fa": "Persian (Farsi)"}

PROMPT = """You are writing the summary slide of an SEO report for readers who are not SEO experts
(marketing and management). Use only the data below. Write in {language}.

Return ONLY a JSON object: {{"bullets": [...], "next_steps": [...]}}
- "bullets": 4 to 6 short bullets in plain language, the biggest change first. Each names the
  number, and a likely reason only when the data shows one.
- "next_steps": 2 to 3 concrete recommended next steps.
No other text.

DATA
{data}
"""


def _post(url: str, headers: dict, body: bytes, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_summary(text: str):
    """(bullets, next_steps) from the model's answer, or None when it is not what we asked for."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    bullets, steps = data.get("bullets"), data.get("next_steps")
    if not (isinstance(bullets, list) and isinstance(steps, list)):
        return None
    bullets = [str(b).strip() for b in bullets if str(b).strip()][:6]
    steps = [str(s).strip() for s in steps if str(s).strip()][:3]
    return (bullets, steps) if bullets and steps else None


def request_summary(aggregated: str, lang: str, env, post=_post):
    """Asks Claude for the summary. Returns (bullets, next_steps) or None on ANY problem."""
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None
    model = (env.get("AI_SUMMARY_MODEL") or "").strip() or DEFAULT_MODEL
    body = json.dumps({
        "model": model, "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": PROMPT.format(
            language=LANGUAGE_NAMES.get(lang, "English"), data=aggregated)}],
    }).encode("utf-8")
    headers = {"x-api-key": key, "anthropic-version": API_VERSION,
               "content-type": "application/json"}
    try:
        raw = post(API_URL, headers, body)
        reply = json.loads(raw.decode("utf-8"))
        text = "".join(block.get("text", "") for block in reply.get("content", [])
                       if block.get("type") == "text")
        return parse_summary(text)
    except Exception:           # noqa: BLE001  any failure falls back to the placeholder
        return None
