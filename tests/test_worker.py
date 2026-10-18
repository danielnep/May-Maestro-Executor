"""Integration proofs use local fixtures and explicit fake model responses, no API."""
import json
import hashlib
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from horizon_core.publish import publish
from horizon_core.gateway import GatewayError
from horizon_core.registry import AGENT_IDS
from horizon_core.state import State
from horizon_core.worker import PersistenceError, Worker


def action(name, args=None, summary='Fixture model decision'):
    return {'action': name, 'args': args or {}, 'summary': summary,
            'metadata': {'model': 'fake-model', 'test_only': True}}


class FakeGateway:
    def __init__(self, decide):
        self.decide, self.calls = decide, []

    def complete(self, agent, messages):
        context = json.loads(messages[0]['content'])
        self.calls.append((agent['id'], context))
        return self.decide(agent, context)


class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'worktree'
        self.root.mkdir()
        (self.root / 'README.md').write_text('fixture repository documentation', encoding='utf-8')
        subprocess.run(['git', 'init', str(self.root)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'switch', '-c', 'feat/test'], check=True, capture_output=True)
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.state = State(self.path)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_seventeen_specialists_independent_calls_tools_memory_and_restore(self):
        def decide(agent, context):
            step = len(context['history'])
            if step == 0:
                return action('read_file', {'path': 'README.md'})
            if step == 1:
                self.assertEqual(context['history'][0]['outcome']['content'], 'fixture repository documentation')
                return action('remember', {'key': 'evidence', 'value': {'agent': agent['id'], 'read': True}})
            self.assertEqual(context['memory']['evidence']['agent'], agent['id'])
            return action('finish', summary='Read fixture and persisted own memory')
        gateway = FakeGateway(decide)
        persists = []
        worker = Worker(self.state, gateway, self.root, persist=lambda: persists.append(True))
        for agent_id in AGENT_IDS:
            self.state.enqueue(agent_id, agent_id, 'fixture', 'Read fixture and remember evidence')
            finished = worker.run_one()
            self.assertEqual(finished['status'], 'succeeded')
            self.assertEqual(finished['result']['status'], 'reported')
            self.assertEqual(finished['result']['agent'], agent_id)
        self.assertEqual(len(gateway.calls), 51)
        self.assertEqual({agent for agent, _ in gateway.calls}, set(AGENT_IDS))
        self.assertGreaterEqual(len(persists), 68)
        snapshot = Path(self.tmp.name) / 'snapshot.sqlite'
        self.state.backup(snapshot)
        restored = State(snapshot)
        try:
            for agent_id in AGENT_IDS:
                self.assertEqual(restored.recall('fixture', agent_id), {'evidence': {'agent': agent_id, 'read': True}})
                self.assertEqual(restored.get(agent_id)['checkpoint']['step'], 2)
                self.assertEqual(restored.get(agent_id)['result']['model']['model'], 'fake-model')
            self.assertEqual(restored.recall('other-project', 'frontend-engineer'), {})
        finally:
            restored.close()

    def test_maestro_delegates_dag_frontend_qa_corregedoria_results(self):
        plan = [
            {'id': 'frontend', 'agent': 'frontend-engineer', 'objective': 'Write sample UI fixture'},
            {'id': 'qa', 'agent': 'qa-engineer', 'objective': 'Test sample UI fixture', 'depends_on': ['frontend']},
            {'id': 'review', 'agent': 'corregedoria-may', 'objective': 'Review QA evidence report', 'depends_on': ['qa']},
        ]
        def decide(agent, context):
            agent_id, history = agent['id'], context['history']
            if agent_id == 'may-maestro':
                return action('delegate', {'tasks': plan}) if not history else action('finish', summary='Delegated, not verified')
            if agent_id == 'frontend-engineer':
                return action('write_file', {'path': 'app/ui.py', 'content': 'TITLE = "Ronda Pro"\n'}) if not history else action('finish', summary='Wrote UI fixture')
            if agent_id == 'qa-engineer':
                self.assertEqual(context['dependencies']['job:frontend']['agent'], 'frontend-engineer')
                return action('run_check', {'name': 'python-unittest'}) if not history else action('finish', summary='Report failed test honestly')
            report = context['dependencies']['job:qa']
            self.assertFalse(report['verification'][0]['passed'])
            self.assertEqual(report['verification'][0]['exit_code'], 1)
            return action('finish', summary='QA reported failure; delivery not verified')
        self.state.enqueue('job', 'may-maestro', 'fixture', 'Delegate fixture implementation and review',
                           payload={'branch': 'feat/test', 'allowed_paths': ['app/*.py']})
        gateway = FakeGateway(decide)
        worker = Worker(self.state, gateway, self.root)
        with patch('horizon_core.tools.shutil.which', return_value='/usr/bin/docker'), patch(
            'horizon_core.tools.Tools._docker_check', return_value={
                'check': 'python-unittest', 'exit_code': 1, 'passed': False, 'output': 'FAIL: fixture assertion'}):
            for expected in ['job', 'job:frontend', 'job:qa', 'job:review']:
                result = worker.run_one()
                self.assertEqual(result['task_id'], expected)
                self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "Ronda Pro"\n')
        self.assertFalse(self.state.get('job:qa')['result']['verification'][0]['passed'])
        self.assertEqual(self.state.get('job:qa')['result']['status'], 'reported')
        self.assertIsNone(worker.run_one())

    def test_retryable_gateway_failure_resumes_history_without_repeating_tool(self):
        attempts = []
        def decide(agent, context):
            attempts.append(len(context['history']))
            if not context['history']:
                return action('remember', {'key': 'completed-step', 'value': 1})
            if len(attempts) == 2:
                raise GatewayError('quota reached', retryable=True)
            self.assertEqual(context['memory']['completed-step'], 1)
            return action('finish')
        gateway = FakeGateway(decide)
        worker = Worker(self.state, gateway, self.root)
        self.state.enqueue('retry', 'frontend-engineer', 'fixture', 'Resume previously persisted checkpoint')
        with patch('horizon_core.state.time.time', return_value=1000):
            result = worker.run_one()
            self.assertEqual(result['status'], 'queued')
            self.assertEqual(result['checkpoint']['step'], 1)
            self.assertEqual(result['not_before'], 1005)
            self.assertIsNone(worker.run_one())
        # New worker + fresh connection simulates process loss between invocations.
        reopened = State(self.path)
        try:
            with patch('horizon_core.state.time.time', return_value=1006):
                result = Worker(reopened, gateway, self.root).run_one()
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['attempts'], 2)
            self.assertEqual(attempts, [0, 1, 1])
            self.assertEqual(sum(event['kind'] == 'checkpoint' for event in reopened.events('retry')), 1)
        finally:
            reopened.close()

    def test_both_providers_unavailable_preserves_checkpoint_and_safe_failure_details(self):
        self.state.enqueue('providers', 'frontend-engineer', 'fixture', 'Keep durable history when both providers fail')
        safe_failures = [
            {'provider': 'gemini', 'reason': 'quota', 'status': 429, 'retryable': True},
            {'provider': 'deepseek', 'reason': 'authentication', 'status': 401, 'retryable': False},
        ]
        def decide(agent, context):
            if not context['history']:
                return action('remember', {'key': 'saved', 'value': True})
            failures = [dict(safe_failures[0], upstream_body='do-not-store-provider-body'), safe_failures[1]]
            raise GatewayError('do-not-store-error-message-or-key', retryable=True,
                               provider='all', reason='providers_unavailable', failures=failures)
        gateway = FakeGateway(decide)
        with patch('horizon_core.state.time.time', return_value=1000):
            task = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(task['checkpoint']['step'], 1)
        self.assertEqual(task['checkpoint']['history'][0]['outcome'], {'remembered': 'saved'})
        self.assertEqual(self.state.recall('fixture', 'frontend-engineer'), {'saved': True})
        self.assertEqual(task['error'], {'kind': 'GatewayError', 'provider': 'all',
            'reason': 'providers_unavailable', 'status': None, 'retryable': True, 'failures': safe_failures})
        persisted = json.dumps(self.state.events('providers'))
        self.assertNotIn('do-not-store', persisted)
        self.assertNotIn('upstream_body', persisted)

    def test_failed_tool_stays_in_verification_even_when_model_finishes(self):
        gateway = FakeGateway(lambda agent, context: action('run_check', {'name': 'python-unittest'})
                              if not context['history'] else action('finish', summary='Could not run Docker check'))
        self.state.enqueue('check', 'qa-engineer', 'fixture', 'Record unavailable verification tool')
        with patch('horizon_core.tools.shutil.which', return_value=None):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['status'], 'reported')
        self.assertEqual(result['result']['verification'][0]['tool_error'], 'RuntimeError')
        self.assertNotIn('passed', result['result']['verification'][0])

    def test_lost_worktree_recovers_checkpoint_and_publishes_succeeded_workspace(self):
        subprocess.run(['git', '-C', str(self.root), 'add', 'README.md'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-m', 'fixture base'], check=True, capture_output=True)
        baseline = Path(self.tmp.name) / 'baseline.git'
        subprocess.run(['git', 'clone', '--bare', str(self.root), str(baseline)], check=True, capture_output=True)
        payload = {'branch': 'feat/test', 'allowed_paths': ['app/*.py']}
        self.state.enqueue('workspace', 'frontend-engineer', 'fixture', 'Write recoverable UI and finish report', payload=payload)
        calls = []
        def decide(agent, context):
            calls.append(len(context['history']))
            if len(context['history']) == 0:
                return action('write_file', {'path': 'app/ui.py', 'content': 'TITLE = "first"\n'})
            if len(context['history']) == 1:
                return action('write_file', {'path': 'app/ui.py', 'content': 'TITLE = "final"\n'})
            if len(calls) == 3:
                raise GatewayError('fake interrupted provider', retryable=True)
            self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "final"\n')
            return action('finish', summary='Recovered UI written by prior attempt')
        gateway = FakeGateway(decide)
        with patch('horizon_core.state.time.time', return_value=1000):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'queued')
        mutation = self.state.recall('fixture', '__workspace__')['app/ui.py']
        self.assertEqual(mutation['content'], 'TITLE = "final"\n')
        self.assertEqual(mutation['expected_sha256'], hashlib.sha256(b'').hexdigest())
        # Recreate from the unchanged Git baseline: generated files exist only in SQLite.
        def lose_disk_and_clone():
            shutil.rmtree(self.root)
            subprocess.run(['git', 'clone', str(baseline), str(self.root)], check=True, capture_output=True)
            self.assertFalse((self.root / 'app/ui.py').exists())
        lose_disk_and_clone()
        reopened = State(self.path)
        try:
            with patch('horizon_core.state.time.time', return_value=1006):
                finished = Worker(reopened, gateway, self.root).run_one()
            self.assertEqual(finished['status'], 'succeeded')
            self.assertEqual(calls, [0, 1, 2, 2])
            self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "final"\n')
            # Succeeded tasks are no longer claimable, but Publisher must still restore them.
            lose_disk_and_clone()
            self.assertIsNone(Worker(reopened, gateway, self.root).run_one())
            base_sha = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'],
                                      check=True, capture_output=True, text=True).stdout.strip()
            requests = []
            class FakeGitHub:
                def _request(inner, method, path, body=None):
                    requests.append((method, path, body))
                    if (method, path) == ('GET', ''):
                        return {'default_branch': 'main'}
                    if method == 'GET' and path.startswith('/git/ref/heads/'):
                        return {'object': {'sha': base_sha}}
                    if method == 'GET' and path.startswith('/git/commits/'):
                        return {'tree': {'sha': 'base-tree'}}
                    if (method, path) == ('POST', '/git/trees'):
                        self.assertEqual(body['tree'], [{'path': 'app/ui.py', 'mode': '100644',
                            'type': 'blob', 'content': 'TITLE = "final"\n'}])
                        return {'sha': 'updated-tree'}
                    if (method, path) == ('POST', '/git/commits'):
                        self.assertEqual(body['parents'], [base_sha])
                        return {'sha': 'generated-commit'}
                    if (method, path) == ('PATCH', '/git/refs/heads/feat/test'):
                        self.assertEqual(body, {'sha': 'generated-commit', 'force': False})
                        return {}
                    raise AssertionError((method, path, body))
            published = publish(reopened, self.root, 'fixture/repository', api=FakeGitHub())
            self.assertTrue(published['published'])
            self.assertEqual(published['files'], ['app/ui.py'])
            self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "final"\n')
            self.assertEqual(reopened.get('workspace')['status'], 'succeeded')
            self.assertEqual(len(requests), 6)
        finally:
            reopened.close()

    def test_persistence_failure_after_complete_preserves_succeeded_and_does_not_fail(self):
        self.state.enqueue('completed', 'frontend-engineer', 'fixture', 'Persist a completed report safely')
        gateway = FakeGateway(lambda agent, context: action('finish', summary='Completed local fixture'))
        persist_calls = []
        def persist():
            persist_calls.append(True)
            if len(persist_calls) == 2:
                raise OSError('fake remote write rejected after local commit')
        with patch.object(self.state, 'fail', wraps=self.state.fail) as fail, self.assertRaises(PersistenceError):
            Worker(self.state, gateway, self.root, persist=persist).run_one()
        fail.assert_not_called()
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(self.state.get('completed')['status'], 'succeeded')
        self.assertEqual(self.state.get('completed')['result']['summary'], 'Completed local fixture')
        self.assertIsNone(self.state.get('completed')['error'])

    def test_default_has_no_step_ceiling_and_preserves_complete_history(self):
        self.state.enqueue('long', 'frontend-engineer', 'fixture', 'Reason through more than twelve fixture decisions')
        observed = []
        def decide(agent, context):
            count = len(context['history'])
            observed.append(count)
            self.assertIsNone(context['remaining_steps'])
            if count == 18:
                self.assertEqual(context['history'][0]['outcome'], {'remembered': 'step-0'})
                self.assertEqual(context['history'][-1]['outcome'], {'remembered': 'step-17'})
                return action('finish')
            return action('remember', {'key': 'step-' + str(count), 'value': count})
        gateway = FakeGateway(decide)
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['steps'], 19)
        self.assertEqual(observed, list(range(19)))
        self.assertEqual(len(result['checkpoint']['history']), 18)

    def test_time_slices_resume_fresh_workers_without_consuming_retry_attempts(self):
        self.state.enqueue('sliced', 'frontend-engineer', 'fixture', 'Resume cooperative slices until actual completion', max_attempts=1)
        lengths = []
        def decide(agent, context):
            count = len(context['history'])
            lengths.append(count)
            if count >= 5:
                return action('finish')
            return action('remember', {'key': 'step-' + str(count), 'value': count})
        gateway = FakeGateway(decide)
        for index in range(5):
            reopened = State(self.path)
            try:
                # Each invocation may complete one action before its host timeslice ends.
                with patch('horizon_core.worker.time.monotonic', side_effect=[0, 2]):
                    result = Worker(reopened, gateway, self.root, slice_seconds=1).run_one()
                self.assertEqual(result['status'], 'queued')
                self.assertEqual(result['attempts'], 0)
                self.assertEqual(result['checkpoint']['step'], index + 1)
                self.assertIsNone(result['lease_owner'])
                self.assertIsNone(result['error'])
            finally:
                reopened.close()
        final = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['attempts'], 1)
        self.assertEqual(lengths, list(range(6)))
        self.assertEqual(sum(event['kind'] == 'yielded' for event in self.state.events('sliced')), 5)
        self.assertGreaterEqual(sum(event['kind'] == 'lease_renewed' for event in self.state.events('sliced')), 11)

    def test_remote_persistence_failure_stops_before_model_or_tool(self):
        self.state.enqueue('persist', 'frontend-engineer', 'fixture', 'Stop if remote checkpoint is unavailable')
        gateway = FakeGateway(lambda agent, context: action('finish'))
        def failed_persist():
            raise OSError('fake remote failure')
        with self.assertRaises(PersistenceError):
            Worker(self.state, gateway, self.root, persist=failed_persist).run_one()
        self.assertEqual(gateway.calls, [])
        self.assertEqual(self.state.get('persist')['status'], 'running')

    def test_unauthorized_model_tool_fails_task_and_budget_is_terminal(self):
        self.state.enqueue('denied', 'corregedoria-may', 'fixture', 'Attempt unauthorized fixture modification')
        gateway = FakeGateway(lambda agent, context: action('write_file', {'path': 'README.md', 'content': 'forbidden'}))
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual((self.root / 'README.md').read_text(), 'fixture repository documentation')
        self.state.enqueue('budget', 'frontend-engineer', 'fixture', 'Exercise bounded fixture reasoning')
        gateway = FakeGateway(lambda agent, context: action('read_file', {'path': 'README.md'}))
        result = Worker(self.state, gateway, self.root, max_steps=2).run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('budget', result['error'])


if __name__ == '__main__':
    unittest.main()
