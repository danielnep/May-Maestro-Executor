"""Isolated KERNEL handoff tests; no live browser or paid request is made."""
import io
import json
import unittest
from unittest.mock import patch

from horizon_core import kernel_live
from horizon_core.commit_bridge import wake_owner_kernel_review, wake_owner_may_reply
from horizon_core.state import State


class Response(io.BytesIO):
    def __init__(self, obj):
        super().__init__(json.dumps(obj).encode("utf-8"))
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class KernelLiveContractTests(unittest.TestCase):
    TASK = {"task_id": "chat-a-ronda-pro-auditoria-total-20261019:audit-ui",
            "project": "chat:a-ronda-pro-auditoria-total-20261019",
            "payload": {"repository": "danielnep/app-hondas-maxvel"}}
    LINK = "https://prod-iad-hypeman-47.kernel.sh:8443/browser/live/H9u9RLratjnt"
    SESSION = "vxmyl2i9mm1h564nzzxuw2fh"

    def test_no_key_is_pending_never_passed(self):
        data = kernel_live.prepare_live_review(self.TASK, environ={},
            opener=lambda *_a, **_k: self.fail("Unexpected remote call"))
        self.assertEqual(data["review_status"], "kernel_key_unavailable")
        self.assertFalse(data["passed"])

    def test_active_session_retrieved_without_playwright_and_ticket_bound(self):
        calls = []
        def opener(req, timeout=None):
            calls.append(req)
            return Response({"session_id": self.SESSION,
                             "browser_live_view_url": self.LINK})
        found = kernel_live.prepare_live_review(self.TASK,
            environ={"KERNEL_API_KEY": "synthetic"}, opener=opener)
        self.assertEqual(found["review_status"], "awaiting_may_visual_review")
        self.assertEqual(found["browser_live_view_url"], self.LINK)
        self.assertFalse(found["passed"])
        self.assertEqual(len(found["ticket"]), 20)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get_method(), "GET")
        self.assertNotIn("playwright", calls[0].full_url)

    def test_never_adopt_untrusted_live_link(self):
        with self.assertRaises(kernel_live.KernelReviewError):
            kernel_live.prepare_live_review(self.TASK,
                environ={"KERNEL_API_KEY": "synthetic"}, opener=lambda *_a, **_k:
                Response({"session_id": self.SESSION,
                          "browser_live_view_url": "https://example.com/browser/live/unsafe"}))

    def test_native_chat_delivery_is_only_ticket_and_link(self):
        calls = []
        def opener(req, timeout=None):
            calls.append(req)
            return Response({"success": True,
                "content": [{"type": "text", "text": '{"status":"sent"}'}]})
        review = {"review_status": "awaiting_may_visual_review",
                  "ticket": "a"*20, "browser_live_view_url": self.LINK}
        sent = kernel_live.send_live_review(review, self.TASK["task_id"],
            environ={"KERNEL_API_KEY": "synthetic"}, opener=opener)
        self.assertEqual(sent, "sent")
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertIn("/browsers/may-chatgpt-72h/repl", req.full_url)
        self.assertNotIn("/playwright/", req.full_url)
        self.assertIn(self.LINK, req.data.decode("utf-8"))
        self.assertNotIn("synthetic", req.data.decode("utf-8"))


class KernelReviewQueueTests(unittest.TestCase):
    TASK = "chat-a-ronda-pro-auditoria-total-20261019:audit-ui"
    TICKET = "a"*20

    def setUp(self):
        self.state = State(":memory:")
        self.addCleanup(self.state.close)
        self.state.enqueue(self.TASK, "horizon-ui-guardian",
            "chat:a-ronda-pro-auditoria-total-20261019",
            "Audit real UI", payload={"repository": "danielnep/app-hondas-maxvel"})
        self.assertEqual(self.state.claim("agent", now=100)["task_id"], self.TASK)
        self.state.checkpoint(self.TASK, "agent", {
            "step": 1, "kernel_finish_summary": "Reviewed screens",
            "kernel_review": {"ticket": self.TICKET,
                "review_status": "awaiting_may_visual_review",
                "workspace_digest": "123",
                "browser_live_view_url": KernelLiveContractTests.LINK}
        }, now=101)
        self.state.defer(self.TASK, "agent", seconds=900, now=102)
        self.event = {"action": "created",
            "repository": {"full_name": "danielnep/May-Maestro-Executor"},
            "issue": {"title": "[May live review] " + self.TICKET,
                      "body": "MAY_KERNEL_REVIEW_REQUEST_V1\nTICKET: " + self.TICKET},
            "comment": {"user": {"login": "danielnep"},
                        "body": "MAY_KERNEL_REVIEW_V1\nTICKET: " + self.TICKET
                                + "\nSCENARIOS: 4\nRESULT: approved"}}

    def test_owner_review_only_once_and_unblocks_scheduled_work(self):
        self.assertEqual(wake_owner_kernel_review(self.state, self.event), [self.TASK])
        row = self.state.get(self.TASK)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["checkpoint"]["kernel_review"]["verified_scenarios"], 4)
        self.assertLess(row["not_before"], 102 + 900)
        self.assertEqual(wake_owner_kernel_review(self.state, self.event), [])

    def test_fake_author_cannot_approve(self):
        self.event["comment"]["user"]["login"] = "unknown"
        self.assertEqual(wake_owner_kernel_review(self.state, self.event), [])
        self.assertFalse(self.state.get(self.TASK)["checkpoint"]["kernel_review"].get("approved_by_owner"))


if __name__ == "__main__":
    unittest.main()
