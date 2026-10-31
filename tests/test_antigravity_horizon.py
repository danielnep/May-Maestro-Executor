"""Offline safety contracts for the standalone Antigravity SDK executor."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from integrations import antigravity_horizon as runtime

ROOT = Path(__file__).resolve().parents[1]


class AntigravityRuntimeTests(unittest.TestCase):
    def test_doctor_is_offline(self):
        result = runtime.doctor()
        self.assertEqual(result["registered_roles"], 17)
        self.assertFalse(result["model_request_sent"])

    def test_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"HORIZON_ANTIGRAVITY_ENABLE": "0"}):
            with self.assertRaises(RuntimeError):
                asyncio.run(runtime.propose_action({}))

    def test_requires_non_root(self):
        with patch.dict(os.environ, {"HORIZON_ANTIGRAVITY_ENABLE": "1"}):
            with patch.object(runtime.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(RuntimeError, "unprivileged"):
                    asyncio.run(runtime.propose_action({}))

    def test_rejects_unknown_role(self):
        with self.assertRaisesRegex(ValueError, "Unregistered specialist"):
            runtime.validate_request({"role": "not-a-specialist", "workspace": str(ROOT),
                                      "prompt": "test"})

    def test_rejects_workspace_outside_worker(self):
        with self.assertRaises(ValueError):
            runtime.validate_request({"role": "may-maestro", "workspace": str(ROOT),
                                      "prompt": "test"})

    def test_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            runtime.validate_request({"role": "may-maestro", "workspace": str(ROOT),
                                      "prompt": ""})

    def test_cli_doctor_does_not_make_model_request(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "integrations/antigravity_horizon.py"), "--doctor"],
            text=True, capture_output=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["model_request_sent"])


if __name__ == "__main__":
    unittest.main()
