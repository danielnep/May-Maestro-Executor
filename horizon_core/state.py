"""Durable SQLite task queue, checkpoints and strictly scoped agent memory.

Dependencies must already exist. This makes cycles impossible by construction;
self dependencies are also rejected. Use unique worker IDs per execution/attempt.
Use backup(), not copying a live WAL file, for GitHub checkpoint snapshots.
"""
from contextlib import contextmanager
import json
import sqlite3
import threading
import time
from pathlib import Path


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class LeaseError(RuntimeError):
    """A mutation did not own a currently valid task lease."""


class State:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                agent TEXT NOT NULL, project TEXT NOT NULL, objective TEXT NOT NULL,
                payload TEXT NOT NULL, depends_on TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('queued','running','succeeded','failed','blocked')),
                priority INTEGER NOT NULL, not_before REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                lease_owner TEXT, lease_until REAL,
                checkpoint TEXT, result TEXT, error TEXT,
                specification TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS tasks_claim ON tasks(status, priority DESC, seq);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                kind TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory (
                project TEXT NOT NULL, agent TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL, PRIMARY KEY(project, agent, key)
            );
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _event(self, task_id, kind, data, now=None):
        self.db.execute("INSERT INTO events(task_id,kind,data,created_at) VALUES(?,?,?,?)",
                        (task_id, kind, _json(data), time.time() if now is None else now))

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        value = dict(row)
        value.pop("specification", None)
        for key in ("payload", "depends_on", "checkpoint", "result", "error"):
            if value.get(key) is not None:
                value[key] = json.loads(value[key])
        return value

    def enqueue(self, task_id, agent, project, objective, payload=None,
                depends_on=None, priority=0, not_before=0, max_attempts=3):
        if not all(isinstance(x, str) and x.strip() for x in
                   (task_id, agent, project, objective)):
            raise ValueError("task_id, agent, project and objective must be nonempty strings")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        deps = sorted(set(depends_on or []))
        if task_id in deps:
            raise ValueError("dependency cycle: task depends on itself")
        specification = _json(dict(agent=agent, project=project, objective=objective,
            payload=payload if payload is not None else {}, depends_on=deps,
            priority=priority, not_before=not_before, max_attempts=max_attempts))
        with self._tx():
            existing = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if existing:
                if existing["specification"] != specification:
                    raise ValueError("task_id already exists with different content")
                return self._decode(existing)
            for dep in deps:
                if not self.db.execute("SELECT 1 FROM tasks WHERE task_id=?", (dep,)).fetchone():
                    raise ValueError("dependency must be enqueued first: " + dep)
            self.db.execute("""INSERT INTO tasks(task_id,agent,project,objective,payload,
                depends_on,status,priority,not_before,max_attempts,specification)
                VALUES(?,?,?,?,?,?,'queued',?,?,?,?)""", (task_id, agent, project,
                objective, _json(payload if payload is not None else {}), _json(deps),
                priority, not_before, max_attempts, specification))
            self._event(task_id, "enqueued", {})
            return self._decode(self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    def claim(self, worker, now=None, lease_seconds=300, project=None):
        now = time.time() if now is None else now
        if not worker or lease_seconds <= 0:
            raise ValueError("worker and positive lease_seconds required")
        if project is not None and (not isinstance(project, str) or not project):
            raise ValueError("project filter must be a nonempty string")
        with self._tx():
            expired = self.db.execute("SELECT * FROM tasks WHERE status='running' AND lease_until<=?", (now,)).fetchall()
            for row in expired:
                status = "failed" if row["attempts"] >= row["max_attempts"] else "queued"
                self.db.execute("UPDATE tasks SET status=?,lease_owner=NULL,lease_until=NULL,error=? WHERE task_id=?",
                                (status, _json("lease expired"), row["task_id"]))
                self._event(row["task_id"], "lease_expired", {"status": status}, now)
            # Propagate terminal dependency failures through the entire DAG.
            while True:
                changed = False
                for row in self.db.execute("SELECT * FROM tasks WHERE status='queued'").fetchall():
                    failed = [dep for dep in json.loads(row["depends_on"]) if
                              self.db.execute("SELECT status FROM tasks WHERE task_id=?", (dep,)).fetchone()[0]
                              in ("failed", "blocked")]
                    if failed:
                        self.db.execute("UPDATE tasks SET status='blocked',error=? WHERE task_id=?",
                                        (_json({"failed_dependencies": failed}), row["task_id"]))
                        self._event(row["task_id"], "blocked", {"dependencies": failed}, now)
                        changed = True
                if not changed:
                    break
            # Never claim a fourth attempt, including legacy queued rows that
            # predate the strict provider retry policy.
            for exhausted in self.db.execute(
                    "SELECT task_id,max_attempts FROM tasks WHERE status='queued' AND attempts>=max_attempts").fetchall():
                self.db.execute(
                    "UPDATE tasks SET status='failed',error=? WHERE task_id=?",
                    (_json({"reason": "retry_limit_exceeded",
                            "max_attempts": exhausted["max_attempts"]}),
                     exhausted["task_id"]))
                self._event(exhausted["task_id"], "retry_limit_exceeded", {}, now)
            ready_sql = "SELECT * FROM tasks WHERE status='queued' AND not_before<=?"
            ready_args = [now]
            if project is not None:
                ready_sql += " AND project=?"
                ready_args.append(project)
            ready_sql += " ORDER BY priority DESC,seq"
            for row in self.db.execute(ready_sql, ready_args).fetchall():
                if any(self.db.execute("SELECT status FROM tasks WHERE task_id=?", (dep,)).fetchone()[0] != "succeeded"
                       for dep in json.loads(row["depends_on"])):
                    continue
                self.db.execute("UPDATE tasks SET status='running',attempts=attempts+1,lease_owner=?,lease_until=? WHERE task_id=?",
                                (worker, now + lease_seconds, row["task_id"]))
                self._event(row["task_id"], "claimed", {"worker": worker, "attempt": row["attempts"] + 1}, now)
                return self._decode(self.db.execute("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone())
            return None

    def _owned(self, task_id, worker, now):
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row or row["status"] != "running" or row["lease_owner"] != worker or row["lease_until"] <= now:
            raise LeaseError("worker does not hold an unexpired lease")
        return row

    def renew_lease(self, task_id, worker, lease_seconds=1800, now=None):
        """Extend a live lease; a stale worker can never reacquire through renewal."""
        now = time.time() if now is None else now
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._tx():
            row = self._owned(task_id, worker, now)
            until = max(row["lease_until"], now + lease_seconds)
            self.db.execute("UPDATE tasks SET lease_until=? WHERE task_id=?", (until, task_id))
            self._event(task_id, "lease_renewed", {"worker": worker, "lease_until": until}, now)
            return until

    def yield_task(self, task_id, worker, now=None):
        """Release at a safe checkpoint without consuming a failure/retry attempt."""
        now = time.time() if now is None else now
        with self._tx():
            self._owned(task_id, worker, now)
            self.db.execute("""UPDATE tasks SET status='queued',attempts=MAX(0,attempts-1),
                lease_owner=NULL,lease_until=NULL,error=NULL,not_before=? WHERE task_id=?""",
                (now, task_id))
            self._event(task_id, "yielded", {"worker": worker}, now)

    def defer(self, task_id, worker, seconds=900, now=None):
        """Queue a pending external consultation without burning retry attempts.

        not_before prevents a tight loop while waiting for the scheduled May
        response, even when the worker resumes immediately after a checkpoint.
        """
        if not isinstance(seconds, int) or not 60 <= seconds <= 3600:
            raise ValueError("Invalid consultation wait")
        now = time.time() if now is None else now
        with self._tx():
            self._owned(task_id, worker, now)
            self.db.execute("""UPDATE tasks SET status='queued',
                attempts=MAX(0,attempts-1),lease_owner=NULL,lease_until=NULL,
                error=NULL,not_before=? WHERE task_id=?""", (now+seconds, task_id))
            self._event(task_id, 'consultation_deferred',
                        {'wait_seconds': seconds}, now)

    def checkpoint(self, task_id, worker, data, now=None):
        now = time.time() if now is None else now
        encoded = _json(data)
        with self._tx():
            self._owned(task_id, worker, now)
            self.db.execute("UPDATE tasks SET checkpoint=? WHERE task_id=?", (encoded, task_id))
            self._event(task_id, "checkpoint", data, now)

    def complete(self, task_id, worker, result, now=None):
        now = time.time() if now is None else now
        encoded = _json(result)
        with self._tx():
            self._owned(task_id, worker, now)
            self.db.execute("UPDATE tasks SET status='succeeded',result=?,error=NULL,lease_owner=NULL,lease_until=NULL WHERE task_id=?", (encoded, task_id))
            self._event(task_id, "succeeded", result, now)

    def fail(self, task_id, worker, error, retryable=True, now=None):
        now = time.time() if now is None else now
        encoded = _json(error)
        with self._tx():
            row = self._owned(task_id, worker, now)
            # Gateway outages respect the same hard attempt ceiling as all other
            # failures. A circuit breaker avoids repeatedly consuming API quota.
            retry = retryable and row["attempts"] < row["max_attempts"]
            # A bounded exponential delay also handles provider quota/transient errors.
            not_before = now + min(300, 5 * 2 ** min(row["attempts"] - 1, 6)) if retry else row["not_before"]
            status = "queued" if retry else "failed"
            self.db.execute("UPDATE tasks SET status=?,error=?,not_before=?,lease_owner=NULL,lease_until=NULL WHERE task_id=?",
                            (status, encoded, not_before, task_id))
            self._event(task_id, "retry_scheduled" if retry else "failed", {"error": error, "not_before": not_before}, now)

    @staticmethod
    def _safe_failure_json(raw):
        try:
            return json.loads(raw) if raw else None
        except (ValueError, TypeError):
            return None

    def recover_provider_outages(self, now=None):
        """Unstick only failed retryable provider tasks and their dependents.

        Preserve checkpoints, task definitions and unrelated failure states.
        This recovery is idempotent across controller restarts.
        """
        now = time.time() if now is None else now
        revived = []
        unblocked = []
        # A no-op must not POST an entire durable SQLite snapshot to Railway.
        # The state bridge commits a new revision after EVERY _tx, even reads.
        with self._lock:
            eligible_failed = any(
                isinstance(detail, dict) and
                detail.get("reason") == "providers_unavailable" and
                detail.get("retryable") is True and
                row["attempts"] < row["max_attempts"]
                for row in self.db.execute(
                    "SELECT error,attempts,max_attempts FROM tasks WHERE status='failed'")
                for detail in [self._safe_failure_json(row["error"])])
            eligible_blocked = any(
                all(self.db.execute("SELECT status FROM tasks WHERE task_id=?", (dep,)).fetchone()
                    and self.db.execute("SELECT status FROM tasks WHERE task_id=?", (dep,)).fetchone()["status"]
                        in ("queued", "running", "succeeded")
                    for dep in json.loads(row["depends_on"]))
                for row in self.db.execute("SELECT depends_on FROM tasks WHERE status='blocked'"))
            if not eligible_failed and not eligible_blocked:
                return {"requeued_provider_failures": 0, "unblocked_dependents": 0}
        with self._tx():
            for row in self.db.execute("SELECT * FROM tasks WHERE status='failed'").fetchall():
                try:
                    detail = json.loads(row["error"] or "null")
                except (ValueError, TypeError):
                    continue
                if (not isinstance(detail, dict) or
                    detail.get("reason") != "providers_unavailable" or
                    detail.get("retryable") is not True or
                    row["attempts"] >= row["max_attempts"]):
                    continue
                self.db.execute(
                    "UPDATE tasks SET status='queued',not_before=?,lease_owner=NULL,lease_until=NULL "
                    "WHERE task_id=? AND status='failed'", (now, row["task_id"]))
                self._event(row["task_id"], "provider_outage_recovered",
                            {"reason": "providers_unavailable"}, now)
                revived.append(row["task_id"])
            # Undo cascade blocks only when every dependency is no longer terminal.
            while True:
                progressed = False
                for row in self.db.execute("SELECT * FROM tasks WHERE status='blocked'").fetchall():
                    deps = json.loads(row["depends_on"])
                    statuses = [self.db.execute("SELECT status FROM tasks WHERE task_id=?",
                                (dep,)).fetchone() for dep in deps]
                    if not all(item and item["status"] in ("succeeded","queued","running")
                               for item in statuses):
                        continue
                    self.db.execute(
                        "UPDATE tasks SET status='queued',error=NULL,not_before=? WHERE task_id=?",
                        (now,row["task_id"]))
                    self._event(row["task_id"], "dependency_unblocked", {"deps": deps}, now)
                    unblocked.append(row["task_id"])
                    progressed = True
                if not progressed:
                    break
        return {"requeued_provider_failures": len(revived),
                "unblocked_dependents": len(unblocked)}

    def supervisor_recover_on_runtime_upgrade(self, sha, now=None):
        """One bounded retry per executor revision; preserve checkpoints and evidence.

        This is the active Railway supervisor's deterministic recovery layer.
        It cannot fabricate visual proof, approve QA, or reset user approvals.
        A new runtime revision is necessary before another retry can be made.
        """
        import re
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("A verified executor SHA is required for supervised recovery")
        now = time.time() if now is None else now
        resumed = []
        with self._tx():
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE status='failed' ORDER BY priority DESC, seq"
            ).fetchall()
            for row in rows:
                try:
                    problem = json.loads(row["error"] or "{}")
                except (ValueError, TypeError):
                    problem = {}
                if not isinstance(problem, dict):
                    problem = {}
                # Evidence/permission failures need fresh proof or operator action,
                # not repeated AI calls with unchanged source and no browser.
                if (problem.get("reason") in (
                    "visual_evidence_missing", "manual_approval_required",
                    "quality_blocked", "security_blocked"
                ) or problem.get("kind") in ("PermissionError", "LeaseError")):
                    continue
                key = "runtime_retry:" + row["task_id"]
                prior = self.db.execute(
                    "SELECT value FROM memory WHERE project=? AND agent=? AND key=?",
                    ("__horizon__", "__supervisor__", key)
                ).fetchone()
                if prior is not None and json.loads(prior["value"]) == sha:
                    continue
                # Exactly one new attempt per code revision. No unlimited retry loop.
                self.db.execute(
                    """UPDATE tasks SET status='queued', attempts=?,
                    not_before=?, lease_owner=NULL, lease_until=NULL, error=NULL
                    WHERE task_id=? AND status='failed'""",
                    (max(0, row["max_attempts"] - 1), now, row["task_id"])
                )
                self._event(row["task_id"], "supervisor_runtime_retry",
                            {"executor_sha": sha[:12], "previous_attempts": row["attempts"]}, now)
                self.db.execute(
                    """INSERT INTO memory(project,agent,key,value) VALUES(?,?,?,?)
                    ON CONFLICT(project,agent,key) DO UPDATE SET value=excluded.value""",
                    ("__horizon__", "__supervisor__", key, _json(sha))
                )
                resumed.append(row["task_id"])
        return resumed

    def remember(self, project, agent, key, value):
        # Public encrypted GitHub snapshots are committed after real SQL changes.
        # Avoid producing a new checkpoint/commit for identical operational data.
        encoded = _json(value)
        with self._lock:
            prior = self.db.execute(
                "SELECT value FROM memory WHERE project=? AND agent=? AND key=?",
                (project, agent, key)).fetchone()
            if prior is not None and prior["value"] == encoded:
                return
            with self._tx():
                self.db.execute(
                    "INSERT INTO memory(project,agent,key,value) VALUES(?,?,?,?) "
                    "ON CONFLICT(project,agent,key) DO UPDATE SET value=excluded.value "
                    "WHERE memory.value<>excluded.value",
                    (project, agent, key, encoded))

    def recall(self, project, agent):
        with self._lock:
            return {row["key"]: json.loads(row["value"]) for row in self.db.execute(
                "SELECT key,value FROM memory WHERE project=? AND agent=? ORDER BY key", (project, agent))}

    def get(self, task_id):
        with self._lock:
            return self._decode(self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    def events(self, task_id):
        with self._lock:
            return [dict(row, data=json.loads(row["data"])) for row in self.db.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,))]

    def tasks(self, project=None):
        with self._lock:
            rows = self.db.execute('SELECT * FROM tasks ORDER BY seq') if project is None else self.db.execute('SELECT * FROM tasks WHERE project=? ORDER BY seq', (project,))
            return [self._decode(row) for row in rows]

    def list_tasks_snapshot(self):
        return [{key: task[key] for key in ('task_id', 'agent', 'status', 'attempts')} for task in self.tasks()]

    def backup(self, destination):
        """Create a consistent standalone SQLite file, including committed WAL data."""
        if self.path != ":memory:" and Path(destination).resolve() == Path(self.path).resolve():
            raise ValueError("backup destination must differ from live database")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(str(destination)) as target:
            self.db.backup(target)
        return str(destination)
