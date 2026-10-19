"""Guard the user's curated frontend/UI research mapping to Horizon specialists."""
import unittest
from horizon_core.registry import load_agent

VISUAL_ROLES = (
    "frontend-engineer", "ui-ux-designer", "horizon-ui-guardian",
    "mobile-app-builder", "software-architect",
)

class FrontendResearchGuidanceTests(unittest.TestCase):
    def test_visual_specialists_load_same_current_study_and_own_role_rules(self):
        for agent_id in VISUAL_ROLES:
            with self.subTest(agent=agent_id):
                instructions = load_agent(agent_id)["instructions"]
                self.assertIn("## Pesquisa aprofundada: engenharia frontend e UI/UX",
                              instructions)
                self.assertIn("## Pesquisa aplicada", instructions)
                self.assertIn("Meticulosidade", instructions)
                self.assertIn("WCAG 2.2 AA", instructions)
                self.assertIn("não impor", instructions.lower())

    def test_qa_has_targeted_guidance_without_unrelated_visual_reference_overhead(self):
        instructions = load_agent("qa-engineer")["instructions"]
        self.assertIn("## Pesquisa aplicada a regressões de UI/frontend", instructions)
        self.assertNotIn("## Pesquisa aprofundada: engenharia frontend e UI/UX",
                         instructions)
        self.assertIn("Meticulosidade", instructions)

    def test_backend_does_not_load_frontend_research_automatically(self):
        instructions = load_agent("backend-engineer")["instructions"]
        self.assertNotIn("## Pesquisa aprofundada: engenharia frontend e UI/UX",
                         instructions)

if __name__ == "__main__":
    unittest.main()
