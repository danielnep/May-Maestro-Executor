"""Regression: provider quota failure must not permanently poison the agent DAG."""
import unittest
from horizon_core.state import State


class ProviderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state = State(':memory:')
        self.addCleanup(self.state.close)

    def test_provider_failures_stop_after_third_attempt_without_losing_checkpoint(self):
        s=self.state
        s.enqueue('arch','software-architect','michelle','Define contracts',max_attempts=3)
        for idx in range(3):
            now=100+idx*1500
            task=s.claim('worker',now=now)
            self.assertEqual(task['task_id'],'arch')
            s.checkpoint('arch','worker',{'step':idx+1},now=now+1)
            s.fail('arch','worker',{'kind':'GatewayError','reason':'providers_unavailable','retryable':True},
                   retryable=True,now=now+2)
            self.assertEqual(s.get('arch')['status'],'failed' if idx == 2 else 'queued')
        self.assertIsNone(s.claim('worker',now=9999))
        self.assertEqual(s.get('arch')['checkpoint']['step'],3)
        self.assertEqual(s.get('arch')['attempts'],3)
        self.assertEqual(s.recover_provider_outages(now=10000)['requeued_provider_failures'],0)

    def test_recovers_only_provider_outage_and_dependents_after_old_failure(self):
        s=self.state
        s.enqueue('arch','software-architect','michelle','Architecture')
        s.enqueue('parser','integration-engineer','michelle','Parse spreadsheets',depends_on=['arch'])
        s.enqueue('frontend','frontend-engineer','michelle','Render dashboard',depends_on=['parser'])
        s.enqueue('unrelated','qa-engineer','other','Stop on invalid schema')
        with s._tx():
            s.db.execute("UPDATE tasks SET status='failed',attempts=2,error=? WHERE task_id='arch'",
                         ('{"reason":"providers_unavailable","retryable":true}',))
            s.db.execute("UPDATE tasks SET status='blocked' WHERE task_id IN ('parser','frontend')")
            s.db.execute("UPDATE tasks SET status='failed',error=? WHERE task_id='unrelated'",
                         ('{"reason":"permission_denied","retryable":false}',))
        value=s.recover_provider_outages(now=1234)
        # A requeued parent is NOT succeeded. Dependent tasks cannot be
        # released merely because the provider fallback became available.
        self.assertEqual(value, {'requeued_provider_failures': 1,
                                 'unblocked_dependents': 0})
        self.assertEqual([s.get(id)['status'] for id in ('arch','parser','frontend','unrelated')],
                         ['queued','blocked','blocked','failed'])
        self.assertEqual(s.recover_provider_outages(now=1235),
                         {'requeued_provider_failures':0,'unblocked_dependents':0})
        with s._tx():
            s.db.execute("UPDATE tasks SET status='succeeded' WHERE task_id='arch'")
        self.assertEqual(s.recover_provider_outages(now=1236)['unblocked_dependents'], 1)
        self.assertEqual(s.get('parser')['status'], 'queued')
        self.assertEqual(s.get('frontend')['status'], 'blocked')
        with s._tx():
            s.db.execute("UPDATE tasks SET status='succeeded' WHERE task_id='parser'")
        self.assertEqual(s.recover_provider_outages(now=1237)['unblocked_dependents'], 1)
        self.assertEqual(s.get('frontend')['status'], 'queued')

    def test_exhausted_legacy_failure_does_not_requeue(self):
        s=self.state
        s.enqueue('old','backend-engineer','legacy','Keep state')
        with s._tx():
            s.db.execute("UPDATE tasks SET status='failed',attempts=6,error=? WHERE task_id='old'",
                         ('{"reason":"providers_unavailable","retryable":true}',))
        self.assertEqual(s.recover_provider_outages(now=1500),
                         {'requeued_provider_failures':0,'unblocked_dependents':0})
        self.assertEqual(s.get('old')['status'],'failed')

    def test_noop_recovery_does_not_begin_transaction_or_snapshot(self):
        s=self.state
        s.enqueue('healthy','software-architect','michelle','Architecture finished')
        original=s._tx
        def refuse():
            raise AssertionError('No-op recovery must never start transaction')
        s._tx=refuse
        self.assertEqual(s.recover_provider_outages(),
                         {'requeued_provider_failures':0,'unblocked_dependents':0})
        s._tx=original

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
