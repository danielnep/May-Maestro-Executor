import base64
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.error import HTTPError

from horizon_core.github_state import GitHubState, GitHubStateError


class Response(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.content = None
        self.sha = None
        self.branch = False
        self.error = None
        self.default = "main"
        self.conflict = False

    def __call__(self, request, timeout):
        self.calls.append(request)
        url, method = request.full_url, request.method
        if self.error:
            raise HTTPError(url, self.error, "secret must not escape", {}, None)
        if "/contents/" in url:
            if method == "GET":
                if self.content is None:
                    raise HTTPError(url, 404, "missing", {}, None)
                return Response({"sha": self.sha, "encoding": "base64",
                                 "content": base64.b64encode(self.content).decode()})
            body = json.loads(request.data)
            if self.conflict or body.get("sha") != self.sha:
                raise HTTPError(url, 409, "secret conflict", {}, None)
            self.content = base64.b64decode(body["content"])
            self.sha = "sha-" + str(len(self.calls))
            return Response({"content": {"sha": self.sha}})
        if "/git/ref/heads/" in url:
            if "agent-state/" in url and not self.branch:
                raise HTTPError(url, 404, "missing", {}, None)
            return Response({"object": {"sha": "base-sha"}})
        if url.endswith("/git/refs"):
            self.branch = True
            return Response({})
        return Response({"default_branch": self.default})


class GitHubStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "source.sqlite3"
        self.destination = Path(self.directory.name) / "restored.sqlite3"
        self.api = FakeGitHub()
        self.state = GitHubState("owner/repo", token="private-token", opener=self.api)

    def database(self):
        connection = sqlite3.connect(self.source)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE memory (value BLOB)")
        connection.execute("INSERT INTO memory VALUES (?)", (b"\x00\xffsynthetic",))
        connection.commit()
        self.addCleanup(connection.close)
        return connection

    def test_missing_state_is_only_false_and_leaves_destination(self):
        self.destination.write_bytes(b"unchanged")
        self.assertFalse(self.state.pull(self.destination))
        self.assertEqual(self.destination.read_bytes(), b"unchanged")

    def test_errors_are_not_empty_state_and_do_not_leak(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.api.error = status
                with self.assertRaisesRegex(GitHubStateError, "GitHub HTTP " + str(status)) as error:
                    self.state.pull(self.destination)
                self.assertNotIn("secret", str(error.exception))
                self.assertNotIn("private-token", str(error.exception))
                self.assertFalse(self.destination.exists())

    def test_wal_snapshot_roundtrip_and_headers(self):
        self.database()  # Keep the connection open, with committed data in WAL.
        self.assertTrue(Path(str(self.source) + "-wal").exists())
        self.assertFalse(self.state.pull(self.destination))
        self.state.push(self.source)
        restored = GitHubState("owner/repo", opener=self.api)
        self.assertTrue(restored.pull(self.destination))
        with sqlite3.connect(self.destination) as connection:
            self.assertEqual(connection.execute("SELECT value FROM memory").fetchone()[0],
                             b"\x00\xffsynthetic")
        put = next(call for call in self.api.calls if call.method == "PUT")
        self.assertEqual(json.loads(put.data)["branch"], "agent-state/runtime")
        self.assertEqual(put.get_header("Authorization"), "Bearer private-token")
        self.assertTrue(put.get_header("User-agent"))
        post = next(call for call in self.api.calls if call.method == "POST")
        self.assertEqual(json.loads(post.data)["ref"], "refs/heads/agent-state/runtime")

    def test_compare_and_swap_never_retries_conflict(self):
        self.database()
        self.state.push(self.source)
        previous_sha = self.state.sha
        self.api.sha = "concurrent-change"
        before = len(self.api.calls)
        with self.assertRaisesRegex(GitHubStateError, "409"):
            self.state.push(self.source)
        self.assertEqual(self.state.sha, previous_sha)
        puts = [call for call in self.api.calls[before:] if call.method == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(json.loads(puts[0].data)["sha"], previous_sha)

    def test_no_blind_overwrite_from_new_instance(self):
        self.database()
        self.state.push(self.source)
        fresh = GitHubState("owner/repo", opener=self.api)
        with self.assertRaisesRegex(GitHubStateError, "Pull checkpoint"):
            fresh.push(self.source)
        fresh.push(self.source, expected_sha=self.api.sha)

    def test_protected_and_invalid_branches_rejected(self):
        for branch in ("main", "master", "release", "agent-state/../main", "agent-state/a//b"):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                GitHubState("owner/repo", branch=branch)
        self.database()
        self.api.default = "agent-state/runtime"
        with self.assertRaisesRegex(GitHubStateError, "default branch"):
            self.state.push(self.source)
        self.assertFalse(any(call.method in ("POST", "PUT") for call in self.api.calls))

    def test_repository_validation(self):
        for repo in ("https://github.com/o/r", "o/r?token=x", "../r", "o/r/extra"):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                GitHubState(repo)

    def test_branch_creation_422_is_not_ignored(self):
        self.database()
        original = self.api

        def opener(request, timeout):
            if request.method == "POST":
                raise HTTPError(request.full_url, 422, "secret", {}, None)
            return original(request, timeout)

        state = GitHubState("owner/repo", opener=opener)
        with self.assertRaisesRegex(GitHubStateError, "422"):
            state.push(self.source)
        self.assertFalse(any(call.method == "PUT" for call in self.api.calls))


if __name__ == "__main__":
    unittest.main()
