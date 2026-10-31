import unittest
from horizon_core.registry import load_agent, AGENT_IDS

class VisualFiscalContract(unittest.TestCase):
    def test_ui_guardian_is_same_agent(self):
        self.assertEqual(len(AGENT_IDS),17)
        self.assertNotIn('ui-fiscal',AGENT_IDS)
        guardian=load_agent('horizon-ui-guardian')
        self.assertIn('UI Guardian | Fiscal visual especializado',guardian['instructions'])
        self.assertIn('ANTES e DEPOIS',guardian['instructions'])
        self.assertIn('Visual Fiscal dedicated reference:',guardian['_groq_instructions'])
    def test_other_agents_are_not_visual_fiscal(self):
        for name in ('backend-engineer','may-maestro','security-engineer'):
            self.assertNotIn('UI Guardian | Fiscal visual especializado',load_agent(name)['instructions'])
