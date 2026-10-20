"""Keep Gemini 3.6 Flash as Groq fallback, with Lite restricted to real quota failures."""
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
                    self.assertIn('error.reason == "quota"',gateway)
                    self.assertIn('error.status == 429',gateway)
                    self.assertIn('GEMINI_FREE_TIER_CONFIRMED',gateway)
                else:
                    self.assertNotIn("GEMINI_FREE_FALLBACK_MODEL: gemini-3.5-flash-lite",text)
    def test_free_tier_confirmation_requires_explicit_operator_attestation(self):
        # A confirmed owner choice is not interchangeable with a default.
        for path in WORKFLOWS:
            body = (ROOT / path).read_text()
            flags = [line.strip() for line in body.splitlines()
                     if line.strip().startswith("GEMINI_FREE_TIER_CONFIRMED:")]
            with self.subTest(path=path):
                self.assertTrue(flags)
                if path.endswith("chat-requests.yml"):
                    self.assertEqual(flags, ["GEMINI_FREE_TIER_CONFIRMED: 'true'"])
                    self.assertIn("Explicit owner attestation received 2026-10-19", body)
                    self.assertNotIn("GEMINI_FREE_TIER_CONFIRMED: ${{ vars.GEMINI_FREE_TIER_CONFIRMED || 'true' }}", body)
                else:
                    self.assertTrue(all("|| 'false'" in flag for flag in flags))

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
