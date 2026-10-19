"""Proof that a UI specialist actually saw a screenshot of the current work."""
VISUAL_AGENTS = frozenset(("ui-ux-designer", "horizon-ui-guardian"))


def requires_visual_review(task):
    payload = task.get("payload") or {}
    configured = payload.get("visual_review_required")
    if configured is None:
        return task.get("agent") in VISUAL_AGENTS
    if type(configured) is not bool:
        raise ValueError("visual_review_required must be boolean")
    return configured


def visual_review_status(history):
    """Only post-edit browser PNG evidence *followed by* real image inspection qualifies."""
    if not isinstance(history, list):
        return {"verified": False, "reason": "missing_browser_capture"}
    last_edit = -1
    for index, action in enumerate(history):
        if isinstance(action, dict) and action.get("action") == "write_file":
            # Any write requires fresh visual inspection afterwards.
            last_edit = index
    last_capture = -1
    for index in range(last_edit + 1, len(history)):
        action = history[index]
        if not isinstance(action, dict) or action.get("action") != "run_check":
            continue
        outcome = action.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("check") != "browser-playwright":
            continue
        screenshots = outcome.get("screenshots")
        if isinstance(screenshots, list) and screenshots and all(
                isinstance(s, dict) and isinstance(s.get("sha256"), str)
                and len(s["sha256"]) == 64 and isinstance(s.get("path"), str)
                for s in screenshots):
            last_capture = index
    if last_capture < 0:
        return {"verified": False, "reason": "missing_post_edit_browser_capture"}
    for index in range(last_capture + 1, len(history)):
        action = history[index]
        if (isinstance(action, dict) and action.get("action") == "inspect_image"
                and isinstance(action.get("outcome"), dict)
                and action["outcome"].get("actual_image_provided") is True):
            return {"verified": True, "reason": "real_browser_image_inspected"}
    return {"verified": False, "reason": "missing_visual_image_inspection"}
