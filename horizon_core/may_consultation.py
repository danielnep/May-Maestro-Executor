"""Optional, bounded consultation for the 17 independent Horizon specialists.

The public GitHub issue is a transport, NOT the ChatGPT API. An independently
scheduled ChatGPT task answers authenticated issue requests, optionally using
one persistent, user-monitored chat. Never copy client data into public issues.
An owner reply can contain a strictly validated specialist tool action, but
cannot grant new file, repository or deployment permissions.
"""
import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from .registry import AGENT_IDS
from . import direct_chat

REPOSITORY = "danielnep/May-Maestro-Executor"
ISSUE_PREFIX = "[May consultation] "
REQUEST_MARKER = "MAY_CONSULT_REQUEST_V2"
REPLY_MARKER = "MAY_CONSULT_REPLY_V2"
LEGACY_REQUEST_MARKER = "MAY_CONSULT_REQUEST_V1"
LEGACY_REPLY_MARKER = "MAY_CONSULT_REPLY_V1"
API_URL = "https://api.github.com/repos/" + REPOSITORY
REASONS = frozenset(("providers_unavailable", "specialist_question"))
# Fixed operator-approved PUBLIC demo URLs only, never private app URLs from model text.
PUBLIC_PREVIEW_BY_PROJECT = {
    "ronda-pro": "https://danielnep.github.io/app-hondas-maxvel/",
}
MAX_QUESTION = 1400
MAX_ADVICE = 12000
MAX_ACTIONS_PER_REPLY = 5
CONSULTATIONS_PER_WINDOW = 4
CONSULTATION_WINDOW_SECONDS = 3600
SAFE_QUESTION = re.compile(r"^[\w \t.,:;!?()[\]{}+=/#\-À-ÿ\n]*$", re.UNICODE)
BLOCKED = re.compile(r"(?:\b(?:api[_ -]?key|secret|password|senha|token|bearer|cpf|cnpj|private[_ -]?key)\b|"
                     r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:sk-|AIza)[\w-]{12,})", re.I)


def ticket(task_id, agent=None, round_index=0):
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Invalid consultation task")
    if agent is not None and agent not in AGENT_IDS:
        raise ValueError("Unknown consultation specialist")
    if type(round_index) is not int or round_index < 0:
        raise ValueError("Invalid consultation round")
    # The first round deliberately preserves issued V2 ticket IDs.
    key = task_id if agent is None else "v2|" + agent + "|" + task_id
    if round_index:
        key += "|round:" + str(round_index)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _github(method, path, *, token=None, body=None):
    secret = token or os.environ.get("GITHUB_TOKEN")
    if not secret:
        raise RuntimeError("GitHub integration unavailable")
    headers = {"Authorization": "Bearer " + secret,
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "Horizon-May-Consultation"}
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if raw is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_URL + path, headers=headers, data=raw, method=method)
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)


def _find_issue(api, key, legacy=False):
    # Browse up to 500 issues to avoid a duplicate when the repository grows.
    # Stop on the first short page to minimize API calls and cost.
    marker = LEGACY_REQUEST_MARKER if legacy else REQUEST_MARKER
    for page in range(1, 6):
        rows = api("GET", "/issues?state=all&per_page=100&page=" + str(page))
        for row in rows:
            if (not row.get("pull_request")
                    and row.get("title") == ISSUE_PREFIX + key
                    and marker in (row.get("body") or "")):
                return row
        if len(rows) < 100:
            return None
    # Failure-closed on large histories: do not create duplicates.
    raise RuntimeError("Consultation search cap reached: verify existing ticket")


def _agent_cooldown(api, agent, now):
    """Sliding one-hour window shared by every task of the same specialist.

    GitHub issue creation timestamps are durable across worker restarts.
    This only counts authenticated V2 consultation requests, not retries.
    """
    cutoff = now - timedelta(seconds=CONSULTATION_WINDOW_SECONDS)
    recent = []
    for page in range(1, 6):
        rows = api("GET", "/issues?state=all&sort=created&direction=desc"
                   "&per_page=100&page=" + str(page))
        for row in rows:
            created_raw = row.get("created_at")
            if not created_raw:
                continue
            try:
                created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if created <= cutoff:
                # GitHub issues were requested in descending creation order.
                return _cooldown_seconds(recent, now)
            body = row.get("body") or ""
            if (not row.get("pull_request")
                    and (row.get("title") or "").startswith(ISSUE_PREFIX)
                    and REQUEST_MARKER in body
                    and ("\nagent: " + agent + "\n") in ("\n" + body)):
                recent.append(created)
        if len(rows) < 100:
            return _cooldown_seconds(recent, now)
    # If the repository was extremely busy, fail closed with a temporary wait.
    return CONSULTATION_WINDOW_SECONDS


def _cooldown_seconds(recent, now):
    if len(recent) < CONSULTATIONS_PER_WINDOW:
        return 0
    oldest = min(recent)
    remaining = (oldest + timedelta(seconds=CONSULTATION_WINDOW_SECONDS) - now).total_seconds()
    return max(60, min(CONSULTATION_WINDOW_SECONDS, int(remaining) + 1))


