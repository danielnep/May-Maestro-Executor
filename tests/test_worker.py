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
        self._fixture_visual = patch('horizon_core.worker.requires_visual_review', return_value=False)
        self._fixture_visual.start()
        self.addCleanup(self._fixture_visual.stop)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_vision_provider_outage_does_not_abort_other_ui_qa_steps(self):
        self.state.enqueue('vision-outage', 'qa-engineer', 'fixture',
                           'Audit a captured screenshot and report its unavailable provider')
        def decide(agent, context):
            if not context['history']:
                return action('inspect_image', {'index': 0, 'question': 'Inspect image layout'})
            evidence = context['history'][-1]['outcome']
            self.assertEqual(evidence['tool_error'], 'VisionUnavailable')
            self.assertFalse(evidence['actual_image_analyzed'])
            self.assertEqual(evidence['status'], 429)
            self.assertEqual(evidence['reason'], 'vision_unavailable')
            return action('finish', summary='Vision unavailable; DOM QA remains required')
        gateway = FakeGateway(decide)
        gateway.inspect_image = lambda *args: (_ for _ in ()).throw(
            GatewayError('Vision quota exhausted', provider='gemini',
                         reason='vision_unavailable', retryable=True, status=429))
        with patch('horizon_core.tools.Tools.evidence_image',
                   return_value=b'PNG evidence provided by browser proof'):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['checkpoint']['history'][0]['action'], 'inspect_image')
        self.assertEqual(result['checkpoint']['history'][0]['outcome']['tool_error'], 'VisionUnavailable')
        self.assertEqual(len(gateway.calls), 2)

    def test_invalid_model_protocol_can_correct_itself_without_replaying_tool(self):
        def decide(agent, context):
            if not context['history']:
                return action('read_file', {'path': 'README.md'})
            if len(context['history']) == 1:
                error = GatewayError('invalid response', retryable=True, provider='groq', reason='invalid_action')
                error.shape = {'object': True, 'exact_keys': False, 'action_permitted': True,
                               'args_object': True, 'summary_text': True}
                raise error
            self.assertEqual([entry['action'] for entry in context['history']], ['read_file', 'protocol_error'])
            self.assertFalse(context['history'][-1]['outcome']['shape']['exact_keys'])
            return action('finish')
        gateway = FakeGateway(decide)
        self.state.enqueue('protocol', 'backend-engineer', 'fixture', 'Read and recover from invalid protocol')
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual(result['checkpoint']['step'], 2)

    def test_rate_window_defers_without_consuming_attempts_or_using_chatgpt(self):
        self.state.enqueue("throttle", "frontend-engineer", "fixture",
                           "Wait for a shared Groq free-minute slot")
        def decide(agent, context):
            error = GatewayError("Groq minute quota pending", provider="groq",
                                 reason="rate_limited_local", retryable=True)
            error.retry_after_seconds = 71
            raise error
        gateway = FakeGateway(decide)
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["attempts"], 0)
        self.assertGreater(result["not_before"], 0)
        self.assertEqual(result["checkpoint"]["step"], 0)
        self.assertNotIn("pending_may", result["checkpoint"])
        self.assertEqual(len(gateway.calls), 1)

    def test_may_consult_action_not_granted_by_any_agent(self):
        from horizon_core.registry import load_agent
        from horizon_core.gateway import ACTIONS
        self.assertNotIn("consult_may", ACTIONS)
        for agent_id in AGENT_IDS:
            self.assertNotIn("consult_may", load_agent(agent_id)["tools"])

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
            self.state.enqueue(agent_id, agent_id, 'fixture', 'Read fixture and remember evidence',
                               payload={'visual_review_required': False})
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

    def test_maestro_recovers_invalid_delegation_without_losing_task(self):
        def decide(agent, context):
            history = context['history']
            if not history:
                # First proposal references a dependency that has not been created.
                return action('delegate', {'tasks': [
                    {'id': 'qa', 'agent': 'qa-engineer',
                     'objective': 'Test a newly implemented synthetic app',
                     'depends_on': ['backend']}
                ]})
            if len(history) == 1:
                outcome = history[0]['outcome']
                self.assertEqual(outcome['reason'], 'invalid_delegation')
                self.assertEqual(outcome['tool_error'], 'ValueError')
                self.assertNotIn('delegated', outcome)
                self.assertEqual(self.state.tasks(project='recoverable'), [self.state.get('fix-plan')])
                # Correct the dependency ordering on the next independent call.
                return action('delegate', {'tasks': [
                    {'id': 'backend', 'agent': 'backend-engineer',
                     'objective': 'Implement synthetic app with deterministic tests',
                     'depends_on': []},
                    {'id': 'qa', 'agent': 'qa-engineer',
                     'objective': 'Execute genuine checks after implementation',
                     'depends_on': ['backend']}
                ]})
            self.fail('Maestro must not request a second finish after delegation')
        self.state.enqueue('fix-plan', 'may-maestro', 'recoverable',
                           'Delegate validated backend and QA responsibilities')
        gateway = FakeGateway(decide)
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(result['checkpoint']['step'], 2)
        self.assertEqual(result['result']['stage'], 'delegated_implementation_pending')
        self.assertEqual(result['result']['delegated'], ['fix-plan:backend', 'fix-plan:qa'])
        self.assertEqual(self.state.get('fix-plan:backend')['status'], 'queued')
        self.assertEqual(self.state.get('fix-plan:qa')['status'], 'queued')

    def test_owner_single_delegate_releases_children_without_second_provider_call(self):
        self.state.enqueue('owner-handoff', 'may-maestro', 'fixture',
                           'Delegate mobile audit and QA after provider outage')
        gateway = FakeGateway(lambda a, c: (_ for _ in ()).throw(
            GatewayError('Both model providers unavailable', provider='all',
                         reason='providers_unavailable', retryable=True)))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'round-zero', 'issue': 41}):
            waiting = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(waiting['status'], 'queued')
        reply = {'source': 'public_owner_comment', 'decision_untrusted': {
            'action': 'delegate', 'args': {'tasks': [
                {'id': 'audit', 'agent': 'horizon-skills',
                 'objective': 'Audit the authorized source', 'depends_on': []},
                {'id': 'qa', 'agent': 'qa-engineer',
                 'objective': 'Review evidence after audit', 'depends_on': ['audit']}]},
            'summary': 'Delegate auditable specialist work'}}
        with patch('horizon_core.state.time.time', return_value=waiting['not_before'] + 1), \
             patch('horizon_core.worker.read_consultation', return_value=reply), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('No second issue should be created')):
            completed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(completed['status'], 'succeeded')
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(completed['result']['stage'], 'delegated_implementation_pending')
        self.assertEqual(completed['result']['verification'], [])
        self.assertNotIn('pending_may', completed['checkpoint'])
        ready = self.state.claim('child-worker', now=waiting['not_before'] + 2)
        self.assertEqual(ready['task_id'], 'owner-handoff:audit')

    def test_legacy_second_round_releases_verified_handoff_without_reply(self):
        parent_id = 'legacy-owner-handoff'
        self.state.enqueue(parent_id, 'may-maestro', 'fixture',
                           'Resume verified specialist delegation')
        self.state.claim('legacy-worker')
        child_id = parent_id + ':audit'
        self.state.enqueue(child_id, 'horizon-skills', 'fixture',
                           'Audit the authorized source', depends_on=[parent_id])
        history = [
            {'action': 'may_provider_fallback', 'round': 0,
             'outcome': {'status': 'requested', 'ticket': 'old'}},
            {'action': 'delegate', 'outcome': {'delegated': [child_id]},
             'summary': 'Accepted owner reply'},
            {'action': 'may_provider_fallback', 'round': 1,
             'outcome': {'status': 'requested', 'ticket': 'new'}}]
        self.state.checkpoint(parent_id, 'legacy-worker', {
            'step': 1, 'history': history, 'delegated': [child_id],
            'pending_may': {'ticket': 'new', 'round': 1}})
        self.state.defer(parent_id, 'legacy-worker', seconds=60)
        future = self.state.get(parent_id)['not_before'] + 1
        gateway = FakeGateway(lambda a, c: self.fail('No model call expected'))
        with patch('horizon_core.state.time.time', return_value=future), \
             patch('horizon_core.worker.read_consultation',
                   side_effect=AssertionError('Unanswered issue must not be read')):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['delegated'], [child_id])
        self.assertEqual(result['result']['stage'], 'delegated_implementation_pending')
        self.assertNotIn('pending_may', result['checkpoint'])
        self.assertEqual(self.state.claim('child-worker', now=future + 2)['task_id'], child_id)
        self.assertEqual(gateway.calls, [])

    def test_round_one_recovers_nonadjacent_owner_delegate_without_issue_50(self):
        parent = 'may-recovery-v2'
        payload = {'branch': 'agent/test-real-ronda', 'allowed_paths': []}
        self.state.enqueue(parent, 'may-maestro', 'fixture', 'Restore durable P0 plan',
                           payload=payload)
        self.state.claim('original-worker')
        children = [parent + ':audit', parent + ':qa']
        self.state.enqueue(children[0], 'horizon-ui-guardian', 'fixture',
                           'Inspect actual UI', depends_on=[parent], payload=payload)
        self.state.enqueue(children[1], 'qa-engineer', 'fixture',
                           'Validate actual implementation',
                           depends_on=[parent, children[0]], payload=payload)
        self.state.checkpoint(parent, 'original-worker', {
            'step': 3,
            'history': [
                {'action': 'may_provider_fallback', 'round': 0,
                 'outcome': {'status': 'requested', 'ticket': 'issue-41'}},
                {'action': 'delegate', 'summary': 'Delegated after owner reply',
                 'outcome': {'delegated': children}},
                {'action': 'remember', 'outcome': {'remembered': 'verified-original-plan'}},
                {'action': 'may_provider_fallback', 'round': 1,
                 'outcome': {'status': 'requested', 'ticket': 'issue-50'}}],
            'delegated': children,
            'may_plan': [{'action': 'finish', 'args': {},
                          'summary': 'Delegation complete'}],
            'pending_may': {'ticket': 'issue-50', 'round': 1}})
        # The legacy workspace has an unrelated branch. Delegation recovery
        # must not replay it or attempt edits before releasing the DAG.
        self.state.remember('fixture', '__workspace__', 'README.md',
                            {'branch': 'other-branch', 'content': 'untouched'})
        self.state.defer(parent, 'original-worker', seconds=60)
        resumed_at = self.state.get(parent)['not_before'] + 1
        gateway = FakeGateway(lambda *a: self.fail('Recovery must not call models'))
        with patch('horizon_core.state.time.time', return_value=resumed_at), \
             patch('horizon_core.worker.read_consultation',
                   side_effect=AssertionError('Do not poll superseded issue')):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['stage'], 'delegated_implementation_pending')
        self.assertEqual(result['result']['verification'], [])
        self.assertEqual(result['result']['delegated'], children)
        self.assertEqual(result['checkpoint']['step'], 3)
        self.assertNotIn('pending_may', result['checkpoint'])
        self.assertNotIn('may_plan', result['checkpoint'])
        self.assertEqual(gateway.calls, [])
        self.assertEqual(self.state.claim('next-agent', now=resumed_at + 1)['task_id'],
                         children[0])

    def test_maestro_recovers_bad_plan_shape_from_model(self):
        def decide(agent, context):
            if not context['history']:
                return action('delegate', {'tasks': [None]})
            self.assertEqual(context['history'][0]['outcome']['reason'], 'invalid_delegation')
            return action('finish', summary='Rejected malformed delegation without external effects')
        self.state.enqueue('bad-shape', 'may-maestro', 'synthetic',
                           'Reject structurally malformed plan without crashing')
        result = Worker(self.state, FakeGateway(decide), self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['checkpoint']['step'], 1)
        self.assertEqual(len(self.state.tasks(project='synthetic')), 1)

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
            {'provider': 'groq', 'reason': 'authentication', 'status': 401, 'retryable': False},
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

    def test_no_automatic_task_step_ceiling_while_context_stays_compact(self):
        self.state.enqueue('long', 'frontend-engineer', 'fixture', 'Reason through more than twelve fixture decisions')
        observed = []
        def decide(agent, context):
            count = context['completed_steps']
            observed.append(count)
            self.assertIsNone(context['remaining_steps'])
            self.assertLessEqual(len(context['history']), 4)
            if count == 18:
                self.assertEqual(context['history'][0]['outcome'], {'remembered': 'step-14'})
                self.assertEqual(context['history'][-1]['outcome'], {'remembered': 'step-17'})
                self.assertEqual(context['history_omitted'], 14)
                return action('finish')
            return action('remember', {'key': 'step-' + str(count), 'value': count})
        gateway = FakeGateway(decide)
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['steps'], 19)
        self.assertEqual(observed, list(range(19)))
        self.assertEqual(len(result['checkpoint']['history']), 18)

    def test_long_model_then_tool_renews_before_tool_and_finishes_with_live_lease(self):
        from horizon_core.tools import Tools
        self.state.enqueue('long-lease', 'frontend-engineer', 'fixture', 'Keep lease across slow model and slow tool')
        clock = [1000]
        def decide(agent, context):
            clock[0] += 1200
            return action('read_file', {'path': 'README.md'}) if not context['history'] else action('finish')
        original_execute = Tools.execute
        tool_lease = []
        def slow_tool(tools, name, args):
            task = self.state.get('long-lease')
            tool_lease.append(task['lease_until'])
            self.assertEqual(task['lease_until'], clock[0] + 1800)
            clock[0] += 800
            self.assertGreater(task['lease_until'], clock[0])
            return original_execute(tools, name, args)
        with patch('horizon_core.state.time.time', side_effect=lambda: clock[0]), patch.object(
                Tools, 'execute', autospec=True, side_effect=slow_tool):
            result = Worker(self.state, FakeGateway(decide), self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['checkpoint']['step'], 1)
        self.assertEqual(tool_lease, [4000])
        self.assertEqual(clock[0], 4200)
        renewals = [event for event in self.state.events('long-lease') if event['kind'] == 'lease_renewed']
        self.assertTrue(any(event['created_at'] == 2200 for event in renewals))

    def test_time_slices_resume_fresh_workers_without_consuming_retry_attempts(self):
        self.state.enqueue('sliced', 'frontend-engineer', 'fixture', 'Resume cooperative slices until actual completion', max_attempts=1)
        lengths = []
        def decide(agent, context):
            count = context['completed_steps']
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
        self.assertEqual(result['error']['reason'], 'step_limit_exceeded')


if __name__ == '__main__':
    unittest.main()
