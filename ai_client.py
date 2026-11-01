"""
PulseO: the AI behind content creation (briefs and drafts)
==========================================================
One small interface, `generate(system, prompt, search=False) -> text`, with two providers behind it,
chosen in the configuration (`ai.provider`: gemini or anthropic; `ai.model`), so the provider can change
without a change of code.

  * Gemini (the start): the free Google AI Studio key, saved as the secret GEMINI_API_KEY. The best free
    Flash model first, Flash-Lite as the fallback when the first one's limit is reached. With
    `search=True` the request may use Google Search (grounding), which is how a brief reads the top results.
  * Anthropic: ANTHROPIC_API_KEY. No web search here.

The free tier has low daily limits and may use prompts to improve Google's products, so only the brief,
the SOP, a row's data and public page text are ever sent. A limit never fails a run: it raises
QuotaExhausted, the caller stops and leaves the rest for the next run. No message holds the key or a
prompt.
"""

import json
import re
import time
import urllib.error
import urllib.request

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODELS = {"gemini": ("gemini-3.8-flash", "gemini-3.5-flash-lite"),
                  "anthropic": ("claude-sonnet-5-5", "")}
MAX_OUTPUT_TOKENS = 24000
TIMEOUT_S = 300
OVERLOAD_RETRIES = 2


class AiError(Exception):
    """The AI could not answer. The message is safe to print."""


class ModelUnavailable(AiError):
    """The key cannot use that model (status 404). The next model is tried."""


class QuotaExhausted(AiError):
    """A request limit is reached (the provider's, or the run's own). Not a failure: the rest waits."""


def default_post(url: str, headers: dict, body: bytes, timeout: float = TIMEOUT_S) -> tuple:
    """(status, bytes). A network problem raises AiError."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AiError(f"no answer from the AI ({type(e).__name__})") from None


def default_get(url: str, headers: dict, timeout: float = 60) -> tuple:
    """(status, bytes) of a GET; a network problem raises AiError."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AiError(f"no answer from the AI ({type(e).__name__})") from None


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _retry_seconds(data: dict) -> float:
    """The wait a 429 asks for ('retryDelay': '23s'), or a big number when it is not a short wait."""
    for detail in (data.get("error", {}) or {}).get("details", []) or []:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
        if m:
            return float(m.group(1))
    return 3600.0


def _unfence(text: str) -> str:
    t = text.strip()
    m = re.fullmatch(r"```(?:markdown|md|text)?\s*\n(.*)\n```", t, re.DOTALL)
    return m.group(1).strip() if m else t


def _quota_note(model: str, data: dict) -> str:
    """Which limit a 429 names: public metric names and numbers only (no key, no text)."""
    bits = []
    for detail in (data.get("error", {}) or {}).get("details", []) or []:
        for v in detail.get("violations", []) or []:
            bits.append(re.sub(r"[^A-Za-z0-9_.:/ -]", "", f"{v.get('quotaId', '')} limit {v.get('quotaValue', '?')}"))
    said = re.sub(r"[^A-Za-z0-9_.:,;/()' -]", " ", str((data.get("error", {}) or {}).get("message", "")))[:240]
    return f"{model}: " + ("; ".join(dict.fromkeys(bits)) or "no detail") + (f" - {said}" if said else "")


