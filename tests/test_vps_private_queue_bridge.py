"""Regression: the VPS bridge imports, holds and resumes only private dummy tasks."""
import subprocess
import sys
import unittest
from pathlib import Path

class VPSPrivateQueueBridgeTest(unittest.TestCase):
    def test_sqlite_bridge_selftest(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "vps_queue_bridge.py"
        result = subprocess.run([sys.executable, str(script), "--selftest"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bridge selftest: ok", result.stdout)
