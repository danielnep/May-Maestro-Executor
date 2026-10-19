"""SQLite GitHub state: migration, durability, idempotence and rejection of stale writes."""
from pathlib import Path
import tempfile
import unittest

from horizon_core.github_sqlite_state import GitHubSQLiteState
from horizon_core.state import State


class GitHubSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.orig = Path(self.tmp.name) / "source.sqlite3"
        state = State(self.orig)
        state.enqueue("original", "may-maestro", "sample", "Preserve an existing checkpoint")
        state.close()

    def test_empty_branch_fails_closed(self):
        class Missing:
            def pull(self, path): return False
        with self.assertRaisesRegex(RuntimeError, "missing"):
            GitHubSQLiteState(api=Missing())

    def test_legacy_state_is_restored_without_recreating_tasks(self):
        class Snapshot:
            restored_legacy = True
            def __init__(self, source): self.source=source; self.writes=0
            def pull(self, dest):
                Path(dest).write_bytes(Path(self.source).read_bytes()); return True
            def push(self, source):
                self.writes += 1
                return "ack"
        store = Snapshot(self.orig)
        state = GitHubSQLiteState(api=store)
        self.assertTrue(state.restored_legacy)
        self.assertEqual(state.get("original")["status"],"queued")
        state.recover_provider_outages()
        self.assertEqual(store.writes,0)
        state.remember("sample","may-maestro","evidence",{"checked":True})
        self.assertEqual(store.writes,1)
        state.close()

    def test_checkpoint_error_stops_after_commit(self):
        class Unavailable:
            restored_legacy = False
            def __init__(self, path): self.path=path
            def pull(self, dest):
                Path(dest).write_bytes(Path(self.path).read_bytes()); return True
            def push(self, src): raise RuntimeError("GitHub storage failed")
        state=GitHubSQLiteState(api=Unavailable(self.orig))
        with self.assertRaisesRegex(RuntimeError, "storage failed"):
            state.remember("sample","may-maestro","incident",{"status":"unconfirmed"})
        state.close()


    def test_identical_memory_does_not_create_another_snapshot(self):
        class Snapshot:
            restored_legacy = False
            def __init__(self, source):
                self.source = source
                self.writes = 0
            def pull(self, dest):
                Path(dest).write_bytes(Path(self.source).read_bytes())
                return True
            def push(self, source):
                self.writes += 1
                return "ack"
        remote = Snapshot(self.orig)
        state = GitHubSQLiteState(api=remote)
        try:
            state.remember("sample", "qa-engineer", "same", {"status": "verified"})
            self.assertEqual(remote.writes, 1)
            state.remember("sample", "qa-engineer", "same", {"status": "verified"})
            self.assertEqual(remote.writes, 1)
            state.remember("sample", "qa-engineer", "same", {"status": "changed"})
            self.assertEqual(remote.writes, 2)
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
