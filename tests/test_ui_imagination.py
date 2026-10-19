"""Preflight produces two precise scenarios without images or model calls."""
import tempfile
from pathlib import Path
import unittest
from horizon_core.ui_imagination import imagine_interface, UI_AGENTS


class ImaginationTests(unittest.TestCase):
    def test_every_principal_ui_role_receives_mobile_desktop_prediction(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"assets").mkdir()
            (root/"assets"/"style.css").write_text("@media (max-width: 600px){.chat{width:100%}}",encoding="utf8")
            for role in UI_AGENTS:
                with self.subTest(agent=role):
                    report=imagine_interface(root,"Corrigir seta das notificações e botão de mensagens",agent=role)
                    self.assertEqual([(r["name"],r["width"],r["height"]) for r in report["viewports"]],
                                     [("mobile",390,844),("desktop",1440,960)])
                    self.assertIn("notifications",report["viewports"][0]["focus"])
                    self.assertIn("message-panel",report["viewports"][1]["focus"])
                    self.assertEqual(report["vision_api_calls"],0)
                    self.assertTrue(report["requires_real_browser_validation"])
                    self.assertEqual(report["style_sources"][0]["breakpoints"],[600])

    def test_backend_does_not_need_layout_predictions(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(imagine_interface(td,"SQL",agent="database-engineer"))


if __name__=="__main__":
    unittest.main()
