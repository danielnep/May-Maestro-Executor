"""Event-driven GitHub owner notification when an agent delivery becomes terminal.

No polling loop. GitHub's native issue assignment produces notifications subject
to the owner's GitHub notification preferences. No secrets or client data in issues.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPOSITORY = "danielnep/May-Maestro-Executor"
OWNER = "danielnep"
NOTIFIABLE = {"reported_complete", "needs_attention", "awaiting_host_publication", "blocked"}


def alert_payload(report):
    if not isinstance(report, dict) or report.get("status") not in NOTIFIABLE:
        return None
    request_id = report.get("request_id")
    if not isinstance(request_id, str) or not request_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in request_id):
        return None
    status = report["status"]
    title = "[Horizon agente] " + request_id + " · " + ("Entrega reportada" if status == "reported_complete" else "Intervenção necessária")
    # Intentionally no model text, private task content, logs or file paths.
    body = ("Relatório automático dos agentes Horizon.\n\n"
            "**Estado:** \`" + status + "\`\n\n"
            "**Resultado:** https://github.com/" + REPOSITORY
            + "/blob/main/tasks/results/" + request_id + ".json\n\n"
            "Status reportado não significa aprovação de QA nem deploy. "
            "Confira testes e publicação no resultado antes de usar.\n\n"
            "<!-- horizon-task:" + request_id + " -->")
    return {"title": title, "body": body, "assignees": [OWNER], "labels": []}


def notify(report, token, opener=urlopen):
    payload = alert_payload(report)
    if payload is None:
        return "not_terminal"
    if not token:
        raise RuntimeError("GITHUB_TOKEN unavailable")
    marker = "<!-- horizon-task:" + report["request_id"] + " -->"
    base = "https://api.github.com/repos/" + REPOSITORY + "/issues"
    headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Horizon-Agent-Notifier"}
    # This action is serialized with the queue by the shared concurrency group.
    try:
        for page in range(1, 6):
            req = Request(base + "?state=all&per_page=100&page=" + str(page), headers=headers)
            with opener(req, timeout=30) as response:
                issues = json.load(response)
            if not isinstance(issues, list):
                raise RuntimeError("Unexpected GitHub issue list")
            if any(marker in issue.get("body", "") for issue in issues if isinstance(issue, dict)):
                return "already_notified"
            if len(issues) < 100:
                break
        else:
            # Fail closed if we cannot establish idempotency.
            raise RuntimeError("Too many issues to verify notification uniqueness")
        req = Request(base, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={**headers, "Content-Type": "application/json"}, method="POST")
        with opener(req, timeout=30) as response:
            if response.status != 201:
                raise RuntimeError("Unexpected issue creation response")
        return "notified"
    except HTTPError as error:
        raise RuntimeError("GitHub issue notification rejected HTTP " + str(error.code)) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("GitHub issue notification transport failed") from None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("tasks/results"))
    args = parser.parse_args(argv)
    if os.getenv("GITHUB_REPOSITORY") != REPOSITORY or os.getenv("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Notifications restricted to controller main")
    token = os.getenv("GITHUB_TOKEN")
    results = []
    for file in sorted(args.results.glob("*.json")):
        if file.is_symlink() or not file.is_file():
            continue
        report = json.loads(file.read_text(encoding="utf-8"))
        result = notify(report, token)
        results.append({"request_id": report.get("request_id"), "notification": result})
    print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
