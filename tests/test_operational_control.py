import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from horizon_core.state import State
from horizon_core.operations import begin,end
from horizon_core.recovery import escalate,gate_original_on_children
from horizon_core.task_receipts import detail,describe,evidence
from horizon_core.task_workspace import prepare


class OperationalControl(unittest.TestCase):
    def setUp(self):
        self.state=State(':memory:')
        self.state.enqueue('original','frontend-engineer','demo','Repair the interface',payload={'allowed_paths':['index.html']})
        self.task=self.state.claim('worker')
    def tearDown(self):self.state.close()
    def test_escalation_claims_maestro_and_gates_original(self):
        out=escalate(self.state,self.task,'worker',{},'check_failure','browser test failed')
        self.assertEqual(out['status'],'queued')
        review=self.state.claim('reviewer')
        self.assertEqual(review['agent'],'may-maestro')
        self.assertEqual(review['payload']['recovery_for'],'original')
        self.assertIsNone(self.state.claim('other'))
        self.state.complete(review['task_id'],'reviewer',{'summary':'fixed'})
        self.assertEqual(self.state.claim('resumed')['task_id'],'original')
    def test_delegated_repair_must_finish_before_original(self):
        out=escalate(self.state,self.task,'worker',{},'check_failure')
        review=self.state.claim('reviewer')
        self.state.enqueue('repair','devops-engineer','demo','Repair browser environment',depends_on=[review['task_id']])
        gate_original_on_children(self.state,review,['repair'])
        self.state.complete(review['task_id'],'reviewer',{'delegated':['repair']})
        child=self.state.claim('repairer');self.assertEqual(child['task_id'],'repair')
        self.assertIsNone(self.state.claim('not-ready'))
        self.state.complete('repair','repairer',{})
        self.assertEqual(self.state.claim('resumed')['task_id'],'original')
    def test_provider_limit_wait_exposes_model_and_next_retry_without_secret(self):
        task=self.task
        self.state.checkpoint(task["task_id"],"worker",{
            "defer_reason":"provider_retry","defer_seconds":300,
            "history":[{"action":"provider_retry","outcome":{
                "failures":[{"provider":"gemini","reason":"quota","status":429,
                             "model":"gemini-3.6-flash","key_slot":"gemini-key-2",
                             "retry_after_seconds":85},
                            {"provider":"groq","reason":"rate_limited_local",
                             "model":"openai/gpt-oss-120b"}]}}]})
        self.state.defer(task["task_id"],"worker",seconds=300)
        result=describe(self.state.get("original"),{"original":self.state.get("original")})
        self.assertEqual(result["waitReason"],"provider_retry")
        self.assertEqual(result["providerWait"][0]["keySlot"],"gemini-key-2")
        self.assertEqual(result["providerWait"][0]["status"],429)
        self.assertEqual(result["providerWait"][0]["providerRetryAfterSeconds"],85)
        self.assertEqual(result["providerWait"][1]["model"],"openai/gpt-oss-120b")
        self.assertTrue(result["nextRetryAt"])
        self.assertNotIn("GEMINI_API_KEY_2",json.dumps(result))

        n=begin(self.state,self.task,'run_check',{'name':'browser-playwright'})
        end(self.state,'original',n,{'passed':False,'output':'permission denied'},failed=True)
        output=detail(self.state.db,'original')
        self.assertEqual(output['commands'][0]['number'],1)
        self.assertEqual(output['commands'][0]['phase'],'failed')
        self.assertEqual(output['commands'][0]['outcome']['output'],'permission denied')
        self.assertEqual(output['evidence'],[])
    def test_logs_omit_file_bodies_and_secrets(self):
        n=begin(self.state,self.task,'write_file',{'path':'index.html','content':'private content','api_key':'private key'})
        end(self.state,'original',n,{'output':'Bearer ABCDEFGHIJKL'})
        raw=json.dumps(detail(self.state.db,'original'))
        self.assertNotIn('private content',raw);self.assertNotIn('private key',raw);self.assertNotIn('ABCDEFGHIJKL',raw)
    def test_dependency_and_defer_are_not_running(self):
        out=escalate(self.state,self.task,'worker',{},'check_failure')
        all_tasks={t['task_id']:t for t in self.state.tasks()}
        projected=describe(out,all_tasks)
        self.assertEqual(projected['executionState'],'waiting_dependency')
        self.assertEqual(projected['blockedBy'][0]['id'],out['checkpoint']['recovery_task'])
        self.assertIsNone(projected['currentCommand'])
    def test_invalid_repository_never_uses_controller(self):
        self.task['payload']['repository']='other/repository'
        with self.assertRaisesRegex(ValueError,'repository_invalid'):prepare(self.task,'/controller')
    def test_evidence_outside_owned_root_is_rejected(self):
        self.state.checkpoint('original','worker',{'checks':[{'screenshots':[{'path':'/etc/passwd','sha256':'anything'}]}]})
        with self.assertRaisesRegex(ValueError,'fora_do_escopo'):evidence(self.state.db,'original',0)
    def test_supervisor_is_independently_loaded_with_recovery_tools(self):
        from horizon_core.registry import load_agent,AGENT_IDS
        self.assertEqual(len(AGENT_IDS),19)
        for aid in AGENT_IDS:
            agent=load_agent(aid);self.assertIn('escalate',agent['tools'])
        self.assertIn('delegate',load_agent('horizon-supervisor')['tools'])


if __name__=='__main__':unittest.main()
