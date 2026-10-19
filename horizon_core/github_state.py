"""SQLite checkpoints on a dedicated GitHub branch, using only the stdlib.

This initial transport is intended for PUBLIC projects and SYNTHETIC data only:
the database, including task text and memory, is visible in repository history.
Never store credentials, personal information or customer data in it. A single
coordinator must serialize writes (e.g. one workflow concurrency group). GitHub
CAS prevents lost updates; it does not provide a distributed task lease.
"""

import base64
import gzip
import binascii
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class GitHubStateError(RuntimeError):
    """A sanitized transport or compare-and-swap failure."""


class GitHubState:
    path = "runtime/state.sqlite3"

    def __init__(self, repo, branch="agent-state/runtime", token=None, opener=None, compressed=False,
                 encryption_key=None):
        if not isinstance(repo, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
        ) or any(part in (".", "..") for part in repo.split("/")):
            raise ValueError("Expected a GitHub owner/repository")
        # Restrict all mutations to an explicitly dedicated state namespace.
        if not isinstance(branch, str) or not re.fullmatch(
            r"agent-state/[A-Za-z0-9][A-Za-z0-9_/-]*", branch
        ) or "//" in branch or branch.endswith("/"):
            raise ValueError("State branch must be inside agent-state/")
        self.repo = repo
        self.branch = branch
        self.compressed = compressed
        if encryption_key is not None and not compressed:
            raise ValueError("Encrypted checkpoints require compression")
        self.encryption_key = encryption_key
        self.path = "runtime/state.sqlite3.gz" if compressed else "runtime/state.sqlite3"
        self.restored_legacy = False
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.opener = opener or urlopen
        self.sha = None
        self._read = False
        self._base = "https://api.github.com/repos/" + repo

    def _request(self, method, endpoint, body=None, missing_ok=False):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Horizon-State/1",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self._base + endpoint, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=30) as response:
                status = response.status
                if status == 404 and missing_ok:
                    return None
                if not 200 <= status < 300:
                    raise GitHubStateError("GitHub HTTP " + str(status))
                raw = response.read()
        except HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise GitHubStateError("GitHub HTTP " + str(exc.code)) from None
        except GitHubStateError:
            raise
        except Exception:
            # Exception strings can contain signed URLs, credentials or bodies.
            raise GitHubStateError("GitHub transport failed") from None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise GitHubStateError("Invalid GitHub JSON response") from None

    def _content(self):
        return self._request("GET", "/contents/" + self.path + "?ref=" + quote(self.branch, safe=""),
                             missing_ok=True)

    def pull(self, destination):
        """Atomically restore the checkpoint; 404 alone means no checkpoint.

        The destination must not be an open SQLite database (including WAL
        connections). A missing checkpoint leaves any destination untouched.
        """
        result = self._content()
        self.restored_legacy = False
        if result is None and self.compressed:
            # First run after an opt-in transition: use the historical SQLite snapshot.
            # Subsequent writes publish a compressed blob without deleting the original.
            previous = self.path
            self.path = "runtime/state.sqlite3"
            try:
                result = self._content()
            finally:
                self.path = previous
            if result is not None:
                self.restored_legacy = True
        if result is None:
            self.sha, self._read = None, True
            return False
        try:
            if result["encoding"] != "base64" or not isinstance(result["sha"], str):
                raise ValueError()
            content = base64.b64decode("".join(result["content"].split()), validate=True)
            if self.compressed and not self.restored_legacy:
                if self.encryption_key is not None:
                    from cryptography.fernet import Fernet, InvalidToken
                    try:
                        content = Fernet(self.encryption_key).decrypt(content)
                    except (InvalidToken, ValueError, TypeError):
                        raise ValueError("Checkpoint decryption failed") from None
                content = gzip.decompress(content)
            if len(content) > 64 * 1024 * 1024 or not content.startswith(b"SQLite format 3\x00"):
                raise ValueError()
            sha = result["sha"]
        except (KeyError, TypeError, ValueError, OSError, EOFError, binascii.Error):
            raise GitHubStateError("Invalid GitHub checkpoint response") from None
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        try:
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)
        # The blob SHA of the legacy path is NOT valid for the new compressed path.
        self.sha, self._read = (None if self.restored_legacy else sha), True
        return True

    def _ensure_branch(self):
        metadata = self._request("GET", "")
        default = metadata.get("default_branch")
        if not isinstance(default, str) or not default or default == self.branch:
            raise GitHubStateError("Refusing to write repository default branch")
        existing = self._request("GET", "/git/ref/heads/" + quote(self.branch, safe="/"),
                                 missing_ok=True)
        if existing is not None:
            return
        base = self._request("GET", "/git/ref/heads/" + quote(default, safe="/"))
        try:
            base_sha = base["object"]["sha"]
            if not isinstance(base_sha, str) or not base_sha:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise GitHubStateError("Invalid GitHub base reference") from None
        # A competing creator's 422 is a conflict, not permission to retry writes.
        self._request("POST", "/git/refs", {"ref": "refs/heads/" + self.branch, "sha": base_sha})

    def push(self, source, expected_sha=None):
        """Upload a WAL-consistent snapshot with CAS; never retry a conflict.

        Restore with pull before editing. A new instance can create missing state
        or use an explicit expected_sha, but cannot silently replace existing state.
        """
        if expected_sha is not None and (not isinstance(expected_sha, str) or not expected_sha):
            raise ValueError("expected_sha must be a nonempty string")
        if not self._read and expected_sha is None:
            if self._content() is not None:
                raise GitHubStateError("Pull checkpoint before replacing existing state")
            self.sha, self._read = None, True
        source = Path(source).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "checkpoint.sqlite3"
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
                with sqlite3.connect(snapshot) as copy:
                    original.backup(copy)
            original_bytes = snapshot.read_bytes()
            packed = gzip.compress(original_bytes, compresslevel=6, mtime=0) if self.compressed else original_bytes
            if self.encryption_key is not None:
                from cryptography.fernet import Fernet
                packed = Fernet(self.encryption_key).encrypt(packed)
            payload = base64.b64encode(packed).decode("ascii")
        self._ensure_branch()
        body = {"message": "Checkpoint Horizon runtime", "branch": self.branch, "content": payload}
        sha = expected_sha if expected_sha is not None else self.sha
        if sha is not None:
            body["sha"] = sha
        result = self._request("PUT", "/contents/" + self.path, body)
        try:
            new_sha = result["content"]["sha"]
            if not isinstance(new_sha, str) or not new_sha:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise GitHubStateError("Invalid GitHub checkpoint acknowledgement") from None
        self.sha, self._read = new_sha, True
        return new_sha
