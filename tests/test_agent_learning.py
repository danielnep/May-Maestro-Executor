import json
from pathlib import Path
import tempfile
import unittest

from horizon_core.state import State
from horizon_core.learning import record_failure, verify_check_recovery, useful_lessons
from horizon_core.deterministic import inspect_project, execute_read_only_with_retry


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state=State(":memory:")
        self.addCleanup(self.state.close)

    def test_dedup_and_persistent_memory_with_no_unguarded_diagnosis(self):
        for _ in range(3):
            record_failure(self.state, "project", "qa-engineer", "task", "tool_error", action="read_file")
        item=useful_lessons(self.state,"project","qa-engineer")[0]
        self.assertEqual(item["occurrences"],3)
        self.assertEqual(item["status"],"observed")
        self.assertNotIn("password",json.dumps(item))

    def test_only_matching_success_promotes_the_failure(self):
        record_failure(self.state, "project", "qa-engineer", "task", "run_check_failed",action="run_check",check="python-unittest")
        self.assertFalse(verify_check_recovery(self.state,"project","qa-engineer","another-task","python-unittest",passed=True))
        self.assertFalse(verify_check_recovery(self.state,"project","qa-engineer","task","python-unittest",passed=False))
        self.assertFalse(verify_check_recovery(self.state,"project","qa-engineer","task","node-test",passed=True))
        self.assertTrue(verify_check_recovery(self.state,"project","qa-engineer","task","python-unittest",passed=True))
        self.assertEqual(useful_lessons(self.state,"project","qa-engineer")[0]["status"],"verified_by_matching_check")

    def test_small_bounded_inventory_avoids_model_calls_and_symlinks(self):
        root=Path(self.directory.name)
        (root/"package.json").write_text('{"scripts":{"test":"node --test","build":"vite","private":"x"}}')
        (root/"README.md").write_text("fixture")
        (root/"AGENTS.md").symlink_to(root/"README.md")
        value=inspect_project(root)
        self.assertEqual(value["available_script_names"],["build","test"])
        self.assertNotIn("AGENTS.md",[row["path"] for row in value["files"]])
        self.assertNotIn("node --test",json.dumps(value))

    def test_read_only_retry_is_deterministic_but_never_retries_write(self):
        class Tools:
            def __init__(self): self.calls=0
            def execute(self, name, args):
                self.calls+=1
                if self.calls < 3: raise OSError("transient")
                return {"ok":True}
        read=Tools()
        self.assertEqual(execute_read_only_with_retry(read,"read_file",{},sleeper=lambda s: None),{"ok":True})
        self.assertEqual(read.calls,3)
        write=Tools()
        with self.assertRaises(OSError):
            execute_read_only_with_retry(write,"write_file",{},sleeper=lambda s:None)
        self.assertEqual(write.calls,1)


if __name__=="__main__":
    unittest.main()
