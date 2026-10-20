"""Direct, authenticated specialist -> ChatGPT Central transport.

Does NOT use GitHub workers or GitHub Issues to transmit a message. Production
agent processes run wherever their task executor runs. Access to the owner's
browser therefore requires a separately configured, project-scoped KERNEL_API_KEY.
Browser cookies/CDP URLs must never leave KERNEL. Browser inspection uses KERNEL Playwright read-only; send uses native Computer
Action endpoints (or OS input within the browser VM). Python owns transport,
authorization, idempotency and response validation. This is NOT the OpenAI API.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .registry import AGENT_IDS

CENTRAL_URL = "https://chatgpt.com/c/6ad68dab-1178-83e9-a409-7c7014281b68"
BROWSER_NAME = "may-chatgpt-72h"
ENDPOINT = ("https://api.onkernel.com/browsers/"
            + urllib.parse.quote(BROWSER_NAME, safe="")
            + "/playwright/execute")
TICKET_PATTERN = re.compile(r"^[a-f0-9]{16,32}$")
MAX_MESSAGE = 1400
MAX_REPLY = 3000
MAX_TIMEOUT = 53

# Playwright is used for read-only UI inspection and locating the composer.
# The actual send goes through KERNEL Computer Action or local OS input.
# This is a fixed, operator-approved conversation and never a ChatGPT API.
_BROWSER_CODE = r"""
const request = JSON.parse(REQUEST_JSON);
if (request.url !== "https://chatgpt.com/c/6ad68dab-1178-83e9-a409-7c7014281b68") {
  throw new Error("Central URL not approved");
}
const central = page;
const current = central.url().split("?")[0];
if (current !== request.url) {
  if (current !== "about:blank" || request.stage !== "prepare")
    return {status: "conversation_unavailable"};
  await central.goto(request.url, {waitUntil: "domcontentloaded"});
}
try {
  await central.getByRole("textbox", {name: "Ask ChatGPT"})
    .waitFor({state: "visible", timeout: 20000});
} catch (_) {
  return {status: "not_authenticated"};
}
const region = central.getByRole("region", {name: "Conversation"});
try {
  await region.waitFor({state: "visible", timeout: 20000});
} catch (_) {
  return {status: "conversation_unavailable"};
}
const input = central.getByRole("textbox", {name: "Ask ChatGPT"});
const marker = "TICKET: " + request.ticket;
const reply = await region.evaluate((root, marker) => {
  const headings = Array.from(root.querySelectorAll("h4")).filter(
    h => ["You said:", "ChatGPT said:"].includes((h.textContent || "").trim()));
  for (let i = headings.length - 1; i >= 0; i--) {
    const item = headings[i];
    if ((item.textContent || "").trim() !== "You said:"
        || !(item.parentElement?.innerText || "").includes(marker)) continue;
    const following = headings[i + 1];
    if (!following || (following.textContent || "").trim() !== "ChatGPT said:")
      return {status: "sent"};
    const text = (following.parentElement?.innerText || "")
      .replace(/^ChatGPT said:\s*/, "").trim().slice(0, 3000);
    return text ? {status: "answered", answer: text} : {status: "sent"};
  }
  return null;
}, marker);
if (reply) return reply;
if (request.stage === "read") return {status: "not_found"};
const draft = (await input.innerText()).trim();
if (request.stage === "draft")
  return {status: draft === request.prompt.trim() ? "draft_ready" : "draft_mismatch"};
if (request.stage !== "prepare") return {status: "conversation_unavailable"};
if (draft) return {status: "composer_busy"};
await central.bringToFront();
const box = await input.boundingBox();
if (!box || box.width < 10 || box.height < 10)
  return {status: "composer_unavailable"};
return {status: "ready", box: {
  x: Math.round(box.x + box.width / 2),
  y: Math.round(box.y + box.height / 2)}};
