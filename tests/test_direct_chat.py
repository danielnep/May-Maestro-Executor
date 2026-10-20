"""Direct KERNEL browser consultation tests, no network or paid key."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from horizon_core import direct_chat
from horizon_core.chat_direct_cli import main as chat_cli_main
from horizon_core.may_consultation import (
    _direct_answer, read_direct_consultation, request_consultation, ticket)
from horizon_core.registry import AGENT_IDS
from horizon_core.state import State


class _Response:
    def __init__(self, body):
        self.stream = io.BytesIO(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self.stream

    def __exit__(self, *args):
        self.stream.close()


class DirectBrowserContractTests(unittest.TestCase):
    def test_no_key_does_not_open_browser_or_contact_github(self):
        result = direct_chat.execute("horizon-skills", "a"*20,
                                     "What should I inspect first?",
                                     environ={}, opener=lambda *_a, **_k:
                                     self.fail("No call authorized"))
        self.assertEqual(result["status"], "not_configured")

    def test_seventeen_specialists_and_sanitized_json_encoding(self):
        for agent in AGENT_IDS:
            text = direct_chat._browser_code(
                agent, "abc123"*3+"ab", "How to inspect a public README?",
                "specialist_question", "send")
            self.assertIn("AGENTE:", text)
            self.assertIn(agent, text)
            self.assertIn("chatgpt.com/c/6ad68dab", text)
        with self.assertRaises(ValueError):
            direct_chat._browser_code("unknown-agent", "a"*20, "Valid question text?",
                                      "specialist_question", "send")
        with self.assertRaises(ValueError):
            direct_chat._browser_code("qa-engineer", "broken-id", "Valid question text?",
                                      "specialist_question", "send")
        payload = direct_chat._browser_code("qa-engineer", "a"*20,
                  'Why this code? "; process.exit(1); //',
                  "specialist_question", "send")
        self.assertNotIn('"; process.exit(1); //";', payload)
        self.assertIn("JSON.parse(", payload)

    def test_authenticated_transport_is_one_fixed_kernel_endpoint(self):
        seen = []
        def opener(request, timeout=None):
            seen.append((request, timeout))
            return _Response({"success": True, "result": {
                "status": "answered", "answer": '{"action":"read_file",'
                '"args":{"path":"README.md"},"summary":"Inspect first"}'}})
        outcome = direct_chat.execute("qa-engineer", "a"*20,
                     "Which public file should QA inspect first?",
                     environ={"KERNEL_API_KEY": "synthetic-unit-key"}, opener=opener)
        self.assertEqual(outcome["status"], "answered")
        self.assertEqual(len(seen), 1)
        req, timeout = seen[0]
        self.assertEqual(req.full_url, direct_chat.ENDPOINT)
        self.assertEqual(req.get_method(), "POST")
        self.assertIn("horizon-central", req.data.decode())
        self.assertIn("a"*20, req.data.decode())
        self.assertIn("Bearer synthetic-unit-key", req.get_header("Authorization"))
        self.assertLessEqual(timeout, 65)
        self.assertNotIn("synthetic-unit-key", req.data.decode())

    def test_ambiguous_network_failures_do_not_resend(self):
        from urllib.error import URLError
        calls = []
        def opener(request, timeout=None):
            calls.append(True)
            raise URLError("provider unavailable")
        result = direct_chat.execute("qa-engineer", "b"*20,
                                     "What is the safest check to run?",
                                     environ={"KERNEL_API_KEY":"synthetic"},
                                     opener=opener)
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(len(calls), 1)

    def test_direct_reply_is_untrusted_not_an_authorization(self):
        action = '{"action":"read_file","args":{"path":"README.md"},"summary":"Check it"}'
        parsed = _direct_answer(action)
        self.assertEqual(parsed["source"], "chatgpt_web_untrusted")
        self.assertEqual(parsed["decision_untrusted"]["action"], "read_file")
        self.assertEqual(_direct_answer("Uncertain; inspect the public README.")["advice_untrusted"],
                         "Uncertain; inspect the public README.")
        got = read_direct_consultation("abc-task", agent="qa-engineer",
            transport=lambda agent, key, **kw:
                {"status": "answered", "ticket": key, "answer": action})
        self.assertEqual(got["decision_untrusted"]["args"]["path"], "README.md")
        self.assertEqual(got["ticket"], ticket("abc-task", "qa-engineer"))

    def test_direct_cooldown_across_tasks_and_after_one_hour(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        now = datetime.now(timezone.utc)
        requested = []
        def fake_send(agent, key, **kwargs):
            requested.append((agent, key))
            return {"status": "sent", "ticket": key, "agent": agent}
        for i in range(4):
            task = {"task_id": "qa-"+str(i), "agent": "qa-engineer",
                    "payload": {}, "objective": "private data"}
            result = request_consultation(
                task, "providers_unavailable", state=state,
                now=now, direct_transport=fake_send)
            self.assertEqual(result["status"], "direct_pending")
            self.assertEqual(result["transport"], "kernel")
        self.assertEqual(len(requested), 4)
        fifth = {"task_id":"qa-later","agent":"qa-engineer","payload":{}}
        wait = request_consultation(fifth, "providers_unavailable",
                                    state=state, now=now, direct_transport=fake_send)
        self.assertEqual(wait["status"], "cooldown")
        self.assertEqual(len(requested), 4)
        other = dict(fifth, agent="backend-engineer")
        self.assertEqual(request_consultation(
            other, "providers_unavailable", state=state, now=now,
            direct_transport=fake_send)["status"], "direct_pending")
        self.assertEqual(request_consultation(
            fifth, "providers_unavailable", state=state,
            now=now+timedelta(seconds=3601),
            direct_transport=fake_send)["status"], "direct_pending")

    def test_independent_agent_cli_checkin_without_worker(self):
        calls = []
        def fake(agent, key, **kwargs):
            calls.append((agent, key, kwargs))
            return {"status": "answered", "answer": "Check-in received."}
        out = io.StringIO()
        with redirect_stdout(out):
            code = chat_cli_main(
                ["--agent", "horizon-skills", "--task-id", "synthetic-proof",
                 "--checkin", "--local-browser"], transport=fake)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], "horizon-skills")
        self.assertEqual(calls[0][2]["reason"], "checkin")
        self.assertEqual(calls[0][2]["mode"], "send")
        self.assertNotIn("Check-in received.", out.getvalue())
        self.assertIn("answered", out.getvalue())

    def test_independent_cli_disallows_unapproved_question(self):
        def should_not_send(*a, **kw):
            self.fail("Unapproved content must not be transmitted")
        with self.assertRaises(SystemExit):
            chat_cli_main(
                ["--agent", "qa-engineer", "--task-id", "synthetic-proof",
                 "--question", "Which private client document should I read?"],
                transport=should_not_send)

    def test_standalone_read_mode_does_not_send_again(self):
        calls = []
        def fake(agent, key, **kwargs):
            calls.append(kwargs)
            return {"status": "sent", "ticket": key}
        with redirect_stdout(io.StringIO()):
            code = chat_cli_main(
                ["--agent", "horizon-skills", "--task-id", "synthetic-proof",
                 "--read"], transport=fake)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0]["mode"], "read")
        self.assertEqual(calls[0]["question"], "")

    def test_no_secrets_or_privacy_claim_from_model(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        task = {"task_id":"secret-task","agent":"qa-engineer",
                "payload":{}, "objective":"secret app content"}
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(request_consultation(
                task, "specialist_question", state=state,
                question="Please inspect our secret client records?",
                direct_transport=lambda *a, **kw: self.fail("must reject"))["status"],
                "private_context_blocked")
        self.assertEqual(state.recall("__runtime__", "qa-engineer"), {})


if __name__ == "__main__":
    unittest.main()
