"""Immediate, checkpoint-safe GitHub Actions handoff for the chat task queue.

Only ready tasks trigger workflow_dispatch; retry backoff and leased work wait for
the existing scheduled watchdog. No external model data controls the endpoint.
"""
import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPOSITORY = "danielnep/May-Maestro-Executor"
WORKFLOW = "chat-requests.yml"
MARKER = Path("runtime/next-session.json")


def ready_request_ids(state, request_ids, now=None):
    """Select requests with an unleased queued task whose prerequisites passed."""
    timestamp = time.time() if now is None else now
    ready = []
    for request_id in dict.fromkeys(request_ids):
        rows = state.tasks(project="chat:" + request_id)
        by_id = {row["task_id"]: row for row in rows}
        for row in rows:
            if row["status"] != "queued" or row.get("not_before", 0) > timestamp:
                continue
            dependencies = row.get("depends_on", [])
            if all(by_id.get(dep, {}).get("status") == "succeeded" for dep in dependencies):
                ready.append(request_id)
                break
    return ready


def write_marker(request_ids, marker=MARKER):
    """Persist only non-sensitive IDs, not objectives/checkpoints/credentials."""
    values = sorted(set(request_ids))
    marker = Path(marker)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"resume": bool(values), "request_ids": values},
                                 sort_keys=True) + "\n", encoding="utf-8")
    return bool(values)


def dispatch_if_ready(marker=MARKER, *, token=None, repository=None, ref=None, opener=urlopen):
    """Workflow-dispatch a single replacement run; GitHub concurrency serializes it."""
    marker = Path(marker)
    if not marker.is_file():
        # An interrupted worker leaves no marker; the five-minute cron is fallback.
        return False
    data = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("resume") is not True:
        return False
    repository = repository or os.environ.get("GITHUB_REPOSITORY")
    ref = ref or os.environ.get("GITHUB_REF")
    if repository != REPOSITORY or ref != "refs/heads/main":
        raise RuntimeError("Handoff is only authorized on the controller main branch")
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN unavailable for workflow handoff")
    request = Request(
        "https://api.github.com/repos/" + REPOSITORY + "/actions/workflows/"
        + WORKFLOW + "/dispatches",
        data=b'{"ref":"main"}',
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "User-Agent": "Horizon-Checkpoint-Handoff",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=30) as response:
            if response.status != 204:
                raise RuntimeError("GitHub workflow dispatch returned unexpected status")
    except HTTPError as exc:
        raise RuntimeError("GitHub workflow dispatch rejected (HTTP " + str(exc.code) + ")") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("GitHub workflow dispatch transport failed") from None
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch", action="store_true", required=True)
    parser.add_argument("--marker", type=Path, default=MARKER)
    args = parser.parse_args(argv)
    fired = dispatch_if_ready(args.marker)
    print(json.dumps({"handoff_dispatched": fired}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
