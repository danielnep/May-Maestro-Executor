"""Maestro's deterministic authorization gate for costlier model reasoning.

Only actual tool/check failures and resumed task evidence can justify a
higher reasoning level. Model-authored summaries and user-provided adjectives
are NOT authorization. No automatic upgrade to a more expensive Pro model.
"""
from dataclasses import dataclass

HARD_CHECKS = frozenset(("python-unittest","node-test","android-gradle","browser-playwright"))
RISK_TERMS = ("concorrên", "concorrenc", "deadlock", "race condition", "transaç",
              "transac", "migraç", "migrac", "idempotên", "idempotenc",
              "seguranç", "seguranc", "arquitet", "integridade",
              "falha intermitente", "distributed", "intermitent")


@dataclass(frozen=True)
class EffortDecision:
    effort: str
    approved: bool
    reason: str
    evidence: tuple
    approver: str = "maestro-policy-v1"

    def public(self):
        return {"effort": self.effort, "approved": self.approved,
                "reason": self.reason, "evidence": list(self.evidence),
                "approver": self.approver}


def decide(context):
    """Never treat a narrative or a failed model response as test proof."""
    if not isinstance(context, dict):
        return EffortDecision("low", False, "missing_trusted_context", ())
    history = context.get("history") or []
    if not isinstance(history, list):
        return EffortDecision("low", False, "invalid_history", ())
    # Truncated model context is no substitute for the persisted checkpoint.
    failed = []
    changed = False
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("action") == "write_file":
            outcome = item.get("outcome")
            if isinstance(outcome, dict) and outcome.get("sha256") and not outcome.get("error"):
                changed = True
        if item.get("action") != "run_check":
            continue
        outcome = item.get("outcome")
        if (not isinstance(outcome, dict) or outcome.get("check") not in HARD_CHECKS
                or outcome.get("source") != "runtime" or outcome.get("simulated") is not False
                or outcome.get("workspace_unchanged") is not True
                or outcome.get("passed") is not False):
            continue
        ident = outcome["check"]
        if ident not in failed:
            failed.append(ident)
    if not failed:
        return EffortDecision("low", False, "no_verified_failed_check", ())
    # A single failed check and a code correction still do not establish that
    # stronger reasoning is required. Need repeated diagnostic evidence.
    evidence = tuple(failed)
    if len(evidence) < 2:
        return EffortDecision("low", False, "investigate_locally_before_escalation", evidence)
    if not changed:
        return EffortDecision("low", False, "no_confirmed_correction_attempt", evidence)
    objective = str(context.get("objective") or "").casefold()
    critical = any(term in objective for term in RISK_TERMS)
    if critical and len(evidence) >= 3:
        return EffortDecision("max", True, "critical_multisystem_failure_confirmed", evidence)
    return EffortDecision("high", True, "multiple_independent_checks_failed_after_edit", evidence)


def select_gemini_level(approval, model):
    if model == "gemini-3.5-flash-lite":
        return "minimal"
    # Does not swap to a paid Pro model.
    return "high" if approval.approved else "low"


def select_deepseek_mode(approval):
    if approval.approved:
        return {"thinking": {"type": "enabled"},
                "reasoning_effort": approval.effort}
    return {"thinking": {"type": "disabled"}}


def describe_for_maestro():
    return (
        "Approve stronger reasoning only after 2+ distinct real, unsimulated "
        "failing checks and at least one actual source correction. Mark 'max' "
        "only for critical architecture, data integrity, migration or security "
        "when 3+ distinct checks fail. Do not escalate for a first attempt, "
        "wording such as 'hard', or repeated identical errors. "
        "Try local deterministic diagnostics and verified memory first. "
        "Record the decision and never automatically buy a Pro model."
    )
