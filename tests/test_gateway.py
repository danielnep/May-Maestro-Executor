import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from horizon_core.state import State

from horizon_core.gateway import GatewayError, GeminiGateway, DeepSeekGateway, ModelGateway
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


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GEMINI_API_KEY": "gemini-test-secret", "GEMINI_MODEL": "gemini-model",
                    "GEMINI_FREE_TIER_CONFIRMED": "true", "DEEPSEEK_API_KEY": "deepseek-test-secret"}
        self.messages = [{"role": "user", "content": "Inspect the allowed fixture"}]
        self.requests = []

    def secondary_response(self, action=None, **extra):
        action = action if action is not None else {"action": "finish", "args": {}, "summary": "verified"}
        value = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(action), "reasoning_content": "private reasoning never retained"}}], "usage": {"total_tokens": 100}}
        value.update(extra)
        return Response(json.dumps(value).encode())

    def failing_primary(self, status=429):
        def opener(request, timeout):
            self.requests.append(request)
            if "googleapis" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, status, "upstream secret", {}, io.BytesIO(b"secret upstream body"))
            return self.secondary_response()
        return opener

    def test_all_17_specialists_fallback_independently_with_one_primary_failure(self):
        gateway = ModelGateway(environ=self.env, opener=self.failing_primary())
        for ident in AGENT_IDS:
            action = gateway.complete(load_agent(ident), self.messages)
            self.assertEqual(action["metadata"]["agent_id"], ident)
            self.assertEqual(action["metadata"]["provider"], "deepseek")
            self.assertEqual(action["metadata"]["fallback_reason"]["status"], 429)
            self.assertIn("Specialist ID: " + ident, json.loads(self.requests[-1].data)["messages"][0]["content"])
        self.assertEqual(len(self.requests), 18)
        self.assertEqual(gateway.gemini.calls, 1)
        self.assertEqual(gateway.deepseek.calls, 17)

    def test_cooldown_persists_and_primary_recovers_on_next_action(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        with patch("horizon_core.gateway.time.time", return_value=1000):
            first = ModelGateway(state=state, environ=self.env, opener=self.failing_primary())
            first.complete(load_agent("qa-engineer"), self.messages)
        with patch("horizon_core.gateway.time.time", return_value=1001):
            resumed = ModelGateway(state=state, environ=self.env, opener=self.failing_primary())
            result = resumed.complete(load_agent("backend-engineer"), self.messages)
            self.assertTrue(result["metadata"]["gemini_cooldown_skipped"])
            self.assertEqual(resumed.gemini.calls, 0)
        with patch("horizon_core.gateway.time.time", return_value=1400):
            resumed.gemini.opener = lambda req, timeout: envelope()
            result = resumed.complete(load_agent("frontend-engineer"), self.messages)
            self.assertEqual(result["metadata"]["provider"], "gemini")
            self.assertEqual(state.recall("__runtime__", "gateway")["circuit"], {})
        stored = json.dumps(state.recall("__runtime__", "gateway"))
        for secret in self.env.values():
            self.assertNotIn(secret, stored)

    def test_fallback_for_auth_quota_server_errors(self):
        for status in (400, 401, 403, 429, 500, 503):
            self.requests.clear()
            gateway = ModelGateway(environ=self.env, opener=self.failing_primary(status))
            result = gateway.complete(load_agent("may-maestro"), self.messages)
            self.assertEqual(result["metadata"]["fallback_reason"]["status"], status)
            self.assertEqual(len(self.requests), 2)

    def test_fallback_for_missing_primary_settings_or_confirmation(self):
        for missing in ("GEMINI_MODEL", "GEMINI_API_KEY", "GEMINI_FREE_TIER_CONFIRMED"):
            env = dict(self.env)
            env.pop(missing)
            self.requests.clear()
            gateway = ModelGateway(environ=env, opener=self.failing_primary())
            result = gateway.complete(load_agent("qa-engineer"), self.messages)
            self.assertEqual(result["metadata"]["fallback_reason"]["reason"], "configuration")
            self.assertEqual(len(self.requests), 1)

    def test_invalid_explicit_primary_config_still_falls_back(self):
        env = dict(self.env, GEMINI_MAX_CALLS="invalid")
        gateway = ModelGateway(environ=env, opener=self.failing_primary())
        result = gateway.complete(load_agent("qa-engineer"), self.messages)
        self.assertEqual(result["metadata"]["fallback_reason"]["reason"], "configuration")
        self.assertEqual(gateway.calls, 1)

    def test_transport_invalid_format_and_blocked_primary_fallback(self):
        for raw in (None, b'not json', b'{"candidates":[{"finishReason":"SAFETY"}]}', b'{"candidates":[{"content":{"parts":[{"text":"oops"}]}}]}'):
            def opener(request, timeout):
                if "googleapis" in request.full_url:
                    if raw is None:
                        raise urllib.error.URLError("gemini-test-secret")
                    return Response(raw)
                return self.secondary_response()
            result = ModelGateway(environ=self.env, opener=opener).complete(load_agent("qa-engineer"), self.messages)
            self.assertEqual(result["metadata"]["provider"], "deepseek")

    def test_deepseek_reasoning_json_and_authorization_only_in_header(self):
        def opener(request, timeout):
            self.requests.append(request)
            return self.secondary_response()
        result = DeepSeekGateway(self.env, opener).complete(load_agent("frontend-engineer"), self.messages)
        payload = json.loads(self.requests[0].data)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(self.requests[0].get_header("Authorization"), "Bearer deepseek-test-secret")
        self.assertNotIn("deepseek-test-secret", self.requests[0].full_url)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("private reasoning", json.dumps(result))
        self.assertEqual(result["metadata"]["model"], "deepseek-flash")

    def test_no_default_call_or_output_caps_but_explicit_override_available(self):
        gateway = GeminiGateway(self.env, lambda req, timeout: envelope())
        self.assertIsNone(gateway.max_calls)
        self.assertIsNone(gateway.max_output_tokens)
        for _ in range(25):
            gateway.complete(load_agent("frontend-engineer"), self.messages)
        def opener(request, timeout):
            self.assertNotIn("maxOutputTokens", json.loads(request.data)["generationConfig"])
            return envelope()
        GeminiGateway(self.env, opener).complete(load_agent("frontend-engineer"), self.messages)
        deepseek = DeepSeekGateway(dict(self.env, DEEPSEEK_MAX_OUTPUT_TOKENS="123456", DEEPSEEK_MODEL="explicit-deepseek-model"), lambda req, timeout: self.secondary_response())
        self.assertEqual(deepseek.max_output_tokens, 123456)
        self.assertEqual(deepseek.complete(load_agent("qa-engineer"), self.messages)["metadata"]["model"], "explicit-deepseek-model")

    def test_both_failures_retained_without_leaks_and_retryable(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "deepseek-test-secret", {}, io.BytesIO(b"gemini-test-secret"))
        gateway = ModelGateway(environ=self.env, opener=opener)
        with self.assertRaises(GatewayError) as caught:
            gateway.complete(load_agent("qa-engineer"), self.messages)
        error = caught.exception
        self.assertTrue(error.retryable)
        self.assertEqual([f["provider"] for f in error.failures], ["gemini", "deepseek"])
        self.assertEqual([f["status"] for f in error.failures], [401, 401])
        for secret in ("gemini-test-secret", "deepseek-test-secret"):
            self.assertNotIn(secret, str(error) + json.dumps(error.failures))

    def test_deepseek_enforces_same_action_permissions_and_rejects_bad_envelopes(self):
        bad = {"action": "write_file", "args": {"path": "x", "content": "x"}, "summary": "unauthorized"}
        for raw in (b'[]', b'{}', b'not json', b'{"choices":[{"finish_reason":"length"}]}'):
            with self.assertRaises(GatewayError):
                DeepSeekGateway(self.env, lambda req, timeout: Response(raw)).complete(load_agent("corregedoria-may"), self.messages)
        with self.assertRaises(GatewayError):
            DeepSeekGateway(self.env, lambda req, timeout: self.secondary_response(bad)).complete(load_agent("corregedoria-may"), self.messages)


if __name__ == "__main__":
    unittest.main()