def _public_question(task, question, reason):
    """Only operator-approved public technical questions may leave the worker."""
    if reason == "providers_unavailable":
        return ("How can the named specialist safely resume a coding task when "
                "Gemini and DeepSeek are both unavailable? Return ONE conservative "
                "JSON tool decision or a recovery diagnosis; never invent tests.")
    if not isinstance(question, str) or not 12 <= len(question.strip()) <= MAX_QUESTION:
        return None
    # A model's assertion of safety is not authorization. The field is set by
    # the owner's task contract, not by the specialist's action arguments.
    allowed = task.get("payload", {}).get("may_public_context_allowed") is True
    if not allowed or BLOCKED.search(question) or not SAFE_QUESTION.fullmatch(question):
        return None
    return question.strip()



def _direct_budget(state, agent, key, now):
    """Four direct consultations per rolling hour, persisted with agent state."""
    data = state.recall("__runtime__", agent).get("may_direct_slots", [])
    recent = []
    for row in data:
        if isinstance(row, dict) and isinstance(row.get("at"), (int, float)):
            if 0 <= now.timestamp() - row["at"] < CONSULTATION_WINDOW_SECONDS:
                recent.append(row)
    if any(row.get("ticket") == key for row in recent):
        return 0, recent, True
    if len(recent) >= CONSULTATIONS_PER_WINDOW:
        oldest = min(row["at"] for row in recent)
        wait = max(60, min(CONSULTATION_WINDOW_SECONDS,
                           int(oldest + CONSULTATION_WINDOW_SECONDS - now.timestamp()) + 1))
        return wait, recent, False
    return 0, recent, False


def _direct_answer(answer):
    """An HTML chat response is untrusted just like a GitHub issue comment."""
    if not isinstance(answer, str) or not answer.strip():
        return None
    clean = answer.strip()[:MAX_ADVICE]
    if clean.startswith(chr(96)*3):
        lines = clean.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == chr(96)*3:
            clean = "\n".join(lines[1:-1]).strip()
    try:
        decision = json.loads(clean)
    except (ValueError, TypeError):
        decision = None
    if (isinstance(decision, dict)
            and set(decision) == {"action", "args", "summary"}
            and isinstance(decision["action"], str)
            and isinstance(decision["args"], dict)
            and isinstance(decision["summary"], str)):
        return {"decision_untrusted": decision, "advice_untrusted": "",
                "source": "chatgpt_web_untrusted"}
    return {"decision_untrusted": None, "advice_untrusted": clean,
            "source": "chatgpt_web_untrusted"}


def read_direct_consultation(task_id, *, agent, round_index=0, transport=None):
    """Read the existing browser conversation; never resend a pending ticket."""
    key = ticket(task_id, agent, round_index)
    call = transport or direct_chat.execute
    try:
        response = call(agent, key, reason="specialist_question", mode="read")
    except (direct_chat.DirectChatError, ValueError):
        return None
    if response.get("status") != "answered":
        return None
    parsed = _direct_answer(response.get("answer"))
    return None if parsed is None else {"ticket": key, "agent": agent, **parsed}


