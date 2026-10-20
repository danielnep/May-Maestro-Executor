"""Verify desktop AND mobile UI by real rendered DOM tests, without mandatory vision API."""
VISUAL_AGENTS = frozenset((
    "frontend-engineer", "ui-ux-designer",
    "horizon-ui-guardian", "mobile-app-builder",
))


def has_source_edits(history):
    """A read/remember/failed check does not count as a changed source file."""
    if not isinstance(history, list):
        return False
    for item in history:
        if not isinstance(item, dict) or item.get("action") != "write_file":
            continue
        outcome = item.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("tool_error"):
            continue
        before, after = outcome.get("before_sha256"), outcome.get("sha256")
        if isinstance(before, str) and isinstance(after, str) and before == after:
            continue
        return True
    return False


def requires_visual_review(task, history=None):
    override = (task.get("payload") or {}).get("visual_review_required")
    if override is not None:
        if type(override) is not bool:
            raise ValueError("visual_review_required must be a boolean")
        return override
    if task.get("agent") not in VISUAL_AGENTS:
        return False
    # Preserve conservative callers that cannot supply history. The worker
    # enforces post-edit proof only after source was actually changed.
    return True if history is None else has_source_edits(history)


def visual_review_status(history):
    if not isinstance(history, list):
        return {"verified": False, "reason": "browser_check_missing_after_edit"}
    last_edit = max((i for i, item in enumerate(history)
                     if isinstance(item, dict) and item.get("action") == "write_file"), default=-1)
    browser_checks = [item["outcome"] for item in history[last_edit + 1:]
                      if isinstance(item, dict) and item.get("action") == "run_check"
                      and isinstance(item.get("outcome"), dict)
                      and item["outcome"].get("check") == "browser-playwright"]
    if not browser_checks:
        return {"verified": False, "reason": "browser_check_missing_after_edit"}
    outcome = browser_checks[-1]
    if not (outcome.get("passed") is True and outcome.get("source") == "runtime"
            and outcome.get("simulated") is False and outcome.get("workspace_unchanged") is True):
        return {"verified": False, "reason": "browser_check_not_verified"}
    screenshots = outcome.get("screenshots")
    names = {row.get("file") for row in screenshots if isinstance(row, dict)
             and isinstance(row.get("sha256"), str) and len(row["sha256"]) == 64} if isinstance(screenshots, list) else set()
    # Flow-specific screenshots are optional evidence; two canonical final PNGs
    # must always exist, but a meticulous journey can retain more frames.
    if not {"mobile-rendered.png", "desktop-rendered.png"}.issubset(names):
        return {"verified": False, "reason": "two_viewports_required"}
    report = outcome.get("report") or {}
    layouts = report.get("layout", [])
    if (not isinstance(layouts, list)
            or {entry.get("name") for entry in layouts if isinstance(entry, dict)} != {"mobile", "desktop"}):
        return {"verified": False, "reason": "dom_geometry_missing"}
    if any(entry.get("blocking_issues") for entry in layouts):
        return {"verified": False, "reason": "layout_blocks_interaction"}
    if type(report.get("functional_scenarios")) is not int or report["functional_scenarios"] < 2:
        return {"verified": False, "reason": "real_interaction_missing"}
    return {"verified": True, "reason": "two_viewports_measured_and_interactions_passed",
            "vision_api_calls_required": 0}
