"""Bounded May consultation through public GitHub issues, coordinated ONLY by Maestro.

The ChatGPT account is never scripted as a substitute API. A scheduled ChatGPT
task may read the public issue and reply using the official connected GitHub app.
No customer objectives, code, credentials, memory, or raw provider errors leave
the runner. Advice is untrusted context, not an executable command.
"""
import hashlib
import json
import os
import urllib.parse
import urllib.request

REPOSITORY = "danielnep/May-Maestro-Executor"
ISSUE_PREFIX = "[May consultation] "
REQUEST_MARKER = "MAY_CONSULT_REQUEST_V1"
REPLY_MARKER = "MAY_CONSULT_REPLY_V1"
API_URL = "https://api.github.com/repos/" + REPOSITORY
PERMITTED_FAILURE = "providers_unavailable"
MAX_ADVICE = 2500


def ticket(task_id):
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Invalid consultation task")
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:20]


def _github(method, path, *, token=None, body=None):
    secret = token or os.environ.get("GITHUB_TOKEN")
    if not secret:
        raise RuntimeError("GitHub integration unavailable")
    url = API_URL + path
    headers = {"Authorization": "Bearer " + secret,
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "Horizon-May-Consultation"}
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if raw is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=raw, method=method)
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)


def _find_issue(api, key):
    issues = api("GET", "/issues?state=all&per_page=100")
    for row in issues:
        if (not row.get("pull_request")
                and row.get("title") == ISSUE_PREFIX + key
                and REQUEST_MARKER in (row.get("body") or "")):
            return row
    return None


def request_consultation(task, reason, *, api=None):
    """One public, metadata-only issue per Maestro task; never calls ChatGPT UI."""
    if task.get("agent") != "may-maestro" or reason != PERMITTED_FAILURE:
        return {"status": "not_applicable"}
    if not os.environ.get("GITHUB_TOKEN") and api is None:
        return {"status": "not_configured"}
    key = ticket(task.get("task_id"))
    call = api or _github
    existing = _find_issue(call, key)
    if existing:
        return {"status": "already_requested", "issue": existing["number"], "ticket": key}
    body = ("\n".join([
        REQUEST_MARKER,
        "ticket: " + key,
        "agent: may-maestro",
        "failure: providers_unavailable",
        "data_class: public_metadata_only",
        "",
        "Gemini and DeepSeek both failed for this coordinator cycle.",
        "Request a short, practical recovery diagnosis using public documentation.",
        "Do not request or expose credentials, client content or repository secrets.",
        "Do not propose ChatGPT browser scraping or automatic output extraction.",
        "Do not replace the specialists' independent reasoning; this is advisory only.",
        "A reply must begin with " + REPLY_MARKER + " on its own line.",
    ]) + "\n")
    created = call("POST", "/issues", body={"title": ISSUE_PREFIX + key,
                                              "body": body,
                                              "assignees": ["danielnep"]})
    return {"status": "requested", "issue": created["number"], "ticket": key}


def read_consultation(task_id, *, api=None):
    """Read only a reply by the repository owner, bounded as untrusted advice."""
    if not os.environ.get("GITHUB_TOKEN") and api is None:
        return None
    call = api or _github
    row = _find_issue(call, ticket(task_id))
    if row is None:
        return None
    comments = call("GET", "/issues/" + str(row["number"]) + "/comments?per_page=100")
    for comment in reversed(comments):
        if (comment.get("user", {}).get("login", "").lower() != "danielnep"):
            continue
        body = comment.get("body") or ""
        if body.startswith(REPLY_MARKER + "\n"):
            advice = body[len(REPLY_MARKER):].strip()[:MAX_ADVICE]
            return {"issue": row["number"], "advice_untrusted": advice,
                    "source": "public_owner_comment"} if advice else None
    return None
