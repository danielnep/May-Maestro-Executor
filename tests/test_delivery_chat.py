"""The outgoing May review uses existing durable tickets; never invents a chat send."""
import unittest
from horizon_core.delivery_chat import MARKER, issue_payload, queue_review, review_ticket


def complete_packet():
    sha = "a" * 40
    repo = "danielnep/app-hondas-maxvel"
    url = "https://danielnep.github.io/app-hondas-maxvel/"
    branch = "agent/chat-ui-proof"
    return {
        "request_id": "ui-proof", "status": "reported_complete",
        "quality": {"approved": True},
        "may_review": {
            "status": "ready_for_may_review", "repository": repo,
            "commit": sha, "branch": branch, "preview_url": url},
        "publication": {"published": True, "target_repo": repo,
                        "branch": branch, "commit": sha,
                        "pages_preview": {"status": "verified", "branch": branch,
                                          "page_url": url}},
        "private_summary": "client confidential never share",
    }


class FakeAPI:
    def __init__(self):
        self.issues = []
        self.actions = []
    def _request(self, method, endpoint, body=None):
        self.actions.append((method, endpoint, body))
        if method == "GET":
            return self.issues
        if method == "POST":
            obj = {**body, "number": len(self.issues) + 1}
            self.issues.append(obj)
            return obj
        raise AssertionError(method)


class DeliveryChatTests(unittest.TestCase):
    def test_one_chat_ticket_after_verified_completion_and_no_private_text(self):
        report = complete_packet()
        data = review_ticket(report)
        self.assertEqual(data["ticket"], "delivery-ui-proof-" + "a" * 12)
        packet = issue_payload(report)
        self.assertIn(MARKER, packet["body"])
        self.assertIn("preview_url: https://danielnep.github.io/", packet["body"])
        self.assertIn("type: delivery_review", packet["body"])
        self.assertNotIn("client confidential", str(packet))
        api = FakeAPI()
        self.assertEqual(queue_review(report, api), "queued")
        self.assertEqual(queue_review(report, api), "already_queued")
        self.assertEqual([a[0] for a in api.actions], ["GET", "POST", "GET"])
        self.assertEqual(len(api.issues), 1)

    def test_never_queue_before_real_qa_and_verified_preview(self):
        api = FakeAPI()
        base = complete_packet()
        for field, value in [
            ("status", "pending"), ("status", "needs_attention"),
            ("quality", {"approved": False}), ("request_id", "../unsafe")]:
            with self.subTest(field=field, value=value):
                self.assertEqual(queue_review({**base, field: value}, api), "not_ready")
        changes = [
            {"pages_preview": {"status": "not_verified", "branch": "agent/chat-ui-proof",
                                "page_url": "https://danielnep.github.io/app-hondas-maxvel/"}},
            {"commit": "bad-commit"},
            {"target_repo": "other/repo"},
        ]
        for change in changes:
            with self.subTest(change=change):
                report = complete_packet()
                report["publication"].update(change)
                self.assertEqual(queue_review(report, api), "not_ready")
        report = complete_packet()
        report["may_review"]["preview_url"] = "http://localhost/"
        self.assertEqual(queue_review(report, api), "not_ready")
        self.assertEqual(api.actions, [])

    def test_duplicate_ticket_not_reposted_after_restart_or_closed_issue(self):
        api = FakeAPI()
        report = complete_packet()
        payload = issue_payload(report)
        api.issues = [{**payload, "number": 17, "state": "closed"}]
        self.assertEqual(queue_review(report, api), "already_queued")
        self.assertEqual(len(api.actions), 1)


if __name__ == "__main__":
    unittest.main()
