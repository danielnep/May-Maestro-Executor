"""Single-writer durable SQLite snapshots on GitHub, without Railway storage.

Restricted to public projects and synthetic metadata: this repository and the
agent-state branch are public. Never import the private Railway PGlite snapshots.
GitHub Actions concurrency serializes workers, with a CAS SHA per snapshot.
"""
from contextlib import contextmanager
import tempfile
from pathlib import Path

from .github_state import GitHubState
from .state import State

REPOSITORY = "danielnep/May-Maestro-Executor"
STATE_BRANCH = "agent-state/runtime"


class GitHubSQLiteState(State):
    def __init__(self, *, api=None):
        self.workdir = tempfile.TemporaryDirectory(prefix="horizon-sqlite-")
        self.remote = api if api is not None else GitHubState(
            REPOSITORY, branch=STATE_BRANCH, compressed=True)
        self.restored_legacy = False
        dbfile = Path(self.workdir.name) / "state.sqlite3"
        try:
            # Refuse to silently initialize empty and lose prior work.
            if not self.remote.pull(dbfile):
                raise RuntimeError("Previous SQLite checkpoint missing; refusing empty queue")
            self.restored_legacy = bool(getattr(self.remote, "restored_legacy", False))
            super().__init__(dbfile)
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Restored SQLite integrity check failed")
        except BaseException:
            self.workdir.cleanup()
            raise

    @contextmanager
    def _tx(self):
        before = self.db.total_changes
        with super()._tx():
            yield
        # No-op SQL does not create a new GitHub commit.
        if self.db.total_changes != before:
            self._persist()

    def _persist(self):
        # Atomic GitHub contents CAS. A failed acknowledgement stops the worker;
        # no silent fallback to unrelated local/remote state.
        self.remote.push(self.path)

    def close(self):
        try:
            super().close()
        finally:
            self.workdir.cleanup()
