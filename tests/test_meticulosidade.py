"""Prove the single Meticulosidade skill reaches every registered specialist."""
from pathlib import Path
import unittest

from horizon_core.registry import AGENTS_ROOT, AGENT_IDS, load_agent


SKILL = AGENTS_ROOT / "skills" / "meticulosidade" / "SKILL.md"
MARKER = "# Meticulosidade | Habilidade transversal dos 17 especialistas"


class MeticulosidadeTests(unittest.TestCase):
    def test_skill_has_specific_verifiable_scope(self):
        source = SKILL.read_text(encoding="utf-8")
        for item in ("name: meticulosidade", MARKER, "observar → reproduzir",
                     "arrasto", "Vigia/Ronda", "Vistoriador", "ícones",
                     "viewBox", "gradiente vertical", "Lanterna",
                     "prefers-reduced-motion", "1440×960", "PR #78",
                     "não considerar APK aprovado"):
            with self.subTest(rule=item):
                self.assertIn(item, source)

    def test_17_specialists_receive_exactly_one_canonical_skill(self):
        self.assertEqual(len(AGENT_IDS), 17)
        for specialist in AGENT_IDS:
            with self.subTest(agent=specialist):
                agent = load_agent(specialist)
                self.assertEqual(agent["id"], specialist)
                instructions = agent["instructions"]
                self.assertEqual(instructions.count(MARKER), 1)
                self.assertIn("Meticulosidade", instructions)
                self.assertIn("Ronda Pro", instructions)
                self.assertIn("Painel Michelle", instructions)

    def test_canonical_skill_is_required_not_silently_optional(self):
        # Check runtime entrypoint so an accidental future optional guard or
        # deletion cannot make 17 agents quietly lose the quality contract.
        registry = (Path(__file__).resolve().parents[1] /
                    "horizon_core" / "registry.py").read_text(encoding="utf-8")
        self.assertIn('meticulous.read_text(encoding="utf-8")', registry)
        self.assertIn('"skills" / "meticulosidade" / "SKILL.md"', registry)


if __name__ == "__main__":
    unittest.main()
