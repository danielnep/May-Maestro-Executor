"""Native KERNEL live-view handoff, not an automated visual approval.

Only public GitHub Pages of owner-authorized source repositories. Session IDs
and links come from the current KERNEL API. No Playwright or Chrome CDP.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.onkernel.com"
LIVE = re.compile(r"^https://[A-Za-z0-9.-]+\.kernel\.sh(?::8443)?/browser/live/[A-Za-z0-9_-]{4,90}$")
REPOSITORY = re.compile(r"^danielnep/([A-Za-z0-9_.-]{2,100})$")


class KernelReviewError(RuntimeError):
    """Only a sanitized reason, never credentials or remote body."""


def _request(method, path, token, data=None, opener=None):
    request = urllib.request.Request(API + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        method=method, headers={"Authorization": "Bearer " + token,
                                "Accept": "application/json",
                                "Content-Type": "application/json",
                                "User-Agent": "Horizon-Live-Kernel/1"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise KernelReviewError("kernel_http_" + str(exc.code)) from None
    except (OSError, TimeoutError, ValueError, urllib.error.URLError):
        raise KernelReviewError("kernel_transport_unavailable") from None


def prepare_live_review(task, *, environ=None, opener=None):
    """Return a current live viewer URL; NEVER fabricate success from its existence."""
    if not isinstance(task, dict) or not task.get("project", "").startswith("chat:"):
        raise KernelReviewError("task_scope_invalid")
    payload = task.get("payload") or {}
    if not isinstance(payload, dict):
        raise KernelReviewError("task_payload_invalid")
    repo = payload.get("repository")
    match = REPOSITORY.fullmatch(repo) if isinstance(repo, str) else None
    if not match:
        return {"check": "kernel-live", "passed": False,
                "review_status": "preview_not_authorized"}
    name = "horizon-visual-" + match.group(1).lower().replace(".", "-")
    # Legacy dedicated Ronda session is discoverable without any hardcoded ID.
    if match.group(1) == "app-hondas-maxvel":
        name = "horizon-ronda-pro-live-audit"
    target = "https://danielnep.github.io/" + match.group(1) + "/"
    key = (os.environ if environ is None else environ).get("KERNEL_API_KEY")
    if not key:
        return {"check": "kernel-live", "passed": False,
                "review_status": "kernel_key_unavailable"}
    route = "/browsers/" + urllib.parse.quote(name, safe="")
    browser = _request("GET", route, key, opener=opener)
    if browser is None:
        browser = _request("POST", "/browsers", key,
            {"name": name, "start_url": target,
             "headless": False, "timeout_seconds": 21600,
             "viewport": {"width": 390, "height": 844}}, opener=opener)
    if not isinstance(browser, dict):
        raise KernelReviewError("kernel_session_unavailable")
    link, session_id = browser.get("browser_live_view_url"), browser.get("session_id")
    if (not isinstance(link, str) or not LIVE.fullmatch(link)
            or not isinstance(session_id, str)
            or not re.fullmatch("[a-z0-9]{10,40}", session_id)):
        raise KernelReviewError("kernel_live_view_invalid")
    return {"check": "kernel-live", "passed": False,
            "review_status": "awaiting_may_visual_review",
            "browser_live_view_url": link, "session_id": session_id,
            "target_url": target, "tests_run": 0, "reviewed_by_may": False}
