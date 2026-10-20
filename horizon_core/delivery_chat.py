"""Create one public-safe May review ticket after a verified agent delivery.

This is the producer half of the already-tested Horizon Central transport:
GitHub issue -> existing hourly ChatGPT KERNEL browser automation -> reply/ack.
Never claim the issue is itself a message delivered inside ChatGPT.
"""
import re

MARKER = "MAY_DELIVERY_REVIEW_V1"
ACK_MARKER = "MAY_DELIVERY_SENT_V1"
OWNER = "danielnep"
REPOSITORY = "danielnep/May-Maestro-Executor"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,69}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"danielnep/[A-Za-z0-9_.-]+\Z")
PUBLIC_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::443)?(?:/[A-Za-z0-9_./%~-]*)?/?\Z")


def review_ticket(report):
    """Only fully verified result packets, never model-authored messages."""
    if not isinstance(report, dict) or report.get("status") != "reported_complete":
        return None
    if (report.get("quality") or {}).get("approved") is not True:
        return None
    req_id = report.get("request_id")
    packet = report.get("may_review")
    if not isinstance(packet, dict) or not isinstance(req_id, str) or not IDENTIFIER.fullmatch(req_id):
        return None
    if packet.get("status") != "ready_for_may_review":
        return None
    repo, commit, preview = (packet.get(x) for x in ("repository", "commit", "preview_url"))
    if not (isinstance(repo, str) and REPO.fullmatch(repo)
            and isinstance(commit, str) and COMMIT.fullmatch(commit)
            and isinstance(preview, str) and len(preview) <= 500 and PUBLIC_URL.fullmatch(preview)):
        return None
    # URL solely from system-confirmed GitHub Pages response; no untrusted
    # model summaries, private task descriptions, or arbitrary screenshots.
    published = report.get("publication") or {}
    verified = published.get("pages_preview") or {}
    if not (published.get("published") is True
            and published.get("target_repo") == repo
            and published.get("commit") == commit
            and verified.get("status") in ("verified", "already_current")
            and verified.get("page_url") == preview
            and verified.get("branch") == packet.get("branch")):
        return None
    return {
        "ticket": "delivery-" + req_id + "-" + commit[:12],
        "request_id": req_id,
        "repository": repo,
        "commit": commit,
        "preview_url": preview,
        "result_url": "https://github.com/" + REPOSITORY + "/blob/main/tasks/results/" + req_id + ".json",
    }


def issue_payload(report):
    ticket = review_ticket(report)
    if ticket is None:
        return None
    body = ("\n".join((
        MARKER,
        "ticket: " + ticket["ticket"],
        "agent: may-maestro",
        "type: delivery_review",
        "data_class: public_verified_preview_only",
        "repository: " + ticket["repository"],
        "commit: " + ticket["commit"],
        "preview_url: " + ticket["preview_url"],
        "result_url: " + ticket["result_url"],
        "",
        "Action for the existing Horizon | Central dos Agentes automation:",
        "Open this verified public preview in the authenticated KERNEL browser,",
        "post ONE concise [HORIZON ENTREGA V1] message with agent, ticket, preview",
        "URL and SHA to the SAME existing specialist chat. Confirm it was posted.",
        "Once confirmed, record MAY_DELIVERY_SENT_V1 with ticket in this issue.",
        "No screenshots or client data in GitHub; review UI, motion, gestures,",
        "navigation and states in-browser. Never claim QA approved by the model.",
    )) + "\n")
    return {"title": "[May delivery] " + ticket["ticket"],
            "body": body, "assignees": [OWNER]}


def queue_review(report, api):
    """Idempotent ticket producer. Serialized by existing workflow concurrency."""
    payload = issue_payload(report)
    if payload is None:
        return "not_ready"
    expected = payload["title"]
    marker = MARKER + "\n"
    for page in range(1, 6):
        issues = api._request("GET", "/issues?state=all&per_page=100&page=" + str(page))
        if not isinstance(issues, list):
            raise ValueError("Invalid GitHub issue listing")
        if any(isinstance(row, dict) and row.get("title") == expected
               and (row.get("body") or "").startswith(marker) for row in issues):
            return "already_queued"
        if len(issues) < 100:
            break
    else:
        raise ValueError("Delivery ticket lookup cap reached")
    created = api._request("POST", "/issues", payload)
    if not isinstance(created, dict) or not isinstance(created.get("number"), int):
        raise ValueError("Delivery ticket not confirmed")
    return "queued"


def queue_kernel_live_review(report, api):
    """Idempotent public-safe notification; LIVE URL goes only to private chat."""
    tickets = report.get("kernel_review_tickets") or []
    if not isinstance(tickets, list) or not tickets:
        return "not_ready"
    existing = api._request("GET", "/issues?state=all&per_page=100")
    if not isinstance(existing, list):
        raise ValueError("Invalid issue listing")
    created = 0
    for item in tickets:
        if not isinstance(item, dict):
            continue
        ticket, task_id = item.get("ticket"), item.get("task_id")
        if (not isinstance(ticket, str) or not re.fullmatch("[0-9a-f]{20}", ticket)
                or not isinstance(task_id, str)
                or not re.fullmatch("[a-z0-9:-]{5,160}", task_id)):
            continue
        title = "[May live review] " + ticket
        if any(isinstance(issue, dict) and issue.get("title") == title
               and (issue.get("body") or "").startswith("MAY_KERNEL_REVIEW_REQUEST_V1")
               for issue in existing):
            continue
        body = ("MAY_KERNEL_REVIEW_REQUEST_V1\n"
                "TICKET: " + ticket + "\n"
                "TASK: " + task_id + "\n"
                "VIEWER: delivered through the private Horizon agent chat, never this issue.\n"
                "STATUS: awaiting live KERNEL mobile/desktop and functional review.\n"
                "After actual inspection, owner may reply strictly:\n"
                "MAY_KERNEL_REVIEW_V1\nTICKET: " + ticket +
                "\nSCENARIOS: <number-of-real-verified-flows>\nRESULT: approved\n"
                "No passing test is inferred from opening the browser.\n")
        result = api._request("POST", "/issues", {
            "title": title, "body": body, "assignees": [OWNER]})
        if not isinstance(result, dict) or type(result.get("number")) is not int:
            raise ValueError("Kernel review ticket not persisted")
        created += 1
        existing.append({"title": title, "body": body})
    return "queued" if created else "already_queued"
