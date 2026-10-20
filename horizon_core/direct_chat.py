"""Direct, authenticated specialist -> ChatGPT Central transport.

Does NOT use GitHub workers or GitHub Issues to transmit a message. Production
agent processes run wherever their task executor runs. Access to the owner's
browser therefore requires a separately configured, project-scoped KERNEL_API_KEY.
Browser cookies/CDP URLs must never leave KERNEL. Browser interaction happens
inside KERNEL via its documented Playwright REST executor; Python owns transport,
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

# This browser script is static and works with ONLY the predetermined chat.
# Never execute tool/model/page-provided JavaScript. All data is JSON encoded.
_BROWSER_CODE = r"""
const request = JSON.parse(REQUEST_JSON);
if (request.url !== "https://chatgpt.com/c/6ad68dab-1178-83e9-a409-7c7014281b68") {
  throw new Error("Central URL not approved");
}
// Named KERNEL executor owns exactly this tab. Other tabs may contain the
// user's unsent drafts or concurrent agents' in-progress work: never touch them.
const central = page;
if (central.url().split("?")[0] !== request.url) {
  await central.goto(request.url, {waitUntil: "domcontentloaded"});
} else if (request.mode === "read") {
  await central.reload({waitUntil: "domcontentloaded"});
}
const input = central.getByRole("textbox", {name: "Ask ChatGPT"});
try {
  await input.waitFor({state: "visible", timeout: 20000});
} catch (_) {
  return {status: "not_authenticated"};
}
const region = central.getByRole("region", {name: "Conversation"});
try {
  await region.waitFor({state: "visible", timeout: 20000});
  await central.getByRole("heading", {name: "You said:"}).first()
       .waitFor({state: "attached", timeout: 20000});
} catch (_) {
  return {status: "conversation_unavailable"};
}
const marker = "TICKET: " + request.ticket;
let transcript = await region.innerText();
let found = transcript.includes(marker);
if (!found && request.mode === "read") {
  return {status: "not_found"};
}
if (!found && request.mode === "send") {
  if ((await input.innerText()).trim()) return {status: "composer_busy"};
  const prompt = [
    "[HORIZON CONSULTA DIRETA V1]",
    "AGENTE: " + request.agent,
    "TICKET: " + request.ticket,
    "TIPO: " + request.reason,
    "PERGUNTA: " + request.question,
    "Responda tecnicamente e de forma curta. Quando houver uma ação permitida",
    "responda somente JSON com action, args e summary. Sem inventar testes",
    "ou ampliar permissões. Sem acesso a segredos ou dados de clientes."
  ].join("\n");
  await input.fill(prompt);
  await central.getByRole("button", {name: "Send", exact: true}).click();
  // React updates asynchronously; a fast immediate DOM read is inconclusive.
  try {
    await central.waitForFunction((m) => document.body.innerText.includes(m),
                                  marker, {timeout: 12000});
  } catch (_) {
    return {status: "send_unconfirmed"};
  }
}
try {
  await central.getByRole("button", {name: "Stop"}).waitFor({state: "hidden", timeout: 30000});
} catch (_) {
  // A slow response is pending, not a license to send again.
}
// Old chats hydrate replies after user messages. Wait for the specific
// ticket's following assistant turn, rather than mistaking partial hydration
// for a completed but answerless consultation.
try {
  await central.waitForFunction((marker) => {
    const root = document.querySelector('[role="region"][aria-label="Conversation"]');
    if (!root) return false;
    const hs = [...root.querySelectorAll("h4")].filter(
      h => ["You said:", "ChatGPT said:"].includes((h.textContent || "").trim()));
    return hs.some((h, i) => (h.textContent || "").trim() === "You said:"
      && (h.parentElement?.innerText || "").includes(marker)
      && hs[i + 1]?.textContent.trim() === "ChatGPT said:"
      && (hs[i + 1]?.parentElement?.innerText || "")
           .replace(/^ChatGPT said:\s*/, "").trim().length > 0);
  }, marker, {timeout: 12000});
} catch (_) {}
const transcriptState = await region.evaluate((root, marker) => {
  const headings = Array.from(root.querySelectorAll("h4")).filter(
    h => ["You said:", "ChatGPT said:"].includes((h.textContent || "").trim()));
  for (let i = headings.length - 1; i >= 0; i--) {
    const heading = headings[i];
    if ((heading.textContent || "").trim() !== "You said:"
        || !(heading.parentElement?.innerText || "").includes(marker)) continue;
    const following = headings[i + 1];
    if (!following || following.textContent.trim() !== "ChatGPT said:")
      return {status: "sent"};
    const response = (following.parentElement?.innerText || "")
       .replace(/^ChatGPT said:\s*/, "").trim().slice(0, 3000);
    return response ? {status: "answered", answer: response} : {status: "sent"};
  }
  return {status: "send_unconfirmed"};
}, marker);
return transcriptState;

"""


class DirectChatError(RuntimeError):
    """Safe, bounded category; never contains key, cookies, request or response body."""


def configured(environ=None):
    env = os.environ if environ is None else environ
    return bool(env.get("KERNEL_API_KEY"))


def _browser_code(agent, key, question, reason, mode):
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
    packet = {"url": CENTRAL_URL, "agent": agent, "ticket": key,
              "question": question if mode == "send" else "",
              "reason": reason, "mode": mode}
    # JSON string rather than code concatenation prohibits JS injection.
    return _BROWSER_CODE.replace("REQUEST_JSON", json.dumps(
        json.dumps(packet, ensure_ascii=False), ensure_ascii=False))


def execute(agent, key, question="", reason="specialist_question", *,
            mode="send", environ=None, opener=None):
    """Use an authenticated, named KERNEL browser, never raw CDP credentials.

    A failure to confirm the send MUST NOT cause callers to post a second message
    through GitHub or retry with another ticket: ambiguous delivery is pending.
    """
    env = os.environ if environ is None else environ
    secret = env.get("KERNEL_API_KEY")
    if not secret:
        return {"status": "not_configured"}
    code = _browser_code(agent, key, question, reason, mode)
    data = json.dumps({"code": code, "executor": "horizon-central",
                       "timeout_sec": MAX_TIMEOUT}).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data, method="POST", headers={
        "Authorization": "Bearer " + secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Horizon-Direct-Chat/1"})
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=MAX_TIMEOUT + 9) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Never copy server bodies: upstream error pages may contain identifiers.
        raise DirectChatError("kernel_http_" + str(exc.code)) from None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        # Timeout could have occurred AFTER the click; preserve ticket.
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    if not isinstance(result, dict) or result.get("success") is not True:
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    payload = result.get("result")
    if not isinstance(payload, dict):
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    status = payload.get("status")
    allowed = {"not_authenticated", "conversation_unavailable", "composer_busy",
               "not_found", "send_unconfirmed", "pending_interleaved", "sent", "answered"}
    if status not in allowed:
        return {"status": "delivery_unknown", "ticket": key, "agent": agent}
    out = {"status": status, "ticket": key, "agent": agent}
    if status == "answered":
        advice = payload.get("answer")
        if not isinstance(advice, str) or not advice.strip():
            return {"status": "delivery_unknown", "ticket": key, "agent": agent}
        out["answer"] = advice[:MAX_REPLY]
    return out

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

