"""Knowledge synchronization must never leak task/customer data or promote untested fixes."""
import unittest

from horizon_core.state import State
from horizon_core.learning import record_failure, verify_check_recovery
from horizon_core.knowledge_sync import verified_lessons, merge_lessons


class KnowledgeSyncTests(unittest.TestCase):
    def setUp(self):
        self.state = State(":memory:")
        self.addCleanup(self.state.close)

    def test_unverified_errors_are_not_published(self):
        record_failure(self.state, "client-private-name", "qa-engineer",
                       "private-task-id", "run_check_failed",
                       action="run_check", check="python-unittest")
        self.assertEqual(verified_lessons(self.state), [])

    def test_matching_real_recovery_is_sanitized(self):
        record_failure(self.state, "client-private-name", "qa-engineer",
                       "private-task-id", "run_check_failed",
                       action="run_check", check="python-unittest")
        self.assertTrue(verify_check_recovery(self.state, "client-private-name",
                        "qa-engineer", "private-task-id", "python-unittest", passed=True))
        rows = verified_lessons(self.state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "qa-engineer")
        self.assertEqual(rows[0]["status"], "verified_by_matching_check")
        self.assertNotIn("client-private-name", repr(rows))
        self.assertNotIn("private-task-id", repr(rows))

    def test_merge_preserves_verified_history(self):
        record_failure(self.state, "project", "qa-engineer", "task",
                       "run_check_failed", action="run_check", check="node-test")
        verify_check_recovery(self.state, "project", "qa-engineer", "task", "node-test", passed=True)
        new = verified_lessons(self.state)
        first = merge_lessons({"schema_version": 1, "entries": []}, new)
        again = merge_lessons(first, new)
        self.assertEqual(first, again)
        self.assertEqual(len(first["entries"]), 1)

    def test_bogus_verification_cannot_be_loaded(self):
        with self.assertRaises(ValueError):
            merge_lessons({"schema_version": 1, "entries": [
                {"agent": "qa-engineer", "id": "f" * 16,
                 "kind": "tool_error", "status": "observed"}]}, [])


if __name__ == "__main__":
    unittest.main()
