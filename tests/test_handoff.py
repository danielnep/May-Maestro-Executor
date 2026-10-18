"""Regression tests for automatic checkpoint handoff, using no network."""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from horizon_core.handoff import ready_request_ids, write_marker, dispatch_if_ready


class FakeState:
    def __init__(self, records):
        self.records = records
    def tasks(self, project=None):
        return self.records.get(project, [])


def row(task_id, status, *, not_before=0, depends_on=()):
    return {"task_id": task_id, "status": status, "not_before": not_before,
            "depends_on": list(depends_on)}


class HandoffTests(unittest.TestCase):
    def test_queued_task_with_satisfied_dependencies_is_ready(self):
        state = FakeState({"chat:work": [
            row("root", "succeeded"),
            row("backend", "succeeded", depends_on=["root"]),
            row("qa", "queued", depends_on=["backend"]),
        ]})
        self.assertEqual(ready_request_ids(state, ["work", "work"], now=1234), ["work"])

    def test_delayed_blocked_running_failed_and_waiting_work_do_not_spin(self):
        state = FakeState({
            "chat:delay": [row("x", "queued", not_before=2000)],
            "chat:blocked": [row("x", "failed"), row("y", "queued", depends_on=["x"])],
            "chat:running": [row("x", "running")],
            "chat:waiting": [row("x", "queued", depends_on=["absent"])],
            "chat:completed": [row("x", "succeeded")],
            "chat:ready": [row("x", "queued", not_before=100)],
        })
        ids = ["delay", "blocked", "running", "waiting", "completed", "ready"]
        self.assertEqual(ready_request_ids(state, ids, now=200), ["ready"])

    def test_marker_records_only_identifiers_and_empty_marker_no_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "runtime" / "next-session.json"
            self.assertTrue(write_marker(["beta", "alpha", "alpha"], marker))
            self.assertEqual(json.loads(marker.read_text()),
                             {"resume": True, "request_ids": ["alpha", "beta"]})
            self.assertFalse(write_marker([], marker))
            self.assertFalse(dispatch_if_ready(marker, opener=lambda *a, **k: self.fail("No API expected")))

    def test_real_dispatch_contract_uses_one_authenticated_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            write_marker(["test"], marker)
            requests = []
            class Response:
                status = 204
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return False
            def opener(request, timeout=None):
                requests.append((request, timeout))
                return Response()
            self.assertTrue(dispatch_if_ready(marker, token="fixture-token",
                repository="danielnep/May-Maestro-Executor", ref="refs/heads/main", opener=opener))
            self.assertEqual(len(requests), 1)
            req, timeout = requests[0]
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.data, b'{"ref":"main"}')
            self.assertEqual(timeout, 30)
            self.assertEqual(req.headers["Authorization"], "Bearer fixture-token")
            self.assertTrue(req.full_url.endswith("/actions/workflows/chat-requests.yml/dispatches"))

    def test_rejects_wrong_repo_branch_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            write_marker(["task"], marker)
            for repo, ref in (("other/repo", "refs/heads/main"),
                              ("danielnep/May-Maestro-Executor", "refs/heads/dev")):
                with self.subTest(repo=repo, ref=ref), self.assertRaisesRegex(RuntimeError, "main branch"):
                    dispatch_if_ready(marker, token="key", repository=repo, ref=ref,
                        opener=lambda *args, **kwargs: self.fail("Must not dispatch"))

    def test_http_failure_does_not_echo_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            write_marker(["task"], marker)
            def forbidden(request, timeout):
                raise HTTPError(request.full_url, 403, "secret-key-in-server-message", {}, None)
            with self.assertRaises(RuntimeError) as context:
                dispatch_if_ready(marker, token="fixture-secret",
                    repository="danielnep/May-Maestro-Executor",
                    ref="refs/heads/main", opener=forbidden)
            self.assertIn("403", str(context.exception))
            self.assertNotIn("secret", str(context.exception))

    def test_missing_marker_is_safe_after_worker_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(dispatch_if_ready(Path(tmp) / "missing.json"))


if __name__ == "__main__":
    unittest.main()
