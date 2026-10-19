"""Regression: provider quota failure must not permanently poison the agent DAG."""
import unittest
from horizon_core.state import State


class ProviderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state = State(':memory:')
        self.addCleanup(self.state.close)

    def test_provider_failures_retry_after_attempt_budget_without_losing_checkpoint(self):
        s=self.state
        s.enqueue('arch','software-architect','michelle','Define contracts',max_attempts=3)
        for idx in range(4):
            now=100+idx*1500
            task=s.claim('worker',now=now)
            self.assertEqual(task['task_id'],'arch')
            s.checkpoint('arch','worker',{'step':idx+1},now=now+1)
            s.fail('arch','worker',{'kind':'GatewayError','reason':'providers_unavailable','retryable':True},
                   retryable=True,now=now+2)
            self.assertEqual(s.get('arch')['status'],'queued')
        self.assertEqual(s.get('arch')['checkpoint']['step'],4)
        self.assertEqual(s.get('arch')['attempts'],4)

    def test_recovers_only_provider_outage_and_dependents_after_old_failure(self):
        s=self.state
        s.enqueue('arch','software-architect','michelle','Architecture')
        s.enqueue('parser','integration-engineer','michelle','Parse spreadsheets',depends_on=['arch'])
        s.enqueue('frontend','frontend-engineer','michelle','Render dashboard',depends_on=['parser'])
        s.enqueue('unrelated','qa-engineer','other','Stop on invalid schema')
        with s._tx():
            s.db.execute("UPDATE tasks SET status='failed',attempts=3,error=? WHERE task_id='arch'",
                         ('{"reason":"providers_unavailable","retryable":true}',))
            s.db.execute("UPDATE tasks SET status='blocked' WHERE task_id IN ('parser','frontend')")
            s.db.execute("UPDATE tasks SET status='failed',error=? WHERE task_id='unrelated'",
                         ('{"reason":"permission_denied","retryable":false}',))
        value=s.recover_provider_outages(now=1234)
        self.assertEqual(value,{'requeued_provider_failures':1,'unblocked_dependents':2})
        self.assertEqual([s.get(id)['status'] for id in ('arch','parser','frontend','unrelated')],
                         ['queued','queued','queued','failed'])
        self.assertEqual(s.recover_provider_outages(now=1235),
                         {'requeued_provider_failures':0,'unblocked_dependents':0})

    def test_unrelated_terminal_dependency_remains_blocked(self):
        s=self.state
        s.enqueue('bad','software-architect','other','Permanently invalid')
        s.enqueue('later','qa-engineer','other','Dependent task',depends_on=['bad'])
        with s._tx():
            s.db.execute("UPDATE tasks SET status='failed',error=? WHERE task_id='bad'",
                         ('{"reason":"invalid_action","retryable":false}',))
            s.db.execute("UPDATE tasks SET status='blocked' WHERE task_id='later'")
        self.assertEqual(s.recover_provider_outages()['unblocked_dependents'],0)
        self.assertEqual(s.get('later')['status'],'blocked')


if __name__=='__main__':
    unittest.main()
