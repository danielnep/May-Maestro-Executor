"""Start a fresh Horizon VPS generation with retained agent learning only.

Source: a verified, inactive SQLite snapshot.
Destination: a NEW database. Neither source nor other application data are altered.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def digest_memory(connection):
    fingerprint = hashlib.sha256()
    count = 0
    for project, agent, key, value in connection.execute(
        "SELECT project,agent,key,value FROM memory ORDER BY project,agent,key"
    ):
        record = json.dumps([project, agent, key, value], separators=(",", ":"), ensure_ascii=False)
        raw = record.encode("utf-8")
        fingerprint.update(len(raw).to_bytes(8, "big"))
        fingerprint.update(raw)
        count += 1
    return count, fingerprint.hexdigest()


def reset_with_learning(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise RuntimeError("Refusing to overwrite existing database or original snapshot")
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("Source must be a real, existing SQLite snapshot")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    uri = source.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as original:
        if original.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Original SQLite integrity check failed")
        tables = {row[0] for row in original.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"tasks", "events", "memory"}.issubset(tables):
            raise RuntimeError("Unrecognized source schema; refusing migration")
        original_count, original_digest = digest_memory(original)
        # Schema is created by the first-party State class, not reconstructed by hand.
        from horizon_core.state import State
        fd, temp = tempfile.mkstemp(prefix=".new-generation-", suffix=".sqlite3",
                                    dir=destination.parent)
        os.close(fd)
        try:
            fresh = State(temp)
            try:
                fresh.db.execute("BEGIN IMMEDIATE")
                for record in original.execute(
                    "SELECT project,agent,key,value FROM memory ORDER BY project,agent,key"):
                    fresh.db.execute(
                        "INSERT INTO memory(project,agent,key,value) VALUES (?,?,?,?)",
                        record)
                fresh.db.execute("COMMIT")
                new_count, new_digest = digest_memory(fresh.db)
                if (new_count, new_digest) != (original_count, original_digest):
                    raise RuntimeError("Memory mismatch; preserving original")
                if fresh.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] != 0:
                    raise RuntimeError("Fresh task queue is not empty")
                if fresh.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] != 0:
                    raise RuntimeError("Fresh event log is not empty")
                if fresh.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("New SQLite integrity check failed")
            finally:
                fresh.close()
            os.chmod(temp, 0o600)
            os.rename(temp, destination)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return {"status": "ready", "learning_records": original_count,
            "memory_sha256": original_digest, "tasks": 0, "events": 0,
            "destination": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/opt/horizon-maestro/state/restored-413c22a0f6db.sqlite3")
    parser.add_argument("--destination", default="/var/lib/horizon-worker/state/active.sqlite3")
    arguments = parser.parse_args()
    result = reset_with_learning(arguments.source, arguments.destination)
    if os.geteuid() == 0:
        import pwd
        worker = pwd.getpwnam("horizon-worker")
        os.chown(arguments.destination, worker.pw_uid, worker.pw_gid)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
