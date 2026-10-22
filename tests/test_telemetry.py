import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from horizon_core.telemetry import record_usage, record_activity

class TelemetryTests(unittest.TestCase):
    def test_provider_usage_counts_are_recorded_without_content(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"HORIZON_DIRECT_INBOX_DIR":temp}):
            self.assertTrue(record_usage("qa-engineer","gemini","gemini-3.5-flash-lite",
                {"promptTokenCount":130,"candidatesTokenCount":21,"thoughtsTokenCount":14,"totalTokenCount":165}))
            self.assertTrue(record_usage("backend-engineer","groq","openai/gpt-oss-120b",
                {"prompt_tokens":210,"completion_tokens":38,"total_tokens":248}))
            records=[json.loads(x) for x in next((Path(temp)/"telemetry").glob("usage-*.jsonl")).read_text().splitlines()]
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]["total"],165)
            self.assertEqual(records[0]["reasoning"],14)
            self.assertEqual(records[1]["input"],210)
            self.assertEqual(records[1]["output"],38)
            self.assertNotIn("message",records[0])
    def test_activity_sanitizes_paths_and_never_requires_model(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"HORIZON_DIRECT_INBOX_DIR":temp}):
            self.assertTrue(record_activity("ctl-12345","qa-engineer","read_file",step=4,path="src/app.js"))
            self.assertTrue(record_activity("ctl-12345","qa-engineer","write_file",step=5,path=".env"))
            self.assertFalse(record_activity("../../x","qa-engineer","run_check",step=3))
            lines=[json.loads(x) for x in next((Path(temp)/"telemetry").glob("activity-*.jsonl")).read_text().splitlines()]
            self.assertEqual(lines[0]["path"],"src/app.js")
            self.assertIsNone(lines[1]["path"])
            self.assertEqual(len(lines),2)
    def test_subtask_colon_ids_are_recorded_without_leaking_content(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"HORIZON_DIRECT_INBOX_DIR":temp}):
            self.assertTrue(record_activity("chat-ctl-123:ui","qa-engineer","read_file",step=1,path="src/app.js"))
            records=[json.loads(x) for x in next((Path(temp)/"telemetry").glob("activity-*.jsonl")).read_text().splitlines()]
            self.assertEqual(records[0]["taskId"],"chat-ctl-123:ui")
            self.assertFalse(record_activity("../secrets","qa-engineer","read_file"))
    def test_missing_usage_is_not_invented(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"HORIZON_DIRECT_INBOX_DIR":temp}):
            self.assertFalse(record_usage("qa-engineer","gemini","gemini-3.5-flash-lite",{}))
            self.assertEqual(list((Path(temp)/"telemetry").glob("usage-*")),[])
if __name__=="__main__": unittest.main()
