"""Production contract: all agents use approved Groq models, no ChatGPT transport.

Offline test; no API secrets, inference, browser preview, or network requests.
"""
import unittest
from horizon_core.registry import AGENT_IDS, load_agent
from horizon_core.gateway import (
    GroqGateway, GROQ_UI_AGENTS, DEFAULT_GROQ_UI_MODEL, DEFAULT_GROQ_CODE_MODEL,
)


class HorizonGroqContract(unittest.TestCase):
    def test_all_specialists_have_only_the_approved_free_groq_models(self):
        gateway = GroqGateway({})
        self.assertEqual(DEFAULT_GROQ_UI_MODEL, "qwen/qwen3.8-27b")
        self.assertEqual(DEFAULT_GROQ_CODE_MODEL, "openai/gpt-oss-120b")
        self.assertEqual(len(AGENT_IDS), 17)
        for agent_id in AGENT_IDS:
            with self.subTest(agent=agent_id):
                agent = load_agent(agent_id)
                self.assertNotIn("consult_may", agent["tools"])
                expected = (DEFAULT_GROQ_UI_MODEL if agent_id in GROQ_UI_AGENTS
                            else DEFAULT_GROQ_CODE_MODEL)
                self.assertEqual(gateway.model_for(agent), expected)


if __name__ == "__main__":
    unittest.main()
