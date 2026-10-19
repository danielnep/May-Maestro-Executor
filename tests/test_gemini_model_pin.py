"""Keep Gemini 3.6 Flash primary and restrict Lite to quota-only last resort."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
WORKFLOWS=(".github/workflows/chat-requests.yml",
           ".github/workflows/agent-executors.yml",
           ".github/workflows/tools-proof.yml")

class ModelPinTest(unittest.TestCase):
    def test_every_live_workflow_uses_requested_model(self):
        for path in WORKFLOWS:
            text=(ROOT/path).read_text()
            with self.subTest(path=path):
                selected=[s.strip() for s in text.splitlines() if s.strip().startswith("GEMINI_MODEL:")]
                self.assertTrue(selected)
                self.assertTrue(all(s=="GEMINI_MODEL: gemini-3.6-flash" for s in selected))
                if path.endswith("chat-requests.yml"):
                    self.assertIn("GEMINI_FREE_FALLBACK_MODEL: gemini-3.5-flash-lite",text)
                    gateway=(ROOT/"horizon_core/gateway.py").read_text()
                    self.assertIn('primary.get("reason") == "quota"',gateway)
                    self.assertIn('primary.get("status") == 429',gateway)
                    self.assertIn('GEMINI_FREE_TIER_CONFIRMED',gateway)
                else:
                    self.assertNotIn("GEMINI_FREE_FALLBACK_MODEL: gemini-3.5-flash-lite",text)
    def test_all_agents_use_runtime_model_no_hidden_override(self):
        import json
        root=ROOT/"agents"
        ids=__import__("horizon_core.registry",fromlist=["AGENT_IDS"]).AGENT_IDS
        for agent in ids:
            with self.subTest(agent=agent):
                config=json.loads((root/agent/"agent.json").read_text())
                self.assertIn(config.get("model"),(None,"gemini-3.6-flash"))
if __name__=="__main__":
    unittest.main()
