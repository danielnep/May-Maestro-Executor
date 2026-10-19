"""End-to-end approval flow with fake providers and deterministic isolated test evidence."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from horizon_core.state import State
from horizon_core.tools import Tools
from horizon_core.worker import Worker


FAILED = {"check": "python-unittest", "passed": False, "source": "runtime",
          "simulated": False, "tests_run": 3, "exit_code": 1,
          "workspace_unchanged": True}


class ControlledGateway:
    def __init__(self):
        self.calls = []
        self.original_count = 0

    def complete(self, agent, messages):
        context = json.loads(messages[0]["content"])
        self.calls.append((agent["id"], agent.get("_reasoning_level")))
        if agent["id"] == "may-maestro":
            self.assert_low(agent)
            if context["completed_steps"] == 0:
                return {"action": "remember", "args": {
                    "key": "reasoning-decision:incident",
                    "value": {"approve": True, "reason":
                              "Two independent failing actual Python test executions"}},
                    "summary": "Approved verified escalation"}
            return {"action": "finish", "args": {}, "summary": "Review recorded"}
        self.original_count += 1
        if self.original_count < 3:
            return {"action": "run_check", "args": {"name": "python-unittest"},
                    "summary": "Reproduce test failure"}
        return {"action": "finish", "args": {}, "summary": "Escalated reasoning used"}

    @staticmethod
    def assert_low(agent):
        if agent.get("_reasoning_level") != "low":
            raise AssertionError("Maestro review must not self-elevate")


class ReasoningFlowTest(unittest.TestCase):
    def test_specialist_gets_approval_only_after_verified_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", str(root)], capture_output=True, check=True)
            state = State(":memory:")
            self.addCleanup(state.close)
            state.enqueue("incident", "backend-engineer", "customer",
                          "Investigate two failed checks",
                          payload={"branch": "fix/test", "allowed_paths": []})
            gateway = ControlledGateway()
            with patch.object(Tools, "run_check", return_value=dict(FAILED)):
                initial = Worker(state, gateway, root).run_one()
                self.assertEqual(initial["status"], "queued")
                self.assertEqual(gateway.calls, [("backend-engineer","low"),
                                                  ("backend-engineer","low")])
                review = state.get("incident:reasoning-review")
                self.assertEqual(review["status"], "queued")
                reviewer = Worker(state, gateway, root).run_one()
                self.assertEqual(reviewer["status"], "succeeded")
                self.assertEqual(reviewer["agent"], "may-maestro")
                resumed = Worker(state, gateway, root).run_one()
                self.assertEqual(resumed["status"], "succeeded")
            self.assertEqual(gateway.calls[-1], ("backend-engineer","high"))
            self.assertEqual(gateway.calls[2:4],
                             [("may-maestro","low"), ("may-maestro","low")])


if __name__ == "__main__":
    unittest.main()
