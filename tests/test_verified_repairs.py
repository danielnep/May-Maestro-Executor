"""Verified procedural learning: identical issue is replayed without model calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from horizon_core.state import State
from horizon_core.tools import Tools
from horizon_core.registry import load_agent
from horizon_core.worker import Worker
from horizon_core.verified_repairs import (
    learn_from_verified_check, replay_if_exact, signature, namespace,
)

QA = {"check":"python-unittest", "project_dir":".", "passed":True,
      "source":"runtime", "simulated":False, "workspace_unchanged":True,
      "tests_run":8, "exit_code":0, "workspace_digest":"good-qa-fixture"}

def sha(x): return hashlib.sha256(x.encode()).hexdigest()

def command(name, args=None):
    return {"action":name, "args":args or {}, "summary":"fixture action"}

class Decider:
    def __init__(self):
        self.calls=0
    def complete(self, agent, messages):
        self.calls+=1
        context=json.loads(messages[0]["content"])
        i=context["completed_steps"]
        if i==0: return command("write_file",{"path":"app/logic.py","content":"VALUE = 2\n"})
        if i==1: return command("run_check",{"name":"python-unittest"})
        return command("finish")

class FailIfInvoked:
    def __init__(self): self.calls=0
    def complete(self, agent, messages):
        self.calls+=1
        raise AssertionError("Repeated verified issue must not spend another AI call")

class VerifiedRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        subprocess.run(["git","init",str(self.root)],check=True,capture_output=True)
        subprocess.run(["git","-C",str(self.root),"switch","-c","fix/sample"],check=True,capture_output=True)
        (self.root/"app").mkdir()
        (self.root/"app"/"logic.py").write_text("VALUE = 1\n")
        self.state=State(":memory:")
        self.addCleanup(self.state.close)
        self.payload={"branch":"fix/sample","allowed_paths":["app/*.py"]}

    def task(self,id):
        self.state.enqueue(id,"frontend-engineer","project-a",
                           "Repair deterministic logic regression",
                           payload=self.payload)
        return self.state.get(id)

    def test_first_verified_fix_is_saved_and_second_exact_task_skips_model(self):
        self.task("original")
        gateway=Decider()
        with patch.object(Tools,"run_check",return_value=QA):
            first=Worker(self.state,gateway,self.root).run_one()
        self.assertEqual(first["status"],"succeeded")
        self.assertEqual(gateway.calls,3)
        cache=self.state.recall("project-a",namespace("frontend-engineer"))["recipes_v1"]
        self.assertEqual(len(cache),1)
        self.assertEqual(cache[0]["status"],"verified_by_isolated_check")
        self.assertEqual(cache[0]["evidence"]["tests_run"],8)
        self.assertEqual(cache[0]["before_sha256"],sha("VALUE = 1\n"))
        self.task("recurrent")
        no_ai=FailIfInvoked()
        with patch.object(Tools,"run_check",return_value=QA) as check:
            second=Worker(self.state,no_ai,self.root).run_one()
        self.assertEqual(second["status"],"succeeded")
        self.assertEqual(second["result"]["model"]["provider"],"none")
        self.assertEqual(second["result"]["model"]["mode"],"verified_repair_reuse")
        self.assertEqual(second["result"]["verification"][0]["tests_run"],8)
        self.assertEqual(no_ai.calls,0)
        check.assert_called_once()

    def test_unverified_or_simulated_check_cannot_be_learned(self):
        self.task("first")
        old="VALUE = 1\n"
        new="VALUE = 2\n"
        self.state.remember("project-a","__workspace__","app/logic.py",{
            "branch":"fix/sample","expected_sha256":sha(old),"content":new})
        history=[
            {"action":"write_file","outcome":{"path":"app/logic.py",
                "sha256":sha(new),"before_sha256":sha(old)}},
            {"action":"run_check","outcome":dict(QA,simulated=True)}]
        self.assertFalse(learn_from_verified_check(self.state,self.task("unverified"),history))
        self.assertFalse(self.state.recall("project-a",namespace("frontend-engineer")).get("recipes_v1"))
        history[-1]["outcome"]=dict(QA,simulated=False,tests_run=0)
        self.assertFalse(learn_from_verified_check(self.state,self.task("unverified"),history))

    def test_modified_source_mismatch_never_replays_stale_patch(self):
        self.task("stored")
        current="VALUE = 1\n"
        new="VALUE = 2\n"
        self.state.remember("project-a","__workspace__","app/logic.py",{
            "branch":"fix/sample","expected_sha256":sha(current),"content":new})
        recorded=[
          {"action":"write_file","outcome":{"path":"app/logic.py",
              "sha256":sha(new),"before_sha256":sha(current)}},
          {"action":"run_check","outcome":QA}]
        self.assertTrue(learn_from_verified_check(self.state,self.state.get("stored"),recorded))
        (self.root/"app"/"logic.py").write_text("VALUE = 99\n")
        tools=Tools(self.root,load_agent("frontend-engineer"),
                    self.payload["allowed_paths"],self.payload["branch"])
        self.assertIsNone(replay_if_exact(self.state,self.state.get("stored"),tools))
        self.assertEqual((self.root/"app"/"logic.py").read_text(),"VALUE = 99\n")

    def test_different_objective_and_agent_are_not_equivalent(self):
        self.task("learned")
        self.state.remember("project-a","__workspace__","app/logic.py",{
            "branch":"fix/sample","expected_sha256":sha("VALUE = 1\n"),"content":"VALUE = 2\n"})
        entries=[{"action":"write_file","outcome":{"path":"app/logic.py",
                "sha256":sha("VALUE = 2\n"),"before_sha256":sha("VALUE = 1\n")}},
                 {"action":"run_check","outcome":QA}]
        self.assertTrue(learn_from_verified_check(self.state,self.state.get("learned"),entries))
        tools=Tools(self.root,load_agent("frontend-engineer"),
                    self.payload["allowed_paths"],self.payload["branch"])
        mismatch=dict(self.state.get("learned"),objective="A different visual request")
        self.assertIsNone(replay_if_exact(self.state,mismatch,tools))
        mismatch_agent=dict(self.state.get("learned"),agent="backend-engineer")
        self.assertIsNone(replay_if_exact(self.state,mismatch_agent,tools))

    def test_failed_recheck_does_not_fake_delivery_and_returns_to_model(self):
        self.task("prior")
        self.state.remember("project-a","__workspace__","app/logic.py",{
            "branch":"fix/sample","expected_sha256":sha("VALUE = 1\n"),"content":"VALUE = 2\n"})
        entries=[{"action":"write_file","outcome":{"path":"app/logic.py",
                "sha256":sha("VALUE = 2\n"),"before_sha256":sha("VALUE = 1\n")}},
                 {"action":"run_check","outcome":QA}]
        self.assertTrue(learn_from_verified_check(self.state,self.state.get("prior"),entries))
        tools=Tools(self.root,load_agent("frontend-engineer"),
                    self.payload["allowed_paths"],self.payload["branch"])
        with patch.object(Tools,"run_check",return_value=dict(QA,passed=False,exit_code=1)):
            result=replay_if_exact(self.state,self.state.get("prior"),tools)
        self.assertIsNotNone(result)
        self.assertFalse(result["verified"])
        self.assertFalse(result["checks"][0]["passed"])

if __name__=="__main__":
    unittest.main()
