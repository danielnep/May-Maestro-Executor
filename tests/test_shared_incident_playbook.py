"""Every specialist receives the versioned, sanitized incident rules."""
import json
import unittest
from pathlib import Path
from horizon_core.registry import AGENT_IDS, AGENTS_ROOT, load_agent


class SharedIncidentPlaybookTests(unittest.TestCase):
    def test_playbook_is_well_formed(self):
        data = json.loads((AGENTS_ROOT / "knowledge" / "verified-error-playbook.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len({entry["id"] for entry in data["entries"]}), len(data["entries"]))
        self.assertTrue(all(entry.get("action") and entry.get("verification") for entry in data["entries"]))

    def test_all_specialists_receive_incident_rules(self):
        self.assertEqual(len(AGENT_IDS), 17)
        for agent_id in AGENT_IDS:
            with self.subTest(agent=agent_id):
                instructions = load_agent(agent_id)["instructions"]
                self.assertIn("gemini-429", instructions)
                self.assertIn("deepseek-402", instructions)
                self.assertIn("state-507", instructions)


if __name__ == "__main__":
    unittest.main()
