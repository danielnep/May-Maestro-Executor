"""Native KERNEL live-view handoff, not an automated visual approval.

Only public GitHub Pages of owner-authorized source repositories. Session IDs
and links come from the current KERNEL API. No Playwright or Chrome CDP.
"""
import hashlib
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
    review_ticket = hashlib.sha256(
        ("kernel-live-v1|" + task["task_id"] + "|" + session_id).encode("utf-8")
    ).hexdigest()[:20]
    return {"check": "kernel-live", "passed": False,
            "ticket": review_ticket,
            "review_status": "awaiting_may_visual_review",
            "browser_live_view_url": link, "session_id": session_id,
            "target_url": target, "tests_run": 0, "reviewed_by_may": False}

CENTRAL = "https://chatgpt.com/c/6ad68dab-1178-83e9-a409-7c7014281b68"
CHAT_BROWSER = "may-chatgpt-72h"
_CHAT_CODE = r"""
const request = JSON.parse(REQUEST_JSON);
const tabId = await newTab(request.url);
await waitForLoad();
const already = await js((ticket) => document.body.innerText.includes(ticket), {arg: request.ticket});
if (already) {
  repl.write(JSON.stringify({status:"already_sent"}));
} else {
  const nodes = (await accessibilitySnapshot()).nodes;
  const input = nodes.find(n => n.role === "textbox" && /Ask ChatGPT|Mensagem/i.test(n.name));
  if (!input) {
    repl.write(JSON.stringify({status:"not_authenticated"}));
  } else {
    await fillInput(input, request.message);
    const sendNodes = (await accessibilitySnapshot()).nodes;
    const send = sendNodes.find(n => n.role === "button" && /^(Send|Enviar)$/i.test(n.name));
    if (!send) {
      repl.write(JSON.stringify({status:"composer_unavailable"}));
    } else {
      await click(send);
      const posted = await js((ticket) => document.body.innerText.includes(ticket), {arg: request.ticket});
      repl.write(JSON.stringify({status: posted ? "sent" : "send_unconfirmed"}));
    }
  }
}
"""


def send_live_review(review, task_id, *, environ=None, opener=None):
    """Send the exact live URL to the dedicated ChatGPT thread via KERNEL native REPL.

    A timed-out send is ambiguous: never send the same ticket again blindly.
    Does not use Playwright or the app browser as a chat transport.
    """
    if (not isinstance(review, dict) or
            review.get("review_status") != "awaiting_may_visual_review" or
            not isinstance(review.get("ticket"), str) or
            not LIVE.fullmatch(review.get("browser_live_view_url", ""))):
        return "not_ready"
    key = (os.environ if environ is None else environ).get("KERNEL_API_KEY")
    if not key:
        return "not_configured"
    message = ("[HORIZON REVISAO KERNEL V1]\\n"
               "AGENTE: horizon-ui-guardian\\n"
               "TICKET: " + review["ticket"] + "\\n"
               "TAREFA: " + task_id + "\\n"
               "NAVEGADOR AO VIVO: " + review["browser_live_view_url"] + "\\n"
               "May: abra este navegador, percorra o Ronda Pro em mobile e "
               "desktop; verifique gestos, ícones, estados e navegação. "
               "Não aprove apenas pelo link. Retorne os defeitos e o parecer.")
    packet = {"url": CENTRAL, "ticket": "TICKET: " + review["ticket"],
              "message": message}
    code = _CHAT_CODE.replace("REQUEST_JSON", json.dumps(
        json.dumps(packet, ensure_ascii=False), ensure_ascii=False))
    try:
        output = _request("POST",
            "/browsers/" + urllib.parse.quote(CHAT_BROWSER, safe="") + "/repl",
            key, {"code": code, "timeout_sec": 45}, opener=opener)
    except KernelReviewError:
        return "delivery_unknown"
    if not isinstance(output, dict) or output.get("success") is not True:
        return "delivery_unknown"
    for item in output.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            status = json.loads(item.get("text", "")).get("status")
        except (ValueError, AttributeError):
            continue
        if status in ("sent", "already_sent", "send_unconfirmed",
                      "not_authenticated", "composer_unavailable"):
            return status
    return "delivery_unknown"
