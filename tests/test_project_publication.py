"""Principal publication applies only generated files onto the observed main tree."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from horizon_core.publication_policy import capture_git_baseline, workspace_fingerprint
from horizon_core.publish import candidate_snapshot, publish
from horizon_core.state import State


class MainAPI:
    def __init__(self, main_head):
        self.head = main_head
        self.calls = []
        self.race = False
        self.observations = 0
    def _request(self, method, endpoint, body=None, missing_ok=False):
        self.calls.append((method, endpoint, body))
        if endpoint == '':
            return {'default_branch': 'main'}
        if method == 'GET' and endpoint == '/git/ref/heads/main':
            self.observations += 1
            if self.race and self.observations > 1:
                return {'object': {'sha': 'a' * 40}}
            return {'object': {'sha': self.head}} if self.head else None
        if method == 'GET' and endpoint.startswith('/git/commits/'):
            return {'tree': {'sha': 'main-base-tree'}}
        if endpoint == '/git/trees':
            return {'sha': 'candidate-tree'}
        if endpoint == '/git/commits':
            return {'sha': 'validated-commit'}
        if method in ('POST', 'PATCH') and endpoint.startswith('/git/ref'):
            self.head = body['sha']
            return {}
        raise AssertionError('Unexpected API operation')


class PrincipalPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', 'https://github.com/danielnep/fixture.git')
        self.state = State(':memory:')
        self.addCleanup(self.state.close)
        self.policy = {'delivery_type': 'new_product', 'target_repo': 'danielnep/fixture',
            'target_branch': 'main', 'allow_new_product_main': True,
            'new_project_namespace': 'apps/new-product'}
        self.elements = {'apps/new-product/app.py': {'path': 'apps/new-product/app.py', 'type': 'blob',
            'mode': '100644', 'content': 'TITLE = "new product"\n'}}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, text=True, check=True).stdout.strip()

    def prepare(self, empty=False, failed=False):
        if not empty:
            (self.root / 'server.py').write_text('ORIGINAL = True\n')
            self.git('add', '.')
            self.git('commit', '-m', 'approved main')
        self.baseline = capture_git_baseline(self.root, 'danielnep/fixture')
        self.git('switch', '-c', 'feat/new-product')
        if not empty:
            (self.root / 'server.py').write_text('UNAPPROVED_FEATURE_CHANGE = True\n')
            self.git('add', 'server.py')
            self.git('commit', '-m', 'must not merge this unrelated feature change')
        self.state.enqueue('fixture', 'frontend-engineer', 'new-product', 'Implement new product',
            payload={'branch': 'feat/new-product', 'allowed_paths': ['apps/new-product/**']})
        for path, element in self.elements.items():
            self.state.remember('new-product', '__workspace__', path, {'content': element['content'], 'branch': 'feat/new-product'})
        self.state.remember('new-product', '__operator__', 'publication_policy', self.policy)
        self.state.remember('new-product', '__operator__', 'baseline', self.baseline.as_dict())
        digest = workspace_fingerprint(candidate_snapshot(self.root, self.baseline.base_sha, self.elements))
        self.check = {'source': 'runtime', 'simulated': False, 'backend': 'railway-sdk', 'execution_id': 'fixture-execution',
            'passed': not failed, 'exit_code': 1 if failed else 0, 'tests_run': 1, 'cleanup_ok': True,
            'timed_out': False, 'truncated': False, 'workspace_digest': digest}
        self.state.claim('fixture-worker')
        self.state.complete('fixture', 'fixture-worker', {'verification': [self.check]})
        self.api = MainAPI(self.baseline.base_sha)

    def test_new_namespace_main_has_only_generated_elements_and_main_parent(self):
        self.prepare()
        feature_head = self.git('rev-parse', 'HEAD')
        result = publish(self.state, self.root, 'danielnep/fixture', api=self.api)
        self.assertTrue(result['published'])
        tree = next(body for method, endpoint, body in self.api.calls if endpoint == '/git/trees')
        self.assertEqual(tree['base_tree'], 'main-base-tree')
        self.assertEqual([entry['path'] for entry in tree['tree']], ['apps/new-product/app.py'])
        commit = next(body for method, endpoint, body in self.api.calls if method == 'POST' and endpoint == '/git/commits')
        self.assertEqual(commit['parents'], [self.baseline.base_sha])
        self.assertNotIn(feature_head, commit['parents'])
        self.assertEqual(self.api.calls[-1], ('PATCH', '/git/refs/heads/main', {'sha': 'validated-commit', 'force': False}))
        self.assertFalse(result['production_deployed'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), feature_head)

    def test_empty_repo_creates_first_commit_and_ref_without_force(self):
        self.prepare(empty=True)
        result = publish(self.state, self.root, 'danielnep/fixture', api=self.api)
        self.assertTrue(result['published'])
        tree = next(body for method, endpoint, body in self.api.calls if endpoint == '/git/trees')
        self.assertNotIn('base_tree', tree)
        commit = next(body for method, endpoint, body in self.api.calls if method == 'POST' and endpoint == '/git/commits')
        self.assertEqual(commit['parents'], [])
        self.assertEqual(self.api.calls[-1], ('POST', '/git/refs', {'ref': 'refs/heads/main', 'sha': 'validated-commit'}))

    def test_failed_verification_creates_no_git_objects_or_ref_updates(self):
        self.prepare(failed=True)
        result = publish(self.state, self.root, 'danielnep/fixture', api=self.api)
        self.assertFalse(result['published'])
        self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))

    def test_final_tree_mismatch_with_feature_checks_blocks_main(self):
        self.prepare()
        modified_digest = workspace_fingerprint({'server.py': 'UNAPPROVED_FEATURE_CHANGE = True\n',
                                                 'apps/new-product/app.py': self.elements['apps/new-product/app.py']['content']})
        # Simulate a runtime check of the wrong feature snapshot, not the actual candidate tree.
        self.state.db.execute('UPDATE tasks SET result=? WHERE task_id=?',
            (json.dumps({'verification': [{**self.check, 'workspace_digest': modified_digest}]}), 'fixture'))
        result = publish(self.state, self.root, 'danielnep/fixture', api=self.api)
        self.assertFalse(result['published'])
        self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))

    def test_race_on_main_never_updates_reference(self):
        self.prepare()
        self.api.race = True
        with self.assertRaisesRegex(ValueError, 'changed remotely'):
            publish(self.state, self.root, 'danielnep/fixture', api=self.api)
        self.assertFalse(any(method == 'PATCH' or (method == 'POST' and endpoint == '/git/refs')
                             for method, endpoint, _ in self.api.calls))

    def test_missing_work_branch_is_created_at_observed_main_without_force(self):
        self.prepare()
        self.git('reset', '--hard', self.baseline.base_sha)
        class WorkAPI(MainAPI):
            work_head = None
            def _request(self, method, endpoint, body=None, missing_ok=False):
                if endpoint == '/git/ref/heads/feat/new-product':
                    self.calls.append((method, endpoint, body))
                    return {'object': {'sha': self.work_head}} if self.work_head else None
                if method == 'POST' and endpoint == '/git/refs' and body['ref'] == 'refs/heads/feat/new-product':
                    self.calls.append((method, endpoint, body))
                    self.work_head = body['sha']
                    return {}
                if method == 'PATCH' and endpoint == '/git/refs/heads/feat/new-product':
                    self.calls.append((method, endpoint, body))
                    self.work_head = body['sha']
                    return {}
                return super()._request(method, endpoint, body, missing_ok)
        api = WorkAPI(self.baseline.base_sha)
        result = publish(self.state, self.root, 'danielnep/fixture', api=api,
                         target_branch='feat/new-product', project='new-product')
        self.assertTrue(result['published'])
        self.assertIn(('POST', '/git/refs', {'ref': 'refs/heads/feat/new-product', 'sha': self.baseline.base_sha}), api.calls)
        self.assertEqual(api.head, self.baseline.base_sha)
        self.assertEqual(api.calls[-1], ('PATCH', '/git/refs/heads/feat/new-product', {'sha': 'validated-commit', 'force': False}))

    def test_project_filter_does_not_mix_another_queued_project_or_workspace(self):
        self.prepare()
        self.state.enqueue('other', 'frontend-engineer', 'unrelated', 'Unrelated update',
            payload={'branch': 'feat/unrelated', 'allowed_paths': ['apps/unrelated/**']})
        self.state.remember('unrelated', '__workspace__', 'apps/unrelated/app.py',
            {'branch': 'feat/unrelated', 'content': 'must not enter first project'})
        result = publish(self.state, self.root, 'danielnep/fixture', api=self.api, project='new-product')
        self.assertTrue(result['published'])
        self.assertEqual(result['files'], ['apps/new-product/app.py'])
        self.assertFalse((self.root / 'apps/unrelated/app.py').exists())
        self.assertEqual(self.state.get('other')['status'], 'queued')

    def test_old_failed_snapshot_does_not_block_current_success_but_latest_current_failure_does(self):
        self.prepare()
        history = [{**self.check, 'check': 'web-app', 'workspace_digest': 'b' * 64, 'passed': False, 'exit_code': 1, 'executed_at': 1},
                   {**self.check, 'check': 'web-app', 'executed_at': 2}]
        self.state.db.execute('UPDATE tasks SET result=? WHERE task_id=?', (json.dumps({'verification': history}), 'fixture'))
        self.assertTrue(publish(self.state, self.root, 'danielnep/fixture', api=self.api, project='new-product')['published'])
        self.api = MainAPI(self.baseline.base_sha)
        history.append({**self.check, 'check': 'web-app', 'passed': False, 'exit_code': 1, 'executed_at': 3})
        self.state.db.execute('UPDATE tasks SET result=? WHERE task_id=?', (json.dumps({'verification': history}), 'fixture'))
        result = publish(self.state, self.root, 'danielnep/fixture', api=self.api, project='new-product')
        self.assertFalse(result['published'])
        self.assertFalse(any(method != 'GET' for method, _, _ in self.api.calls))

    def test_latest_current_check_keeps_independent_configurations_separate(self):
        self.prepare()
        checks = [{**self.check, 'check': 'web-app', 'project_dir': 'apps/new-product', 'executed_at': 1},
                  {**self.check, 'check': 'web-app', 'project_dir': 'apps/another', 'passed': False, 'exit_code': 1, 'executed_at': 2}]
        self.state.db.execute('UPDATE tasks SET result=? WHERE task_id=?', (json.dumps({'verification': checks}), 'fixture'))
        self.assertFalse(publish(self.state, self.root, 'danielnep/fixture', api=self.api, project='new-product')['published'])


if __name__ == '__main__':
    unittest.main()
