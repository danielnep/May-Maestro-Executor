import unittest
from unittest.mock import patch
from horizon_core.telemetry import record_usage


class TestTelemetryTaskId(unittest.TestCase):
    def test_valid_task_id_is_recorded(self):
        with patch("horizon_core.telemetry._write", return_value=True) as write:
            self.assertTrue(record_usage("qa-engineer", "groq", "openai/gpt-oss-120b", {"total_tokens": 5}, task_id="ctl-123:t1"))
            self.assertEqual(write.call_args.args[1]["taskId"], "ctl-123:t1")

    def test_legacy_usage_without_id_works(self):
        with patch("horizon_core.telemetry._write", return_value=True) as write:
            self.assertTrue(record_usage("qa-engineer", "groq", "openai/gpt-oss-120b", {"total_tokens": 5}))
            self.assertNotIn("taskId", write.call_args.args[1])

    def test_invalid_id_not_emitted(self):
        with patch("horizon_core.telemetry._write", return_value=True) as write:
            self.assertTrue(record_usage("qa-engineer", "groq", "openai/gpt-oss-120b", {"total_tokens": 5}, task_id="../etc/passwd"))
            self.assertNotIn("taskId", write.call_args.args[1])

    def test_bad_or_empty_usage_does_not_write(self):
        with patch("horizon_core.telemetry._write") as write:
            self.assertFalse(record_usage("qa-engineer", "groq", "openai/gpt-oss-120b", {}, task_id="ctl-123"))
            write.assert_not_called()
