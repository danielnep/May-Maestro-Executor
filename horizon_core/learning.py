"""Evidence-gated learning from agent failures; no LLM required for bookkeeping.

A tool error is an observation, not a validated root cause. Only a matching
successful regression check promotes a previously failed check to verified.
Do not store raw exception strings, customer files, tokens or model output.
"""
import hashlib
import time

KEY = "__learning_v1__"
MAX_CASES = 24


def learning_namespace(agent):
    # Tool remember is agent-scoped. Keep observed/verified evidence in a separate
    # runtime-owned namespace so model output cannot promote its own lesson.
    if not isinstance(agent, str) or not agent or len(agent) > 90:
        raise ValueError('Invalid learning namespace')
    return '__agent_learning__:' + agent

RULES = {
    "protocol_error": "Return exactly one JSON action, args object and summary text.",
    "invalid_delegation": "Validate specialist identifiers and topological prerequisites before enqueue.",
    "tool_error": "Inspect the tool contract and authorized path; retry only demonstrably transient read errors.",
    "run_check_failed": "Fix the failing check, rerun the same test, and require a passing result before verification.",
    "provider_unavailable": "Preserve checkpoint and use the configured fallback/cooldown, not unbounded requests.",
}


def incident_id(kind, action, check):
    raw = "|".join((kind, action, check))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def record_failure(state, project, agent, task_id, kind, *, action="", check=""):
    if kind not in RULES:
        raise ValueError("Unknown failure class")
    if not isinstance(action, str) or not isinstance(check, str):
        raise ValueError("Invalid incident attributes")
    # Constrain to known procedural actions, not raw model or exception text.
    action = action[:48] if action.isidentifier() else ""
    check = check[:64] if all(c.isalnum() or c in "-_." for c in check) else ""
    fingerprint = incident_id(kind, action, check)
    memory = state.recall(project, learning_namespace(agent))
    cases = list(memory.get(KEY, [])) if isinstance(memory.get(KEY, []), list) else []
    existing = next((case for case in cases if case.get("id") == fingerprint), None)
    if existing:
        existing["occurrences"] = min(100000, existing.get("occurrences", 1) + 1)
        existing["latest_task_id"] = task_id[:100]
    else:
        cases.append({
            "id": fingerprint, "kind": kind, "action": action, "check": check,
            "status": "observed", "occurrences": 1,
            "latest_task_id": task_id[:100], "rule": RULES[kind],
        })
    state.remember(project, learning_namespace(agent), KEY, cases[-MAX_CASES:])
    return fingerprint


def verify_check_recovery(state, project, agent, task_id, check, *, passed):
    """A matching failed check must pass in the same task to count as verified."""
    if passed is not True or not isinstance(check, str):
        return False
    memory = state.recall(project, learning_namespace(agent))
    cases = list(memory.get(KEY, [])) if isinstance(memory.get(KEY, []), list) else []
    updated = False
    for case in cases:
        if (case.get("kind") == "run_check_failed"
                and case.get("check") == check
                and case.get("latest_task_id") == task_id
                and case.get("status") == "observed"):
            case["status"] = "verified_by_matching_check"
            updated = True
    if updated:
        state.remember(project, learning_namespace(agent), KEY, cases[-MAX_CASES:])
    return updated


def useful_lessons(state, project, agent):
    memory = state.recall(project, learning_namespace(agent))
    lessons = memory.get(KEY, [])
    if not isinstance(lessons, list):
        return []
    # Give verifiable evidence priority, followed by recurring observations.
    valid = [row for row in lessons if isinstance(row, dict) and row.get("kind") in RULES]
    return sorted(valid, key=lambda row: (
        row.get("status") != "verified_by_matching_check", -row.get("occurrences", 0)
    ))[:8]
