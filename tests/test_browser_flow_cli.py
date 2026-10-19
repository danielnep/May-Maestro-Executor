"""Browser journeys are executed in isolated Chromium, not simulated DOM."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from horizon_core.tools import Tools
from horizon_core.web_checks import run_web_check

class BrowserJourneyTests(unittest.TestCase):
    def test_real_browser_journey_and_evidence(self):
        if not shutil.which("docker"):
            self.skipTest("Docker not available")
        if subprocess.run(["docker","image","inspect","horizon-web-proof:1.58.2"],
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
            self.skipTest("Browser image not built yet")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"app";root.mkdir()
            (root/"index.html").write_text('<!doctype html><html lang="pt-BR"><title>Teste</title><body><h1>Teste visual real</h1><button id="open" onclick="document.querySelector(\'#msg\').showModal()">Mensagens</button><dialog id="msg"><p>Conversa aberta e visível.</p><button onclick="document.querySelector(\'#msg\').close()">Fechar mensagens</button></dialog></body>',encoding="utf-8")
            flows=[
                {"name":"chat-mobile","viewport":"mobile","path":"/","ready":{"role":"button","name":"Mensagens"},"steps":[
                    {"action":"click","target":{"role":"button","name":"Mensagens"}},
                    {"action":"expect_visible","target":{"css":"dialog[open]"},"capture":True},
                    {"action":"click","target":{"role":"button","name":"Fechar mensagens"}},
                    {"action":"expect_hidden","target":{"css":"dialog[open]"}}]},
                {"name":"chat-desktop","viewport":"desktop","path":"/","ready":{"role":"button","name":"Mensagens"},"steps":[
                    {"action":"click","target":{"role":"button","name":"Mensagens"}},
                    {"action":"expect_text","text":"Conversa aberta e visível."}]}
            ]
            (root/"horizon.browser.json").write_text(json.dumps({"profile":"interactive-flows-v1","flows":flows}))
            result=run_web_check(root,Tools(root,{"tools":["run_check"]}),"browser-playwright",evidence_dir=Path(temp)/"evidence")
            self.assertTrue(result["passed"],str(result.get("reason") or result.get("report",{}).get("failures")))
            files={row["file"] for row in result["screenshots"]}
            self.assertTrue({"mobile-rendered.png","desktop-rendered.png","mobile-chat-mobile-2.png"}.issubset(files))
            self.assertEqual(len(result["report"]["flows"]),2)
            self.assertEqual(result["report"]["failures"],[])
if __name__=="__main__":unittest.main()