class Gemini:
    provider = "gemini"

    def __init__(self, api_key: str, model: str = "", fallback: str = None, post=default_post, sleep=time.sleep,
                 get=default_get):
        self.key = api_key
        self.model = model or DEFAULT_MODELS["gemini"][0]
        self.fallback = DEFAULT_MODELS["gemini"][1] if fallback is None else fallback
        self.post, self.sleep, self.get = post, sleep, get
        self.calls = 0                                   # requests sent, retries included
        self.search_ok = True                            # false once the search tool answered "no quota"

    def model_names(self) -> list:
        """The names of the models this key may use to write text (public names, safe to print); [] when unknown."""
        try:
            status, raw = self.get(GEMINI_MODELS_URL, {"x-goog-api-key": self.key})
        except AiError:
            return []
        if status != 200:
            return []
        return sorted(str(m.get("name", "")).replace("models/", "") for m in _json(raw).get("models", [])
                      if "generateContent" in (m.get("supportedGenerationMethods") or []))

    def probe(self, model: str, search: bool = False) -> str:
        """One tiny request to a model, for a diagnostic: "ok", or the status with Google's short message."""
        body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK."}]}],
                "generationConfig": {"maxOutputTokens": 64}}
        if search:
            body["tools"] = [{"google_search": {}}]
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.key}
        self.calls += 1
        status, raw = self.post(GEMINI_URL.format(model=model), headers, json.dumps(body).encode("utf-8"))
        if status == 200:
            return "ok"
        data = _json(raw)
        said = re.sub(r"[^A-Za-z0-9_.:,;/()' -]", " ", str((data.get("error", {}) or {}).get("message", "")))[:160]
        return f"status {status} {(data.get('error', {}) or {}).get('status', '')} {said}".strip()

    def generate(self, system: str, prompt: str, search: bool = False, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
        """The text for a prompt. With `search` Google Search is used; the search tool has a quota of its own
        (none on a free key), so when it is out the request is made again without it, and later ones skip it."""
        if search and self.search_ok:
            try:
                return self._generate(system, prompt, True, max_tokens)
            except QuotaExhausted:
                self.search_ok = False
        return self._generate(system, prompt, False, max_tokens)

    def _generate(self, system: str, prompt: str, search: bool, max_tokens: int) -> str:
        models = [self.model] + ([self.fallback] if self.fallback and self.fallback != self.model else [])
        unavailable, limits = None, []
        for model in models:
            try:
                return self._ask(model, system, prompt, search, max_tokens)
            except ModelUnavailable as e:
                unavailable = e                          # this key cannot use that model: the next one
            except QuotaExhausted as e:
                limits.append(str(e))                    # this model's limit: the next one, if there is one
        if unavailable and not limits:
            raise AiError(str(unavailable))
        raise QuotaExhausted("the AI's request limit is reached: " + " | ".join(limits))

    def _ask(self, model, system, prompt, search, max_tokens) -> str:
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.6, "maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if search:
            body["tools"] = [{"google_search": {}}]
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.key}
        payload = json.dumps(body).encode("utf-8")
        waited_for_minute = False
        attempt = 0
        while True:
            self.calls += 1
            status, raw = self.post(GEMINI_URL.format(model=model), headers, payload)
            data = _json(raw)
            if status == 429:
                text = raw.decode("utf-8", errors="replace")
                wait = _retry_seconds(data)
                if "PerDay" in text or waited_for_minute or wait > 60:
                    raise QuotaExhausted(f"a request limit of the AI is reached ({_quota_note(model, data)})")
                waited_for_minute = True
                self.sleep(wait + 1)
                continue
            if status in (500, 502, 503, 504):
                attempt += 1
                if attempt > OVERLOAD_RETRIES:
                    raise AiError(f"the AI is busy (status {status})")
                self.sleep(5 * attempt)
                continue
            if status != 200:
                reason = (data.get("error", {}) or {}).get("status", "")
                hint = ""
                if status == 404:                        # a model this key cannot use (retired, or not for this key)
                    names = [n for n in self.model_names() if "gemini" in n and "embedding" not in n]
                    said = re.sub(r"\s+", " ", str((data.get("error", {}) or {}).get("message", "")))[:200]
                    hint = (f"; model {model} not available ({said}); models that can write: "
                            f"{', '.join(names[:25]) or 'unknown'}")
                    raise ModelUnavailable(f"the AI refused the request (status {status} {reason}){hint}".strip())
                raise AiError(f"the AI refused the request (status {status} {reason})".strip())
            return self._text(data)

    @staticmethod
    def _text(data: dict) -> str:
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise AiError("the AI did not answer this prompt (blocked)")
        candidates = data.get("candidates") or []
        if not candidates:
            raise AiError("the AI sent no answer")
        c = candidates[0]
        text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) or []
                       if not p.get("thought"))
        if c.get("finishReason") == "MAX_TOKENS":
            raise AiError("the AI's answer was cut off")
        if not text.strip():
            raise AiError("the AI's answer was empty")
        return _unfence(text)


class Anthropic:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str = "", fallback: str = None, post=default_post, sleep=time.sleep):
        self.key = api_key
        self.model = model or DEFAULT_MODELS["anthropic"][0]
        self.post, self.sleep = post, sleep
        self.calls = 0

    def generate(self, system: str, prompt: str, search: bool = False, max_tokens: int = 16000) -> str:
        body = {"model": self.model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        headers = {"content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2023-06-01"}
        payload = json.dumps(body).encode("utf-8")
        attempt = 0
        while True:
            self.calls += 1
            status, raw = self.post(ANTHROPIC_URL, headers, payload)
            data = _json(raw)
            if status == 429:
                raise QuotaExhausted("a request limit of the AI is reached")
            if status in (500, 502, 503, 504, 529):
                attempt += 1
                if attempt > OVERLOAD_RETRIES:
                    raise AiError(f"the AI is busy (status {status})")
                self.sleep(5 * attempt)
                continue
            if status != 200:
                raise AiError(f"the AI refused the request (status {status})")
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            if data.get("stop_reason") == "max_tokens":
                raise AiError("the AI's answer was cut off")
            if not text.strip():
                raise AiError("the AI's answer was empty")
            return _unfence(text)


def make_client(settings: dict, env, post=default_post, sleep=time.sleep):
    """The client the configuration asks for: settings = {"provider": "gemini", "model": ..., "fallback_model": ...}.
    The key comes from the environment (GEMINI_API_KEY, ANTHROPIC_API_KEY); without one: AiError."""
    provider = (settings.get("provider") or "gemini").lower()
    if provider not in DEFAULT_MODELS:
        raise AiError("the AI provider in the configuration is not known (gemini or anthropic)")
    key = (env.get("GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise AiError(f"no key for the AI provider {provider} (secret not set)")
    cls = Gemini if provider == "gemini" else Anthropic
    return cls(key, settings.get("model", ""), settings.get("fallback_model"), post=post, sleep=sleep)


class Metered:
    """A client with a limit of requests for one run (stopped before the provider's own daily limit)."""

    def __init__(self, client, limit: int, used_today: int = 0, daily: int = 0):
        self.client, self.limit, self.daily = client, limit, daily
        self.start_calls = client.calls
        self.used_today = used_today

    @property
    def used(self) -> int:
        return self.client.calls - self.start_calls

    def generate(self, system: str, prompt: str, search: bool = False, **kw) -> str:
        if self.used >= self.limit:
            raise QuotaExhausted("this run's own limit of AI requests is reached")
        if self.daily and self.used_today + self.used >= self.daily:
            raise QuotaExhausted("the day's own limit of AI requests is reached")
        return self.client.generate(system, prompt, search, **kw)