def request_consultation(task, reason, *, api=None, question=None, round_index=0,
                         now=None, state=None, direct_transport=None):
    """Idempotent request for any registered agent. No raw private task context."""
    agent = task.get("agent")
    if agent not in AGENT_IDS or reason not in REASONS:
        return {"status": "not_applicable"}
    public_question = _public_question(task, question, reason)
    if public_question is None:
        return {"status": "private_context_blocked"}
    try:
        key = ticket(task.get("task_id"), agent, round_index)
    except ValueError:
        return {"status": "invalid_consultation_round"}
    # Prefer direct ChatGPT contact when a KERNEL credential has been installed
    # into the running specialist executor. No GitHub worker/issue is involved.
    if api is None and (direct_transport is not None or direct_chat.configured()):
        moment = now or datetime.now(timezone.utc)
        if state is not None:
            wait, recent, already = _direct_budget(state, agent, key, moment)
            if wait and not already:
                return {"status": "cooldown", "agent": agent,
                        "retry_after_seconds": wait,
                        "window_seconds": CONSULTATION_WINDOW_SECONDS}
        else:
            # Never bypass rate-limit accounting for an unexpected caller.
            return {"status": "direct_state_required"}
        transport = direct_transport or direct_chat.execute
        try:
            direct = transport(agent, key, question=public_question,
                               reason=reason, mode="send")
        except (direct_chat.DirectChatError, ValueError):
            direct = {"status": "transport_unavailable"}
        direct_status = direct.get("status")
        if direct_status in ("sent", "answered", "pending_interleaved",
                             "send_unconfirmed", "delivery_unknown"):
            if not already:
                state.remember("__runtime__", agent, "may_direct_slots",
                               (recent + [{"ticket": key, "at": moment.timestamp()}])[-16:])
            # An ambiguous send is deliberately NOT re-posted via GitHub.
            return {"status": "direct_pending", "agent": agent,
                    "ticket": key, "transport": "kernel"}
        # A definitively unavailable browser may use the existing GitHub fallback.
    if not os.environ.get("GITHUB_TOKEN") and api is None:
        return {"status": "not_configured"}
    call = api or _github
    existing = _find_issue(call, key)
    if existing:
        return {"status": "already_requested", "issue": existing["number"],
                "ticket": key, "agent": agent}
    moment = now or datetime.now(timezone.utc)
    wait = _agent_cooldown(call, agent, moment)
    if wait:
        return {"status": "cooldown", "agent": agent,
                "retry_after_seconds": wait, "window_seconds": CONSULTATION_WINDOW_SECONDS}
    body = "\n".join((
        REQUEST_MARKER,
        "ticket: " + key,
        "agent: " + agent,
        "reason: " + reason,
        "round: " + str(round_index),
        "data_class: public_technical_only",
        "response_contract: MAY_CONSULT_REPLY_V2 followed by one JSON tool action",
        'or {"actions":[1..5 JSON tool actions]}; diagnosis only if no safe action exists',
        "",
        "Technical question:",
        public_question,
        "",
        "User permissions are NOT expanded by this request. Tools are restricted by",
        "the existing runtime contracts. Do not include secrets or client data.",
        "May may use the one named ChatGPT agent conversation as a helper;",
        "the GitHub issue is the durable response channel.",
    )) + "\n"
    # Public preview only: the model cannot choose an arbitrary URL, and
    # no screenshot or private session data is written to a public issue.
    public_preview = PUBLIC_PREVIEW_BY_PROJECT.get(task.get("project"))
    if public_preview:
        body += ("\nApproved live public preview for May browser QA:\n"
                 + public_preview +
                 "\nTest actual user actions and return a concise reproduction and correction. "
                 "No screenshots or customer data are attached by this ticket.\n")
    created = call("POST", "/issues", body={
        "title": ISSUE_PREFIX + key, "body": body, "assignees": ["danielnep"]})
    return {"status": "requested", "issue": created["number"],
            "ticket": key, "agent": agent}


def _valid_reply(body):
    if not isinstance(body, str):
        return None
    marker = None
    for known in (REPLY_MARKER, LEGACY_REPLY_MARKER):
        if body.startswith(known + "\n"):
            marker = known
            break
    if marker is None:
        return None
    payload = body[len(marker):].strip()[:MAX_ADVICE]
    if not payload:
        return None
    try:
        decision = json.loads(payload)
    except (ValueError, TypeError):
        decision = None
    def well_formed(action):
        return (isinstance(action, dict)
                and set(action) == {"action", "args", "summary"}
                and isinstance(action["action"], str)
                and isinstance(action["args"], dict)
                and isinstance(action["summary"], str))

    # Shape only: the worker validates specialist tool and path permissions.
    if well_formed(decision):
        return {"decision_untrusted": decision, "advice_untrusted": ""}
    if isinstance(decision, dict) and set(decision) == {"actions"}:
        actions = decision["actions"]
        if (isinstance(actions, list)
                and 1 <= len(actions) <= MAX_ACTIONS_PER_REPLY
                and all(well_formed(action) for action in actions)):
            return {"decisions_untrusted": actions, "advice_untrusted": ""}
    return {"advice_untrusted": payload}


def read_consultation(task_id, *, api=None, agent=None, round_index=0):
    """Accept only a repository-owner comment with the expected V2 marker."""
    if not os.environ.get("GITHUB_TOKEN") and api is None:
        return None
    call = api or _github
    row = _find_issue(call, ticket(task_id, agent, round_index), legacy=agent is None)
    if row is None:
        return None
    # GitHub's issue comments are paginated oldest first. An owner's reply
    # after the first hundred must not be mistaken for an unanswered ticket.
    # A saturated bound fails closed rather than silently trusting old pages.
    latest = None
    for page in range(1, 11):
        comments = call("GET", "/issues/" + str(row["number"])
                        + "/comments?per_page=100&page=" + str(page))
        if not isinstance(comments, list):
            raise RuntimeError("Invalid consultation comments response")
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            actor = comment.get("user", {}).get("login", "").lower()
            owner = actor == "danielnep"
            # The dedicated in-repository Actions relay uses the scoped GitHub
            # workflow token and is authenticated as github-actions[bot].
            # This is NOT general permission for models or unknown commenters.
            trusted_relay = (actor == "github-actions[bot]" and agent is not None)
            if not owner and not trusted_relay:
                continue
            if agent is not None and not (comment.get("body") or "").startswith(REPLY_MARKER + "\n"):
                continue
            parsed = _valid_reply(comment.get("body"))
            if parsed is not None:
                latest = {"issue": row["number"], **parsed,
                          "source": "public_owner_comment" if owner else "scoped_actions_relay",
                          "agent": agent}
        if len(comments) < 100:
            return latest
    raise RuntimeError("Consultation comment pagination cap reached")
