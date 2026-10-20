import io
import json
import unittest
from urllib.error import HTTPError

from horizon_core.provider_access_probe import check_groq, UI_MODEL, CODE_MODEL


class Response(io.BytesIO):
    status = 200
    def __init__(self, doc):
        super().__init__(json.dumps(doc).encode())


class ProbeTests(unittest.TestCase):
    def test_missing_key_sends_no_requests(self):
        def fail(*args, **kwargs):
            raise AssertionError("should not connect")
        self.assertEqual(check_groq({}, fail)["status"], "missing_key")

    def test_auth_catalog_and_no_secret_in_output(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/models")
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-secret")
            return Response({"data": [{"id": UI_MODEL}, {"id": CODE_MODEL}]})
        report = check_groq({"GROQ_API_KEY": "synthetic-secret"}, opener)
        self.assertEqual(report["status"], "authenticated")
        self.assertTrue(report["usable"])
        self.assertTrue(all(report["models_listed"].values()))
        self.assertEqual(report["inference_requests"], 0)
        self.assertNotIn("synthetic-secret", str(report))
        self.assertEqual(len(seen), 1)

    def test_not_listed_model_does_not_claim_ready(self):
        report = check_groq({"API_IA_GROQ": "x"},
            lambda *a, **k: Response({"data": [{"id": "other"}]}))
        self.assertEqual(report["status"], "authenticated")
        self.assertFalse(report["usable"])

    def test_bad_catalog_is_rejected(self):
        report = check_groq({"GROQ_API_KEY": "x"},
            lambda *a, **k: Response({"models": []}))
        self.assertEqual(report["status"], "invalid_response")

    def test_auth_quota_and_server_errors(self):
        for status, expected in [(401, "invalid_credentials"), (403, "invalid_credentials"),
                                 (429, "rate_limited"), (503, "http_error")]:
            with self.subTest(status=status):
                def bad(req, timeout):
                    raise HTTPError(req.full_url, status, "secret", {}, None)
                report = check_groq({"GROQ_API_KEY": "secret"}, bad)
                self.assertEqual(report["status"], expected)
                self.assertNotIn("secret", str(report))


if __name__ == "__main__":
    unittest.main()
