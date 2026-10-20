"""KERNEL live browser handoff. Screenshots are NOT functional QA or May approval.

Dedicated project sessions only. No Playwright, CDP, personal ChatGPT session,
private app URLs, cookies or raw screenshot data in public task reports.
"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.onkernel.com"
NAME = "horizon-ronda-pro-live-audit"
URL = "https://danielnep.github.io/app-hondas-maxvel/"
LIVE_URL = re.compile(r"^https://[A-Za-z0-9.-]+\\.kernel\\.sh(?::8443)?/browser/live/[A-Za-z0-9_-]{4,90}$")


class KernelReviewError(RuntimeError):
    pass


def _request(method, path, token, data=None, opener=None):
    url = API + path
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json" if method != "POST" or not path.endswith("/screenshot")
                  else "image/png",
        "Content-Type": "application/json",
        "User-Agent": "Horizon-Kernel-Visual-Review/1",
    })
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=25) as response:
            payload = response.read(5000000)
            kind = response.headers.get("Content-Type", "")
            return payload if "image/png" in kind else json.loads(payload)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise KernelReviewError("kernel_http_" + str(exc.code)) from None
    except (OSError, TimeoutError, ValueError, urllib.error.URLError):
        raise KernelReviewError("kernel_transport_unavailable") from None


def _session(token, opener=None):
    name = urllib.parse.quote(NAME, safe="")
    existing = _request("GET", "/browsers/" + name, token, opener=opener)
    if existing is None:
        existing = _request("POST", "/browsers", token, {
            "name": NAME, "start_url": URL, "headless": False,
            "viewport": {"width": 390, "height": 844},
            "timeout_seconds": 21600}, opener=opener)
    if not isinstance(existing, dict):
        raise KernelReviewError("kernel_session_unavailable")
    live = existing.get("browser_live_view_url")
    session_id = existing.get("session_id")
    if (not isinstance(live, str) or not LIVE_URL.fullmatch(live)
            or not isinstance(session_id, str)
            or not re.fullmatch(r"[a-z0-9]{10,40}", session_id)):
        raise KernelReviewError("kernel_session_invalid")
    return session_id, live


def initiate_review(task, environ=None, opener=None):
    """Produce a LIVE viewer link, not a synthetic passing browser check."""
    if (not isinstance(task, dict)
            or task.get("project") != "chat:a-ronda-pro-auditoria-total-20261019"
            or task.get("repository") not in (None, "danielnep/app-hondas-maxvel")):
        raise KernelReviewError("project_not_approved")
    token = (os.environ if environ is None else environ).get("KERNEL_API_KEY")
    if not token:
        return {"check": "kernel-live", "passed": False,
                "review_status": "kernel_key_unavailable", "tests_run": 0}
    sid, url = _session(token, opener=opener)
    # The current live view is available to the owner for physical interaction.
    # A screenshot alone NEVER grants QA approval or unblocks specialists.
    return {"check": "kernel-live", "passed": False,
            "review_status": "awaiting_may_visual_review",
            "browser_live_view_url": url, "session_id": sid,
            "tests_run": 0, "reviewed_by_may": False}