"""

class DirectChatError(RuntimeError):
    """Safe, bounded category; never contains key, cookies, request or response body."""


def configured(environ=None):
    env = os.environ if environ is None else environ
    return bool(env.get("KERNEL_API_KEY"))


def _prompt(agent, key, question, reason):
    return "\n".join((
        "[HORIZON CONSULTA DIRETA V1]",
        "AGENTE: " + agent,
        "TICKET: " + key,
        "TIPO: " + reason,
        "PERGUNTA: " + question,
        "Responda tecnicamente e de forma curta. Quando houver uma ação permitida",
        "responda somente JSON com action, args e summary. Sem inventar testes",
        "ou ampliar permissões. Sem acesso a segredos ou dados de clientes.",
    ))


def _browser_code(agent, key, question, reason, mode, *, stage=None):
    if agent not in AGENT_IDS:
        raise ValueError("Unregistered specialist")
    if not isinstance(key, str) or not TICKET_PATTERN.fullmatch(key):
        raise ValueError("Invalid consultation ticket")
    if mode not in ("read", "send"):
        raise ValueError("Invalid browser mode")
    if mode == "send" and (not isinstance(question, str)
                           or not 12 <= len(question) <= MAX_MESSAGE):
        raise ValueError("Invalid public question")
    if reason not in ("specialist_question", "providers_unavailable", "checkin"):
        raise ValueError("Invalid consultation reason")
    phase = stage or ("read" if mode == "read" else "prepare")
    if phase not in ("read", "prepare", "draft"):
        raise ValueError("Invalid browser inspection stage")
    packet = {"url": CENTRAL_URL, "agent": agent, "ticket": key,
              "question": question if mode == "send" else "",
              "reason": reason, "stage": phase,
              "prompt": _prompt(agent, key, question, reason) if mode == "send" else ""}
    # JSON string rather than code concatenation prohibits JS injection.
    return _BROWSER_CODE.replace("REQUEST_JSON", json.dumps(
        json.dumps(packet, ensure_ascii=False), ensure_ascii=False))


def _kernel_post(suffix, data, secret, call, *, expect_json=False):
    """Authorized KERNEL control surface, never ChatGPT endpoints or cookies."""
    url = ("https://api.onkernel.com/browsers/"
           + urllib.parse.quote(BROWSER_NAME, safe="") + suffix)
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
        method="POST", headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Horizon-Direct-Chat/1"})
    with call(req, timeout=MAX_TIMEOUT + 9) as response:
        raw = response.read()
    return json.loads(raw) if expect_json else None


def _status(payload, agent, key):
    if not isinstance(payload, dict):
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    status = payload.get("status")
    allowed = {"not_authenticated", "conversation_unavailable", "composer_busy",
               "composer_unavailable", "not_found", "send_unconfirmed",
               "pending_interleaved", "sent", "answered"}
    if status not in allowed:
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    out = {"status": status, "ticket": key, "agent": agent}
    if status == "answered":
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return {"status": "delivery_unknown", "ticket": key, "agent": agent}
        out["answer"] = answer[:MAX_REPLY]
    return out


def execute(agent, key, question="", reason="specialist_question", *,
            mode="send", environ=None, opener=None):
    """Send through native KERNEL computer controls; inspect only via Playwright.

    Once the composer was touched, any failure is ambiguous: never retry or
    switch transports without verifying the same ticket in the existing chat.
    """
    env = os.environ if environ is None else environ
    secret = env.get("KERNEL_API_KEY")
    if not secret:
        return {"status": "not_configured"}
    call = opener or urllib.request.urlopen
    prompt = _prompt(agent, key, question, reason) if mode == "send" else ""
    def inspect(stage):
        body = {"code": _browser_code(agent, key, question, reason, mode, stage=stage),
                "executor": "horizon-central", "timeout_sec": MAX_TIMEOUT}
        result = _kernel_post("/playwright/execute", body, secret, call, expect_json=True)
        return result.get("result") if (isinstance(result, dict)
                and result.get("success") is True) else None
    try:
        initial = inspect("read" if mode == "read" else "prepare")
        if mode == "read" or not isinstance(initial, dict) or initial.get("status") != "ready":
            return _status(initial, agent, key)
        box = initial.get("box") or {}
        if not isinstance(box.get("x"), int) or not isinstance(box.get("y"), int):
            return _status(None, agent, key)
        _kernel_post("/computer/click_mouse", {"x": box["x"], "y": box["y"]},
                     secret, call)
        _kernel_post("/computer/type_text", {"text": prompt}, secret, call)
        draft = inspect("draft")
        if not isinstance(draft, dict) or draft.get("status") != "draft_ready":
            # Leave the text exactly as it is. Never erase or submit partial work.
            return _status(None, agent, key)
        _kernel_post("/computer/press_key", {"keys": ["Return"]}, secret, call)
        # A confirmed click is not proof that ChatGPT accepted the message.
        posted = inspect("read")
        return _status(posted if posted and posted.get("status") != "not_found"
                       else {"status": "send_unconfirmed"}, agent, key)
    except urllib.error.HTTPError as exc:
        # HTTP errors can arrive after a physical action: never auto-resend.
        if mode == "read":
            raise DirectChatError("kernel_http_" + str(exc.code)) from None
    except (urllib.error.URLError, OSError, ValueError, TypeError, TimeoutError):
        pass
    return _status(None, agent, key)

def execute_local(agent, key, question="", reason="specialist_question", *,
                  mode="send", runner=None):
    """Run from the SAME KERNEL browser VM, as in the proven external test.

    This endpoint is loopback inside KERNEL, not the GitHub runner's localhost.
    It uses Playwright Core over Chrome DevTools Protocol in a subordinate Node
    process; Python handles the external agent contract. Never start Chrome,
    read cookies or copy a browser websocket URL into state.
    """
    browser_code = _browser_code(agent, key, question, reason, mode)
    js = """
const {chromium} = require('/usr/local/lib/node_modules/playwright-core');
(async()=> {
  const browser = await chromium.connectOverCDP(
    'http://127.0.0.1:9222', {timeout: 7000});
  const contexts = browser.contexts();
  if(contexts.length !== 1) throw Error('Unrecognized browser context');
  const context = contexts[0];
  const page = await context.newPage();
  let outcome;
  try {
    outcome = await (async()=>{ BROWSER_CODE })();
  } finally {
    await page.close();
  }
  // Disconnect only this worker process. Do not close Chrome or its profile.
  process.stdout.write(JSON.stringify(outcome) + '\\n', () => process.exit(0));
})().catch(e=>{
  process.stderr.write('kernel_local_unavailable\\n', () => process.exit(2));
});
""".replace("BROWSER_CODE", browser_code)
    call = runner or subprocess.run
    try:
        result = call(["node", "-e", js], capture_output=True, text=True,
                      timeout=MAX_TIMEOUT + 10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    if result.returncode:
        return {"status": "local_browser_unavailable", "ticket": key, "agent": agent}
    try:
        response = json.loads(result.stdout.strip())
    except (ValueError, TypeError):
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    if not isinstance(response, dict) or response.get("status") not in (
            "answered", "sent", "pending_interleaved", "send_unconfirmed",
            "not_found", "not_authenticated", "conversation_unavailable",
            "composer_busy"):
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    output = {"status": response["status"], "ticket": key, "agent": agent}
    if response["status"] == "answered":
        value = response.get("answer")
        if not isinstance(value, str) or not value:
            return {"status": "delivery_unknown", "ticket": key, "agent": agent}
        output["answer"] = value[:MAX_REPLY]
    return output

