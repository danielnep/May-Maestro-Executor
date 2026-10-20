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
    """Bound the serialized request, including escaping, field count and keys.

    Allocate a separate share to every recent action so old output cannot
    evict the most recent tool result. Originals remain untouched on disk.
    """
    if not isinstance(history, list):
        return []
    recent = [row for row in history[-MAX_RECENT_ACTIONS:] if isinstance(row, dict)]
    if not recent:
        return []
    budget = MAX_HISTORY_TOTAL_CHARS // len(recent) - 4
    answer = []
    priority = ('check', 'passed', 'tool_error', 'reason', 'path', 'source',
                'simulated', 'workspace_unchanged', 'content', 'output', 'stdout',
                'stderr', 'body', 'html', 'screenshot')
    for record in recent:
        outcome = record.get('outcome', {})
        if not isinstance(outcome, dict):
            outcome = {'result': type(outcome).__name__}
        element = {'action': str(record.get('action', ''))[:70],
                   'summary': str(record.get('summary', ''))[:180], 'outcome': {}}
        keys = [key for key in priority if key in outcome]
        keys.extend(key for key in outcome if key not in priority)
        for original_key in keys:
            key = str(original_key)[:100]
            value = outcome[original_key]
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            if len(encoded) > MAX_HISTORY_TEXT_CHARS:
                value = _slice(encoded, MAX_HISTORY_TEXT_CHARS)
            element['outcome'][key] = value
            if len(json.dumps(element, ensure_ascii=False, default=str)) <= budget:
                continue
            # Measure the final JSON representation: quotes, backslashes and
            # control characters can expand far beyond the raw string length.
            text = value if isinstance(value, str) else encoded
            lo, hi = 0, min(len(text), MAX_HISTORY_TEXT_CHARS)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                element['outcome'][key] = _slice(text, mid)
                if len(json.dumps(element, ensure_ascii=False, default=str)) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            element['outcome'][key] = _slice(text, lo)
            if len(json.dumps(element, ensure_ascii=False, default=str)) > budget:
                del element['outcome'][key]
            break
        answer.append(element)
    return answer
