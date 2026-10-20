"""Worker-level proof that UI tools are executed by algorithm, not model choice."""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from horizon_core.state import State
from horizon_core.tools import Tools
from horizon_core.worker import Worker

def proof(passed=True):
    return {"check":"browser-playwright","passed":passed,"source":"runtime",
            "simulated":False,"workspace_unchanged":True,"tests_run":7,
            "screenshots":[{"file":name+"-rendered.png","sha256":"a"*64}
                           for name in ("desktop","mobile")],
            "report":{"layout":[{"name":"desktop","blocking_issues":[]},
                                {"name":"mobile","blocking_issues":[]}],
                      "functional_scenarios":4}}

class Model:
    def __init__(self): self.contexts=[]
    def complete(self, agent, messages):
        self.contexts.append(json.loads(messages[0]["content"]))
        return {"action":"finish","args":{},"summary":"Validated interface"}

class RuntimeVisualTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        subprocess.run(["git","init",str(self.root)],check=True,capture_output=True)
        subprocess.run(["git","-C",str(self.root),"switch","-c","feat/ui-test"],check=True,capture_output=True)
        (self.root/"index.html").write_text("<html><body><button>Mensagens</button></body></html>")
        (self.root/"styles.css").write_text("@media (max-width:600px){button{width:100%}}")
        self.state=State(":memory:")
        self.addCleanup(self.state.close)

    def test_two_views_checked_automatically_without_vision_call(self):
        self.state.enqueue("ui1","ui-ux-designer","ronda-pro","Corrigir botão de mensagens")
        llm=Model()
        with patch.object(Tools,"run_check",return_value=proof()) as checker:
            result=Worker(self.state,llm,self.root).run_one()
        self.assertEqual(result["status"],"succeeded")
        checker.assert_called_once_with({"name":"browser-playwright"})
        self.assertEqual(len(llm.contexts),1)
        imagination=llm.contexts[0]["ui_imagination"]
        self.assertEqual([v["name"] for v in imagination["viewports"]],["mobile","desktop"])
        self.assertEqual(imagination["vision_api_calls"],0)
        self.assertEqual(len(result["result"]["verification"][0]["screenshots"]),2)

    def test_real_browser_failure_is_never_marked_passed(self):
        self.state.enqueue("ui2","horizon-ui-guardian","ronda-pro","Revisar navegação")
        llm=Model()
        with patch.object(Tools,"run_check",return_value=proof(False)):
            result=Worker(self.state,llm,self.root).run_one()
        self.assertEqual(result["status"],"queued")
        self.assertIsNone(result.get("result"))
        self.assertTrue(any(item.get("action") == "repair_cycle"
                            for item in result["checkpoint"]["history"]))
        self.assertGreaterEqual(len(llm.contexts),3)

if __name__=="__main__":
    unittest.main()
