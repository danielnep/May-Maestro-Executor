"""Railway bridge contract tests: fake child process, no credentials or VM requests."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from horizon_core.railway import RailwayBroker, RailwayError
from horizon_core.registry import load_agent
from horizon_core.state import State
from horizon_core.tools import Tools


class RailwayTests(unittest.TestCase):
    def setUp(self):
        self.state = State(':memory:')
        self.addCleanup(self.state.close)
        self.env = {'PATH': '/fixture/bin', 'RAILWAY_API_TOKEN': 'railway-sensitive-fixture',
                    'RAILWAY_ENVIRONMENT_ID': 'env-1', 'GEMINI_API_KEY': 'gemini-sensitive-fixture',
                    'DEEPSEEK_API_KEY': 'deepseek-sensitive-fixture', 'GH_TOKEN': 'github-sensitive-fixture',
                    'GITHUB_TOKEN': 'github-alternate-sensitive', 'UNRELATED_CONFIG': 'must-not-inherit'}
        self.requests = []
        self.saved = []
        self.response = {'ok': True, 'sandbox_id': 'sandbox-fixture', 'status': 'RUNNING', 'reused': False}
        def runner(command, **kwargs):
            self.requests.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.response), stderr='')
        self.broker = RailwayBroker(self.state, lambda: self.saved.append(True), self.env, runner)

    def test_child_receives_only_scoped_railway_credentials_and_path(self):
        self.broker.request({'operation': 'preflight'})
        command, options = self.requests[0]
        self.assertEqual(command[0], 'node')
        self.assertTrue(command[1].endswith('/sandbox/bridge.mjs'))
        self.assertEqual(options['env'], {key: self.env[key] for key in ('PATH', 'RAILWAY_API_TOKEN', 'RAILWAY_ENVIRONMENT_ID')})
        self.assertEqual(json.loads(options['input']), {'operation': 'preflight'})
        self.assertTrue(options['capture_output'])
        self.assertTrue(options['text'])
        self.assertGreater(options['timeout'], 0)
        for key in ('DEEPSEEK_API_KEY', 'GEMINI_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'):
            self.assertNotIn(key, options['env'])
            self.assertNotIn(self.env[key], options['input'])

    def test_missing_configuration_never_starts_child(self):
        for missing in ('RAILWAY_API_TOKEN', 'RAILWAY_ENVIRONMENT_ID'):
            env = dict(self.env)
            env.pop(missing)
            broker = RailwayBroker(self.state, environ=env, runner=self.broker.runner)
            with self.assertRaises(RailwayError):
                broker.request({'operation': 'preflight'})
        self.assertEqual(self.requests, [])

    def test_first_ensure_omits_absent_sandbox_id_and_checkpoints_identity(self):
        self.broker.ensure()
        payload = json.loads(self.requests[0][1]['input'])
        self.assertEqual(payload, {'operation': 'ensure'})
        self.assertEqual(self.state.recall('__runtime__', 'railway')['sandbox'],
                         {'id': 'sandbox-fixture', 'environment_id': 'env-1'})
        self.assertEqual(self.saved, [True])

    def test_same_environment_reuses_saved_identity(self):
        self.state.remember('__runtime__', 'railway', 'sandbox', {'id': 'prior-vm', 'environment_id': 'env-1'})
        self.broker.ensure()
        self.assertEqual(json.loads(self.requests[0][1]['input'])['sandbox_id'], 'prior-vm')

    def test_environment_change_does_not_reuse_previous_sandbox(self):
        self.state.remember('__runtime__', 'railway', 'sandbox', {'id': 'other-environment-vm', 'environment_id': 'env-old'})
        self.broker.ensure()
        self.assertNotIn('sandbox_id', json.loads(self.requests[0][1]['input']))
        self.assertEqual(self.state.recall('__runtime__', 'railway')['sandbox']['environment_id'], 'env-1')

    def test_malformed_protocol_and_failed_status_do_not_leak_secrets(self):
        responses = ['not json', '[]', 'null', '"unexpected"', '{"ok":false}',
                     json.dumps({'ok': False, 'error': {'name': self.env['RAILWAY_API_TOKEN'], 'message': self.env['GH_TOKEN']}}),
                     json.dumps({'ok': True, 'error': self.env['GEMINI_API_KEY']})]
        for stdout in responses:
            def runner(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout=stdout, stderr='stderr ' + self.env['DEEPSEEK_API_KEY'])
            broker = RailwayBroker(self.state, environ=self.env, runner=runner)
            with self.subTest(stdout=stdout):
                with self.assertRaises(RailwayError) as caught:
                    broker.request({'operation': 'ensure'})
                self.assert_safe_error(caught.exception)

    def test_nonzero_exit_and_transport_errors_are_sanitized(self):
        def nonzero(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout='{"ok":true}', stderr=self.env['RAILWAY_API_TOKEN'])
        failures = [nonzero]
        for exception in (OSError(self.env['GH_TOKEN']), subprocess.TimeoutExpired('node', 480, stderr=self.env['DEEPSEEK_API_KEY'])):
            def failed(command, _exception=exception, **kwargs):
                raise _exception
            failures.append(failed)
        for runner in failures:
            with self.assertRaises(RailwayError) as caught:
                RailwayBroker(self.state, environ=self.env, runner=runner).request({'operation': 'preflight'})
            self.assert_safe_error(caught.exception)

    def assert_safe_error(self, error):
        for key, value in self.env.items():
            if 'KEY' in key or 'TOKEN' in key:
                self.assertNotIn(value, str(error))

    def test_invalid_sandbox_identity_is_not_persisted(self):
        for sandbox_id in (None, '', 10):
            self.response = {'ok': True, 'sandbox_id': sandbox_id, 'status': 'RUNNING'}
            with self.assertRaises(RailwayError):
                self.broker.ensure()
        self.assertEqual(self.saved, [])
        self.assertEqual(self.state.recall('__runtime__', 'railway'), {})

    def test_ensure_requires_running_status_before_persisting(self):
        for status in ('FAILED', 'DESTROYED', 'CREATING', None, ['RUNNING']):
            self.response = {'ok': True, 'sandbox_id': 'sandbox-fixture', 'status': status}
            with self.subTest(status=status):
                with self.assertRaises(RailwayError):
                    self.broker.ensure()
        self.assertEqual(self.saved, [])

    def execute_fixture_check(self, check_result, populate=None):
        responses = [{'ok': True, 'sandbox_id': 'sandbox-fixture', 'status': 'RUNNING'}, {'ok': True, **check_result}]
        def runner(command, **kwargs):
            self.requests.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(responses.pop(0)), stderr='')
        broker = RailwayBroker(self.state, environ=self.env, runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tests').mkdir()
            (root / 'tests/test_fixture.py').write_text('import unittest\n')
            if populate:
                populate(root)
            tools = Tools(root, load_agent('qa-engineer'))
            return broker.run_check(root, tools)

    def test_snapshot_excludes_hidden_runtime_and_symlink_paths(self):
        def populate(root):
            (root / '.env').write_text('TOKEN=must-not-upload')
            (root / '.git').mkdir()
            (root / '.git/config').write_text('private repo metadata')
            (root / 'runtime').mkdir()
            (root / 'runtime/checkpoint.json').write_text('private checkpoint')
            (root / 'app.py').write_text('print("fixture")')
            (root / 'linked.py').symlink_to(root / 'app.py')
        result = self.execute_fixture_check({'exitCode': 0, 'timedOut': False, 'truncated': False,
            'stdout': 'Ran 1 test in 0.001s\nOK', 'stderr': '', 'passed': True, 'cleanup_ok': True, 'tests_run': 1}, populate)
        payload = json.loads(self.requests[-1][1]['input'])
        self.assertEqual(payload['operation'], 'run-check')
        self.assertEqual({item['path'] for item in payload['files']}, {'app.py', 'tests/test_fixture.py'})
        self.assertEqual(payload['check'], 'python-unittest')
        self.assertTrue(result['passed'])
        self.assertEqual(result['backend'], 'railway-sdk')

    def test_zero_exit_alone_does_not_override_failed_bridge_cleanup_or_empty_tests(self):
        for overrides in ({'passed': False}, {'cleanup_ok': False}, {'tests_run': 0},
                          {'timedOut': True}, {'truncated': True}, {'exitCode': 1}):
            self.requests.clear()
            base = {'exitCode': 0, 'timedOut': False, 'truncated': False, 'stdout': '', 'stderr': '',
                    'passed': True, 'cleanup_ok': True, 'tests_run': 1}
            with self.subTest(overrides=overrides):
                result = self.execute_fixture_check({**base, **overrides})
                self.assertFalse(result['passed'])


if __name__ == '__main__':
    unittest.main()
