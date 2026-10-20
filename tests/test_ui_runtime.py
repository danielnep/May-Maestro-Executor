"""Live KERNEL review is mandatory for visual specialists (no Playwright)."""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from horizon_core.state import State
from horizon_core.tools import Tools
from horizon_core.worker import Worker


class Model:
    def __init__(self):
        self.contexts = []
    def complete(self, agent, messages):
        self.contexts.append(json.loads(messages[0]["content"]))
        return {"action": "finish", "args": {}, "summary": "Audit requires live May review"}


class RuntimeVisualTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "switch", "-c", "feat/ui-test"],
                       check=True, capture_output=True)
        (self.root / "index.html").write_text("<html><button>Mensagens</button></html>")
        self.state = State(":memory:")
        self.addCleanup(self.state.close)

    def test_unedited_real_ui_audit_still_waits_for_live_review(self):
        self.state.enqueue("ui1", "horizon-ui-guardian", "chat:ronda",
                           "Review navigation", payload={"repository": "danielnep/app-hondas-maxvel"})
        browser = {"review_status": "awaiting_may_visual_review", "check": "kernel-live",
                   "passed": False, "ticket": "a"*20,
                   "browser_live_view_url": "https://prod-kernel.kernel.sh/browser/live/session1"}
        llm = Model()
        with patch("horizon_core.kernel_live.prepare_live_review", return_value=browser), \
             patch("horizon_core.kernel_live.send_live_review", return_value="sent"), \
             patch.object(Tools, "run_check", side_effect=AssertionError("No Playwright")):
            row = Worker(self.state, llm, self.root).run_one()
        self.assertEqual(row["status"], "queued")
        self.assertEqual(len(llm.contexts), 1)
        self.assertEqual(row["checkpoint"]["kernel_review"]["chat_delivery"], "sent")
        self.assertFalse(row["checkpoint"]["kernel_review"]["passed"])

    def test_missing_kernel_does_not_trigger_browser_fallback_or_fake_success(self):
        self.state.enqueue("ui2", "ui-ux-designer", "chat:ronda", "Visual review")
        llm = Model()
        with patch("horizon_core.kernel_live.prepare_live_review",
                   return_value={"check":"kernel-live","passed":False,
                                 "review_status":"kernel_key_unavailable"}), \
             patch.object(Tools, "run_check", side_effect=AssertionError("No Playwright")):
            first = Worker(self.state, llm, self.root).run_one()
            self.assertEqual(first["status"], "queued")
            second = Worker(self.state, llm, self.root).run_one()
        self.assertIsNone(second)
        self.assertEqual(len(llm.contexts), 1)

    def test_authenticated_review_resumes_without_model_or_new_browser(self):
        self.state.enqueue("ui3","horizon-ui-guardian","chat:ronda","Inspect UI",
                           payload={"repository": "danielnep/app-hondas-maxvel"})
        llm = Model()
        browser = {"review_status": "awaiting_may_visual_review", "check": "kernel-live",
                   "passed": False, "ticket":"b"*20,
                   "browser_live_view_url":"https://prod-kernel.kernel.sh/browser/live/session2"}
        with patch("horizon_core.kernel_live.prepare_live_review", return_value=browser), \
             patch("horizon_core.kernel_live.send_live_review", return_value="sent"):
            pending = Worker(self.state, llm, self.root).run_one()
        self.assertEqual(pending["status"], "queued")
        self.assertTrue(self.state.approve_kernel_review("ui3", "b"*20, 3))
        with patch("horizon_core.kernel_live.prepare_live_review",
                   side_effect=AssertionError("Must reuse review")), \
             patch.object(llm, "complete", side_effect=AssertionError("No new Gemini")):
            resumed = Worker(self.state, llm, self.root).run_one()
        self.assertEqual(resumed["status"], "succeeded")
        self.assertEqual(resumed["result"]["verification"][0]["check"],
                         "kernel-live-owner-review")
        self.assertEqual(resumed["result"]["verification"][0]["tests_run"], 3)


if __name__ == "__main__":
    unittest.main()
