"""Publication contract checks with a real local git fixture and fake REST API."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from horizon_core.publish import publish
from horizon_core.state import State


class FakeAPI:
    def __init__(self, head):
        self.head = head
        self.default = 'main'
        self.same_tree = False
        self.calls = []

    def _request(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        if endpoint == '':
            return {'default_branch': self.default}
        if method == 'GET' and endpoint.startswith('/git/ref/heads/'):
            return {'object': {'sha': self.head}}
        if method == 'GET' and endpoint.startswith('/git/commits/'):
            return {'tree': {'sha': 'base-tree'}}
        if endpoint == '/git/trees':
            return {'sha': 'base-tree' if self.same_tree else 'generated-tree'}
        if endpoint == '/git/commits':
            return {'sha': 'new-commit'}
        if method == 'PATCH':
            return {}
        raise AssertionError('Unexpected REST request')


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'worktree'
        self.root.mkdir()
        self.git('init')
        self.git('switch', '-c', 'feat/patch')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'README.md').write_text('synthetic fixture\n')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'fixture')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.state = State(':memory:')
        self.addCleanup(self.state.close)
        self.api = FakeAPI(self.head)

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.root), *arguments],
                              check=True, capture_output=True, text=True).stdout

    def task(self, branch='feat/patch', filename='app/ui.py'):
        self.state.enqueue('fixture', 'frontend-engineer', 'synthetic', 'Write synthetic UI',
                           payload={'branch': branch, 'allowed_paths': [filename]})
        self.state.remember('synthetic', '__workspace__', filename,
                            {'content': 'TITLE = "Ronda Pro"\n', 'branch': branch})

    def test_publish_patches_only_authorized_work_branch_without_force(self):
        self.task()
        result = publish(self.state, self.root, 'owner/repo', api=self.api)
        self.assertTrue(result['published'])
        self.assertEqual(result['files'], ['app/ui.py'])
        trees = [body for method, endpoint, body in self.api.calls if endpoint == '/git/trees']
        self.assertEqual(trees[0]['base_tree'], 'base-tree')
        self.assertEqual([entry['path'] for entry in trees[0]['tree']], ['app/ui.py'])
        self.assertEqual(self.api.calls[-1], ('PATCH', '/git/refs/heads/feat/patch',
                                            {'sha': 'new-commit', 'force': False}))
        commits = [body for method, endpoint, body in self.api.calls
                   if method == 'POST' and endpoint == '/git/commits']
        self.assertEqual(commits[0]['parents'], [self.head])
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.head)
        self.assertEqual(self.git('branch', '--show-current').strip(), 'feat/patch')

    def test_rejects_remote_head_changed_without_mutation(self):
        self.task()
        self.api.head = 'changed-remotely'
        with self.assertRaisesRegex(ValueError, 'changed remotely'):
            publish(self.state, self.root, 'owner/repo', api=self.api)
        self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))

    def test_rejects_main_branch_before_any_remote_write(self):
        self.git('switch', '-c', 'main')
        self.task(branch='main')
        with self.assertRaises(ValueError):
            publish(self.state, self.root, 'owner/repo', api=self.api)
        self.assertFalse((self.root / 'app/ui.py').exists())
        self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))

    def test_rejects_repository_default_even_when_named_like_work_branch(self):
        self.task()
        self.api.default = 'feat/patch'
        with self.assertRaisesRegex(ValueError, 'default branch'):
            publish(self.state, self.root, 'owner/repo', api=self.api)
        self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))

    def test_same_tree_creates_no_commit_or_reference_update(self):
        self.task()
        self.api.same_tree = True
        result = publish(self.state, self.root, 'owner/repo', api=self.api)
        self.assertFalse(result['published'])
        self.assertFalse(any(method == 'PATCH' or (method == 'POST' and endpoint == '/git/commits')
                             for method, endpoint, _ in self.api.calls))

    def test_control_plane_and_traversal_never_modified_or_published(self):
        for filename in ('horizon_core/app.py', '.github/workflows/agent.yml',
                         'runtime/state.sqlite3', '../outside.txt'):
            with self.subTest(filename=filename):
                state = State(':memory:')
                try:
                    state.enqueue('fixture', 'frontend-engineer', 'synthetic', 'Malicious fixture',
                                  payload={'branch': 'feat/patch', 'allowed_paths': ['*']})
                    state.remember('synthetic', '__workspace__', filename, {'content': 'forbidden', 'branch': 'feat/patch'})
                    with self.assertRaises(ValueError):
                        publish(state, self.root, 'owner/repo', api=self.api)
                    self.assertFalse((self.root / filename).exists())
                    self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))
                finally:
                    state.close()


if __name__ == '__main__':
    unittest.main()
