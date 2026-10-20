"""GitHub-only regression of the real Groq gateway wiring, without network access."""
import io
import json
import unittest
from urllib.error import HTTPError

from horizon_core.gateway import GatewayError, GroqGateway, ModelGateway
from horizon_core.registry import AGENT_IDS, load_agent


class GroqGatewayTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GROQ_API_KEY": "synthetic-groq-only-key",
                    "GROQ_MODEL": "openai/gpt-oss-120b",
                    "GEMINI_API_KEY": "synthetic-gemini-key",
                    "GEMINI_MODEL": "gemini-3.6-flash",
                    "GEMINI_FREE_TIER_CONFIRMED": "true",
                    "DEEPSEEK_API_KEY": "should-never-use"}
        self.messages = [{"role": "user", "content": "Read the fixture"}]
        self.urls = []

    def opener(self, request, timeout):
        self.urls.append(request.full_url)
        if "generativelanguage.googleapis.com" in request.full_url:
            raise HTTPError(request.full_url, 429, "quota", {}, None)
        if "api.deepseek.com" in request.full_url:
            raise AssertionError("DeepSeek should never be called when Groq is configured")
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-groq-only-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "openai/gpt-oss-120b")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["include_reasoning"], False)
        self.assertEqual(payload["reasoning_effort"], "low")
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"action": "finish", "args": {}, "summary": "completed"})}}], "usage": {"total_tokens": 30}}
        return io.BytesIO(json.dumps(response).encode())

    def test_all_specialists_fallback_to_groq_without_deepseek(self):
        gateway = ModelGateway(environ=self.env, opener=self.opener)
        for name in AGENT_IDS:
            value = gateway.complete(load_agent(name), self.messages)
            self.assertEqual(value["metadata"]["provider"], "groq")
            self.assertEqual(value["metadata"]["agent_id"], name)
            self.assertEqual(value["metadata"]["model"], "openai/gpt-oss-120b")
        self.assertEqual(gateway.groq.calls, len(AGENT_IDS))
        self.assertEqual(gateway.deepseek.calls, 0)

    def test_explicit_groq_key_name_alias(self):
        env = dict(self.env)
        env["API_IA_GROQ"] = env.pop("GROQ_API_KEY")
        result = GroqGateway(env, self.opener).complete(load_agent("qa-engineer"), self.messages)
        self.assertEqual(result["metadata"]["provider"], "groq")

    def test_groq_failure_is_sanitized_and_does_not_call_deepseek(self):
        def failing(request, timeout):
            if "generativelanguage.googleapis.com" in request.full_url:
                raise HTTPError(request.full_url, 429, "quota", {}, None)
            self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
            raise HTTPError(request.full_url, 401, "synthetic-groq-only-key", {}, None)
        with self.assertRaises(GatewayError) as caught:
            ModelGateway(environ=self.env, opener=failing).complete(load_agent("qa-engineer"), self.messages)
        error = caught.exception
        self.assertEqual(error.reason, "providers_unavailable")
        self.assertEqual([x["provider"] for x in error.failures], ["gemini", "groq"])
        self.assertNotIn("synthetic-groq-only-key", str(error))

    def test_missing_key_never_calls_network(self):
        with self.assertRaises(GatewayError) as caught:
            GroqGateway({}, lambda *a, **k: self.fail("network call forbidden")).complete(
                load_agent("qa-engineer"), self.messages)
        self.assertEqual(caught.exception.reason, "configuration")

if __name__ == "__main__":
    unittest.main()
