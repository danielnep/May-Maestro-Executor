"""Switch an existing GitHub Pages preview to the just-published work branch.

Never changes the repository default branch or publishes to production.
Requires a separate GitHub token with Pages:write and Administration:write
of the destination repository; GITHUB_TOKEN is intentionally insufficient.
"""
import json
import os
import re
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPO = re.compile(r"^danielnep/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^(?:agent|fix|feat|test)/[A-Za-z0-9][A-Za-z0-9_./-]*$")


def _request(opener, token, repo, method, suffix, body=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Horizon-Pages-Preview/1",
        "Authorization": "Bearer " + token,
    }
    data = None if body is None else json.dumps(body).encode("utf8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request("https://api.github.com/repos/" + repo + suffix,
                      headers=headers, data=data, method=method)
    try:
        with opener(request, timeout=20) as response:
            result = response.read(16000)
            status = response.status
    except HTTPError as error:
        # Never report raw response: it can include server-provided sensitive text.
        return error.code, None
    except (URLError, TimeoutError, OSError):
        return 0, None
    if not result:
        return status, {}
    try:
        value = json.loads(result)
        return status, value if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        return status, None


def sync_pages_source(repo, branch, *, token=None, opener=urlopen):
    """Return an evidence-bearing result; unconfigured/failed changes are never success."""
    if (not isinstance(repo, str) or not REPO.fullmatch(repo)
            or '..' in repo or not isinstance(branch, str)
            or not BRANCH.fullmatch(branch) or '..' in branch or '//' in branch
            or branch.endswith('/')):
        raise ValueError("A validated owner repository and non-default work branch are required")
    token = token if token is not None else os.getenv("HORIZON_PAGES_TOKEN", "")
    if not token:
        return {"status": "needs_pages_admin_token", "updated": False,
                "reason": "Grant Pages and Administration write on the destination repository"}
    branch_ref = "/git/ref/heads/" + quote(branch, safe="/")
    code, reference = _request(opener, token, repo, "GET", branch_ref)
    if code != 200 or not isinstance(reference, dict):
        return {"status": "branch_not_verified" if code == 404 else "branch_check_failed",
                "updated": False, "http_status": code}
    code, pages = _request(opener, token, repo, "GET", "/pages")
    if code == 404:
        return {"status": "pages_not_enabled", "updated": False}
    if code != 200 or not isinstance(pages, dict):
        return {"status": "pages_check_failed", "updated": False, "http_status": code}
    if pages.get("build_type") == "workflow":
        return {"status": "workflow_mode_requires_explicit_deployment", "updated": False}
    source = pages.get("source")
    if not isinstance(source, dict) or source.get("path") not in ("/", "/docs"):
        return {"status": "unknown_pages_source", "updated": False}
    current = source.get("branch")
    if current == branch:
        return {"status": "already_current", "updated": False,
                "branch": branch, "page_url": pages.get("html_url")}
    path = source["path"]
    code, _ = _request(opener, token, repo, "PUT", "/pages",
                       {"source": {"branch": branch, "path": path}})
    if code != 204:
        return {"status": "pages_update_failed", "updated": False, "http_status": code}
    confirmed_code, confirmed = _request(opener, token, repo, "GET", "/pages")
    if confirmed_code != 200 or not isinstance(confirmed, dict) or (
        confirmed.get("source") or {}).get("branch") != branch:
        return {"status": "not_verified", "updated": False,
                "http_status": confirmed_code}
    return {"status": "verified", "updated": True,
            "branch": branch, "page_url": confirmed.get("html_url")}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args(argv)
    result = sync_pages_source(args.repo, args.branch)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in ("already_current", "verified", "pages_not_enabled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
