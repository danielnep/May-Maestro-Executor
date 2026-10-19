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

from .registry import AGENT_IDS

REPOSITORY = "danielnep/May-Maestro-Executor"
ISSUE_PREFIX = "[May consultation] "
REQUEST_MARKER = "MAY_CONSULT_REQUEST_V2"
REPLY_MARKER = "MAY_CONSULT_REPLY_V2"
LEGACY_REQUEST_MARKER = "MAY_CONSULT_REQUEST_V1"
LEGACY_REPLY_MARKER = "MAY_CONSULT_REPLY_V1"
API_URL = "https://api.github.com/repos/" + REPOSITORY
REASONS = frozenset(("providers_unavailable", "specialist_question"))
MAX_QUESTION = 1400
MAX_ADVICE = 3000
MAX_CONSULTATIONS_PER_TASK = 3
SAFE_QUESTION = re.compile(r"^[\w \t.,:;!?()[\]{}+=/#\-À-ÿ\n]*$", re.UNICODE)
BLOCKED = re.compile(r"(?:\b(?:api[_ -]?key|secret|password|senha|token|bearer|cpf|cnpj|private[_ -]?key)\b|"
                     r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:sk-|AIza)[\w-]{12,})", re.I)


def ticket(task_id, agent=None, round_index=0):
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Invalid consultation task")
    if agent is not None and agent not in AGENT_IDS:
        raise ValueError("Unknown consultation specialist")
    if not isinstance(round_index, int) or not 0 <= round_index < MAX_CONSULTATIONS_PER_TASK:
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


def request_consultation(task, reason, *, api=None, question=None, round_index=0):
    """Idempotent request for any registered agent. No raw private task context."""
    agent = task.get("agent")
    if agent not in AGENT_IDS or reason not in REASONS:
        return {"status": "not_applicable"}
    if not os.environ.get("GITHUB_TOKEN") and api is None:
        return {"status": "not_configured"}
    public_question = _public_question(task, question, reason)
    if public_question is None:
        return {"status": "private_context_blocked"}
    try:
        key = ticket(task.get("task_id"), agent, round_index)
    except ValueError:
        return {"status": "consultation_budget_exhausted"}
    call = api or _github
    existing = _find_issue(call, key)
    if existing:
        return {"status": "already_requested", "issue": existing["number"],
                "ticket": key, "agent": agent}
    body = "\n".join((
        REQUEST_MARKER,
        "ticket: " + key,
        "agent: " + agent,
        "reason: " + reason,
        "round: " + str(round_index),
        "data_class: public_technical_only",
        "response_contract: MAY_CONSULT_REPLY_V2 followed by JSON tool action OR a short diagnosis",
        "",
        "Technical question:",
        public_question,
        "",
        "User permissions are NOT expanded by this request. Tools are restricted by",
        "the existing runtime contracts. Do not include secrets or client data.",
        "May may use the one named ChatGPT agent conversation as a helper;",
        "the GitHub issue is the durable response channel.",
    )) + "\n"
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
    if isinstance(decision, dict) and set(decision) == {"action", "args", "summary"}:
        # Do NOT execute here: the worker validates the action against the
        # actual specialist capability/allowlist at execution time.
        if isinstance(decision["action"], str) and isinstance(decision["args"], dict) and isinstance(decision["summary"], str):
            return {"decision_untrusted": decision, "advice_untrusted": ""}
    return {"advice_untrusted": payload}


def read_consultation(task_id, *, api=None, agent=None, round_index=0):
    """Accept only a repository-owner comment with the expected V2 marker."""
    if not os.environ.get("GITHUB_TOKEN") and api is None:
        return None
    call = api or _github
    row = _find_issue(call, ticket(task_id, agent, round_index), legacy=agent is None)
    if row is None:
        return None
    comments = call("GET", "/issues/" + str(row["number"]) + "/comments?per_page=100")
    for comment in reversed(comments):
        if comment.get("user", {}).get("login", "").lower() != "danielnep":
            continue
        if agent is not None and not (comment.get("body") or "").startswith(REPLY_MARKER + "\n"):
            continue
        parsed = _valid_reply(comment.get("body"))
        if parsed is not None:
            return {"issue": row["number"], **parsed,
                    "source": "public_owner_comment", "agent": agent}
    return None
