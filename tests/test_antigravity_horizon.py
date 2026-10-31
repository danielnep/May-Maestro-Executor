"""Offline safety contract for optional Google Antigravity SDK adapter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from integrations.antigravity_horizon import doctor, run_read_only


class AntigravityBridgeTest(unittest.TestCase):
    def test_doctor_is_offline_and_does_not_disclose_secret(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "SECRET_DO_NOT_PRINT"}, clear=False):
            result = doctor()
        self.assertEqual(result["model_request_sent"], False)
        self.assertEqual(result["registered_roles"], 17)
        self.assertNotIn("SECRET_DO_NOT_PRINT", json.dumps(result))

    def test_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"HORIZON_ANTIGRAVITY_ENABLE": "0"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                import asyncio
                asyncio.run(run_read_only("may-maestro", "test", str(Path(__file__).parent)))

    def test_no_key_blocks_before_any_sdk_call(self):
        with patch.dict(os.environ, {"HORIZON_ANTIGRAVITY_ENABLE": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                import asyncio
                asyncio.run(run_read_only("may-maestro", "test", str(Path(__file__).parent)))

    def test_cli_doctor_is_parseable(self):
        r = subprocess.run([sys.executable, str(Path(__file__).parents[1] /
            "integrations" / "antigravity_horizon.py"), "--doctor"],
            text=True, capture_output=True, timeout=12)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(json.loads(r.stdout)["model_request_sent"])


if __name__ == "__main__":
    unittest.main()
