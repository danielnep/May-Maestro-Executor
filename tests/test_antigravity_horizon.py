"""Offline contracts for Horizon Antigravity proposal bridge."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from integrations import antigravity_horizon as runtime
from horizon_core.antigravity_bridge import AntigravityGateway
from horizon_core.gateway import GatewayError


ROOT = Path(__file__).resolve().parents[1]


class AntigravityRuntimeTests(unittest.TestCase):
    def test_doctor_offline(self):
        report = runtime.doctor()
        self.assertEqual(report["registered_roles"], 17)
        self.assertFalse(report["model_request_sent"])

    def test_requires_opt_in(self):
        with patch.dict(os.environ, {"HORIZON_ANTIGRAVITY_ENABLE": "0"}, clear=False):
            with self.assertRaises(RuntimeError):
                asyncio.run(runtime.propose_action({}))

    def test_requires_non_root(self):
        with patch.dict(os.environ, {"HORIZON_ANTIGRAVITY_ENABLE": "1"}, clear=False):
            with patch.object(runtime.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(RuntimeError, "unprivileged"):
                    asyncio.run(runtime.propose_action({}))

    def test_rejects_unknown_role(self):
        with self.assertRaisesRegex(ValueError, "Unregistered specialist"):
            runtime.validate_request({"role": "untrusted", "workspace": str(ROOT),
                                      "prompt": "test"})

    def test_workspace_scope(self):
        with self.assertRaises(ValueError):
            runtime.validate_request({"role": "may-maestro", "workspace": str(ROOT),
                                      "prompt": "test"})

    def test_cli_doctor(self):
        result = subprocess.run([sys.executable,
            str(ROOT / "integrations/antigravity_horizon.py"), "--doctor"],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["model_request_sent"])


class AntigravityGatewayTests(unittest.TestCase):
    def setUp(self):
        self.agent = {"id": "qa-engineer", "tools": ["finish", "read_file"],
                      "_task_id": "demo-task"}
        self.messages = [{"role": "user", "content": "Report only"}]

    def test_disabled_does_not_call_runner(self):
        def must_not_run(*args, **kwargs):
            raise AssertionError("SDK must remain off")
        gw = AntigravityGateway(environ={}, runner=must_not_run)
        with self.assertRaises(GatewayError):
            gw.complete(self.agent, self.messages)
        self.assertEqual(gw.calls, 0)

    def test_valid_proposal_passes_through_maestro_validation(self):
        def fake_runner(*args, **kwargs):
            payload = json.loads(kwargs["input"])
            self.assertEqual(payload["role"], "qa-engineer")
            self.assertEqual(args[0][-1], "--gateway")
            return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps({
                "status": "proposed",
                "proposal": json.dumps({"action": "finish", "args": {},
                                        "summary": "Review complete"})
            }))
        gw = AntigravityGateway(environ={"HORIZON_ANTIGRAVITY_ENABLE": "1"},
                                runner=fake_runner, workspace=str(ROOT))
        result = gw.complete(self.agent, self.messages)
        self.assertEqual(result["action"], "finish")
        self.assertEqual(result["metadata"]["task_id"], "demo-task")
        self.assertEqual(gw.calls, 1)

    def test_rejects_forbidden_action(self):
        def fake_runner(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps({
                "status": "proposed",
                "proposal": json.dumps({"action": "write_file", "args": {"path": "/etc/passwd"},
                                        "summary": "bad"})
            }))
        gw = AntigravityGateway(environ={"HORIZON_ANTIGRAVITY_ENABLE": "1"},
                                runner=fake_runner, workspace=str(ROOT))
        with self.assertRaisesRegex(GatewayError, "rejected"):
            gw.complete(self.agent, self.messages)

    def test_rejects_provider_failure_without_leaking_stderr(self):
        def fake_runner(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 3,
                                               stdout="unexpected", stderr="PRIVATE")
        gw = AntigravityGateway(environ={"HORIZON_ANTIGRAVITY_ENABLE": "1"},
                                runner=fake_runner, workspace=str(ROOT))
        with self.assertRaises(GatewayError) as caught:
            gw.complete(self.agent, self.messages)
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_timeout_is_retryable(self):
        def fake_runner(*args, **kwargs):
            raise subprocess.TimeoutExpired("sdk", 1)
        gw = AntigravityGateway(environ={"HORIZON_ANTIGRAVITY_ENABLE": "1"},
                                runner=fake_runner, workspace=str(ROOT))
        with self.assertRaises(GatewayError) as caught:
            gw.complete(self.agent, self.messages)
        self.assertTrue(caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
