"""Tool check routing/stamps tested with fake isolated executors, never host app code."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from horizon_core.publication_policy import workspace_fingerprint
from horizon_core.state import State
from horizon_core.tools import Tools


class CheckIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / 'apps/demo'
        self.app.mkdir(parents=True)
        (self.app / 'index.html').write_text('<h1>fixture</h1>')
        (self.root / 'README.md').write_text('full repo')
        self.tools = Tools(self.root, {'tools': ['run_check']})
        self.success = {'passed': True, 'exit_code': 0, 'tests': 2, 'cleanup': True, 'output': 'fixture isolated result'}

    def test_browser_subdirectory_receives_scoped_tools_and_full_root_runtime_stamp(self):
        def checker(root, tools, name, **kwargs):
            self.assertEqual(root, self.app)
            self.assertEqual(tools.root, self.app)
            self.assertEqual(name, 'browser-playwright')
            self.assertTrue(kwargs['evidence_dir'].is_relative_to(self.root / 'runtime'))
            return {**self.success, 'screenshots': []}
        with patch('horizon_core.web_checks.run_web_check', side_effect=checker):
            result = self.tools.execute('run_check', {'name': 'browser-playwright', 'project_dir': 'apps/demo'})
        self.assertTrue(result['passed'])
        self.assertEqual(result['tests_run'], 2)
        self.assertEqual(result['workspace_digest'], workspace_fingerprint({'README.md': 'full repo', 'apps/demo/index.html': '<h1>fixture</h1>'}))
        self.assertEqual(result['source'], 'runtime')
        self.assertFalse(result['simulated'])
        self.assertEqual(len(result['execution_id']), 32)
        self.assertGreater(result['executed_at'], 0)
        self.assertEqual(result['project_dir'], 'apps/demo')

    def test_android_routes_to_native_checker_with_controller_owned_artifacts(self):
        def checker(root, artifacts_dir):
            self.assertEqual(root, self.app)
            self.assertTrue(artifacts_dir.is_relative_to(self.root / 'runtime/android-evidence'))
            return {'passed': True, 'tests_run': 1, 'exit_code': 0, 'timeout': False, 'truncated': False,
                    'cleanup': True, 'artifacts': ['fixture.apk'], 'output': 'isolated Android'}
        with patch('android_tools.check.isolated_check', side_effect=checker):
            result = self.tools.execute('run_check', {'name': 'android-gradle', 'project_dir': 'apps/demo'})
        self.assertTrue(result['passed'])
        self.assertEqual(result['backend'], 'docker-android')
        self.assertEqual(result['artifacts'], ['fixture.apk'])

    def test_zero_tests_cleanup_timeout_truncation_and_mutated_workspace_fail_closed(self):
        for updates in ({'tests': 0}, {'cleanup': False}, {'timed_out': True}, {'truncated': True}, {'exit_code': 1}):
            with self.subTest(updates=updates), patch('horizon_core.web_checks.run_web_check', return_value={**self.success, **updates}):
                result = self.tools.execute('run_check', {'name': 'node-test', 'project_dir': 'apps/demo'})
                self.assertFalse(result['passed'])
        def mutate(*args, **kwargs):
            (self.app / 'index.html').write_text('changed after snapshot')
            return self.success
        with patch('horizon_core.web_checks.run_web_check', side_effect=mutate):
            result = self.tools.execute('run_check', {'name': 'node-test', 'project_dir': 'apps/demo'})
        self.assertFalse(result['passed'])
        self.assertFalse(result['workspace_unchanged'])

    def test_project_paths_cannot_escape_or_follow_links(self):
        (self.root / 'linked').symlink_to(self.app, target_is_directory=True)
        for project_dir in ('../private', '/etc', '.git', 'runtime', 'apps/../demo', 'apps//demo', 'linked', 'apps/demo/index.html'):
            with self.subTest(project_dir=project_dir), self.assertRaises(ValueError):
                self.tools.execute('run_check', {'name': 'node-test', 'project_dir': project_dir})

    def test_snapshot_ignores_credentials_bytecode_runtime_and_image_integrity_is_checked(self):
        for name in ('config.env', 'signing.jks', 'release.keystore', 'local.properties', '__pycache__/module.pyc', 'runtime/artifact.txt'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private')
        files = self.tools.snapshot_files()
        self.assertEqual(set(files), {'README.md', 'apps/demo/index.html'})
        image = self.root / 'proof.png'
        image.write_bytes(b'png-fixture')
        self.tools.evidence = [{'path': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}]
        self.assertEqual(self.tools.evidence_image(0), b'png-fixture')
        image.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.tools.evidence_image(0)

    def test_atomic_project_claim_does_not_take_other_queue_work(self):
        state = State(':memory:')
        self.addCleanup(state.close)
        state.enqueue('other-first', 'qa-engineer', 'other', 'Other project', priority=100)
        state.enqueue('mine', 'qa-engineer', 'mine', 'Selected project', priority=0)
        self.assertEqual(state.claim('worker', now=100, project='mine')['task_id'], 'mine')
        self.assertEqual(state.get('other-first')['status'], 'queued')
        self.assertIsNone(state.claim('worker-2', now=100, project='mine'))
        self.assertEqual(state.claim('worker-3', now=100, project='other')['task_id'], 'other-first')


if __name__ == '__main__':
    unittest.main()
