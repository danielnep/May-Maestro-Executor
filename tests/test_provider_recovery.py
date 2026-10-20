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
        self.assertEqual(value,{'requeued_provider_failures':1,'unblocked_dependents':2})
        self.assertEqual([s.get(id)['status'] for id in ('arch','parser','frontend','unrelated')],
                         ['queued','queued','queued','failed'])
        self.assertEqual(s.recover_provider_outages(now=1235),
                         {'requeued_provider_failures':0,'unblocked_dependents':0})

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

    def test_recovers_legacy_unedited_visual_failure_once(self):
        s = self.state
        s.enqueue('ui', 'horizon-ui-guardian', 'chat:proof', 'Audit current source')
        s.enqueue('frontend', 'frontend-engineer', 'chat:proof', 'Implement fixes',
                  depends_on=['ui'])
        history = [
            {'action': 'read_file', 'outcome': {}},
            {'action': 'run_check', 'outcome': {'check': 'browser-playwright', 'passed': False}},
            *[{'action': 'visual_evidence_required',
               'outcome': {'reason': 'browser_check_not_verified'}} for _ in range(3)]
        ]
        self.assertEqual(s.claim('agent', now=100)['task_id'], 'ui')
        s.checkpoint('ui', 'agent', {'step': 7, 'history': history,
            'failure_counts': {'missing_visual_evidence': 3,
                               'check:browser-playwright': 2}}, now=101)
        s.fail('ui', 'agent', {'reason': 'visual_evidence_missing',
               'detail': 'browser_check_not_verified'}, retryable=False, now=102)
        self.assertIsNone(s.claim('idle', now=103))
        self.assertEqual(s.get('frontend')['status'], 'blocked')
        self.assertEqual(s.recover_legacy_visual_audits(now=200), ['ui'])
        self.assertEqual(s.recover_legacy_visual_audits(now=201), [])
        row = s.get('ui')
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['checkpoint']['history'], history)
        self.assertTrue(row['checkpoint']['visual_audit_recovery_used'])
        self.assertFalse(row['checkpoint']['failure_counts'])
        self.assertEqual(s.recover_provider_outages(now=202)['unblocked_dependents'], 1)
        self.assertEqual(s.get('frontend')['status'], 'queued')
        self.assertEqual(s.claim('agent', now=203)['task_id'], 'ui')
        s.fail('ui', 'agent', {'reason': 'visual_evidence_missing',
               'detail': 'browser_check_not_verified'}, retryable=False, now=204)
        self.assertEqual(s.recover_legacy_visual_audits(now=205), [])

    def test_edited_visual_failure_never_automatically_recovers(self):
        s = self.state
        s.enqueue('ui', 'horizon-ui-guardian', 'chat:proof', 'Fix source')
        self.assertEqual(s.claim('agent', now=100)['task_id'], 'ui')
        history = [{'action': 'write_file', 'outcome': {
            'before_sha256': 'old', 'sha256': 'new'}},
            {'action': 'run_check', 'outcome': {
                'check': 'browser-playwright', 'passed': False}},
            *[{'action': 'visual_evidence_required'} for _ in range(3)]]
        s.checkpoint('ui', 'agent', {'history': history}, now=101)
        s.fail('ui', 'agent', {'reason': 'visual_evidence_missing',
               'detail': 'browser_check_not_verified'}, retryable=False, now=102)
        self.assertEqual(s.recover_legacy_visual_audits(now=200), [])
        self.assertEqual(s.get('ui')['status'], 'failed')

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
