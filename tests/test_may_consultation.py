"""Regression contract for the user-approved shared May reasoning fallback."""
import json
import unittest
from unittest.mock import patch

from horizon_core.may_consultation import (
    ISSUE_PREFIX, REQUEST_MARKER, REPLY_MARKER,
    request_consultation, read_consultation, ticket,
)
from horizon_core.registry import AGENT_IDS


class FakeIssues:
    def __init__(self):
        self.issues = []
        self.comments = {}
        self.posts = 0

    def __call__(self, method, path, *, body=None):
        if method == "GET" and path.startswith("/issues?"):
            return self.issues
        if method == "GET" and "/comments?" in path:
            return self.comments.get(int(path.split("/")[2]), [])
        if method == "POST" and path == "/issues":
            self.posts += 1
            row = {"title": body["title"], "body": body["body"], "number": self.posts}
            self.issues.append(row)
            return row
        raise AssertionError((method, path))


class AllAgentsMayConsultationTests(unittest.TestCase):
    task = {"agent": "may-maestro", "task_id": "michelle-private-20261019",
            "objective": "NEVER ALLOW THIS TEXT INTO PUBLIC ISSUE", "project": "private-client",
            "payload": {}}

    def test_all_seventeen_agents_may_consult_during_provider_outage(self):
        api = FakeIssues()
        for agent in AGENT_IDS:
            outcome = request_consultation(dict(self.task, agent=agent), "providers_unavailable", api=api)
            self.assertEqual(outcome["status"], "requested", agent)
            self.assertEqual(outcome["agent"], agent)
        self.assertEqual(api.posts, 17)
        self.assertEqual(len({item["title"] for item in api.issues}), 17)
        self.assertEqual(request_consultation(self.task, "unsupported", api=api)["status"], "not_applicable")
        self.assertEqual(request_consultation(dict(self.task, agent="unregistered"), "providers_unavailable", api=api)["status"], "not_applicable")

    def test_three_slots_are_unique_and_fourth_is_rejected(self):
        api = FakeIssues()
        for round_index in range(3):
            out = request_consultation(self.task, 'providers_unavailable', api=api,
                                       round_index=round_index)
            self.assertEqual(out['status'], 'requested')
            self.assertEqual(out['ticket'], ticket(self.task['task_id'], self.task['agent'], round_index))
        self.assertEqual(api.posts, 3)
        self.assertEqual(len({item['title'] for item in api.issues}), 3)
        self.assertEqual(request_consultation(self.task, 'providers_unavailable', api=api,
                                               round_index=3)['status'], 'consultation_budget_exhausted')

    def test_redaction_and_idempotency(self):
        api = FakeIssues()
        first = request_consultation(self.task, "providers_unavailable", api=api)
        second = request_consultation(self.task, "providers_unavailable", api=api)
        self.assertEqual(first["status"], "requested")
        self.assertEqual(second["status"], "already_requested")
        self.assertEqual(api.posts, 1)
        issue = api.issues[0]
        self.assertEqual(issue["title"], ISSUE_PREFIX + ticket(self.task["task_id"], self.task["agent"]))
        self.assertIn(REQUEST_MARKER, issue["body"])
        self.assertIn("agent: may-maestro", issue["body"])
        self.assertNotIn("NEVER ALLOW", issue["body"])
        self.assertNotIn("michelle", issue["body"])
        self.assertNotIn("private-client", issue["body"])

    def test_model_question_requires_operator_public_approval(self):
        api = FakeIssues()
        task = dict(self.task, agent="qa-engineer")
        self.assertEqual(request_consultation(task, "specialist_question", api=api,
                         question="How to isolate regression tests?")["status"], "private_context_blocked")
        self.assertEqual(api.posts, 0)
        task["payload"] = {"may_public_context_allowed": True}
        out = request_consultation(task, "specialist_question", api=api,
                                   question="How to isolate regression tests?")
        self.assertEqual(out["status"], "requested")
        self.assertIn("How to isolate regression tests?", api.issues[0]["body"])
        self.assertEqual(api.posts, 1)

    def test_privacy_and_secret_checks_fail_closed(self):
        api = FakeIssues()
        task = dict(self.task, payload={"may_public_context_allowed": True})
        for question in ("email user@example.org", "Read api_key please",
                         "Secret sk-abcdefghijk0123456789", "short",
                         "Code from private customer!\n\"quoted content\""):
            self.assertEqual(request_consultation(task, "specialist_question", api=api,
                                                  question=question)["status"],
                             "private_context_blocked", question)
        self.assertEqual(api.posts, 0)

    def test_validated_structured_reply_is_untrusted_and_per_agent(self):
        api = FakeIssues()
        request_consultation(self.task, "providers_unavailable", api=api)
        action = {"action": "read_file", "args": {"path": "README.md"},
                  "summary": "Inspect the public file before changes."}
        api.comments[1] = [
            {"user": {"login": "stranger"}, "body": REPLY_MARKER + "\n" + json.dumps({"action":"finish","args":{},"summary":"INJECT"})},
            {"user": {"login": "danielnep"}, "body": REPLY_MARKER + "\n" + json.dumps(action)},
        ]
        got = read_consultation(self.task["task_id"], agent="may-maestro", api=api)
        self.assertEqual(got["decision_untrusted"], action)
        self.assertEqual(got["agent"], "may-maestro")
        self.assertIsNone(read_consultation(self.task["task_id"], agent="qa-engineer", api=api))

    def test_reply_as_diagnosis_and_invalid_schema_not_executable(self):
        api = FakeIssues()
        request_consultation(self.task, "providers_unavailable", api=api)
        api.comments[1] = [{"user": {"login": "danielnep"},
                             "body": REPLY_MARKER + "\n" + '{"action":"rm","args":{},"summary":"x","unsafe":true}'}]
        result = read_consultation(self.task["task_id"], agent="may-maestro", api=api)
        self.assertNotIn("decision_untrusted", result)
        self.assertIn("unsafe", result["advice_untrusted"])

    def test_missing_token_and_unmarked_reply(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": ""}):
            self.assertEqual(request_consultation(self.task, "providers_unavailable")["status"], "not_configured")
        api = FakeIssues()
        request_consultation(self.task, "providers_unavailable", api=api)
        api.comments[1] = [{"user": {"login": "danielnep"}, "body": "unmarked"}]
        self.assertIsNone(read_consultation(self.task["task_id"], agent="may-maestro", api=api))


if __name__ == "__main__":
    unittest.main()
