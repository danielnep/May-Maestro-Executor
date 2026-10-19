"""Trusted Maestro reasoning approval: no escalation without changed code and unique QA."""
import json
import unittest
from horizon_core.state import State
from horizon_core.reasoning_policy import (failed_checks, escalation_justified,
    create_reasoning_review, save_maestro_decision, select_model_effort)

def changed():
    return {"action": "write_file", "outcome": {
        "before_sha256": "0" * 64, "sha256": "1" * 64}}

def failed(check="python-unittest", digest="a" * 64):
    return {"action": "run_check", "outcome": {
        "check": check, "workspace_digest": digest, "source": "runtime",
        "simulated": False, "passed": False, "tests_run": 3, "exit_code": 1,
        "cleanup_ok": True, "timed_out": False, "workspace_unchanged": True}}

class MaestroRoutingTests(unittest.TestCase):
    def setUp(self):
        self.state = State(":memory:")
        self.addCleanup(self.state.close)
        self.target = self.state.enqueue(
            "task-a", "backend-engineer", "project", "Fix an intermittent backend error",
            payload={"branch": "fix/real", "allowed_paths": ["server/*"]})

    def test_repeated_same_check_does_not_escalate(self):
        history = [changed(), failed(), failed()]
        self.assertEqual(failed_checks(history), 1)
        self.assertFalse(escalation_justified(self.target, history))
        self.assertIsNone(create_reasoning_review(self.state, self.target, history))

    def test_not_before_real_correction_or_runtime_evidence(self):
        self.assertEqual(failed_checks([failed(), failed("node-test")]), 0)
        self.assertEqual(failed_checks([{"action": "write_file", "outcome": {
            "before_sha256": "0" * 64, "sha256": "0" * 64}},
            failed(), failed("node-test")]), 0)
        forged = failed("node-test")
        forged["outcome"]["source"] = "model"
        self.assertEqual(failed_checks([changed(), failed(), forged]), 1)
        partial = failed("node-test")
        partial["outcome"]["cleanup_ok"] = False
        self.assertEqual(failed_checks([changed(), failed(), partial]), 1)

    def test_after_code_correction_distinct_real_checks_request_maestro(self):
        history = [changed(), failed(), failed("node-test")]
        self.assertEqual(failed_checks(history), 2)
        review_id = create_reasoning_review(self.state, self.target, history)
        self.assertEqual(review_id, "task-a:reasoning-review")
        reviewer = self.state.get(review_id)
        self.assertEqual(reviewer["agent"], "may-maestro")
        self.assertEqual(reviewer["payload"]["allowed_paths"], [])
        self.assertEqual(create_reasoning_review(self.state, self.target, history), review_id)
        self.assertRaises(ValueError, save_maestro_decision, self.state, reviewer,
                          "reasoning-decision:task-a",
                          {"approve": True, "reason": "Two valid failures after changed code"})
        self.state.db.execute("UPDATE tasks SET checkpoint=? WHERE task_id=?",
                              (json.dumps({"history": history}), "task-a"))
        self.assertTrue(save_maestro_decision(self.state, reviewer,
                         "reasoning-decision:task-a",
                         {"approve": True, "reason": "Two separate real QA checks failed"}))
        self.assertEqual(select_model_effort(self.state, self.target, history)["level"], "high")
        self.assertEqual(select_model_effort(self.state, dict(self.target,
                         objective="Different task"), history)["level"], "low")

    def test_denial_is_binding_and_cannot_be_forged(self):
        history = [changed(), failed(), failed("node-test")]
        self.state.db.execute("UPDATE tasks SET checkpoint=? WHERE task_id=?",
                              (json.dumps({"history": history}), "task-a"))
        review_id = create_reasoning_review(self.state, self.target, history)
        reviewer = self.state.get(review_id)
        self.assertFalse(save_maestro_decision(self.state, self.target,
                         "reasoning-decision:task-a",
                         {"approve": True, "reason": "Try high anyway due to complexity"}))
        self.assertTrue(save_maestro_decision(self.state, reviewer,
                         "reasoning-decision:task-a",
                         {"approve": False, "reason": "Continue deterministic diagnosis first"}))
        self.assertEqual(select_model_effort(self.state, self.target, history)["level"], "low")
        forged = dict(self.target, payload={"approved_by_maestro": True})
        self.assertEqual(select_model_effort(self.state, forged, history)["level"], "low")

if __name__ == "__main__":
    unittest.main()
