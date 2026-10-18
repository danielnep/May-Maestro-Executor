"""Contract tests: GitHub completion notice without external side effects."""
import io
import json
import unittest

from horizon_core.notify import alert_payload, notify


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status = data, status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args): return json.dumps(self.data).encode()


class NotificationTests(unittest.TestCase):
    def test_pending_does_not_notify(self):
        self.assertIsNone(alert_payload({"request_id": "demo", "status": "pending"}))
        self.assertEqual(notify({"request_id": "demo", "status": "pending"}, None), "not_terminal")

    def test_terminal_result_sends_one_assigned_notification(self):
        requests = []
        def opener(req, timeout):
            requests.append(req)
            return FakeResponse([], 200) if req.get_method() == "GET" else FakeResponse({}, 201)
        report = {"request_id": "demo-2", "status": "reported_complete",
                  "summary": "private report that must never appear"}
        self.assertEqual(notify(report, "test-token", opener), "notified")
        self.assertEqual(len(requests), 2)
        payload = json.loads(requests[-1].data)
        self.assertEqual(payload["assignees"], ["danielnep"])
        self.assertIn("tasks/results/demo-2.json", payload["body"])
        self.assertNotIn("private report", str(payload))
        self.assertEqual(requests[-1].get_method(), "POST")

    def test_retry_idempotent_from_existing_issue(self):
        def opener(req, timeout):
            self.assertEqual(req.get_method(), "GET")
            return FakeResponse([{"body": "<!-- horizon-task:demo-2 -->"}])
        self.assertEqual(notify({"request_id": "demo-2", "status": "needs_attention"},
                                "token", opener), "already_notified")

    def test_invalid_request_does_not_alert(self):
        self.assertIsNone(alert_payload({"request_id": "../secret", "status": "reported_complete"}))
        self.assertIsNone(alert_payload({"request_id": "ok", "status": "running"}))


if __name__ == "__main__":
    unittest.main()
