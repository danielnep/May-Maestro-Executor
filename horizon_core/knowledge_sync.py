"""Publish only independently verified, non-sensitive incident lessons to GitHub.

GitHub's encrypted SQLite checkpoint remains the durable queue; this public
knowledge manifest stores only whitelisted procedural metadata, never source
files, raw errors, task objectives, customer identifiers or credentials.
"""
import base64
import json
import os
import re

from .github_sqlite_state import GitHubSQLiteState
from .github_state import GitHubState, GitHubStateError
from .learning import KEY, RULES
from .registry import AGENT_IDS

REPO = "danielnep/May-Maestro-Executor"
PATH = "agents/knowledge/verified-runtime-lessons.json"
MAX_ENTRIES = 256
HEX_ID = re.compile(r"^[0-9a-f]{16}$")
SAFE_CHECK = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
SAFE_ACTION = re.compile(r"^[A-Za-z_][A-Za-z_0-9]{0,47}$")


def verified_lessons(state):
    """Get a deterministic, privacy-minimal index from the local SQLite DB."""
    indexed = {}
    rows = state.db.execute(
        "SELECT agent,value FROM memory WHERE key=? AND agent LIKE '__agent_learning__:%'",
        (KEY,),
    ).fetchall()
    for row in rows:
        agent = row["agent"].removeprefix("__agent_learning__:")
        if agent not in AGENT_IDS:
            continue
        try:
            cases = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict) or case.get("status") != "verified_by_matching_check":
                continue
            fingerprint = case.get("id")
            kind = case.get("kind")
            action = case.get("action", "")
            check = case.get("check", "")
            if not isinstance(fingerprint, str) or not HEX_ID.fullmatch(fingerprint):
                continue
            if kind not in RULES or not isinstance(action, str) or not isinstance(check, str):
                continue
            if (action and not SAFE_ACTION.fullmatch(action)) or not SAFE_CHECK.fullmatch(check):
                continue
            occurrences = case.get("occurrences")
            if type(occurrences) is not int or not 1 <= occurrences <= 100000:
                continue
            key = (agent, fingerprint)
            value = {
                "agent": agent, "id": fingerprint, "kind": kind, "action": action,
                "check": check, "status": "verified_by_matching_check",
                "occurrences": occurrences, "rule": RULES[kind],
            }
            if key not in indexed or occurrences > indexed[key]["occurrences"]:
                indexed[key] = value
    return sorted(indexed.values(), key=lambda row: (row["agent"], row["id"]))


def merge_lessons(existing, new):
    if not isinstance(existing, dict) or existing.get("schema_version") != 1:
        raise ValueError("Invalid public lesson manifest")
    rows = existing.get("entries")
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise ValueError("Invalid lesson collection")
    combined = {}
    for entry in rows + new:
        if not isinstance(entry, dict):
            raise ValueError("Malformed lesson")
        agent, fingerprint, kind = entry.get("agent"), entry.get("id"), entry.get("kind")
        if agent not in AGENT_IDS or not isinstance(fingerprint, str) or not HEX_ID.fullmatch(fingerprint):
            raise ValueError("Invalid lesson identity")
        if kind not in RULES or entry.get("status") != "verified_by_matching_check":
            raise ValueError("Lesson is not independently verified")
        check, action, occurrences = entry.get("check"), entry.get("action"), entry.get("occurrences")
        if not isinstance(check, str) or not SAFE_CHECK.fullmatch(check) or not isinstance(action, str):
            raise ValueError("Invalid lesson metadata")
        if (action and not SAFE_ACTION.fullmatch(action)) or type(occurrences) is not int or not 1 <= occurrences <= 100000:
            raise ValueError("Invalid lesson metadata")
        key = (agent, fingerprint)
        previous = combined.get(key)
        if previous is None or occurrences >= previous["occurrences"]:
            combined[key] = {
                "agent": agent, "id": fingerprint, "kind": kind, "action": action,
                "check": check, "status": "verified_by_matching_check",
                "occurrences": occurrences, "rule": RULES[kind],
            }
    if len(combined) > MAX_ENTRIES:
        raise RuntimeError("Verified lesson registry full; archive and review, never silently drop")
    return {"schema_version": 1, "entries": sorted(combined.values(), key=lambda row: (row["agent"], row["id"]))}


def publish_verified_lessons(state, api=None):
    fresh = verified_lessons(state)
    if not fresh:
        return {"status": "no_verified_lessons", "count": 0}
    client = api or GitHubState(REPO, branch="agent-state/runtime")
    endpoint = "/contents/" + PATH
    old = client._request("GET", endpoint + "?ref=main", missing_ok=True)
    previous = {"schema_version": 1, "entries": []}
    if old is not None:
        if old.get("encoding") != "base64" or not isinstance(old.get("sha"), str):
            raise GitHubStateError("Malformed existing knowledge file")
        try:
            previous = json.loads(base64.b64decode("".join(old["content"].split())).decode("utf-8"))
        except (ValueError, TypeError, KeyError):
            raise GitHubStateError("Cannot decode existing knowledge file") from None
    result = merge_lessons(previous, fresh)
    if result == previous:
        return {"status": "unchanged", "count": len(result["entries"])}
    content = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    body = {"message": "knowledge: publish independently verified incident lessons",
            "branch": "main",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
    if old:
        body["sha"] = old["sha"]
    receipt = client._request("PUT", endpoint, body)
    if not receipt or not receipt.get("commit", {}).get("sha"):
        raise GitHubStateError("Knowledge commit not acknowledged")
    confirmed = client._request("GET", endpoint + "?ref=main")
    try:
        stored = base64.b64decode("".join(confirmed["content"].split())).decode("utf-8")
    except (ValueError, KeyError, TypeError):
        raise GitHubStateError("Knowledge verification failed") from None
    if stored != content:
        raise GitHubStateError("Knowledge readback mismatch")
    return {"status": "published", "count": len(result["entries"]),
            "commit": receipt["commit"]["sha"]}


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise RuntimeError("Knowledge publishing is restricted to the Horizon executor")
    state = GitHubSQLiteState()
    try:
        result = publish_verified_lessons(state)
        print(json.dumps(result, sort_keys=True))
    finally:
        state.close()


if __name__ == "__main__":
    main()
