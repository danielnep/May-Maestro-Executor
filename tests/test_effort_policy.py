"""Deterministic Maestro authorization gate and exact provider request parameters."""
import io
import json
import unittest
import urllib.error

from horizon_core.effort_policy import decide, select_deepseek_mode, select_gemini_level
from horizon_core.gateway import DeepSeekGateway, GeminiGateway, ModelGateway
from horizon_core.registry import load_agent


class FakeResponse(io.BytesIO):
    pass


def verification(check, *, passed=False):
    return {"action": "run_check", "outcome": {"check": check, "passed": passed,
            "source": "runtime", "simulated": False, "workspace_unchanged": True}}


def corrected():
    return {"action": "write_file", "outcome": {"sha256": "a" * 64}}


def context(*, objective="Reparar falhas", failures=()):
    return {"objective": objective, "project": "synthetic", "history": [corrected()]
            + [verification(item) for item in failures]}


def gemini_envelope():
    action = {"action": "finish", "args": {}, "summary": "test"}
    return FakeResponse(json.dumps({"candidates": [{"finishReason": "STOP",
        "content": {"parts": [{"text": json.dumps(action)}]}}],
        "usageMetadata": {"totalTokenCount": 1}}).encode())


def deepseek_envelope():
    action = {"action": "finish", "args": {}, "summary": "test"}
    return FakeResponse(json.dumps({"choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps(action)}}], "usage": {"total_tokens": 1}}).encode())


class MaestroEffortTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GEMINI_API_KEY": "fake", "GEMINI_MODEL": "gemini-3.6-flash",
                    "GEMINI_FREE_TIER_CONFIRMED": "true", "DEEPSEEK_API_KEY": "fake"}

    def test_default_has_no_deep_thinking(self):
        decision = decide({"objective": "botao", "history": []})
        self.assertFalse(decision.approved)
        self.assertEqual(select_deepseek_mode(decision), {"thinking": {"type": "disabled"}})
        self.assertEqual(select_gemini_level(decision, "gemini-3.6-flash"), "low")
        self.assertEqual(select_gemini_level(decision, "gemini-3.5-flash-lite"), "minimal")

    def test_no_deep_reasoning_for_single_failure_or_imaginary_claim(self):
        one = decide(context(failures=["python-unittest"]))
        self.assertFalse(one.approved)
        claim = decide({"objective": "Extremely hard security challenge",
                        "history": [{"action": "run_check",
                        "outcome": {"check": "node-test", "passed": False,
                                    "source": "model", "simulated": True}}]})
        self.assertFalse(claim.approved)
        no_edit = decide({"objective": "Hard bug", "history": [
            verification("node-test"), verification("python-unittest")]})
        self.assertFalse(no_edit.approved)

    def test_two_independent_checks_approve_high(self):
        approval = decide(context(failures=["node-test","python-unittest"]))
        self.assertTrue(approval.approved)
        self.assertEqual(approval.effort, "high")
        self.assertEqual(approval.approver, "maestro-policy-v1")
        self.assertEqual(select_deepseek_mode(approval)["reasoning_effort"], "high")

    def test_repeated_same_check_only_escalates_after_source_changes(self):
        base={"objective":"Reparar retorno inesperado","history":[],"effort_history":[corrected()]}
        def observed(digest):
            outcome=verification("python-unittest")
            outcome["outcome"]["workspace_digest"]=digest*64
            return outcome
        no_progress=dict(base,effort_history=[corrected(),observed("a"),observed("a")])
        self.assertFalse(decide(no_progress).approved)
        progress=dict(base,effort_history=[corrected(),observed("a"),corrected(),observed("b")])
        self.assertTrue(decide(progress).approved)
        self.assertEqual(decide(progress).effort,"high")

    def test_three_checks_plus_risk_approve_max(self):
        approval = decide(context(objective="Corrigir deadlock de transacao e integridade",
                                  failures=["node-test","python-unittest","browser-playwright"]))
        self.assertTrue(approval.approved)
        self.assertEqual(approval.effort, "max")
        self.assertEqual(select_gemini_level(approval,"gemini-3.6-flash"),"high")

    def test_deepseek_standard_json_and_temperature(self):
        requests=[]
        def open_stub(req, timeout):
            requests.append(json.loads(req.data))
            return deepseek_envelope()
        d=DeepSeekGateway(self.env,open_stub)
        result=d.complete(load_agent("backend-engineer"),[{"role":"user","content":"{}"}])
        self.assertEqual(requests[-1]["model"],"deepseek-flash")
        self.assertEqual(requests[-1]["thinking"],{"type":"disabled"})
        self.assertEqual(requests[-1]["temperature"],0.2)
        self.assertNotIn("reasoning_effort",requests[-1])
        self.assertEqual(result["metadata"]["reasoning_effort"],"none")
        d.complete(load_agent("backend-engineer"),[{"role":"user","content":"{}"}],
                   effort={"thinking":{"type":"enabled"},"reasoning_effort":"max"})
        self.assertNotIn("temperature",requests[-1])
        self.assertEqual(requests[-1]["reasoning_effort"],"max")
        self.assertEqual(requests[-1]["model"],"deepseek-flash")

    def test_gemini_uses_low_by_default_and_high_if_maestro_approves(self):
        requests=[]
        def open_stub(req,timeout):
            requests.append(json.loads(req.data))
            return gemini_envelope()
        gw=ModelGateway(environ=self.env,opener=open_stub)
        specialist=load_agent("frontend-engineer")
        ordinary=gw.complete(specialist,[{"role":"user","content":json.dumps({"objective":"Botao","history":[]})}])
        self.assertEqual(requests[-1]["generationConfig"]["thinkingConfig"]["thinkingLevel"],"low")
        self.assertFalse(ordinary["metadata"]["maestro_effort_approval"]["approved"])
        approved=gw.complete(specialist,[{"role":"user","content":json.dumps(context(failures=["node-test","python-unittest"]))}])
        self.assertEqual(requests[-1]["generationConfig"]["thinkingConfig"]["thinkingLevel"],"high")
        self.assertTrue(approved["metadata"]["maestro_effort_approval"]["approved"])

    def test_retired_deepseek_names_do_not_enable_deep_thinking(self):
        for obsolete in ("deepseek-chat", "deepseek-reasoner"):
            requests=[]
            def opener(req,timeout):
                requests.append(json.loads(req.data))
                return deepseek_envelope()
            env=dict(self.env,DEEPSEEK_MODEL=obsolete)
            result=DeepSeekGateway(env,opener).complete(load_agent("backend-engineer"),
                [{"role":"user","content":"{}"}])
            self.assertEqual(requests[-1]["model"],"deepseek-flash")
            self.assertEqual(requests[-1]["thinking"],{"type":"disabled"})
            self.assertTrue(result["metadata"]["retired_alias_normalized"])

    def test_pro_requires_two_guards_not_just_agent_claim(self):
        c=context(objective="Corrigir deadlock de integridade e transacao",
                  failures=["node-test","python-unittest","browser-playwright"])
        for allow,expected in ((False,"deepseek-flash"),(True,"deepseek-v4-pro")):
            requests=[]
            def opener(req,timeout):
                requests.append(req)
                if "googleapis" in req.full_url:
                    raise urllib.error.HTTPError(req.full_url,429,"quota",{},io.BytesIO(b"secret"))
                return deepseek_envelope()
            env=dict(self.env)
            if allow:env["DEEPSEEK_PRO_ALLOWED"]="true"
            gw=ModelGateway(environ=env,opener=opener)
            result=gw.complete(load_agent("backend-engineer"),
                [{"role":"user","content":json.dumps(c)}])
            self.assertTrue(result["metadata"]["maestro_effort_approval"]["approved"])
            deep=json.loads(requests[-1].data)
            self.assertEqual(deep["model"],expected)
            self.assertEqual(deep["reasoning_effort"],"max")
            self.assertEqual(result["metadata"]["pro_explicitly_authorized"],allow)

    def test_gemini_quota_switches_to_cheap_deepseek_not_max(self):
        requests=[]
        def open_stub(req,timeout):
            requests.append(req)
            if "googleapis" in req.full_url:
                raise urllib.error.HTTPError(req.full_url,429,"quota",{},io.BytesIO(b"private"))
            return deepseek_envelope()
        gw=ModelGateway(environ=self.env,opener=open_stub)
        result=gw.complete(load_agent("backend-engineer"),
                           [{"role":"user","content":json.dumps({"objective":"Corrigir erro","history":[]})}])
        deep=json.loads(requests[-1].data)
        self.assertEqual(deep["thinking"],{"type":"disabled"})
        self.assertNotIn("reasoning_effort",deep)
        self.assertEqual(result["metadata"]["provider"],"deepseek")

if __name__=="__main__":
    unittest.main()
