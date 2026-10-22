#!/usr/bin/env python3
"""Bridge the Horizon Central's private file queue into the VPS SQLite worker.

No Railway/GitHub fallback. Only authorized, validated files from the worker's
private shared queue are imported. Running tasks are never forcibly cancelled.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if (repo_root / "horizon_core").is_dir():
    sys.path.insert(0, str(repo_root))
else:
    sys.path.insert(0, "/var/lib/horizon-worker/workspace")
from horizon_core.registry import AGENT_IDS, load_agent
from horizon_core.state import State

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{2,69}$")
REPO = re.compile(r"^danielnep/[A-Za-z0-9_.-]+$")
HELD = 4102444800.0
GROUPS = ("requests", "paused", "cancelled", "archived")


def safe_read(path, limit=131072):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("unsafe queue file: " + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def save_atomic(path, obj):
    encoded = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                     dir=str(path.parent), prefix=".horizon-",
                                     suffix=".tmp", delete=False) as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
        name = f.name
    os.chmod(name, 0o600)
    os.replace(name, path)


def collect(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("shared queue root unavailable")
    found = {}
    for group in GROUPS:
        folder = root / group
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("shared queue group unavailable: " + group)
        for path in folder.glob("*.json"):
            if not IDENTIFIER.fullmatch(path.stem) or path.stem in found:
                raise ValueError("duplicate or invalid task ID")
            found[path.stem] = (group, path)
    if len(found) > 160:
        raise ValueError("queue exceeds configured capacity")
    order_path = root / "queue-order.json"
    order = safe_read(order_path, 16384) if order_path.exists() else []
    if not isinstance(order, list) or len(order) > 160 or any(
            not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in order):
        raise ValueError("queue order is invalid")
    active = sorted(x for x, (g, _) in found.items() if g == "requests")
    order = list(dict.fromkeys([x for x in order if x in active] + active))
    return found, order


def verified_request(path, task_id):
    item = safe_read(path)
    if not isinstance(item, dict) or item.get("task_id") != task_id:
        raise ValueError("task ID does not match file")
    if item.get("owner") != "danielnep" or not REPO.fullmatch(str(item.get("repository", ""))):
        raise ValueError("unauthorized task owner/repository")
    if item.get("agent") not in AGENT_IDS:
        raise ValueError("unknown agent")
    load_agent(item["agent"])
    if not IDENTIFIER.fullmatch(str(item.get("project", ""))):
        raise ValueError("invalid project")
    if not isinstance(item.get("objective"), str) or not 12 <= len(item["objective"]) <= 12000:
        raise ValueError("invalid objective")
    payload = item.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("allowed_paths"), list):
        raise ValueError("invalid task paths")
    return item


def reflect(root, state, managed):
    results = root / "results"
    if results.is_symlink() or not results.is_dir():
        raise ValueError("results directory unavailable")
    count = 0
    for task_id in sorted(managed):
        if not IDENTIFIER.fullmatch(task_id):
            raise ValueError("invalid managed ID")
        task = state.get(task_id)
        if not task or (task["attempts"] == 0 and task["status"] == "queued"):
            continue
        row = {
            "status": task["status"],
            "tasks": [{
                "task_id": task_id,
                "agent": task["agent"],
                "status": task["status"],
                "attempts": task["attempts"],
                "summary": "Estado registrado pelo executor SQLite da VPS",
            }],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "origin": "horizon-worker:sqlite",
        }
        if isinstance(task.get("result"), dict):
            result = task["result"]
            if isinstance(result.get("quality"), dict):
                row["quality"] = result["quality"]
            if isinstance(result.get("publication"), dict):
                row["publication"] = result["publication"]
        destination = results / (task_id + ".json")
        if destination.is_symlink():
            raise ValueError("result file symlink rejected")
        save_atomic(destination, row)
        count += 1
    return count


def synchronize(root, db_path, observe_only=False):
    found, order = collect(root)
    marker = root / ".worker-managed.json"
    managed_obj = safe_read(marker, 65536) if marker.exists() else {}
    if not isinstance(managed_obj, dict):
        raise ValueError("invalid managed registry")
    managed = set(managed_obj.get("ids", []))
    if any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in managed):
        raise ValueError("invalid managed registry entries")
    state = State(db_path)
    imported = updated = 0
    try:
        if not observe_only:
            for position, task_id in enumerate(order):
                group, path = found[task_id]
                item = verified_request(path, task_id)
                priority = 100000 - position
                current = state.get(task_id)
                if current is None:
                    state.enqueue(
                        task_id=task_id, agent=item["agent"],
                        project=item["project"], objective=item["objective"],
                        payload=item["payload"], priority=priority,
                    )
                    managed.add(task_id)
                    imported += 1
                elif task_id in managed and current["status"] == "queued":
                    # Reconfigure only unclaimed tasks; do not overwrite work in progress.
                    with state._tx():
                        if current["attempts"] == 0:
                            changes = dict(agent=item["agent"], project=item["project"],
                                           objective=item["objective"], payload=item["payload"],
                                           priority=priority, not_before=0)
                            specification = dict(changes, depends_on=[],
                                                 max_attempts=current["max_attempts"])
                            state.db.execute(
                                "UPDATE tasks SET agent=?, project=?, objective=?, payload=?, "
                                "priority=?, not_before=?, specification=? WHERE task_id=?",
                                (changes["agent"], changes["project"], changes["objective"],
                                 json.dumps(changes["payload"], sort_keys=True),
                                 priority, 0,
                                 json.dumps(specification, sort_keys=True), task_id))
                            updated += 1
                        else:
                            # A resumed owner hold must be released without discarding
                            # a provider's independently scheduled retry delay.
                            resume_at = 0 if current["not_before"] == HELD else current["not_before"]
                            state.db.execute(
                                "UPDATE tasks SET priority=?, not_before=? WHERE task_id=?",
                                (priority, resume_at, task_id))
            for task_id in managed:
                if task_id not in found:
                    continue
                group, _ = found[task_id]
                if group == "requests":
                    continue
                current = state.get(task_id)
                if current and current["status"] == "queued":
                    with state._tx():
                        state.db.execute(
                            "UPDATE tasks SET not_before=? WHERE task_id=?",
                            (HELD, task_id))
            if managed != set(managed_obj.get("ids", [])):
                save_atomic(marker, {"ids": sorted(managed)})
        states = state.list_tasks_snapshot()
        heartbeat = {
            "at": datetime.now(timezone.utc).isoformat(),
            "source": "horizon-worker-sqlite",
            "tasks": [
                {"id": task["task_id"], "agent": task["agent"],
                 "status": task["status"], "attempts": task["attempts"]}
                for task in states[:200]
            ],
        }
        save_atomic(root / ".worker-state.json", heartbeat)
        observations = reflect(root, state, managed)
        return dict(imported=imported, updated=updated, results=observations,
                    active=len(order), managed=len(managed))
    finally:
        state.close()


def selftest():
    with tempfile.TemporaryDirectory(prefix="horizon-vps-bridge-test-") as temp:
        base = Path(temp)
        queue = base / "queue"
        queue.mkdir()
        for g in GROUPS:
            (queue / g).mkdir()
        (queue / "results").mkdir()
        task = {
            "owner": "danielnep", "repository": "danielnep/horizon-site",
            "task_id": "ctl-bridge-test-one", "agent": "qa-engineer",
            "project": "horizon-site",
            "objective": "P2 | Verificar a fila sem executar alteracoes",
            "payload": {"allowed_paths": ["central/**"]},
        }
        save_atomic(queue / "requests" / "ctl-bridge-test-one.json", task)
        database = base / "state.sqlite3"
        r = synchronize(queue, database)
        assert r["imported"] == 1, r
        s = State(database)
        assert s.get(task["task_id"])["status"] == "queued"
        s.close()
        (queue / "requests" / "ctl-bridge-test-one.json").rename(
            queue / "paused" / "ctl-bridge-test-one.json")
        synchronize(queue, database)
        s = State(database)
        assert s.get(task["task_id"])["not_before"] == HELD
        s.close()
        (queue / "paused" / "ctl-bridge-test-one.json").rename(
            queue / "requests" / "ctl-bridge-test-one.json")
        synchronize(queue, database)
        s = State(database)
        assert s.get(task["task_id"])["not_before"] == 0
        s.close()
    print("bridge selftest: ok")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) >= 4 and sys.argv[1] in ("sync", "observe"):
        print(json.dumps(synchronize(Path(sys.argv[2]), Path(sys.argv[3]),
                                     observe_only=sys.argv[1] == "observe")))
    else:
        raise SystemExit("usage: queue_bridge.py sync|observe QUEUE_DIR SQLITE_DB")
