"""Offline contract tests for safe automatic GitHub Pages branch previews."""
import io
import json
import unittest
from urllib.error import HTTPError
from horizon_core.pages_preview import sync_pages_source


class Reply(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


class FakeGitHub:
    def __init__(self, *, source="main", status=204, workflow=False, pages=True):
        self.current=source
        self.status=status
        self.workflow=workflow
        self.pages=pages
        self.calls=[]
    def __call__(self, req, timeout=None):
        self.calls.append((req.method, req.full_url, req.data))
        if req.full_url.endswith("/git/ref/heads/fix/mobile-chat-sheet-20261019"):
            return Reply({"ref":"refs/heads/fix/mobile-chat-sheet-20261019"})
        if req.full_url.endswith("/pages"):
            if not self.pages:
                raise HTTPError(req.full_url,404,"not enabled",{},None)
            if req.method == "GET":
                return Reply({"source":{"branch":self.current,"path":"/"},
                              "build_type":"workflow" if self.workflow else "legacy",
                              "html_url":"https://sample.invalid/example/"})
            if req.method == "PUT":
                if self.status!=204:
                    raise HTTPError(req.full_url,self.status,"denied",{},None)
                body=json.loads(req.data)
                assert set(body)=={"source"}
                assert body["source"]["path"]=="/"
                self.current=body["source"]["branch"]
                return Reply(None,status=204)
        raise AssertionError("Unexpected HTTP request")


class PreviewTest(unittest.TestCase):
    repo="danielnep/app-hondas-maxvel"
    branch="fix/mobile-chat-sheet-20261019"
    def test_no_admin_token_no_network_or_change(self):
        sender=FakeGitHub()
        result=sync_pages_source(self.repo,self.branch,token="",opener=sender)
        self.assertEqual(result["status"],"needs_pages_admin_token")
        self.assertEqual(sender.calls,[])
    def test_successful_switch_gets_confirmed_from_pages(self):
        sender=FakeGitHub()
        result=sync_pages_source(self.repo,self.branch,token="test-token",opener=sender)
        self.assertEqual(result["status"],"verified")
        self.assertTrue(result["updated"])
        self.assertEqual(sender.current,self.branch)
        self.assertEqual([v[0] for v in sender.calls],["GET","GET","PUT","GET"])
        self.assertNotIn("test-token",str(result))
    def test_idempotent_does_not_generate_second_page_update(self):
        sender=FakeGitHub(source=self.branch)
        result=sync_pages_source(self.repo,self.branch,token="test-token",opener=sender)
        self.assertEqual(result["status"],"already_current")
        self.assertFalse(result["updated"])
        self.assertNotIn("PUT",[v[0] for v in sender.calls])
    def test_refuses_to_override_workflow_pages_mode(self):
        sender=FakeGitHub(workflow=True)
        self.assertEqual(sync_pages_source(self.repo,self.branch,token="test",opener=sender)["status"],
                         "workflow_mode_requires_explicit_deployment")
        self.assertNotIn("PUT",[v[0] for v in sender.calls])
    def test_no_pages_site_is_not_created(self):
        sender=FakeGitHub(pages=False)
        self.assertEqual(sync_pages_source(self.repo,self.branch,token="test",opener=sender)["status"],
                         "pages_not_enabled")
        self.assertNotIn("POST",[v[0] for v in sender.calls])
    def test_permission_denied_is_reported_not_suppressed(self):
        sender=FakeGitHub(status=403)
        result=sync_pages_source(self.repo,self.branch,token="test",opener=sender)
        self.assertEqual(result["status"],"pages_update_failed")
        self.assertEqual(result["http_status"],403)
        self.assertFalse(result["updated"])
    def test_invalid_branch_or_unknown_owner_never_touches_network(self):
        sender=FakeGitHub()
        for repo,branch in [
            ("danielnep/app-hondas-maxvel","main"),
            ("other/app-hondas-maxvel",self.branch),
            (self.repo,"fix/../main"),
            (self.repo,"fix//invalid")
        ]:
            with self.subTest(repo=repo,branch=branch),self.assertRaises(ValueError):
                sync_pages_source(repo,branch,token="test",opener=sender)
        self.assertEqual(sender.calls,[])


if __name__=="__main__":
    unittest.main()
