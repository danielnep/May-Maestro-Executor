"""Regressions for GitHub Actions handoff after a controller error.

Pure standard-library tests: no provider, Railway, or GitHub calls.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HandoffWorkflowRegressionTests(unittest.TestCase):
    def test_failure_still_reaches_handoff_when_not_cancelled(self):
        workflow = (ROOT / ".github/workflows/chat-requests.yml").read_text(encoding="utf-8")
        worker_name = "      - name: Importar pedidos, retomar especialistas e registrar resultados"
        handoff_name = "      - name: Continuar imediatamente quando o checkpoint estiver pronto"
        incident_name = "      - name: Registrar incidente quando o controlador falhar"
        self.assertIn(worker_name, workflow)
        self.assertIn(handoff_name, workflow)
        self.assertIn(incident_name, workflow)
        self.assertLess(workflow.index(worker_name), workflow.index(handoff_name))
        self.assertLess(workflow.index(handoff_name), workflow.index(incident_name))
        handoff_step = workflow.split(handoff_name, 1)[1].split("\n      - name:", 1)[0]
        self.assertRegex(handoff_step, r"(?m)^        if: \$\{\{ !cancelled\(\) \}\}$")
        self.assertIn("run: python -m horizon_core.handoff --dispatch", handoff_step)
        self.assertNotIn("if: success()", handoff_step)

    def test_controller_workflow_has_no_duplicate_triggers_or_regressions(self):
        workflow = (ROOT / ".github/workflows/chat-requests.yml").read_text(encoding="utf-8")
        paths = workflow.split("    paths:", 1)[1].split("  workflow_dispatch:", 1)[0]
        watched = [line.strip() for line in paths.splitlines()
                   if line.strip().startswith("- '")]
        self.assertEqual(len(watched), len(set(watched)))
        tests = re.findall(r"(?m)^          python -m unittest discover -s tests -p (\S+) -v$", workflow)
        self.assertEqual(len(tests), len(set(tests)))

    def test_marker_written_before_returning_failure(self):
        controller = (ROOT / "horizon_core/commit_bridge.py").read_text(encoding="utf-8")
        self.assertLess(controller.index("write_marker(continuation)"),
                        controller.index("return 0", controller.index("write_marker(continuation)")))

    def test_handoff_requires_checkpoint_marker(self):
        source = (ROOT / "horizon_core/handoff.py").read_text(encoding="utf-8")
        self.assertIn('data.get("resume") is not True', source)
        self.assertIn("if not marker.is_file():", source)


if __name__ == "__main__":
    unittest.main()
