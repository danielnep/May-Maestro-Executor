"""Bounded, lossless-on-disk / small-on-model context windows.

Checkpoints keep full execution evidence in SQLite. A specialist receives only
recent relevant tool outcomes and compact project-scoped memory; tasks start fresh.
"""
import json

MAX_RECENT_ACTIONS = 4
MAX_MEMORY_ITEMS = 8
MAX_MEMORY_VALUE_CHARS = 1000
MAX_HISTORY_TEXT_CHARS = 4500
MAX_HISTORY_TOTAL_CHARS = 14000


def _slice(value, limit):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(value) <= limit else value[:limit] + " …[truncated; re-read by line range]"


def compact_memory(memory):
    if not isinstance(memory, dict):
        return {}
    result = {}
    # Latest project-specific evidence first. Full original remains persisted.
    for key, value in list(memory.items())[-MAX_MEMORY_ITEMS:]:
        if not isinstance(key, str):
            continue
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        result[key] = value if len(encoded) <= MAX_MEMORY_VALUE_CHARS else {
            "excerpt": _slice(encoded, MAX_MEMORY_VALUE_CHARS),
            "truncated": True}
    return result


def compact_history(history):
    if not isinstance(history, list):
        return []
    recent = history[-MAX_RECENT_ACTIONS:]
    answer = []
    remaining = MAX_HISTORY_TOTAL_CHARS
    for record in recent:
        if not isinstance(record, dict):
            continue
        outcome = record.get("outcome", {})
        if not isinstance(outcome, dict):
            outcome = {"result": str(type(outcome).__name__)}
        # Tools return structured metadata. No full copied page, test log or file
        # is needed in old steps. Latest tool gets a larger useful excerpt.
        formatted = {}
        limit = min(MAX_HISTORY_TEXT_CHARS if record is recent[-1] else 2000, remaining)
        for key, value in outcome.items():
            if key in ("content", "output", "stdout", "stderr", "body", "html", "screenshot"):
                formatted[key] = _slice(value, max(250, limit))
            else:
                encoded = json.dumps(value, ensure_ascii=False, default=str)
                formatted[key] = value if len(encoded) <= 1400 else _slice(encoded, 1400)
        element = {"action": str(record.get("action", ""))[:70],
                   "summary": str(record.get("summary", ""))[:180],
                   "outcome": formatted}
        serial = json.dumps(element, ensure_ascii=False, default=str)
        # Still preserve action/status if a previous entry exhausted the budget.
        if len(serial) > remaining and remaining < 350:
            element = {"action": element["action"], "outcome": {"truncated": True}}
            serial = json.dumps(element)
        remaining = max(0, remaining - len(serial))
        answer.append(element)
    return answer
