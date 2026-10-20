"""KERNEL approval must follow last source edit and contain owner evidence."""
import unittest
from horizon_core.registry import load_agent
from horizon_core.visual_contract import requires_visual_review, visual_review_status


class VisualReviewTests(unittest.TestCase):
    def test_four_visual_agents_cannot_skip_review_without_source_edits(self):
        for ident in ("frontend-engineer", "ui-ux-designer",
                      "horizon-ui-guardian", "mobile-app-builder"):
            with self.subTest(agent=ident):
                self.assertTrue(requires_visual_review({"agent": ident}))
                self.assertIn("run_check", load_agent(ident)["tools"])
        self.assertFalse(requires_visual_review({"agent": "backend-engineer"}))
        self.assertFalse(requires_visual_review({"agent": "ui-ux-designer",
            "payload": {"visual_review_required": False}}))
        with self.assertRaises(ValueError):
            requires_visual_review({"agent": "frontend-engineer",
                "payload": {"visual_review_required": "no"}})

    def test_owner_review_must_follow_last_edit(self):
        good = {"action": "kernel_review_verified", "outcome": {
            "source": "authenticated_owner_review", "passed": True,
            "verified_scenarios": 3}}
        self.assertTrue(visual_review_status([{"action":"write_file"},good])["verified"])
        self.assertFalse(visual_review_status([good,{"action":"write_file"}])["verified"])
        self.assertFalse(visual_review_status([{"action":"run_check",
            "outcome":{"check":"browser-playwright","passed":True}}])["verified"])
        self.assertFalse(visual_review_status([{"action":"kernel_live_requested"}])["verified"])

    def test_report_or_model_claim_cannot_forge_approval(self):
        for source, count, passed in (
            ("model", 3, True), ("authenticated_owner_review", 0, True),
            ("authenticated_owner_review", 3, False)):
            proof = {"action": "kernel_review_verified", "outcome": {
                "source": source, "passed": passed, "verified_scenarios": count}}
            self.assertFalse(visual_review_status([proof])["verified"])
        self.assertEqual(visual_review_status([])["reason"], "kernel_live_review_missing")


if __name__ == "__main__":
    unittest.main()
