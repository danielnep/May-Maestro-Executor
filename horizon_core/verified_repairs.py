"""Deterministic replay of small, test-proven fixes on an identical source state.

No semantic guesses: project + agent + exact normalized objective + same
work branch + exact file SHA must match. Only the runtime can create entries.
A verified check is executed again before skipping the LLM.
"""
import hashlib
import json

NAMESPACE_PREFIX = "__verified_repairs__:"
KEY = "recipes_v1"
MAX_RECIPES = 12
MAX_FILE_BYTES = 32_000
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def signature(objective):
    if not isinstance(objective, str):
        raise ValueError("Invalid repair objective")
    return sha(" ".join(objective.split()).encode("utf-8"))


def namespace(agent):
    if not isinstance(agent, str) or not agent or len(agent) > 90:
        raise ValueError("Invalid repair agent")
    return NAMESPACE_PREFIX + agent


def _entries(state, project, agent):
    rows = state.recall(project, namespace(agent)).get(KEY, [])
    return rows if isinstance(rows, list) else []


def learn_from_verified_check(state, task, history):
    """Store one-file patch only after actual post-edit test evidence, not model claims."""
    writes = [(i, row) for i, row in enumerate(history)
              if isinstance(row, dict) and row.get("action") == "write_file"
              and isinstance(row.get("outcome"), dict)
              and row["outcome"].get("sha256")
              and row["outcome"].get("before_sha256")]
    if not writes:
        return False
    paths = {row["outcome"].get("path") for _, row in writes}
    if len(paths) != 1:
        return False
    last_pos, last = writes[-1]
    path = last["outcome"]["path"]
    # Unambiguous previous content: only a single recorded edit on this path
    # is reused; multi-step revisions remain valuable evidence but need a model.
    if len(writes) != 1 or not isinstance(path, str):
        return False
    previous_sha = last["outcome"]["before_sha256"]
    updated_sha = last["outcome"]["sha256"]
    if previous_sha == updated_sha:
        return False
    candidates = []
    for row in history[last_pos + 1:]:
        if not isinstance(row, dict):
            continue
        if row.get("action") == "write_file":
            return False
        if row.get("action") != "run_check":
            continue
        check = row.get("outcome")
        if not isinstance(check, dict):
            continue
        if check.get("passed") is not True:
            # A failing later check invalidates a previous pass.
            candidates = []
            continue
        if (check.get("source") != "runtime" or check.get("simulated") is not False
                or check.get("workspace_unchanged") is not True
                or type(check.get("tests_run")) is not int or check["tests_run"] < 1
                or check.get("exit_code") != 0
                or check.get("check") not in ("python-unittest", "node-test",
                                             "browser-playwright", "android-gradle")):
            continue
        candidates.append(check)
    if not candidates:
        return False
    workspace = state.recall(task["project"], "__workspace__").get(path, {})
    content = workspace.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return False
    if sha(content.encode("utf-8")) != updated_sha:
        return False
    if workspace.get("branch") != task["payload"].get("branch"):
        return False
    # Precondition must be the original observed content, not a guessed source.
    if workspace.get("expected_sha256") != previous_sha:
        return False
    check = candidates[-1]
    recipe = {
        "signature": signature(task["objective"]),
        "branch": workspace["branch"], "path": path, "before_sha256": previous_sha,
        "after_sha256": updated_sha, "content": content,
        "check": check["check"], "project_dir": check.get("project_dir", "."),
        "evidence": {"tests_run": check["tests_run"],
                     "workspace_digest": check.get("workspace_digest", "")[:64]},
        "source_task": task["task_id"], "status": "verified_by_isolated_check"}
    previous = [row for row in _entries(state, task["project"], task["agent"])
                if isinstance(row, dict) and row.get("signature") != recipe["signature"]]
    state.remember(task["project"], namespace(task["agent"]),
                   KEY, (previous + [recipe])[-MAX_RECIPES:])
    return True


def replay_if_exact(state, task, tools):
    """Return None when uncertain, else a verified tool trace; never call an LLM."""
    if task.get("depends_on") or not task["payload"].get("branch"):
        return None
    if not {"write_file", "run_check", "finish"}.issubset(set(tools.agent["tools"])):
        return None
    want = signature(task["objective"])
    matches = [r for r in reversed(_entries(state, task["project"], task["agent"]))
               if isinstance(r, dict) and r.get("signature") == want
               and r.get("branch") == task["payload"]["branch"]
               and r.get("status") == "verified_by_isolated_check"]
    for recipe in matches:
        try:
            path = tools.path(recipe.get("path"), write=True)
            content = recipe.get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                continue
            after_sha = sha(content.encode("utf-8"))
            if after_sha != recipe.get("after_sha256"):
                continue
            before = sha(path.read_bytes() if path.exists() else b"")
            if before not in (recipe.get("before_sha256"), after_sha):
                continue
            history = []
            if before != after_sha:
                result = tools.execute("write_file", {"path": recipe["path"],
                    "content": content, "expected_sha256": before})
                if result.get("sha256") != after_sha:
                    continue
                previous_write = state.recall(task["project"], "__workspace__").get(recipe["path"])
                origin = previous_write.get("expected_sha256") if isinstance(previous_write, dict) else before
                state.remember(task["project"], "__workspace__", recipe["path"],
                               {"content": content, "expected_sha256": origin,
                                "branch": task["payload"]["branch"]})
                history.append({"action": "write_file", "summary": "verified patch replay",
                                "outcome": {"path": recipe["path"], "sha256": after_sha,
                                            "before_sha256": before}})
            checked = tools.execute("run_check", {"name": recipe["check"],
                                               "project_dir": recipe.get("project_dir", ".")})
            history.append({"action": "run_check", "summary": "independent replay validation",
                            "outcome": checked})
            valid = (isinstance(checked, dict) and checked.get("passed") is True
                     and checked.get("source") == "runtime"
                     and checked.get("simulated") is False
                     and checked.get("workspace_unchanged") is True
                     and type(checked.get("tests_run")) is int
                     and checked["tests_run"] >= 1 and checked.get("exit_code") == 0)
            return {"verified": valid, "history": history,
                    "checks": [checked], "recipe_source": recipe.get("source_task")}
        except (ValueError, OSError, RuntimeError):
            # Check or environment unavailable: let normal reasoning diagnose it.
            return None
    return None
