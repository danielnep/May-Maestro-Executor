"""Regression coverage for private, API-key-free CPU inference fallback."""
import io
import json
import unittest

from horizon_core.gateway import ModelGateway, OllamaGateway, GatewayError
from horizon_core.registry import load_agent


class LocalInferenceTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        def fake(request, timeout):
            self.requests.append((request, timeout))
            reply = {"message": {"role": "assistant", "content": json.dumps(
                {"action": "finish", "args": {}, "summary": "Local model responded"})},
                "prompt_eval_count": 123, "eval_count": 20}
            return io.BytesIO(json.dumps(reply).encode())
        self.fake = fake
        self.agent = load_agent("ui-ux-designer")
        self.messages = [{"role": "user", "content": "Check local model"}]

    def test_local_never_sends_data_to_public_provider(self):
        gateway = ModelGateway(
            environ={"HORIZON_LOCAL_MODEL_ENABLED": "true",
                     "OLLAMA_MODEL": "qwen3:4b",
                     "OLLAMA_HTTP_TIMEOUT_SECONDS": "300"},
            opener=self.fake)
        result = gateway.complete(self.agent, self.messages)
        self.assertEqual(result["action"], "finish")
        self.assertTrue(result["metadata"]["local_inference"])
        self.assertEqual(result["metadata"]["provider"], "ollama")
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(len(self.requests), 1)
        req, timeout = self.requests[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(timeout, 300)
        payload = json.loads(req.data)
        self.assertEqual(payload["format"], "json")
        self.assertFalse(payload["think"])
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertIn("Specialist ID: ui-ux-designer",
                      payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"], "Check local model")

    def test_local_opt_in_is_explicit(self):
        gateway = OllamaGateway({}, self.fake)
        with self.assertRaises(GatewayError) as err:
            gateway.complete(self.agent, self.messages)
        self.assertEqual(err.exception.reason, "configuration")
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
