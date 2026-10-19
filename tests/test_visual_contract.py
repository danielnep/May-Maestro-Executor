"""A UI review requires measured mobile and desktop DOM, not Gemini image calls."""
import unittest
from horizon_core.registry import load_agent
from horizon_core.visual_contract import requires_visual_review, visual_review_status

def proof(*, mobile=True, desktop=True, passed=True, geometry=True, clicks=4, blocked=False):
    screenshots=[{"file":name+"-rendered.png","sha256":"a"*64}
        for name,available in (("mobile",mobile),("desktop",desktop)) if available]
    return {"action":"run_check","outcome":{
        "check":"browser-playwright","passed":passed,"source":"runtime",
        "simulated":False,"workspace_unchanged":True,"screenshots":screenshots,
        "report":{"functional_scenarios":clicks,
          "layout":[{"name":"mobile","blocking_issues":[{"kind":"covered_control"}] if blocked else []},
                    {"name":"desktop","blocking_issues":[]}] if geometry else []}
    }}

class VisualReviewTests(unittest.TestCase):
    def test_four_ui_roles_have_tools_and_require_review(self):
        for ident in ("frontend-engineer","ui-ux-designer","horizon-ui-guardian","mobile-app-builder"):
            with self.subTest(agent=ident):
                self.assertTrue(requires_visual_review({"agent":ident}))
                self.assertIn("run_check",load_agent(ident)["tools"])
        self.assertFalse(requires_visual_review({"agent":"backend-engineer"}))
        self.assertFalse(requires_visual_review({"agent":"ui-ux-designer",
                                                  "payload":{"visual_review_required":False}}))
        with self.assertRaises(ValueError):
            requires_visual_review({"agent":"frontend-engineer",
                                    "payload":{"visual_review_required":"no"}})

    def test_multiple_real_flow_screenshots_do_not_replace_required_final_views(self):
        history = [proof()]
        original = history[0]["outcome"]
        original["screenshots"].extend([
            {"file": "mobile-stage-1-chat-open.png", "sha256": "b" * 64},
            {"file": "mobile-stage-7-inspection.png", "sha256": "c" * 64},
            {"file": "desktop-stage-3-events.png", "sha256": "d" * 64},
        ])
        self.assertTrue(visual_review_status(history)["verified"])
        original["screenshots"] = [s for s in original["screenshots"]
                                   if s["file"] != "desktop-rendered.png"]
        self.assertFalse(visual_review_status(history)["verified"])

    def test_both_viewports_required_after_last_edit(self):
        edit={"action":"write_file"}
        self.assertTrue(visual_review_status([edit,proof()])["verified"])
        self.assertFalse(visual_review_status([proof(),edit])["verified"])
        for bad in (proof(mobile=False),proof(desktop=False),proof(passed=False),
                    proof(geometry=False),proof(clicks=0),proof(blocked=True)):
            self.assertFalse(visual_review_status([edit,bad])["verified"])
        self.assertEqual(visual_review_status([edit])["reason"],"browser_check_missing_after_edit")


if __name__=="__main__":
    unittest.main()
