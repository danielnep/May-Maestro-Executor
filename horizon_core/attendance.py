"""Public-safe attendance projection from durable specialist task events.

ChatGPT does not receive direct pushes from GitHub Actions. This durable feed
is read by the ONE scheduled ChatGPT Central and relayed into the existing chat.
Only verified queue transitions are published, never raw model output, objectives,
workspace paths, checkpoints, credentials, customer content or source code.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import re

from .github_state import GitHubStateError
from .registry import AGENT_IDS

REPOSITORY = "danielnep/May-Maestro-Executor"
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{0,69}$")
SCHEMA = "horizon.agent.attendance.v1"
CHAT_LABEL = "Horizon | Central dos Agentes"
EVENT_STATES = {
    "claimed": "iniciou",
    "succeeded": "concluiu",
    "failed": "bloqueado",
    "blocked": "bloqueado",
    "retry_limit_exceeded": "bloqueado",
    "lease_expired": "aguardando",
    "retry_scheduled": "aguardando",
    "consultation_deferred": "aguardando",
}
TASK_STATES = {
    "running": "executando",
    "succeeded": "concluido",
    "failed": "bloqueado",
    "blocked": "bloqueado",
    "queued": "na_fila",
}


def _ref(task_id):
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]


def _when(value):
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def attendance_snapshot(state, request_id, *, max_events=160):
    """Return a bounded, reproducible feed; never reveal raw worker task text."""
    if not isinstance(request_id, str) or not IDENTIFIER.fullmatch(request_id):
        raise ValueError("Invalid public request identifier")
    if type(max_events) is not int or not 1 <= max_events <= 250:
        raise ValueError("Invalid attendance event budget")
    all_events = []
    roster = []
    tasks = state.tasks(project="chat:" + request_id)
    for task in tasks:
        agent = task["agent"]
        if agent not in AGENT_IDS:
            continue
        task_id = task["task_id"]
        ref = _ref(task_id)
        rows = state.events(task_id)
        visible = []
        claimed_seen = False
        for row in rows:
            kind = row["kind"]
            if kind == "claimed":
                status = "retomou" if claimed_seen else "iniciou"
                claimed_seen = True
            else:
                status = EVENT_STATES.get(kind)
            if status is None:
                continue
            # An expired lease that was requeued is waiting; terminal failures
            # remain blocked. No raw event payload is copied into this feed.
            if kind == "lease_expired" and row.get("data", {}).get("status") == "failed":
                status = "bloqueado"
            event_id = int(row["id"])
            visible.append({
                "id": event_id, "ref": ref, "agent": agent,
                "status": status, "at": _when(row["created_at"]),
            })
        all_events.extend(visible)
        checkpoint = task.get("checkpoint") or {}
        actual = task["status"]
        current = TASK_STATES.get(actual, "desconhecido")
        last_transition = next((entry["kind"] for entry in reversed(rows)
                                if entry["kind"] in ("claimed", "consultation_deferred",
                                                     "yielded", "retry_scheduled", "enqueued")), None)
        if actual == "queued" and (checkpoint.get("pending_may")
                                    or checkpoint.get("deferred_may_request")
                                    or last_transition in ("consultation_deferred", "yielded",
                                                           "retry_scheduled")):
            current = "aguardando"
        roster.append({
            "ref": ref, "agent": agent, "status": current,
            "attempts": int(task.get("attempts") or 0),
            "step": int(checkpoint.get("step") or 0)
                if isinstance(checkpoint.get("step"), int) else 0,
        })
    if not roster:
        return None
    all_events.sort(key=lambda row: row["id"])
    return {
        "schema": SCHEMA,
        "request_id": request_id,
        "chat": CHAT_LABEL,
        "source": "durable_state_events",
        "events": all_events[-max_events:],
        "roster": roster,
        "result_url": "https://github.com/" + REPOSITORY
                      + "/blob/main/tasks/results/" + request_id + ".json",
    }


def publish_attendance(api, snapshot, *, branch="main"):
    """Publish idempotently using GitHub's contents CAS, never on product branches."""
    if branch != "main" or not isinstance(snapshot, dict):
        raise ValueError("Attendance publication restricted to controller main")
    key = snapshot.get("request_id")
    if not isinstance(key, str) or not IDENTIFIER.fullmatch(key):
        raise ValueError("Invalid attendance request")
    if snapshot.get("schema") != SCHEMA:
        raise ValueError("Invalid attendance schema")
    path = "tasks/attendance/" + key + ".json"
    endpoint = "/contents/" + path
    raw = (json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                      indent=2, allow_nan=False) + "\n").encode("utf-8")
    sha = None
    try:
        previous = api._request("GET", endpoint + "?ref=main")
        sha = previous["sha"]
        if base64.b64decode(previous["content"]) == raw:
            return {"written": False, "path": path}
    except GitHubStateError as exc:
        if str(exc) != "GitHub HTTP 404":
            raise
    body = {"branch": "main", "message": "Horizon: ponto " + key + " [skip ci]",
            "content": base64.b64encode(raw).decode("ascii")}
    if sha is not None:
        body["sha"] = sha
    api._request("PUT", endpoint, body)
    return {"written": True, "path": path}


def team_snapshot(state):
    """Current assignment/idle visibility for all seventeen specialists.

    Task objectives, branches and workspaces remain private; a specialist can
    be idle even after completing work, while failed tasks are still counted.
    """
    by_agent = {agent: [] for agent in AGENT_IDS}
    for row in state.tasks():
        if row["agent"] in by_agent:
            by_agent[row["agent"]].append(row)
    agents = []
    for agent, tasks in by_agent.items():
        running = [task for task in tasks if task["status"] == "running"]
        queued = [task for task in tasks if task["status"] == "queued"]
        waiting = [task for task in queued if
                   (task.get("checkpoint") or {}).get("pending_may")
                   or (task.get("checkpoint") or {}).get("deferred_may_request")
                   or any(entry["kind"] in ("consultation_deferred", "yielded", "retry_scheduled")
                          for entry in state.events(task["task_id"])[-2:])]
        if running:
            status, relevant = "executando", running[-1]
        elif waiting:
            status, relevant = "aguardando", waiting[-1]
        elif queued:
            status, relevant = "na_fila", queued[-1]
        else:
            status, relevant = "ocioso", tasks[-1] if tasks else None
        agents.append({
            "agent": agent,
            "status": status,
            "task_ref": _ref(relevant["task_id"]) if relevant else None,
            "finished": sum(task["status"] == "succeeded" for task in tasks),
            "blocked": sum(task["status"] in ("failed", "blocked") for task in tasks),
            "queued": len(queued),
            "running": len(running),
        })
    return {"schema": "horizon.agent.roster.v1", "chat": CHAT_LABEL,
            "source": "durable_state_tasks", "agents": agents}


def publish_team_attendance(api, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "horizon.agent.roster.v1":
        raise ValueError("Invalid specialist roster schema")
    raw = (json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                      indent=2, allow_nan=False) + "\n").encode("utf-8")
    endpoint = "/contents/tasks/attendance/equipe.json"
    sha = None
    try:
        prior = api._request("GET", endpoint + "?ref=main")
        sha = prior["sha"]
        if base64.b64decode(prior["content"]) == raw:
            return {"written": False}
    except GitHubStateError as exc:
        if str(exc) != "GitHub HTTP 404":
            raise
    body = {"branch": "main", "message": "Horizon: atualizar ponto da equipe [skip ci]",
            "content": base64.b64encode(raw).decode("ascii")}
    if sha is not None:
        body["sha"] = sha
    api._request("PUT", endpoint, body)
    return {"written": True}
