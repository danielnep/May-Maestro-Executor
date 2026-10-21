"""Real SQLite/git fixtures, fake provider/publisher; no live credentials."""
import base64
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from horizon_core.commit_bridge import (REPOSITORY, ScopedState, import_request,
    process_request, publish_result, summarize, validate_request)
from horizon_core.github_state import GitHubStateError
from horizon_core.publication_policy import capture_git_baseline
from horizon_core.state import State


def request(identifier='proof-request', project='sample-app'):
    return {'owner': 'danielnep', 'repository': REPOSITORY, 'task_id': identifier,
            'agent': 'may-maestro', 'project': project, 'objective': 'Implement a synthetic reservation engine',
            'payload': {'allowed_paths': ['apps/' + project + '/**']}}


class ContentAPI:
    def __init__(self):
        self.calls = []
        self.content = None
        self.fail = None

    def _request(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        if self.fail:
            raise GitHubStateError('GitHub HTTP ' + str(self.fail))
        if method == 'GET':
            if self.content is None:
                raise GitHubStateError('GitHub HTTP 404')
            return {'sha': 'observed-sha', 'content': base64.b64encode(self.content).decode()}
        self.content = base64.b64decode(body['content'])
        return {'content': {'sha': 'new-sha'}}


class CommitBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = State(Path(self.temporary.name) / 'state.sqlite3')
        self.addCleanup(self.state.close)
        self.root = Path(self.temporary.name) / 'repo'
        self.root.mkdir()
        self.git('init')
        self.git('switch', '-c', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Synthetic fixture')
        self.git('remote', 'add', 'origin', 'https://github.com/' + REPOSITORY + '.git')
        (self.root / 'README.md').write_text('Synthetic controller fixture')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'fixture')
        self.baseline = capture_git_baseline(self.root, REPOSITORY)
        self.git('switch', '-c', 'agent/chat-proof-request')
        self.validated = validate_request(request(), 'proof-request')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              text=True, capture_output=True).stdout

    @contextmanager
    def prepare(self, repo, branch, operator_policy=None, api=None):
        self.assertEqual(repo, REPOSITORY)
        self.assertEqual(branch, 'agent/chat-proof-request')
        self.assertTrue(operator_policy['allow_new_product_main'])
        yield SimpleNamespace(root=self.root, baseline=self.baseline)

    def test_owner_reset_is_idempotent_and_preserves_new_tasks(self):
        from horizon_core.commit_bridge import owner_reset
        self.state.enqueue('old-task', 'backend-engineer', 'synthetic', 'Previous queue task')
        original = owner_reset(self.state, 'github-sqlite', 'horizon-clean-start-20261021')
        self.assertEqual(original, {'applied': True, 'removed': 1})
        self.assertEqual(self.state.tasks(), [])
        self.state.enqueue('new-task', 'backend-engineer', 'synthetic', 'New legitimate task')
        repeat = owner_reset(self.state, 'github-sqlite', 'horizon-clean-start-20261021')
        self.assertEqual(repeat, {'applied': False, 'removed': 0})
        self.assertEqual([task['task_id'] for task in self.state.tasks()], ['new-task'])

    def test_owner_reset_rejects_unsupported_backend(self):
        from horizon_core.commit_bridge import owner_reset
        with self.assertRaisesRegex(RuntimeError, 'canonical encrypted GitHub SQLite'):
            owner_reset(self.state, 'embedded-postgres', 'horizon-clean-start-20261021')
        self.assertIsNone(owner_reset(self.state, 'embedded-postgres', None))

    def test_controller_assigns_branch_project_and_bounded_namespace(self):
        value = self.validated
        self.assertEqual(value['task']['project'], 'chat:proof-request')
        self.assertEqual(value['branch'], 'agent/chat-proof-request')
        self.assertEqual(value['namespace'], 'apps/sample-app')
        self.assertEqual(value['task']['payload']['repository'], REPOSITORY)
        self.assertNotIn('publication_policy', value['task']['payload'])

    def test_rejects_owner_repo_agent_filename_and_permission_escalation(self):
        invalid = [dict(owner='other'), dict(repository='customer/private'), dict(task_id='other-id'),
                   dict(agent='not-an-agent'), dict(project='../escape'), dict(publication_policy={'allow_main': True}),
                   dict(payload={'branch': 'main'}), dict(payload={'allowed_paths': ['horizon_core/app.py']}),
                   dict(payload={'allowed_paths': ['apps/sample-app/../other.py']}),
                   dict(payload={'allowed_paths': ['apps/sample-app/.env']}),
                   dict(payload={'allowed_paths': ['apps/sample-app//engine.py']}),
                   dict(payload={'allowed_paths': [{}]}), dict(payload={'env': {'SECRET': 'not-allowed'}})]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_request({**request(), **changes}, 'proof-request')

    def test_initiative_requires_reason_and_keeps_same_permissions(self):
        document = {**request(), 'initiative': True}
        with self.assertRaises(ValueError):
            validate_request(document, 'proof-request')
        document['initiative_reason'] = 'Improve deterministic verification of a new synthetic app'
        value = validate_request(document, 'proof-request')
        self.assertEqual(value['task']['payload']['allowed_paths'], ['apps/sample-app/**'])

    def test_import_is_idempotent_across_reopen_and_changed_request_rejected(self):
        import_request(self.state, self.validated)
        import_request(self.state, self.validated)
        self.assertEqual(len(self.state.tasks()), 1)
        self.assertEqual(len(self.state.events('chat-proof-request')), 1)
        path = self.state.path
        self.state.close()
        self.state = State(path)
        self.addCleanup(self.state.close)
        import_request(self.state, self.validated)
        self.assertEqual(len(self.state.tasks()), 1)
        changed = request()
        changed['objective'] += ' And expand the scope'
        with self.assertRaisesRegex(ValueError, 'Immutable'):
            import_request(self.state, validate_request(changed, 'proof-request'))

    def test_scoped_claim_never_consumes_another_project(self):
        self.state.enqueue('other', 'backend-engineer', 'another-project', 'Must remain queued', priority=100)
        import_request(self.state, self.validated)
        scope = ScopedState(self.state, 'chat:proof-request')
        task = scope.claim('owner')
        self.assertEqual(task['task_id'], 'chat-proof-request')
        self.assertEqual(self.state.get('other')['status'], 'queued')
        self.assertEqual(len(scope.tasks()), 1)

    def test_result_writer_is_idempotent_cas_and_cannot_escape_main_result_path(self):
        api = ContentAPI()
        report = {'status': 'pending', 'request_id': 'proof-request'}
        self.assertTrue(publish_result(api, 'proof-request', report)['written'])
        self.assertFalse(publish_result(api, 'proof-request', report)['written'])
        self.assertEqual(len([call for call in api.calls if call[0] == 'PUT']), 1)
        report['status'] = 'reported_complete'
        publish_result(api, 'proof-request', report)
        self.assertEqual(api.calls[-1][2]['sha'], 'observed-sha')
        self.assertEqual(api.calls[-1][2]['branch'], 'main')
        self.assertEqual(api.calls[-1][1], '/contents/tasks/results/proof-request.json')
        for identifier in ('../outside', 'app/results.json'):
            with self.assertRaises(ValueError):
                publish_result(api, identifier, {})
        api.fail = 403
        with self.assertRaisesRegex(GitHubStateError, '403'):
            publish_result(api, 'proof-request', report)

    def test_process_worker_uses_real_project_claim_and_falls_back_to_branch(self):
        calls = []
        class FixtureWorker:
            def __init__(self, state, gateway, root, **kwargs):
                self.state = state
            def run_one(self):
                task = self.state.claim('fixture-worker')
                if task is None:
                    return None
                self.state.complete(task['task_id'], 'fixture-worker', {'summary': 'Fixture implemented', 'verification': []})
                return self.state.get(task['task_id'])
        def publisher(*args, **kwargs):
            calls.append(kwargs)
            return {'published': False, 'reason': 'real_current_checks_missing'} if kwargs['target_branch'] == 'main' else {
                'published': True, 'branch': kwargs['target_branch'], 'commit': 'fixture-commit'}
        result = process_request(self.state, self.validated, api=ContentAPI(), session_seconds=10,
            prepare=self.prepare, gateway_factory=lambda **kwargs: None, worker_factory=FixtureWorker, publisher=publisher)
        self.assertEqual(result['status'], 'reported_complete')
        self.assertEqual([call['target_branch'] for call in calls], ['main', 'agent/chat-proof-request'])
        self.assertEqual(result['publication']['main_pending_reason'], 'real_current_checks_missing')
        # Replay does not run the worker or publish again.
        replay = process_request(self.state, self.validated, api=ContentAPI(),
            prepare=lambda *args, **kwargs: self.fail('Terminal request must not execute again'))
        self.assertEqual(replay, result)

    def test_checkpoint_after_interruption_resumes_without_duplicate_task(self):
        class InterruptedWorker:
            def __init__(self, state, *args, **kwargs): self.state = state
            def run_one(self):
                task = self.state.claim('first-worker', now=0, lease_seconds=1)
                self.state.checkpoint(task['task_id'], 'first-worker', {'step': 3, 'fixture': 'durable'}, now=0)
                raise OSError('simulated runner interruption')
        with self.assertRaises(OSError):
            process_request(self.state, self.validated, api=ContentAPI(), prepare=self.prepare,
                gateway_factory=lambda **kwargs: None, worker_factory=InterruptedWorker)
        class ResumedWorker:
            def __init__(inner, state, *args, **kwargs): inner.state = state
            def run_one(inner):
                task = inner.state.claim('second-worker')
                if task is None: return None
                self.assertEqual(task['checkpoint'], {'step': 3, 'fixture': 'durable'})
                inner.state.complete(task['task_id'], 'second-worker', {'summary': 'Resumed fixture'})
                return inner.state.get(task['task_id'])
        result = process_request(self.state, self.validated, api=ContentAPI(), prepare=self.prepare,
            gateway_factory=lambda **kwargs: None, worker_factory=ResumedWorker,
            publisher=lambda *args, **kwargs: {'published': True, 'branch': 'main'})
        self.assertEqual(len(self.state.tasks()), 1)
        self.assertEqual(self.state.get('chat-proof-request')['attempts'], 2)
        self.assertEqual(result['status'], 'reported_complete')

    def test_may_review_packet_never_invents_live_link(self):
        # A completed model job is not a verified public page or an audit.
        tasks = [{'task_id': 'fixture', 'agent': 'qa-engineer', 'status': 'succeeded',
                  'result': {'verification': [
                      {'check': 'browser-playwright', 'passed': True, 'tests_run': 3}]}}]
        publication = {
            'published': True, 'target_repo': 'danielnep/app-hondas-maxvel',
            'branch': 'agent/demo-patch', 'commit': 'a' * 40,
            'pages_preview': {'status': 'needs_pages_admin_token', 'page_url': 'https://example.test/'}}
        result = summarize('proof-request', tasks, publication)
        self.assertEqual(result['status'], 'reported_complete')
        self.assertTrue(result['quality']['approved'])
        self.assertIsNone(result['may_review']['preview_url'])
        self.assertEqual(result['may_review']['status'], 'preview_unavailable')
        self.assertEqual(result['may_review']['handoff_mode'],
                         'durable_github_result_not_chat_push')
        publication['pages_preview'] = {'status': 'verified', 'branch': 'agent/demo-patch',
                                        'page_url': 'https://danielnep.github.io/app-hondas-maxvel/'}
        result = summarize('proof-request', tasks, publication)
        self.assertEqual(result['may_review']['status'], 'ready_for_may_review')
        self.assertEqual(result['may_review']['preview_url'],
                         'https://danielnep.github.io/app-hondas-maxvel/')
        self.assertEqual(result['may_review']['commit_url'],
                         'https://github.com/danielnep/app-hondas-maxvel/commit/' + 'a' * 40)
        # If workflow marks job pending or QA failed, link remains for diagnosis
        # but never claims a completed review packet.
        tasks[0]['status'] = 'queued'
        pending = summarize('proof-request', tasks, publication)
        self.assertEqual(pending['may_review']['status'], 'in_progress')
        tasks[0]['status'] = 'succeeded'
        tasks[0]['result']['verification'][0]['passed'] = False
        failed = summarize('proof-request', tasks, publication)
        self.assertEqual(failed['may_review']['status'], 'quality_blocked')
        publication['pages_preview']['branch'] = 'agent/different'
        self.assertIsNone(summarize('proof-request', tasks, publication)['may_review']['preview_url'])

    def test_pending_and_failed_work_never_have_qa_approved_true(self):
        for state in ("queued", "running", "blocked", "failed"):
            with self.subTest(state=state):
                report = summarize("proof-request", [{
                    "task_id": "task-1", "agent": "may-maestro",
                    "status": state, "attempts": 1,
                    "result": {"verification": []},
                }])
                self.assertFalse(report["quality"]["approved"])
                self.assertNotEqual(report["status"], "reported_complete")

    def test_summarize_keeps_verification_failure_without_raw_logs_or_keys(self):
        result = summarize('proof-request', [{'task_id': 'x', 'agent': 'qa-engineer', 'status': 'succeeded',
            'result': {'summary': 'Finished investigation', 'verification': [{'passed': False, 'exit_code': 1,
                'output': 'SECRET-RAW-LOGS', 'check': 'python-unittest'}]}}])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertFalse(result['quality']['approved'])
        self.assertEqual(result['quality']['failed_checks'][0]['exit_code'], 1)
        self.assertFalse(result['tasks'][0]['verification'][0]['passed'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_external_public_repo_rewrites_target_and_returns_host_patch_on_denied_write(self):
        document = {**request(), 'repository': 'danielnep/public-fixture'}
        validated = validate_request(document, 'proof-request')
        self.assertEqual(validated['task']['payload']['repository'], 'danielnep/public-fixture')
        @contextmanager
        def prepare(repo, branch, **kwargs):
            self.assertEqual(repo, 'danielnep/public-fixture')
            yield SimpleNamespace(root=self.root, baseline=self.baseline, default_branch='main')
        class FixtureWorker:
            def __init__(self, state, *args, **kwargs): self.state = state
            def run_one(self):
                task = self.state.claim('fixture-worker')
                if task is None: return None
                self.state.remember(task['project'], '__workspace__', 'apps/sample-app/engine.py',
                    {'content': 'VALUE = 1\n', 'branch': task['payload']['branch'], 'expected_sha256': 'empty-file-hash'})
                self.state.complete(task['task_id'], 'fixture-worker', {'summary': 'Implemented synthetic app'})
                return self.state.get(task['task_id'])
        def denied(*args, **kwargs):
            self.assertEqual(args[2], 'danielnep/public-fixture')
            raise GitHubStateError('GitHub HTTP 403')
        result = process_request(self.state, validated, api=ContentAPI(), prepare=prepare,
            worker_factory=FixtureWorker, gateway_factory=lambda **kwargs: None, publisher=denied)
        self.assertEqual(result['status'], 'awaiting_host_publication')
        self.assertFalse(result['publication']['published'])
        self.assertEqual(result['publication']['target_repo'], 'danielnep/public-fixture')
        self.assertEqual(result['publication']['patch_files'][0]['content'], 'VALUE = 1\n')
        # A later scheduled run must not reopen an already completed task
        # or overwrite the real host-publication handoff with ValueError.
        resumed = process_request(
            self.state, validated, api=ContentAPI(),
            prepare=lambda *a, **k: self.fail('Completed handoff must not rerun'))
        self.assertEqual(resumed, result)
        self.assertFalse(resumed['publication']['published'])

    def test_terminal_task_without_publication_record_still_attempts_delivery(self):
        import_request(self.state, self.validated)
        task = ScopedState(self.state, 'chat:proof-request').claim('fixture-worker')
        self.state.complete(task['task_id'], 'fixture-worker', {'summary': 'Completed before interrupted publication'})
        class NoWork:
            def __init__(self, *args, **kwargs): pass
            def run_one(self): return None
        calls = []
        def publisher(*args, **kwargs):
            calls.append(True)
            return {'published': True, 'branch': 'main'}
        process_request(self.state, self.validated, api=ContentAPI(), prepare=self.prepare,
            worker_factory=NoWork, gateway_factory=lambda **kwargs: None, publisher=publisher)
        self.assertEqual(len(calls), 1)

    def test_public_results_expose_only_sanitized_may_and_qa_blockers(self):
        # The owner can see precisely why an Issue #50 is waiting without
        # leaking model prompts, tokens, tool output or private exception text.
        tasks = [
            {'task_id': 'chat-p0', 'agent': 'may-maestro',
             'status': 'queued', 'attempts': 2, 'result': None,
             'checkpoint': {'pending_may': {'ticket': 'a' * 20, 'round': 1}}},
            {'task_id': 'chat-p0:audit-ui', 'agent': 'horizon-ui-guardian',
             'status': 'failed', 'attempts': 1, 'result': None,
             'error': {'kind': 'RepeatedUncorrectedFailure',
                       'reason': 'retry_limit_exceeded',
                       'signatures': ['check:browser-playwright'],
                       'secret': 'sk-private-do-not-publish',
                       'message': 'Bearer do-not-publish'}},
            {'task_id': 'chat-p0:frontend', 'agent': 'frontend-engineer',
             'status': 'blocked', 'attempts': 0, 'result': None,
             'error': {'failed_dependencies': ['chat-p0:audit-ui']}},
        ]
        summary = summarize('proof-request', tasks)
        by_agent = {r['agent']: r for r in summary['tasks']}
        self.assertEqual(by_agent['may-maestro']['blocker'],
                         {'code': 'awaiting_may_consultation',
                          'round': 1, 'ticket': 'a' * 20})
        self.assertEqual(by_agent['horizon-ui-guardian']['blocker'],
                         {'code': 'task_failed', 'kind': 'RepeatedUncorrectedFailure',
                          'reason': 'retry_limit_exceeded',
                          'signatures': ['check:browser-playwright']})
        self.assertEqual(by_agent['frontend-engineer']['blocker'],
                         {'code': 'task_blocked',
                          'failed_dependencies': ['chat-p0:audit-ui']})
        self.assertNotIn('sk-private', json.dumps(summary))
        self.assertNotIn('Bearer', json.dumps(summary))
        self.assertFalse(summary['quality']['approved'])

    def test_existing_product_requires_explicit_scope_and_accepts_portfolio_paths(self):
        document = {**request(), 'delivery_type': 'existing_product', 'repository': 'danielnep/ronda-pro'}
        document.pop('payload')
        with self.assertRaisesRegex(ValueError, 'explicit allowed_paths'):
            validate_request(document, 'proof-request')
        document['payload'] = {'allowed_paths': ['src/**', 'index.html', 'android/app/build.gradle']}
        validated = validate_request(document, 'proof-request')
        self.assertEqual(validated['delivery_type'], 'existing_product')
        self.assertIsNone(validated['namespace'])
        self.assertNotIn('application_namespace', validated['task']['payload'])
        self.assertEqual(validated['task']['payload']['allowed_paths'], document['payload']['allowed_paths'])

    def test_existing_product_cannot_authorize_controller_or_sensitive_roots(self):
        for path in ['**', '*.py', 'src/../other.py', 'src//ui.py', '.github/workflows/x.yml',
                     'src/.env', 'credentials.json', 'src/private_key.pem', 'runtime/state.sqlite3',
                     'horizon_core/**', 'agents/**', 'sandbox/**', 'android/**', 'android_tools/**',
                     'chat_bridge/**', 'tasks/**', 'tests/**', 'supervisor.py']:
            document = {**request(), 'delivery_type': 'existing_product', 'payload': {'allowed_paths': [path]}}
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_request(document, 'proof-request')

    def test_existing_product_publication_never_attempts_main(self):
        validated = validate_request({**request(), 'delivery_type': 'existing_product',
            'payload': {'allowed_paths': ['apps/sample-app/**']}}, 'proof-request')
        @contextmanager
        def prepare(repo, branch, operator_policy=None, **kwargs):
            self.assertEqual(operator_policy['delivery_type'], 'existing_product_update')
            self.assertEqual(operator_policy['target_branch'], 'agent/chat-proof-request')
            self.assertNotIn('allow_new_product_main', operator_policy)
            self.assertNotIn('allow_existing_product_main', operator_policy)
            self.assertNotIn('new_project_namespace', operator_policy)
            yield SimpleNamespace(root=self.root, baseline=self.baseline, default_branch='main')
        class FixtureWorker:
            def __init__(self, state, *args, **kwargs): self.state = state
            def run_one(self):
                task = self.state.claim('existing-worker')
                if task is None: return None
                self.state.complete(task['task_id'], 'existing-worker', {'summary': 'Existing product review'})
                return self.state.get(task['task_id'])
        calls = []
        def publisher(*args, **kwargs):
            calls.append(kwargs['target_branch'])
            return {'published': False, 'reason': 'Nothing changed'}
        process_request(self.state, validated, api=ContentAPI(), prepare=prepare, worker_factory=FixtureWorker,
                        gateway_factory=lambda **kwargs: None, publisher=publisher)
        self.assertEqual(calls, ['agent/chat-proof-request'])

    def test_existing_external_host_patch_uses_allowlist_not_new_app_namespace(self):
        validated = validate_request({**request(), 'delivery_type': 'existing_product',
            'repository': 'danielnep/ronda-pro', 'payload': {'allowed_paths': ['src/ui.js']}}, 'proof-request')
        @contextmanager
        def prepare(*args, **kwargs):
            yield SimpleNamespace(root=self.root, baseline=self.baseline, default_branch='main')
        class FixtureWorker:
            def __init__(self, state, *args, **kwargs): self.state = state
            def run_one(self):
                task = self.state.claim('external-worker')
                if task is None: return None
                self.state.remember(task['project'], '__workspace__', 'src/ui.js',
                    {'content': 'export const title = "Ronda Pro";', 'branch': task['payload']['branch']})
                self.state.complete(task['task_id'], 'external-worker', {'summary': 'Updated authorized source'})
                return self.state.get(task['task_id'])
        def denied(*args, **kwargs):
            self.assertEqual(kwargs['target_branch'], 'agent/chat-proof-request')
            raise GitHubStateError('GitHub HTTP 403')
        result = process_request(self.state, validated, api=ContentAPI(), prepare=prepare,
            worker_factory=FixtureWorker, gateway_factory=lambda **kwargs: None, publisher=denied)
        self.assertEqual(result['status'], 'awaiting_host_publication')
        self.assertEqual(result['publication']['target_branch'], 'agent/chat-proof-request')
        self.assertEqual(result['publication']['patch_files'][0]['path'], 'src/ui.js')


    def test_direct_result_is_durable_without_github_commit(self):
        from horizon_core.commit_bridge import publish_direct_result
        with tempfile.TemporaryDirectory() as base:
            report={'request_id':'task-proof','status':'waiting',
                    'tasks':[], 'quality':{'approved':False}}
            result=publish_direct_result(base,'task-proof',report)
            self.assertTrue(result['written'])
            stored=Path(base)/'results'/'task-proof.json'
            self.assertEqual(json.loads(stored.read_text()),report)
            self.assertEqual(stored.stat().st_mode & 0o077,0)
            report['status']='completed'
            publish_direct_result(base,'task-proof',report)
            self.assertEqual(json.loads(stored.read_text())['status'],'completed')
            self.assertEqual(list((Path(base)/'results').glob('*.tmp')),[])
            with self.assertRaises(ValueError):
                publish_direct_result(base,'../escape',report)

    def test_supervisor_gate_avoids_replaying_settled_or_delayed_tasks(self):
        from horizon_core.commit_bridge import supervisor_gate
        self.assertEqual(supervisor_gate(self.state,self.validated,now=100),'new')
        task=self.validated['task']
        self.state.enqueue(task['task_id'],task['agent'],task['project'],task['objective'],
                           payload=task['payload'],not_before=200)
        self.assertEqual(supervisor_gate(self.state,self.validated,now=100),'waiting')
        self.assertEqual(supervisor_gate(self.state,self.validated,now=201),'ready')
        self.state.claim('test',now=201,lease_seconds=10)
        self.assertEqual(supervisor_gate(self.state,self.validated,now=205),'waiting')
        self.assertEqual(supervisor_gate(self.state,self.validated,now=212),'expired_lease')
        self.state.complete(task['task_id'],'test',{'summary':'done'},now=205)
        self.assertEqual(supervisor_gate(self.state,self.validated,now=212),'waiting')


if __name__ == '__main__':
    unittest.main()
