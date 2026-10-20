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

    def test_authenticated_transport_is_native_computer_controls_only(self):
        seen = []
        stages = [
            {"success": True, "result": {"status": "ready", "box": {"x": 115, "y": 152}}},
            {"success": True, "result": {"status": "draft_ready"}},
            {"success": True, "result": {"status": "answered",
                "answer": '{"action":"read_file","args":{"path":"README.md"},'
                          '"summary":"Inspect first"}'}}]
        def opener(request, timeout=None):
            seen.append((request, timeout))
            if request.full_url.endswith("/playwright/execute"):
                return _Response(stages.pop(0))
            return _Response({})
        outcome = direct_chat.execute(
            "qa-engineer", "a"*20, "Which public file should QA inspect first?",
            environ={"KERNEL_API_KEY": "synthetic-unit-key"}, opener=opener)
        self.assertEqual(outcome["status"], "answered")
        self.assertEqual([req.full_url.rsplit("/", 1)[-1] for req, _ in seen],
                         ["execute", "click_mouse", "type_text", "execute",
                          "press_key", "execute"])
        self.assertEqual(len(stages), 0)
        self.assertIn("horizon-central", seen[0][0].data.decode())
        self.assertIn("a"*20, seen[0][0].data.decode())
        for req, timeout in seen:
            self.assertEqual(req.get_method(), "POST")
            self.assertIn("Bearer synthetic-unit-key", req.get_header("Authorization"))
            self.assertLessEqual(timeout, 65)
            self.assertNotIn("synthetic-unit-key", req.data.decode())
        click = json.loads(seen[1][0].data)
        self.assertEqual(click, {"x": 115, "y": 152})
        typed = json.loads(seen[2][0].data)["text"]
        self.assertIn("TICKET: " + "a"*20, typed)
        self.assertEqual(json.loads(seen[4][0].data), {"keys": ["Return"]})
        for request, _ in (seen[0], seen[3], seen[5]):
            script = json.loads(request.data)["code"]
            self.assertNotIn(".fill(", script)
            self.assertNotIn(".click(", script)
            self.assertNotIn("fetch(", script)

    def test_existing_foreign_composer_draft_is_never_touched(self):
        calls = []
        def opener(req, timeout=None):
            calls.append(req.full_url)
            return _Response({"success": True, "result": {"status": "composer_busy"}})
        result = direct_chat.execute("qa-engineer", "b"*20,
            "Read the approved public README before proceeding?",
            environ={"KERNEL_API_KEY": "synthetic"}, opener=opener)
        self.assertEqual(result["status"], "composer_busy")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/playwright/execute"))

    def test_mismatched_draft_stops_without_enter_or_erasure(self):
        calls = []
        inspections = [
            {"status": "ready", "box": {"x": 20, "y": 40}},
            {"status": "draft_mismatch"}]
        def opener(req, timeout=None):
            calls.append(req.full_url)
            if req.full_url.endswith("/playwright/execute"):
                return _Response({"success": True, "result": inspections.pop(0)})
            return _Response({})
        result = direct_chat.execute("qa-engineer", "c"*20,
            "Which public check should the specialist inspect first?",
            environ={"KERNEL_API_KEY": "synthetic"}, opener=opener)
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(len(calls), 4)
        self.assertFalse(any(x.endswith("/press_key") for x in calls))

    def test_existing_ticket_only_reads_without_resending(self):
        calls = []
        def opener(req, timeout=None):
            calls.append(req.full_url)
            return _Response({"success": True, "result": {"status": "sent"}})
        result = direct_chat.execute("qa-engineer", "d"*20,
            "Which public file should QA inspect first?",
            environ={"KERNEL_API_KEY": "synthetic"}, opener=opener)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(calls), 1)

    def test_local_mode_does_not_use_playwright_to_submit(self):
        calls = []
        class Completed:
            returncode = 0
            stdout = '{"status":"sent"}'
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return Completed()
        got = direct_chat.execute_local("horizon-skills", "e"*20,
            "Which public README should be inspected first?", runner=runner)
        self.assertEqual(got["status"], "sent")
        self.assertEqual(len(calls), 1)
        js = calls[0][0][2]
        self.assertIn("spawnSync('xdotool'", js)
        self.assertIn("shift+Return", js)
        self.assertIn("draft_ready", js)
        self.assertNotIn(".fill(", js)
        self.assertNotIn(".click(", js)
        self.assertNotIn("await browser.close()", js)

    def test_unconfirmed_send_cli_is_not_marked_success(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = chat_cli_main(
                ["--agent", "horizon-skills", "--task-id", "synthetic-check", "--checkin"],
                transport=lambda agent, key, **kwargs: {"status": "send_unconfirmed"})
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["status"], "send_unconfirmed")


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

    def test_existing_tabs_only_and_foreign_drafts_block_every_send(self):
        code = direct_chat._browser_code("qa-engineer", "a"*20,
                                        "Inspect the approved public README first?",
                                        "providers_unavailable", "send")
        self.assertIn("context.pages().filter", code)
        self.assertIn("if (busy) return {status: \"composer_busy\"}", code)
        self.assertNotIn("central.goto(", code)
        self.assertNotIn("newPage(", code)
        self.assertNotIn(".fill(", code)
        self.assertNotIn("fetch(", code)
        self.assertIn("TICKET: ", code)

    def test_remote_preclick_error_can_be_deferred_without_issue(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        task = {"task_id": "ronda-private", "agent": "qa-engineer",
                "payload": {"may_public_context_allowed": True}}
        with patch.dict("os.environ", {"GITHUB_TOKEN": "synthetic", "KERNEL_API_KEY": "synthetic"}, clear=True):
            for status in ("composer_busy", "not_authenticated",
                           "conversation_unavailable", "transport_unavailable"):
                outcome = request_consultation(task, "providers_unavailable",
                    state=state, direct_transport=lambda *a, **k: {"status": status})
                self.assertEqual(outcome["status"], "cooldown")
                self.assertEqual(outcome["retry_after_seconds"], 300)
                self.assertEqual(outcome["reason"], "direct_browser_" + status)
            self.assertEqual(state.recall("__runtime__", "qa-engineer"), {})

    def test_uncertain_direct_delivery_keeps_same_ticket_no_public_issue(self):
        state = State(":memory:")
        self.addCleanup(state.close)
        task = {"task_id": "ronda-private", "agent": "qa-engineer", "payload": {}}
        with patch.dict("os.environ", {"GITHUB_TOKEN": "synthetic", "KERNEL_API_KEY": "synthetic"}, clear=True):
            result = request_consultation(task, "providers_unavailable",
                state=state, direct_transport=lambda *a, **k: {"status": "delivery_unknown"})
        self.assertEqual(result["status"], "direct_pending")
        self.assertEqual(result["transport"], "kernel")
        self.assertEqual(result["ticket"], ticket(task["task_id"], task["agent"]))
        self.assertEqual(state.recall("__runtime__", "qa-engineer")["may_direct_slots"][0]["ticket"],
                         result["ticket"])

    def test_no_authenticating_tab_does_not_start_new_browser_tab(self):
        code = direct_chat._browser_code("horizon-skills", "e"*20,
                                        "Which public README should be checked?",
                                        "specialist_question", "send")
        self.assertIn("return {status: \"conversation_unavailable\"}", code)
        script_calls = []
        class Completed:
            returncode = 0
            stdout = '{"status":"conversation_unavailable"}'
        def fake_run(args, **kwargs):
            script_calls.append(args[2])
            return Completed()
        outcome = direct_chat.execute_local("horizon-skills", "e"*20,
            "Which public README should be checked?", runner=fake_run)
        self.assertEqual(outcome["status"], "conversation_unavailable")
        self.assertNotIn("context.newPage()", script_calls[0])
        self.assertNotIn("await page.close()", script_calls[0])
        self.assertIn("context.pages().find", script_calls[0])

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
