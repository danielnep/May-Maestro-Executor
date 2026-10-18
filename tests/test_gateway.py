import io
import json
import unittest
import urllib.error

from horizon_core.gateway import GatewayError, GeminiGateway
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

    def test_limits_must_be_positive_and_bounded(self):
        for value in ("0", "-1", "1001", "nonsense"):
            with self.assertRaises(GatewayError):
                GeminiGateway(dict(self.env, GEMINI_MAX_CALLS=value))


if __name__ == "__main__":
    unittest.main()
