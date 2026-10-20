"""Terminal delivery must not hide failed or unexecuted QA checks."""
import unittest
from horizon_core.commit_bridge import summarize

class ReportQualityTests(unittest.TestCase):
    def task(self, checks, agent="qa-engineer"):
        return {"task_id":"qa","agent":agent,"status":"succeeded","attempts":1,
            "result":{"summary":"Finished","verification":checks}}

    def test_failed_check_cannot_be_reported_complete(self):
        report=summarize("fixture",[self.task([{"check":"node-test","passed":False,"exit_code":125,"tests_run":0}])])
        self.assertEqual(report["status"],"needs_attention")
        self.assertFalse(report["quality"]["approved"])
        self.assertEqual(report["quality"]["failed_checks"][0]["exit_code"],125)

    def test_missing_qa_check_cannot_be_reported_complete(self):
        report=summarize("fixture",[self.task([])])
        self.assertEqual(report["status"],"needs_attention")
        self.assertEqual(report["quality"]["qa_missing_evidence"],["qa"])

    def test_verified_check_can_report_complete(self):
        report=summarize("fixture",[self.task([{"check":"node-test","passed":True,
            "exit_code":0,"tests_run":7}])])
        self.assertEqual(report["status"],"reported_complete")
        self.assertTrue(report["quality"]["approved"])

    def test_published_delivery_requires_independent_qa(self):
        publication={"published":True,"target_repo":"danielnep/app-hondas-maxvel",
          "branch":"agent/review","commit":"a"*40,
          "pages_preview":{"status":"verified","branch":"agent/review",
            "page_url":"https://danielnep.github.io/app-hondas-maxvel/"}}
        report=summarize("fixture",[self.task([],agent="may-maestro")],publication)
        self.assertFalse(report["quality"]["approved"])
        self.assertTrue(report["quality"]["qa_missing_for_publication"])
        self.assertEqual(report["status"],"needs_attention")
        self.assertEqual(report["may_review"]["status"],"quality_blocked")

    def test_model_identity_requires_real_result_metadata_and_is_sanitized(self):
        valid=self.task([{"check":"node-test","passed":True,"exit_code":0,"tests_run":1}])
        valid["result"]["model"]={"provider":"gemini","model":"gemini-3.8-flash",
                                   "secret":"must-not-leak"}
        report=summarize("fixture",[valid])
        self.assertEqual(report["tasks"][0]["model_used"],
                         {"provider":"gemini","model":"gemini-3.8-flash"})
        self.assertNotIn("must-not-leak",str(report))
        valid["result"]["model"]={"provider":"unknown","model":"attack"}
        report=summarize("fixture",[valid])
        self.assertIsNone(report["tasks"][0]["model_used"])

    def test_awaiting_publication_preserves_quality_failure(self):
        report=summarize("fixture",[self.task([{"check":"node-test","passed":False,
            "exit_code":125,"tests_run":0}])],{"awaiting_host_publication":True})
        self.assertEqual(report["status"],"awaiting_host_publication")
        self.assertFalse(report["quality"]["approved"])
        self.assertEqual(report["quality"]["failed_checks"][0]["check"],"node-test")

if __name__=="__main__":
    unittest.main()
