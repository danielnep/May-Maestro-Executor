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
        return envelope() if "generativelanguage.googleapis.com" in req.full_url else self.groq_response()

    def test_owner_groq_only_never_calls_gemini_even_with_valid_key(self):
        env = dict(self.env, HORIZON_AI_ROUTE='groq_only')
        gateway = ModelGateway(environ=env, opener=self.opener)
        with patch("horizon_core.groq_minute_budget.GroqMinuteBudget.reserve",
                   return_value=("synthetic", 0, 1536)), patch(
                   "horizon_core.groq_minute_budget.GroqMinuteBudget.actual_usage"):
            for agent_id in ('may-maestro', 'ui-ux-designer', 'frontend-engineer'):
                outcome = gateway.complete(load_agent(agent_id), self.messages)
                self.assertEqual(outcome['metadata']['provider'], 'groq')
                self.assertEqual(outcome['metadata']['routing'], 'groq_only')
                self.assertNotIn('fallback_from', outcome['metadata'])
                self.assertNotIn('fallback_reasons', outcome['metadata'])
        self.assertEqual(gateway.gemini.calls, 0)
        self.assertEqual(len(self.requests), 3)
        self.assertTrue(all('api.groq.com' in request.full_url for request in self.requests))

    def test_gemini_primary_for_all_17_specialists(self):
        gateway = ModelGateway(environ=self.env, opener=self.opener)
        for agent_id in AGENT_IDS:
            action = gateway.complete(load_agent(agent_id), self.messages)
            self.assertEqual(action["metadata"]["provider"], "gemini")
            self.assertEqual(action["metadata"]["agent_id"], agent_id)
        self.assertEqual(len(self.requests), 17)
        self.assertEqual(gateway.groq.calls, 0)

    def test_gemini_outage_routes_each_specialist_to_its_groq_model(self):
        def no_gemini(req, timeout):
            self.requests.append(req)
            if "generativelanguage.googleapis.com" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"PRIVATE"))
            return self.groq_response()
        env = dict(self.env)
        env.pop("GEMINI_FREE_FALLBACK_MODEL", None)
        gateway = ModelGateway(environ=env, opener=no_gemini)
        with patch("horizon_core.groq_minute_budget.GroqMinuteBudget.reserve",
                   return_value=("synthetic", 0, 1536)), patch(
                   "horizon_core.groq_minute_budget.GroqMinuteBudget.actual_usage"):
            for agent_id in AGENT_IDS:
                action = gateway.complete(load_agent(agent_id), self.messages)
                expected = "openai/gpt-oss-120b"
                self.assertEqual(action["metadata"]["model"], expected)
                self.assertEqual(action["metadata"]["provider"], "groq")
        self.assertEqual(gateway.groq.calls, 17)
        self.assertNotIn("PRIVATE", json.dumps(self.requests[-1].full_url))

    def test_gemma_31b_second_receives_same_task_context(self):
        def primary_unavailable(req, timeout):
            self.requests.append(req)
            if "gemma-4-31b-it" not in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"PRIVATE"))
            return envelope()
        gateway = ModelGateway(environ=self.env, opener=primary_unavailable)
        agent = dict(load_agent("backend-engineer"), _task_id="romaneio-compare-01")
        action = gateway.complete(agent, self.messages)
        self.assertEqual(action["metadata"]["model"], "gemma-4-31b-it")
        self.assertEqual(action["metadata"]["routing"], "gemma_second")
        self.assertEqual(len(self.requests), 2)
        first, second = (json.loads(req.data)["contents"] for req in self.requests)
        self.assertEqual(first, second)

    def test_gemini_gemma_and_gpt_oss_outage_uses_qwen_fourth(self):
        def failing(req, timeout):
            self.requests.append(req)
            if "generativelanguage.googleapis.com" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"PRIVATE"))
            body = json.loads(req.data)
            if body["model"] == "openai/gpt-oss-120b":
                raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"PRIVATE"))
            return self.groq_response()
        action = ModelGateway(environ=self.env, opener=failing).complete(
            load_agent("backend-engineer"), self.messages)
        self.assertEqual(action["metadata"]["provider"], "groq")
        self.assertEqual(action["metadata"]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(len(self.requests), 4)

    def test_local_model_minute_wait_moves_to_other_groq_model(self):
        def opener(req, timeout):
            self.requests.append(req)
            return self.groq_response()
        gateway = ModelGateway(environ={"GROQ_API_KEY": "synthetic-groq"}, opener=opener)
        def fake_reserve(model, messages, max_output):
            if model == "openai/gpt-oss-120b":
                return None, 120, 1500
            return "ok", 0, 1500
        with patch.object(gateway.groq.minute_budget, "reserve", side_effect=fake_reserve):
            result = gateway.complete(load_agent("backend-engineer"), self.messages)
        self.assertEqual(result["metadata"]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(len(self.requests), 1)

    def test_existing_groq_key_alias_is_accepted(self):
        result = GroqGateway({"API_IA_GROQ": "another-synthetic-key"}, self.opener).complete(
            load_agent("backend-engineer"), self.messages)
        self.assertEqual(result["metadata"]["provider"], "groq")
        self.assertEqual(self.requests[-1].get_header("Authorization"), "Bearer another-synthetic-key")

    def test_all_unavailable_preserves_sanitized_diagnostics(self):
        def fail(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "do not log", {}, io.BytesIO(b"SECRET"))
        with self.assertRaises(GatewayError) as caught:
            ModelGateway(environ=self.env, opener=fail).complete(load_agent("backend-engineer"), self.messages)
        self.assertEqual(caught.exception.reason, "providers_unavailable")
        self.assertEqual([x["provider"] for x in caught.exception.failures], ["gemini", "gemini", "groq"])
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("synthetic-groq", str(caught.exception))

    def test_gemini_cooldown_reused_across_worker_instances(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        def opener(req, timeout):
            self.requests.append(req)
            if "generativelanguage.googleapis.com" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"private"))
            return self.groq_response()
        with patch("horizon_core.gateway.time.time", return_value=1000):
            first = ModelGateway(state=state, environ=self.env, opener=opener)
            self.assertEqual(first.complete(load_agent("backend-engineer"), self.messages)["metadata"]["provider"], "groq")
        with patch("horizon_core.gateway.time.time", return_value=1001):
            resumed = ModelGateway(state=state, environ=self.env, opener=opener)
            result = resumed.complete(load_agent("backend-engineer"), self.messages)
            self.assertTrue(result["metadata"]["fallback_reasons"])
            self.assertEqual(resumed.gemini.calls, 0)
        self.assertNotIn("synthetic-groq", json.dumps(state.recall("__runtime__", "gateway")))

    def test_specialist_prompt_concise_and_own_identity(self):
        for agent_id in AGENT_IDS:
            agent = load_agent(agent_id)
            concise = agent["_groq_instructions"]
            self.assertIn("Shared mandatory engineering rules", concise)
            self.assertIn("Never ask the external ChatGPT", concise)
            self.assertLess(len(concise.encode("utf-8")), 12500)
            self.assertNotIn("consult_may", agent["tools"])

    def test_high_reasoning_for_gpt_oss_and_qwen(self):
        agent = dict(load_agent("backend-engineer"), _reasoning_level="high")
        GroqGateway(self.env, self.opener).complete(agent, self.messages)
        self.assertEqual(json.loads(self.requests[-1].data)["reasoning_effort"], "high")
        GroqGateway(self.env, self.opener).complete(
            dict(load_agent("mobile-app-builder"), groq_model="qwen/qwen3.8-27b",
                 _reasoning_level="high"), self.messages)
        self.assertEqual(json.loads(self.requests[-1].data)["reasoning_effort"], "high")
        self.assertEqual(json.loads(self.requests[-1].data)["reasoning_format"], "hidden")

    def test_forbidden_action_blocked(self):
        def invalid(req, timeout):
            return self.groq_response({"action": "write_file", "args": {}, "summary": "bad"})
        with self.assertRaises(GatewayError):
            GroqGateway(self.env, invalid).complete(load_agent("may-persona"), self.messages)


if __name__ == "__main__":
    unittest.main()
