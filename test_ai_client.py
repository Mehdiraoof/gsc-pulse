"""Tests for the AI client of content creation (no network: every answer is made up here)."""

import json
import unittest

import ai_client as ai

KEY = "SECRET-KEY-123"


def answer(text="hello", **extra):
    body = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
    body.update(extra)
    return 200, json.dumps(body).encode()


def error(status, message="x", details=None, code="RESOURCE_EXHAUSTED"):
    return status, json.dumps({"error": {"code": status, "message": message, "status": code,
                                         "details": details or []}}).encode()


class FakeNet:
    """Scripted answers: a list of (status, bytes); each call takes the next."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, url, headers, body, timeout=0):
        self.calls.append((url, headers, json.loads(body)))
        return self.script.pop(0)


def gemini(*script, **kw):
    net = FakeNet(*script)
    sleeps = []
    return ai.Gemini(KEY, post=net, sleep=sleeps.append, **kw), net, sleeps


class GeminiTests(unittest.TestCase):

    def test_an_answer(self):
        g, net, _ = gemini(answer("The text."))
        self.assertEqual(g.generate("be brief", "Write.", search=True), "The text.")
        url, headers, body = net.calls[0]
        self.assertIn("gemini-3.8-flash:generateContent", url)
        self.assertEqual(headers["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, url)
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "be brief")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "Write.")
        self.assertEqual(body["tools"], [{"google_search": {}}])
        self.assertEqual(g.calls, 1)
        self.assertEqual(g.by_model, {"gemini-3.8-flash": 1})

    def test_no_search_tool_unless_asked(self):
        g, net, _ = gemini(answer())
        g.generate("", "Write.")
        self.assertNotIn("tools", net.calls[0][2])
        self.assertNotIn("systemInstruction", net.calls[0][2])

    def test_a_fenced_answer_is_unwrapped_and_thoughts_are_dropped(self):
        body = {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True},
                                                      {"text": "```markdown\n# Title\n```"}]}}]}
        g, _, _ = gemini((200, json.dumps(body).encode()))
        self.assertEqual(g.generate("", "x"), "# Title")

    def test_the_daily_limit_of_the_first_model_goes_to_the_fallback(self):
        g, net, _ = gemini(error(429, "quota PerDay"), answer("from lite"))
        self.assertEqual(g.generate("", "x"), "from lite")
        self.assertIn("gemini-3.5-flash-lite:generateContent", net.calls[1][0])

    def test_both_models_limited_is_quota_exhausted_not_a_failure(self):
        g, _, _ = gemini(error(429, "PerDay"), error(429, "PerDay"))
        with self.assertRaises(ai.QuotaExhausted):
            g.generate("", "x")

    def test_the_limit_that_was_hit_is_named_without_secrets(self):
        details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20",
                                    "quotaMetric": "x"}]}]
        g, _, _ = gemini(error(429, "quota PerDay", details), error(429, "quota PerDay", details))
        with self.assertRaises(ai.QuotaExhausted) as caught:
            g.generate("", "x")
        text = str(caught.exception)
        self.assertIn("GenerateRequestsPerDayPerProjectPerModel-FreeTier limit 20", text)
        self.assertIn("gemini-3.8-flash", text)
        self.assertNotIn(KEY, text)

    def test_search_without_quota_is_given_up_and_the_text_still_comes(self):
        g, net, _ = gemini(error(429, "no quota"), error(429, "no quota"), answer("without search"), answer("again"))
        self.assertEqual(g.generate("", "x", search=True), "without search")
        self.assertFalse(g.search_ok)
        self.assertIn("tools", net.calls[0][2])
        self.assertNotIn("tools", net.calls[2][2])                 # the same prompt, asked again without the search tool
        self.assertEqual(g.generate("", "y", search=True), "again")
        self.assertNotIn("tools", net.calls[3][2])                 # later requests do not try the search tool again
        self.assertEqual(g.calls, 4)

    def test_a_real_limit_is_still_a_limit_when_search_is_given_up(self):
        g, _, _ = gemini(*[error(429, "PerDay")] * 4)
        with self.assertRaises(ai.QuotaExhausted):
            g.generate("", "x", search=True)

    def test_a_short_wait_is_waited_out_once(self):
        wait = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]
        g, net, sleeps = gemini(error(429, "per minute", wait), answer("ok"))
        self.assertEqual(g.generate("", "x"), "ok")
        self.assertEqual(sleeps, [8.0])
        self.assertEqual(len(net.calls), 2)
        self.assertIn("flash:generateContent", net.calls[1][0].replace("-lite", ""))

    def test_a_long_wait_is_a_limit(self):
        wait = [{"retryDelay": "3000s"}]
        g, _, sleeps = gemini(error(429, "x", wait), error(429, "x", wait))
        with self.assertRaises(ai.QuotaExhausted):
            g.generate("", "x")
        self.assertEqual(sleeps, [])

    def test_a_busy_service_is_retried_then_reported(self):
        g, _, sleeps = gemini(error(503, code="UNAVAILABLE"), answer("ok"))
        self.assertEqual(g.generate("", "x"), "ok")
        self.assertEqual(sleeps, [5])
        g, _, _ = gemini(*[error(503, code="UNAVAILABLE")] * 3)
        with self.assertRaises(ai.AiError) as caught:
            g.generate("", "x")
        self.assertNotIsInstance(caught.exception, ai.QuotaExhausted)

    def test_other_refusals_say_the_status_and_nothing_else(self):
        g, _, _ = gemini(error(400, "API key not valid: SECRET-KEY-123 prompt text", code="INVALID_ARGUMENT"))
        with self.assertRaises(ai.AiError) as caught:
            g.generate("", "my secret prompt")
        text = str(caught.exception)
        self.assertIn("400", text)
        for leaked in (KEY, "prompt text", "my secret prompt"):
            self.assertNotIn(leaked, text)

    def test_a_missing_model_lists_the_models_that_can_write(self):
        listing = (200, json.dumps({"models": [
            {"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-1", "supportedGenerationMethods": ["embedContent"]}]}).encode())
        seen = []

        def get(url, headers):
            seen.append(headers)
            return listing
        g = ai.Gemini(KEY, post=FakeNet(error(404, "x", code="NOT_FOUND"), error(404, "x", code="NOT_FOUND")), sleep=lambda s: None, get=get)
        with self.assertRaises(ai.AiError) as caught:
            g.generate("", "x")
        text = str(caught.exception)
        self.assertIn("404", text)
        self.assertIn("gemini-9-flash", text)
        self.assertNotIn("embedding", text)
        self.assertNotIn(KEY, text)
        broken = ai.Gemini(KEY, post=FakeNet(error(404, "x", code="NOT_FOUND"), error(404, "x", code="NOT_FOUND")), get=lambda url, headers: (500, b""))
        with self.assertRaises(ai.AiError) as caught:
            broken.generate("", "x")
        self.assertIn("unknown", str(caught.exception))

    def test_a_model_the_key_cannot_use_goes_to_the_next_one(self):
        listing = (200, b'{"models": []}')
        g = ai.Gemini(KEY, post=FakeNet(error(404, "gone", code="NOT_FOUND"), answer("from lite")),
                      sleep=lambda s: None, get=lambda url, headers: listing)
        self.assertEqual(g.generate("", "x"), "from lite")

    def test_a_blocked_empty_or_cut_off_answer(self):
        for body in ({"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": []},
                     {"candidates": [{"content": {"parts": [{"text": "  "}]}}]},
                     {"candidates": [{"content": {"parts": [{"text": "half"}]}, "finishReason": "MAX_TOKENS"}]}):
            g, _, _ = gemini((200, json.dumps(body).encode()))
            with self.assertRaises(ai.AiError):
                g.generate("", "x")

    def test_a_network_problem_is_an_ai_error_without_details(self):
        def broken(url, headers, body, timeout=0):
            raise ai.AiError("no answer from the AI (URLError)")
        g = ai.Gemini(KEY, post=broken)
        with self.assertRaises(ai.AiError):
            g.generate("", "x")

    def test_a_model_from_the_configuration(self):
        g, net, _ = gemini(answer(), model="gemini-x", fallback="")
        g.generate("", "x")
        self.assertIn("models/gemini-x:", net.calls[0][0])


class AnthropicTests(unittest.TestCase):

    def test_an_answer_and_the_limit(self):
        body = {"content": [{"type": "text", "text": "Hi"}, {"type": "tool_use"}], "stop_reason": "end_turn"}
        net = FakeNet((200, json.dumps(body).encode()), (429, b"{}"))
        a = ai.Anthropic(KEY, post=net)
        self.assertEqual(a.generate("sys", "prompt"), "Hi")
        self.assertEqual(net.calls[0][1]["x-api-key"], KEY)
        self.assertEqual(net.calls[0][2]["system"], "sys")
        with self.assertRaises(ai.QuotaExhausted):
            a.generate("sys", "prompt")


class MakeClientTests(unittest.TestCase):

    def test_the_provider_and_key_come_from_the_settings_and_the_environment(self):
        client = ai.make_client({}, {"GEMINI_API_KEY": " k "})
        self.assertIsInstance(client, ai.Gemini)
        self.assertEqual(client.key, "k")
        self.assertEqual(client.model, "gemini-3.8-flash")
        self.assertEqual(client.fallback, "gemini-3.5-flash-lite")
        client = ai.make_client({"provider": "gemini", "model": "m1", "fallback_model": ""}, {"GEMINI_API_KEY": "k"})
        self.assertEqual((client.model, client.fallback), ("m1", ""))
        self.assertIsInstance(ai.make_client({"provider": "anthropic"}, {"ANTHROPIC_API_KEY": "k"}), ai.Anthropic)

    def test_a_run_may_name_the_model(self):
        client = ai.make_client({}, {"GEMINI_API_KEY": "k", "AI_MODEL": " gemini-x "})
        self.assertEqual((client.model, client.fallback), ("gemini-x", "gemini-3.5-flash-lite"))
        client = ai.make_client({"model": "from-settings"}, {"GEMINI_API_KEY": "k", "AI_MODEL": "gemini-x"})
        self.assertEqual(client.model, "from-settings")                              # the configuration wins

    def test_no_key_or_an_unknown_provider(self):
        with self.assertRaises(ai.AiError) as caught:
            ai.make_client({}, {})
        self.assertNotIn("GEMINI_API_KEY=", str(caught.exception))
        with self.assertRaises(ai.AiError):
            ai.make_client({"provider": "other"}, {"GEMINI_API_KEY": "k"})


class MeteredTests(unittest.TestCase):

    def test_the_runs_own_limit_stops_before_the_next_request(self):
        g, _, _ = gemini(answer("1"), answer("2"), answer("3"))
        m = ai.Metered(g, limit=2)
        self.assertEqual((m.generate("", "a"), m.generate("", "b")), ("1", "2"))
        with self.assertRaises(ai.QuotaExhausted):
            m.generate("", "c")
        self.assertEqual((m.used, g.calls), (2, 2))

    def test_retries_count_as_requests(self):
        wait = [{"retryDelay": "1s"}]
        g, _, _ = gemini(error(429, "x", wait), answer("ok"))
        m = ai.Metered(g, limit=5)
        m.generate("", "a")
        self.assertEqual(m.used, 2)

    def test_the_days_own_limit_counts_what_earlier_runs_used(self):
        g, _, _ = gemini(answer("1"), answer("2"))
        m = ai.Metered(g, limit=10, used_today=17, daily=18)
        self.assertEqual(m.generate("", "a"), "1")
        with self.assertRaises(ai.QuotaExhausted):
            m.generate("", "b")


if __name__ == "__main__":
    unittest.main()
