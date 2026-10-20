"""Evidence-backed attendance transport; never model-generated status."""
import base64
import json
import unittest
from unittest.mock import patch

from horizon_core.attendance import (
    attendance_snapshot, publish_attendance, team_snapshot, publish_team_attendance
)
from horizon_core.github_state import GitHubStateError
from horizon_core.registry import AGENT_IDS
from horizon_core.state import State


class FakeGitHub:
    def __init__(self):
        self.contents = {}
        self.writes = []

    def _request(self, method, endpoint, body=None):
        if method == "GET":
            key = endpoint.split("?ref=", 1)[0]
            if key not in self.contents:
                raise GitHubStateError("GitHub HTTP 404")
            return {"sha": "cas-sha", "content": base64.b64encode(self.contents[key]).decode()}
        if method == "PUT":
            assert body["branch"] == "main"
            key = endpoint
            if key in self.contents:
                assert body["sha"] == "cas-sha"
            self.contents[key] = base64.b64decode(body["content"])
            self.writes.append((endpoint, body))
            return {"content": {"sha": "new-sha"}}
        raise AssertionError(method)


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.state = State(":memory:")
        self.addCleanup(self.state.close)
        self.api = FakeGitHub()

    def test_start_wait_resume_finish_and_privacy(self):
        self.state.enqueue("chat-synthetic:qa-review", "qa-engineer", "chat:synthetic",
                           "PRIVATE CLIENT NAME, PASSWORD and 1000 records",
                           payload={"sensitive": "DO NOT PUBLISH THIS SECRET"})
        self.assertEqual(team_snapshot(self.state)["agents"][10]["status"], "na_fila")
        self.state.claim("tester", now=1000, lease_seconds=500)
        self.assertEqual(team_snapshot(self.state)["agents"][10]["status"], "executando")
        self.state.checkpoint("chat-synthetic:qa-review", "tester",
                              {"step": 2, "private": "ANOTHER PRIVATE VALUE"}, now=1001)
        self.state.defer("chat-synthetic:qa-review", "tester", seconds=120, now=1002)
        self.assertEqual(team_snapshot(self.state)["agents"][10]["status"], "aguardando")
        self.state.claim("tester", now=1123, lease_seconds=500)
        self.state.complete("chat-synthetic:qa-review", "tester",
                            {"summary": "SECRET OUTPUT NEVER EXPOSE"}, now=1124)
        feed = attendance_snapshot(self.state, "synthetic")
        self.assertEqual([row["status"] for row in feed["events"]],
                         ["iniciou", "aguardando", "retomou", "concluiu"])
        self.assertEqual(feed["roster"][0]["status"], "concluido")
        self.assertEqual(team_snapshot(self.state)["agents"][10]["status"], "ocioso")
        self.assertNotIn("PRIVATE", json.dumps(feed))
        self.assertNotIn("SECRET", json.dumps(feed))
        self.assertNotIn("PASSWORD", json.dumps(feed))
        self.assertNotIn("PRIVATE", json.dumps(team_snapshot(self.state)))

    def test_failed_and_blocked_are_not_mistaken_for_success(self):
        self.state.enqueue("chat-synthetic:frontend", "frontend-engineer", "chat:synthetic",
                           "Unverified change")
        self.state.claim("test", now=10, lease_seconds=100)
        self.state.fail("chat-synthetic:frontend", "test",
                        {"message": "SECRET failure"}, retryable=False, now=11)
        feed = attendance_snapshot(self.state, "synthetic")
        self.assertEqual(feed["events"][-1]["status"], "bloqueado")
        self.assertEqual(feed["roster"][0]["status"], "bloqueado")
        self.assertEqual([a for a in team_snapshot(self.state)["agents"]
                          if a["agent"] == "frontend-engineer"][0]["blocked"], 1)
        self.assertNotIn("SECRET", json.dumps(feed))

    def test_all_agents_have_idle_presence_when_unused(self):
        roster = team_snapshot(self.state)
        self.assertEqual({a["agent"] for a in roster["agents"]}, set(AGENT_IDS))
        self.assertTrue(all(a["status"] == "ocioso" for a in roster["agents"]))
        self.assertIsNone(attendance_snapshot(self.state, "synthetic"))

    def test_idempotent_publication_and_safe_paths(self):
        self.state.enqueue("chat-synthetic:qa", "qa-engineer", "chat:synthetic",
                           "Review")
        self.state.claim("worker", now=400, lease_seconds=500)
        feed = attendance_snapshot(self.state, "synthetic")
        self.assertTrue(publish_attendance(self.api, feed)["written"])
        self.assertFalse(publish_attendance(self.api, feed)["written"])
        self.assertTrue(publish_team_attendance(self.api, team_snapshot(self.state))["written"])
        self.assertFalse(publish_team_attendance(self.api, team_snapshot(self.state))["written"])
        self.assertEqual(len(self.api.writes), 2)
        self.assertTrue(all(path.startswith("/contents/tasks/attendance/") for path, _ in self.api.writes))
        with self.assertRaises(ValueError):
            attendance_snapshot(self.state, "../bad")
        with self.assertRaises(ValueError):
            publish_attendance(self.api, {"schema": "fake", "request_id": "synthetic"})
        with self.assertRaises(ValueError):
            publish_attendance(self.api, feed, branch="agent/bypass")


if __name__ == "__main__":
    unittest.main()
