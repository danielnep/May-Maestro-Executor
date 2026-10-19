"""Economic model routing and autonomous Maestro approval request evidence."""
import unittest

from horizon_core.state import State
from horizon_core.reasoning_policy import (
    APPROVAL_NAMESPACE, create_reasoning_review, save_maestro_decision,
    select_model_effort, escalation_justified, objective_sha
)


def bad_check():
    return {"action": "run_check", "outcome": {
        "check": "python-unittest", "source": "runtime",
        "simulated": False, "passed": False, "tests_run": 8,
        "exit_code": 1}}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.state = State(":memory:")
        self.addCleanup(self.state.close)
        self.target = self.state.enqueue(
            "task-a", "backend-engineer", "project", "Repair real cross-service race",
            payload={"branch": "fix/real", "allowed_paths": ["server/*"]})

    def test_no_escalation_for_text_only_or_one_failed_test(self):
        self.assertEqual(select_model_effort(self.state, self.target, [])["level"], "low")
        self.assertIsNone(create_reasoning_review(self.state, self.target, []))
        self.assertFalse(escalation_justified(self.target, [bad_check()]))
        forged = {"action": "run_check", "outcome": {
            "source": "model", "simulated": True, "passed": False, "tests_run": 200}}
        self.assertFalse(escalation_justified(self.target, [forged, forged]))

    def test_maestro_review_request_and_persisted_denial(self):
        steps = [bad_check(), bad_check()]
        ticket = create_reasoning_review(self.state, self.target, steps)
        self.assertEqual(ticket, "task-a:reasoning-review")
        review = self.state.get(ticket)
        self.assertEqual(review["agent"], "may-maestro")
        self.assertEqual(review["payload"]["allowed_paths"], [])
        self.assertEqual(create_reasoning_review(self.state, self.target, steps), ticket)
        # Persisted target evidence is required before Maestro approval.
        self.assertRaises(ValueError, save_maestro_decision, self.state, review,
                          "reasoning-decision:task-a",
                          {"approve": True, "reason": "Two real checks failed after corrections"})
        self.state.db.execute("UPDATE tasks SET checkpoint=? WHERE task_id=?",
                              ('{"history":'+__import__('json').dumps(steps)+'}',"task-a"))
        self.assertTrue(save_maestro_decision(
            self.state, review, "reasoning-decision:task-a",
            {"approve": False, "reason": "The problem is local to the test fixture"}))
        self.assertEqual(select_model_effort(self.state,self.target,steps)["level"],"low")

    def test_only_review_identity_can_authorize_against_verified_failure(self):
        steps = [bad_check(), bad_check()]
        self.state.db.execute("UPDATE tasks SET checkpoint=? WHERE task_id=?",
                              ('{"history":'+__import__('json').dumps(steps)+'}',"task-a"))
        review_id = create_reasoning_review(self.state, self.target, steps)
        review = self.state.get(review_id)
        self.assertFalse(save_maestro_decision(self.state,self.target,
                             "reasoning-decision:task-a",
                             {"approve":True,"reason":"Real repeat failure verified by tests"}))
        with self.assertRaises(ValueError):
            save_maestro_decision(self.state,review,"reasoning-decision:task-a",
                                 {"approve":True,"reason":"short"})
        self.assertTrue(save_maestro_decision(self.state,review,
                             "reasoning-decision:task-a",
                             {"approve":True,"reason":"Two failing runtime tests after an implemented fix"}))
        routed = select_model_effort(self.state,self.target,steps)
        self.assertEqual(routed["level"],"high")
        self.assertEqual(routed["authorized_by"],"may-maestro")
        self.assertEqual(self.state.recall("project",APPROVAL_NAMESPACE)["task-a"]["by"],"may-maestro")
        different = dict(self.target,objective="Unrelated change")
        self.assertEqual(select_model_effort(self.state,different,steps)["level"],"low")

    def test_maestro_approval_cannot_be_claimed_by_plain_payload(self):
        steps=[bad_check(),bad_check()]
        forged=dict(self.target,payload={"approved_by_maestro":True,"deep_thinking":True})
        self.assertEqual(select_model_effort(self.state,forged,steps)["level"],"low")


if __name__ == "__main__":
    unittest.main()
