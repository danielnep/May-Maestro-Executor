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

    def test_awaiting_publication_preserves_quality_failure(self):
        report=summarize("fixture",[self.task([{"check":"node-test","passed":False,
            "exit_code":125,"tests_run":0}])],{"awaiting_host_publication":True})
        self.assertEqual(report["status"],"awaiting_host_publication")
        self.assertFalse(report["quality"]["approved"])
        self.assertEqual(report["quality"]["failed_checks"][0]["check"],"node-test")

if __name__=="__main__":
    unittest.main()
