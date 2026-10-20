"""Real visual QA needs a KERNEL browser and authenticated May review."""
VISUAL_AGENTS = frozenset((
    "frontend-engineer", "ui-ux-designer",
    "horizon-ui-guardian", "mobile-app-builder",
))


def requires_visual_review(task):
    override = (task.get("payload") or {}).get("visual_review_required")
    if override is not None:
        if type(override) is not bool:
            raise ValueError("visual_review_required must be a boolean")
        return override
    return task.get("agent") in VISUAL_AGENTS


def visual_review_status(history):
    """Only controller-validated KERNEL review counts after the latest code edit."""
    if not isinstance(history, list):
        return {"verified": False, "reason": "kernel_live_review_missing"}
    last_edit = max((i for i, item in enumerate(history)
                     if isinstance(item, dict) and item.get("action") == "write_file"),
                    default=-1)
    for row in reversed(history[last_edit + 1:]):
        if not isinstance(row, dict) or row.get("action") != "kernel_review_verified":
            continue
        outcome = row.get("outcome") or {}
        if (isinstance(outcome, dict) and outcome.get("passed") is True
                and outcome.get("source") == "authenticated_owner_review"
                and type(outcome.get("verified_scenarios")) is int
                and outcome["verified_scenarios"] >= 2):
            return {"verified": True, "reason": "kernel_live_may_reviewed"}
    return {"verified": False, "reason": "kernel_live_review_missing"}
