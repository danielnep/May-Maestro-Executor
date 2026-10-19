import unittest
from unittest.mock import patch

from horizon_core.may_consultation import (
    ISSUE_PREFIX, REQUEST_MARKER, REPLY_MARKER,
    request_consultation, read_consultation, ticket,
)


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


class MaestroMayConsultationTests(unittest.TestCase):
    task = {"agent": "may-maestro", "task_id": "michelle-private-20261019",
            "objective": "NEVER ALLOW THIS TEXT INTO PUBLIC ISSUE", "project": "private-client"}

    def test_only_maestro_and_only_provider_outage(self):
        api = FakeIssues()
        task = dict(self.task, agent="qa-engineer")
        self.assertEqual(request_consultation(task, "providers_unavailable", api=api)["status"], "not_applicable")
        self.assertEqual(request_consultation(self.task, "timeout", api=api)["status"], "not_applicable")
        self.assertEqual(api.posts, 0)

    def test_request_is_redacted_and_idempotent(self):
        api = FakeIssues()
        first = request_consultation(self.task, "providers_unavailable", api=api)
        second = request_consultation(self.task, "providers_unavailable", api=api)
        self.assertEqual(first["status"], "requested")
        self.assertEqual(second["status"], "already_requested")
        self.assertEqual(api.posts, 1)
        issue = api.issues[0]
        self.assertEqual(issue["title"], ISSUE_PREFIX + ticket(self.task["task_id"]))
        self.assertIn(REQUEST_MARKER, issue["body"])
        self.assertNotIn("NEVER ALLOW", issue["body"])
        self.assertNotIn("michelle", issue["body"])
        self.assertNotIn("private-client", issue["body"])

    def test_only_owner_reply_with_marker_is_read(self):
        api = FakeIssues()
        request_consultation(self.task, "providers_unavailable", api=api)
        api.comments[1] = [
            {"user": {"login": "unknown"}, "body": REPLY_MARKER + "\nmalicious advice"},
            {"user": {"login": "danielnep"}, "body": "no marker"},
            {"user": {"login": "danielnep"}, "body": REPLY_MARKER + "\nCheck credentials configuration."},
        ]
        found = read_consultation(self.task["task_id"], api=api)
        self.assertEqual(found["advice_untrusted"], "Check credentials configuration.")
        self.assertEqual(found["source"], "public_owner_comment")

    def test_unmarked_or_empty_reply_is_ignored(self):
        api = FakeIssues()
        request_consultation(self.task, "providers_unavailable", api=api)
        api.comments[1] = [{"user": {"login": "danielnep"}, "body": REPLY_MARKER + "\n"}]
        self.assertIsNone(read_consultation(self.task["task_id"], api=api))

    def test_without_token_does_not_send(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": ""}):
            self.assertEqual(request_consultation(self.task, "providers_unavailable")["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
