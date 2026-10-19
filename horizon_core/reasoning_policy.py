"""Deterministic routing and Maestro-controlled approval of costly model reasoning.

Model-generated text or a specialist's memory cannot grant elevation.
Only a scoped runtime receipt produced by the actual Maestro identity counts.
"""
import hashlib
import re

APPROVAL_NAMESPACE = "__maestro_reasoning_approvals__"
REVIEW_SUFFIX = ":reasoning-review"


def objective_sha(objective):
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


HARD_CHECKS = frozenset(("python-unittest", "node-test", "android-gradle", "browser-playwright"))


def failed_checks(history):
    """Count independent *runtime* failures only after a real source correction.

    Repeated unchanged checks, timeouts, and forged/model-supplied outcomes
    cannot authorize more expensive model inference.
    """
    corrected = False
    failed = set()
    if not isinstance(history, list):
        return 0
    for step in history:
        if not isinstance(step, dict):
            continue
        outcome = step.get("outcome")
        if not isinstance(outcome, dict):
            continue
        if step.get("action") == "write_file":
            old_sha, new_sha = outcome.get("before_sha256"), outcome.get("sha256")
            if (isinstance(old_sha, str) and isinstance(new_sha, str)
                    and len(old_sha) == 64 and len(new_sha) == 64
                    and old_sha != new_sha and not outcome.get("tool_error")):
                corrected = True
            continue
        if step.get("action") != "run_check" or not corrected:
            continue
        name, digest = outcome.get("check"), outcome.get("workspace_digest")
        if (name not in HARD_CHECKS or not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest.lower())
                or outcome.get("source") != "runtime"
                or outcome.get("simulated") is not False
                or outcome.get("workspace_unchanged") is not True
                or outcome.get("cleanup_ok") is not True
                or outcome.get("timed_out") is not False
                or outcome.get("passed") is not False
                or type(outcome.get("tests_run")) is not int
                or outcome["tests_run"] < 1
                or type(outcome.get("exit_code")) is not int
                or outcome["exit_code"] == 0):
            continue
        failed.add((name, digest))
    return len(failed)


def escalation_justified(task, history):
    if task.get("payload", {}).get("reasoning_approval_target"):
        return False
    return failed_checks(history) >= 2


def approval_receipt(state, task):
    receipt = state.recall(task["project"], APPROVAL_NAMESPACE).get(task["task_id"])
    if (not isinstance(receipt, dict) or receipt.get("by") != "may-maestro"
            or receipt.get("objective_sha") != objective_sha(task["objective"])
            or receipt.get("decision") not in ("approved", "denied")
            or not isinstance(receipt.get("reason"), str) or len(receipt["reason"]) < 12):
        return None
    return receipt


def select_model_effort(state, task, history):
    receipt = approval_receipt(state, task)
    if receipt and receipt["decision"] == "approved" and escalation_justified(task, history):
        return {"level": "high", "authorized_by": "may-maestro",
                "reason": receipt["reason"][:240]}
    return {"level": "low", "authorized_by": "routine_policy",
            "reason": "Keep standard calls low; Maestro approves deep reasoning"}


def create_reasoning_review(state, task, history):
    """Enqueue a review once; never ask for elevated thinking on text alone."""
    if task["agent"] == "may-maestro" or not escalation_justified(task, history):
        return None
    if approval_receipt(state, task):
        return None
    review_id = task["task_id"] + REVIEW_SUFFIX
    try:
        prior = state.get(review_id)
    except (KeyError, ValueError):
        prior = None
    if prior:
        return review_id
    state.enqueue(
        review_id, "may-maestro", task["project"],
        "Decide whether costly deep model reasoning is JUSTIFIED for task "
        + task["task_id"] + ". The runtime observed at least two distinct "
        "executed failing isolated checks. Inspect relevant evidence, prefer "
        "deterministic repairs and decide approved/denied. Use remember with key "
        "'reasoning-decision:" + task["task_id"] + "' and value "
        '{"approve":true or false,"reason":"specific technical justification"} '
        "before finish. Never approve from mere complexity claims.",
        payload={"reasoning_approval_target": task["task_id"],
                 "allowed_paths": [],
                 "branch": task.get("payload", {}).get("branch")},
        priority=min(100, task.get("priority", 0) + 1))
    return review_id


def save_maestro_decision(state, maestro_task, key, value):
    """Translate Maestro's bounded tool decision into a runtime-scoped receipt."""
    if maestro_task["agent"] != "may-maestro":
        return False
    target_id = maestro_task.get("payload", {}).get("reasoning_approval_target")
    if not target_id or key != "reasoning-decision:" + target_id:
        return False
    if not isinstance(value, dict) or type(value.get("approve")) is not bool:
        raise ValueError("Maestro must explicitly approve or deny deep reasoning")
    reason = value.get("reason")
    if not isinstance(reason, str) or not 12 <= len(reason) <= 240:
        raise ValueError("Deep reasoning requires a concrete bounded justification")
    target = state.get(target_id)
    if not target or target["project"] != maestro_task["project"]:
        raise ValueError("Target task not available in the same project")
    history = (target.get("checkpoint") or {}).get("history", [])
    if value["approve"] and not escalation_justified(target, history):
        raise ValueError("No verified repeated test failures justify escalation")
    state.remember(target["project"], APPROVAL_NAMESPACE, target_id,
                   {"by": "may-maestro", "decision": "approved" if value["approve"] else "denied",
                    "reason": reason, "objective_sha": objective_sha(target["objective"])})
    return True
