import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from horizon_core.state import State

from horizon_core.gateway import GatewayError, GeminiGateway, GroqGateway, ModelGateway
from horizon_core.registry import AGENT_IDS, load_agent


class Response(io.BytesIO):
    pass


def envelope(action=None, **extra):
    value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(action or {"action": "finish", "args": {}, "summary": "verified"})}]}}], "usageMetadata": {"totalTokenCount": 42}}
    value.update(extra)
    return Response(json.dumps(value).encode())


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GEMINI_API_KEY": "not-a-real-key", "GEMINI_MODEL": "explicit-test-model", "GEMINI_FREE_TIER_CONFIRMED": "true"}
        self.requests = []
        def fake(request, timeout):
            self.requests.append(request)
            return envelope()
        self.gateway = GeminiGateway(self.env, fake)
        self.messages = [{"role": "user", "content": "Inspect the allowed fixture"}]

    def test_each_specialist_owns_identity_context_and_call(self):
        for ident in AGENT_IDS:
            action = self.gateway.complete(load_agent(ident), self.messages)
            self.assertEqual(action["metadata"]["agent_id"], ident)
            self.assertEqual(action["metadata"]["usage"]["totalTokenCount"], 42)
            payload = json.loads(self.requests[-1].data)
            self.assertIn("Specialist ID: " + ident, payload["systemInstruction"]["parts"][0]["text"])
            self.assertEqual(payload["contents"][0]["parts"][0]["text"], self.messages[0]["content"])
            self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(len(self.requests), 17)
        self.assertEqual(len(set(AGENT_IDS)), 17)

    def test_key_in_header_not_url_and_specialist_override(self):
        self.env["FRONTEND_GEMINI_KEY"] = "specialist-credential"
        agent = load_agent("frontend-engineer")
        agent.update(model="specialist-model", api_key_env="FRONTEND_GEMINI_KEY")
        result = self.gateway.complete(agent, self.messages)
        self.assertEqual(result["metadata"]["model"], "specialist-model")
        self.assertEqual(self.requests[0].get_header("X-goog-api-key"), "specialist-credential")
        self.assertNotIn("specialist-credential", self.requests[0].full_url)

    def test_no_live_without_explicit_prerequisites(self):
        for missing in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FREE_TIER_CONFIRMED"):
            env = dict(self.env)
            env.pop(missing)
            gateway = GeminiGateway(env, self.gateway.opener)
            with self.subTest(missing=missing), self.assertRaises(GatewayError):
                gateway.complete(load_agent("frontend-engineer"), self.messages)
        self.assertEqual(self.requests, [])

    def test_output_and_call_budget(self):
        self.env.update(GEMINI_MAX_CALLS="1", GEMINI_MAX_OUTPUT_TOKENS="100")
        gateway = GeminiGateway(self.env, self.gateway.opener)
        gateway.complete(load_agent("qa-engineer"), self.messages)
        self.assertEqual(json.loads(self.requests[0].data)["generationConfig"]["maxOutputTokens"], 100)
        with self.assertRaisesRegex(GatewayError, "budget exhausted"):
            gateway.complete(load_agent("qa-engineer"), self.messages)
        self.assertEqual(len(self.requests), 1)

    def test_rate_limit_and_server_errors_are_retryable_without_leaking(self):
        for status in (429, 500, 503, 401):
            def fail(request, timeout):
                raise urllib.error.HTTPError(request.full_url, status, self.env["GEMINI_API_KEY"], {}, io.BytesIO(b"secret provider body"))
            gateway = GeminiGateway(self.env, fail)
            with self.assertRaises(GatewayError) as caught:
                gateway.complete(load_agent("qa-engineer"), self.messages)
            self.assertEqual(caught.exception.retryable, status != 401)
            self.assertNotIn(self.env["GEMINI_API_KEY"], str(caught.exception))
            self.assertNotIn("secret provider body", str(caught.exception))
            self.assertEqual(gateway.calls, 1)

    def test_invalid_output_and_unauthorized_actions_rejected(self):
        actions = [[], {"action": "write_file", "args": {}, "summary": "x"},
                   {"action": "run_check", "args": {}, "summary": "x"},
                   {"action": "finish", "args": [], "summary": "x"},
                   {"action": "finish", "args": {}, "summary": "x", "unexpected": True},
                   {"action": "delegate", "args": {}, "summary": "x"}]
        for action in actions:
            gateway = GeminiGateway(self.env, lambda req, timeout: envelope(action))
            with self.subTest(action=action):
                # [] needs explicit malformed candidate because envelope's fixture defaults empty values.
                if action == []:
                    gateway.opener = lambda req, timeout: Response(b'{}')
                with self.assertRaises(GatewayError):
                    gateway.complete(load_agent("corregedoria-may"), self.messages)

    def test_malformed_provider_envelope_and_truncated_generation(self):
        for raw in (b'not json', b'{"candidates":[]}', b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}'):
            gateway = GeminiGateway(self.env, lambda req, timeout: Response(raw))
            with self.assertRaises(GatewayError):
                gateway.complete(load_agent("frontend-engineer"), self.messages)

    def test_only_maestro_has_delegation_and_auditors_cannot_write(self):
        for ident in AGENT_IDS:
            tools = load_agent(ident)["tools"]
            self.assertEqual("delegate" in tools, ident == "may-maestro")
        for ident in ("may-maestro", "horizon-ui-guardian", "security-engineer", "corregedoria-may", "may-persona"):
            self.assertNotIn("write_file", load_agent(ident)["tools"])
        self.assertIn("write_file", load_agent("frontend-engineer")["tools"])
        self.assertIn("run_check", load_agent("qa-engineer")["tools"])

    def test_registry_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            load_agent("../../supervisor")

    def test_explicit_limits_must_be_positive(self):
        for value in ("0", "-1", "nonsense"):
            with self.assertRaises(GatewayError):
                GeminiGateway(dict(self.env, GEMINI_MAX_CALLS=value))



class GroqRoutingTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GROQ_API_KEY": "synthetic-groq",
                    "GEMINI_API_KEY": "synthetic-gemini",
                    "GEMINI_MODEL": "gemini-3.6-flash",
                    "GEMINI_FREE_TIER_CONFIRMED": "true"}
        self.messages = [{"role": "user", "content": "Inspect the fixture"}]
        self.requests = []

    def groq_response(self, action=None):
        body = action if action is not None else {"action": "finish", "args": {}, "summary": "ok"}
        return Response(json.dumps({"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(body), "reasoning_content": "private"}}],
            "usage": {"total_tokens": 12}}).encode())

    def opener(self, req, timeout):
        self.requests.append(req)
        return self.groq_response()

    def test_role_specific_models_cover_all_17_specialists(self):
        gateway = ModelGateway(environ=self.env, opener=self.opener)
        for agent_id in AGENT_IDS:
            result = gateway.complete(load_agent(agent_id), self.messages)
            expected = ("qwen/qwen3.8-27b" if agent_id in
                ("frontend-engineer", "mobile-app-builder", "ui-ux-designer", "horizon-ui-guardian")
                else "openai/gpt-oss-120b")
            self.assertEqual(result["metadata"]["model"], expected)
            self.assertEqual(result["metadata"]["provider"], "groq")
            body = json.loads(self.requests[-1].data)
            self.assertEqual(body["model"], expected)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual(self.requests[-1].get_header("Authorization"), "Bearer synthetic-groq")
            self.assertNotIn("synthetic-groq", self.requests[-1].full_url)
            self.assertNotIn("private", json.dumps(result))
        self.assertEqual(gateway.calls, 17)

    def test_existing_railway_key_alias_is_accepted_if_in_same_process(self):
        env = {"API_IA_GROQ": "another-synthetic-key"}
        result = GroqGateway(env, self.opener).complete(load_agent("backend-engineer"), self.messages)
        self.assertEqual(result["metadata"]["provider"], "groq")
        self.assertEqual(self.requests[-1].get_header("Authorization"), "Bearer another-synthetic-key")

    def test_qwen_unavailable_uses_gpt_oss_inside_groq(self):
        def opener(req, timeout):
            self.requests.append(req)
            payload = json.loads(req.data)
            if payload["model"].startswith("qwen/"):
                raise urllib.error.HTTPError(req.full_url, 404, "not enabled", {}, io.BytesIO(b"secret body"))
            return self.groq_response()
        result = ModelGateway(environ=self.env, opener=opener).complete(load_agent("mobile-app-builder"), self.messages)
        self.assertEqual(result["metadata"]["model"], "openai/gpt-oss-120b")
        self.assertEqual(result["metadata"]["fallback_from"], "groq_ui")
        self.assertEqual(len(self.requests), 2)
        self.assertNotIn("secret body", json.dumps(result))

    def test_missing_groq_key_falls_back_to_gemini(self):
        requests = []
        def opener(req, timeout):
            requests.append(req)
            return envelope()
        env = {name: value for name, value in self.env.items() if name != "GROQ_API_KEY"}
        result = ModelGateway(environ=env, opener=opener).complete(load_agent("frontend-engineer"), self.messages)
        self.assertEqual(result["metadata"]["provider"], "gemini")
        self.assertEqual(result["metadata"]["fallback_from"], "groq")
        self.assertEqual(len(requests), 1)

    def test_groq_401_uses_gemini_without_retrying_other_groq_model(self):
        def opener(req, timeout):
            self.requests.append(req)
            if "api.groq.com" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 401, "token", {}, io.BytesIO(b"private"))
            return envelope()
        result = ModelGateway(environ=self.env, opener=opener).complete(load_agent("ui-ux-designer"), self.messages)
        self.assertEqual(result["metadata"]["provider"], "gemini")
        self.assertEqual(len(self.requests), 2)

    def test_both_providers_fail_with_sanitized_diagnostics(self):
        def fail(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "do not log", {}, io.BytesIO(b"SECRET"))
        with self.assertRaises(GatewayError) as caught:
            ModelGateway(environ=self.env, opener=fail).complete(load_agent("backend-engineer"), self.messages)
        self.assertEqual(caught.exception.reason, "providers_unavailable")
        self.assertEqual([x["provider"] for x in caught.exception.failures], ["groq", "gemini"])
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("synthetic-groq", str(caught.exception))

    def test_groq_429_cooldown_persists_across_gateway_instances(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        def opener(req, timeout):
            self.requests.append(req)
            if "api.groq.com" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"private"))
            return envelope()
        with patch("horizon_core.gateway.time.time", return_value=1000):
            first = ModelGateway(state=state, environ=self.env, opener=opener)
            self.assertEqual(first.complete(load_agent("backend-engineer"), self.messages)["metadata"]["provider"], "gemini")
        with patch("horizon_core.gateway.time.time", return_value=1001):
            resumed = ModelGateway(state=state, environ=self.env, opener=opener)
            result = resumed.complete(load_agent("backend-engineer"), self.messages)
            self.assertTrue(result["metadata"]["groq_cooldown_skipped"])
            self.assertEqual(resumed.groq.calls, 0)
        self.assertNotIn("synthetic-groq", json.dumps(state.recall("__runtime__", "gateway")))

    def test_high_reasoning_only_set_for_gpt_oss(self):
        agent = dict(load_agent("backend-engineer"), _reasoning_level="high")
        GroqGateway(self.env, self.opener).complete(agent, self.messages)
        self.assertEqual(json.loads(self.requests[-1].data)["reasoning_effort"], "high")
        GroqGateway(self.env, self.opener).complete(
            dict(load_agent("mobile-app-builder"), _reasoning_level="high"), self.messages)
        self.assertNotIn("reasoning_effort", json.loads(self.requests[-1].data))

    def test_invalid_action_is_blocked(self):
        def fake(req, timeout):
            return self.groq_response({"action": "write_file", "args": {}, "summary": "bad"})
        with self.assertRaises(GatewayError):
            GroqGateway(self.env, fake).complete(load_agent("may-persona"), self.messages)


if __name__ == "__main__":
    unittest.main()
