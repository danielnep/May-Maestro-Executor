"""Real temporary Git worktrees; Docker calls are mocked and never launched."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from horizon_core.registry import load_agent
from horizon_core.tools import Tools


class ToolsBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        (self.root / 'README.md').write_text('fixture', encoding='utf-8')
        subprocess.run(['git', 'init', str(self.root)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'switch', '-c', 'feat/test'], check=True, capture_output=True)
        self.tools = Tools(self.root, load_agent('frontend-engineer'), ['app/*'], 'feat/test')

    def tearDown(self):
        self.tmp.cleanup()

    def test_capability_and_argument_boundary(self):
        reviewer = Tools(self.root, load_agent('corregedoria-may'), ['*'], 'feat/test')
        with self.assertRaisesRegex(ValueError, 'Capability'):
            reviewer.execute('write_file', {'path': 'README.md', 'content': 'no'})
        with self.assertRaises(ValueError):
            self.tools.execute('read_file', ['README.md'])
        with self.assertRaises(ValueError):
            self.tools.execute('shell', {'command': 'echo denied'})

    def test_protected_hidden_absolute_parent_and_secret_paths(self):
        paths = ['../outside', '/etc/passwd', '.env', '.git/config', 'app/../README.md',
                 'app\\file.py', 'runtime/queue.json',
                 'secrets/key.txt', 'credentials/config.json', 'node_modules/lib/index.js',
                 'app/api_token.txt', 'app/private-key.txt', 'app/password.json',
                 'app/cert.pem', 'app/key.key', 'app/memory.db', 'app/memory.sqlite', 'app/memory.sqlite3']
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.tools.path(path)
        self.assertEqual(self.tools.path('app/normal.py'), self.root / 'app/normal.py')

    def test_control_plane_readable_but_cannot_be_rewritten(self):
        for path in ['agents/config.json', 'horizon_core/worker.py', 'GEMINI.md', 'AGENTS.md', 'maestro_exec/runner.py', 'executors/runner.py', 'supervisor.py']:
            with self.subTest(path=path):
                self.assertEqual(self.tools.path(path), self.root / path)
                with self.assertRaisesRegex(ValueError, 'control plane'):
                    self.tools.path(path, write=True)

    def test_symlink_file_and_parent_are_rejected_and_not_listed(self):
        outside = Path(self.tmp.name) / 'private.txt'
        outside.write_text('outside fixture', encoding='utf-8')
        (self.root / 'link.txt').symlink_to(outside)
        (self.root / 'linked').symlink_to(Path(self.tmp.name), target_is_directory=True)
        for path in ['link.txt', 'linked/private.txt']:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'Symlink'):
                self.tools.execute('read_file', {'path': path})
        self.assertNotIn('link.txt', self.tools.execute('list_files', {})['files'])

    def test_actual_branch_task_branch_allowlist_and_conflict(self):
        for branch in ['main', 'master', 'production', 'release', 'feat/../main', None, 'fix/other']:
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                Tools(self.root, load_agent('frontend-engineer'), ['*'], branch).execute(
                    'write_file', {'path': 'app/test.py', 'content': 'x'})
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            self.tools.execute('write_file', {'path': 'README.md', 'content': 'x'})
        content = 'print("fixture")\n'
        result = self.tools.execute('write_file', {'path': 'app/test.py', 'content': content,
                                                  'expected_sha256': hashlib.sha256(b'').hexdigest()})
        self.assertEqual(result['sha256'], hashlib.sha256(content.encode()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.tools.execute('write_file', {'path': 'app/test.py', 'content': 'new',
                                              'expected_sha256': hashlib.sha256(b'').hexdigest()})
        # Repeating the identical write after interruption is safe and idempotent.
        again = self.tools.execute('write_file', {'path': 'app/test.py', 'content': content,
                                                 'expected_sha256': hashlib.sha256(b'').hexdigest()})
        self.assertEqual(again, result)
        subprocess.run(['git', '-C', str(self.root), 'switch', '-c', 'main'], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, 'branch'):
            self.tools.execute('write_file', {'path': 'app/test.py', 'content': 'wrong branch'})

    def test_read_and_write_size_budgets(self):
        (self.root / 'large.txt').write_text('a' * 50001)
        with self.assertRaisesRegex(ValueError, '50KB'):
            self.tools.execute('read_file', {'path': 'large.txt'})
        with self.assertRaisesRegex(ValueError, '100KB'):
            self.tools.execute('write_file', {'path': 'app/large.txt', 'content': 'a' * 100001})

    def test_docker_receives_sanitized_snapshot_only(self):
        for name in ['.env', '.git/config', 'runtime/state.json', 'credentials/auth.json', 'app_token.txt', 'db.sqlite']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture-secret')
        (self.root / 'tests').mkdir()
        (self.root / 'tests/test_ok.py').write_text('import unittest\n')
        outside = Path(self.tmp.name) / 'outside.txt'
        outside.write_text('private outside')
        (self.root / 'link.txt').symlink_to(outside)
        inspected = []
        def inspect_snapshot(snapshot):
            inspected.append(snapshot)
            self.assertNotEqual(snapshot, self.root)
            self.assertEqual((snapshot / 'README.md').read_text(), 'fixture')
            self.assertTrue((snapshot / 'tests/test_ok.py').exists())
            for name in ['.env', '.git', 'runtime', 'credentials', 'app_token.txt', 'db.sqlite', 'link.txt']:
                self.assertFalse((snapshot / name).exists(), name)
            return {'passed': True, 'exit_code': 0, 'tests_run': 1, 'cleanup_ok': True, 'output': 'mocked Docker'}
        with patch('horizon_core.tools.shutil.which', return_value='/usr/bin/docker'), patch.object(
                Tools, '_docker_check', side_effect=inspect_snapshot):
            result = Tools(self.root, load_agent('qa-engineer')).execute('run_check', {'name': 'python-unittest'})
        self.assertTrue(result['passed'])
        self.assertFalse(inspected[0].exists())

    def test_docker_command_has_no_host_secrets_and_always_cleans_container(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b'OK fixture\n')
        os.close(write_fd)
        reader = os.fdopen(read_fd, 'rb')
        process = MagicMock()
        process.stdout = reader
        process.wait.return_value = 0
        process.poll.return_value = 0
        try:
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake-provider-secret', 'GH_TOKEN': 'fake-git-secret'}), patch(
                    'horizon_core.tools.subprocess.Popen', return_value=process) as launch, patch(
                    'horizon_core.tools.subprocess.run') as cleanup:
                result = self.tools._docker_check(self.root)
            command = launch.call_args.args[0]
            self.assertEqual(command[:2], ['docker', 'run'])
            for flag in ['--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=65534:65534']:
                self.assertIn(flag, command)
            self.assertNotIn('shell', launch.call_args.kwargs)
            self.assertEqual(set(launch.call_args.kwargs['env']) - {'PATH', 'HOME'}, set())
            self.assertNotIn('fake-provider-secret', repr(launch.call_args))
            self.assertNotIn('fake-git-secret', repr(launch.call_args))
            name = command[command.index('--name') + 1]
            self.assertEqual(cleanup.call_args.args[0], ['docker', 'rm', '-f', name])
            self.assertEqual(result['output'], 'OK fixture\n')
            self.assertTrue(result['passed'])
        finally:
            reader.close()

    def test_docker_timeout_kills_process_and_removes_container(self):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, 'rb')
        process = MagicMock()
        process.stdout = reader
        process.poll.return_value = None
        try:
            with patch('horizon_core.tools.subprocess.Popen', return_value=process), patch(
                    'horizon_core.tools.subprocess.run') as cleanup, patch(
                    'horizon_core.tools.time.monotonic', side_effect=[0, 91]), self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.tools._docker_check(self.root)
            process.kill.assert_called_once()
            self.assertEqual(cleanup.call_args.args[0][:3], ['docker', 'rm', '-f'])
        finally:
            os.close(write_fd)
            reader.close()

    def test_unapproved_check_and_missing_docker_fail_closed(self):
        qa = Tools(self.root, load_agent('qa-engineer'))
        with self.assertRaisesRegex(ValueError, 'approved'):
            qa.execute('run_check', {'name': 'arbitrary-shell'})
        with patch('horizon_core.tools.shutil.which', return_value=None), self.assertRaisesRegex(RuntimeError, 'Docker unavailable'):
            qa.execute('run_check', {'name': 'python-unittest'})


if __name__ == '__main__':
    unittest.main()
