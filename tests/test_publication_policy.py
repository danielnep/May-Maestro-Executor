"""Deterministic publication rules using real local Git baselines, no remote writes."""
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest

from horizon_core.publication_policy import (RepositoryBaseline, capture_git_baseline,
    decide_publication, restore_controller_baseline, workspace_fingerprint)


class PublicationPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', 'https://github.com/owner/product.git')
        self.repo = 'owner/product'
        self.digest = workspace_fingerprint({'app.py': 'print("fixture")', 'tests/test_app.py': 'fixture'})
        self.check = {'source': 'runtime', 'simulated': False, 'backend': 'railway-sdk',
                      'execution_id': 'test-operation-1', 'passed': True, 'exit_code': 0,
                      'tests_run': 3, 'timed_out': False, 'truncated': False,
                      'cleanup_ok': True, 'workspace_digest': self.digest}
        self.policy = {'delivery_type': 'new_product', 'target_repo': self.repo,
                       'target_branch': 'main', 'allow_new_product_main': True}

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.root), *arguments],
                              capture_output=True, text=True, check=True).stdout.strip()

    def baseline(self):
        return capture_git_baseline(self.root, self.repo)

    def decide(self, **overrides):
        inputs = dict(repo=self.repo, branch='main', default_branch='main', operator_policy=self.policy,
                      baseline=self.baseline(), checks=[self.check], completed=True,
                      head_sha=None, changed_paths=['app.py', 'tests/test_app.py'], workspace_digest=self.digest)
        inputs.update(overrides)
        return decide_publication(**inputs)

    def existing_product(self):
        (self.root / 'client.py').write_text('APPROVED_CLIENT_DATA_CONTRACT = True\n')
        self.git('add', 'client.py')
        self.git('commit', '-m', 'Existing customer product')
        return self.git('rev-parse', 'HEAD')

    def test_validated_new_product_automatically_allowed_in_empty_repository(self):
        result = self.decide()
        self.assertTrue(result['allowed'])
        self.assertEqual(result['reason'], 'validated_new_product_in_empty_repository')
        self.assertFalse(result['production_deploy_allowed'])
        self.assertFalse(result['force_push_allowed'])

    def test_existing_client_repository_cannot_be_relabelled_new_by_llm(self):
        head = self.existing_product()
        result = self.decide(head_sha=head)
        self.assertFalse(result['allowed'])
        fake = {'repo': self.repo, 'base_sha': head, 'existing_paths': [], 'source': 'git', 'observed_by': 'controller'}
        self.assertFalse(self.decide(head_sha=head, baseline=fake)['allowed'])
        fabricated = RepositoryBaseline(self.repo, head, ())
        self.assertFalse(self.decide(head_sha=head, baseline=fabricated)['allowed'])

    def test_authorized_new_namespace_in_existing_repo_is_limited_to_that_namespace(self):
        head = self.existing_product()
        operator = {**self.policy, 'new_project_namespace': 'products/new-app'}
        self.assertTrue(self.decide(head_sha=head, operator_policy=operator,
            changed_paths=['products/new-app/app.py', 'products/new-app/tests/test_app.py'])['allowed'])
        self.assertFalse(self.decide(head_sha=head, operator_policy=operator,
            changed_paths=['products/new-app/app.py', 'client.py'])['allowed'])
        self.assertEqual((self.root / 'client.py').read_text(), 'APPROVED_CLIENT_DATA_CONTRACT = True\n')

    def test_existing_namespace_is_an_update_even_when_new_product_flag_set(self):
        (self.root / 'products/new-app').mkdir(parents=True)
        (self.root / 'products/new-app/app.py').write_text('already exists')
        self.git('add', '.')
        self.git('commit', '-m', 'Existing namespace')
        head = self.git('rev-parse', 'HEAD')
        operator = {**self.policy, 'new_project_namespace': 'products/new-app'}
        result = self.decide(head_sha=head, operator_policy=operator, changed_paths=['products/new-app/app.py'])
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'project_namespace_already_exists')

    def test_new_product_operator_flag_cannot_approve_an_existing_update(self):
        policy = {**self.policy, 'delivery_type': 'existing_product_update'}
        self.assertFalse(self.decide(operator_policy=policy)['allowed'])
        self.assertTrue(self.decide(branch='feat/customer-update', operator_policy=policy)['allowed'])

    def test_existing_update_main_requires_specific_approval_for_current_content_and_head(self):
        head = self.existing_product()
        policy = {**self.policy, 'delivery_type': 'existing_product_update',
                  'allow_existing_product_main': True, 'approval_id': 'explicit-user-approval-42',
                  'approved_workspace_digest': self.digest, 'approved_base_sha': head}
        self.assertTrue(self.decide(head_sha=head, operator_policy=policy)['allowed'])
        self.assertFalse(self.decide(head_sha='a' * 40, operator_policy=policy)['allowed'])
        self.assertFalse(self.decide(head_sha=head, operator_policy={**policy, 'approval_id': ''})['allowed'])
        self.assertFalse(self.decide(head_sha=head, operator_policy={**policy, 'approved_workspace_digest': 'b' * 64})['allowed'])

    def test_target_binding_cannot_be_reused_for_another_repository_or_branch(self):
        for overrides in ({'repo': 'owner/client'}, {'operator_policy': {**self.policy, 'target_branch': 'other'}},
                          {'operator_policy': {**self.policy, 'allow_new_product_main': False}}):
            self.assertFalse(self.decide(**overrides)['allowed'])
        self.assertFalse(self.decide(default_branch='trunk', branch='trunk')['allowed'])

    def test_failed_zero_empty_simulated_stale_or_truncated_checks_never_approve_main(self):
        for changes in ({'passed': False}, {'exit_code': 1}, {'exit_code': False}, {'tests_run': 0},
                        {'tests_run': True}, {'simulated': True}, {'source': 'model'}, {'timed_out': True},
                        {'truncated': True}, {'cleanup_ok': False}, {'execution_id': ''},
                        {'workspace_digest': 'a' * 64}):
            with self.subTest(changes=changes):
                self.assertFalse(self.decide(checks=[{**self.check, **changes}])['allowed'])
        self.assertFalse(self.decide(checks=[])['allowed'])
        self.assertFalse(self.decide(completed=False)['allowed'])
        self.assertFalse(self.decide(checks=[self.check, {**self.check, 'passed': False}])['allowed'])
        incomplete = dict(self.check)
        incomplete.pop('timed_out')
        self.assertFalse(self.decide(checks=[incomplete])['allowed'])

    def test_changed_workspace_invalidates_prior_successful_checks(self):
        digest = workspace_fingerprint({'app.py': 'changed after validation'})
        self.assertNotEqual(digest, self.digest)
        self.assertFalse(self.decide(workspace_digest=digest)['allowed'])

    def test_remote_head_advance_invalidates_new_product_baseline(self):
        self.assertFalse(self.decide(head_sha='a' * 40)['allowed'])
        self.assertFalse(decide_publication(repo=self.repo, branch='main', operator_policy=self.policy,
            baseline=self.baseline(), completed=True, checks=[self.check], workspace_digest=self.digest,
            changed_paths=['app.py'])['allowed'])

    def test_work_branch_authorization_is_not_production_or_main_authorization(self):
        result = decide_publication(repo=self.repo, branch='feat/prototype', operator_policy={})
        self.assertTrue(result['allowed'])
        self.assertFalse(result['production_deploy_allowed'])
        self.assertFalse(result['force_push_allowed'])
        self.assertFalse(decide_publication(repo=self.repo, branch='main')['allowed'])
        for branch in ('production', 'release', 'master', 'feat/../main', 'feat//bad'):
            self.assertFalse(decide_publication(repo=self.repo, branch=branch)['allowed'])

    def test_baseline_includes_untracked_and_staged_files_and_origin_is_bound(self):
        (self.root / 'app.py').write_text('existing but not committed')
        self.assertIn('app.py', self.baseline().existing_paths)
        self.git('add', 'app.py')
        self.assertIn('app.py', self.baseline().existing_paths)
        with self.assertRaises(ValueError):
            capture_git_baseline(self.root, 'owner/some-other-repo')

    def test_controller_baseline_restores_without_reclassifying_existing_paths(self):
        head = self.existing_product()
        captured = self.baseline()
        restored = restore_controller_baseline(captured.as_dict())
        self.assertEqual(restored.existing_paths, captured.existing_paths)
        self.assertFalse(self.decide(head_sha=head, baseline=restored)['allowed'])
        with self.assertRaises(ValueError):
            restore_controller_baseline({'repo': self.repo, 'existing_paths': [], 'source': 'model'})

    def test_publication_policy_never_mutates_git_history_or_worktree(self):
        head = self.existing_product()
        before = self.git('status', '--porcelain')
        self.decide(head_sha=head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('status', '--porcelain'), before)

    def test_namespace_paths_are_canonical_and_cannot_escape(self):
        head = self.existing_product()
        for namespace in ('../client', '/absolute', 'products/../client', 'products//new', 'products\\new'):
            self.assertFalse(self.decide(head_sha=head, operator_policy={**self.policy, 'new_project_namespace': namespace},
                changed_paths=['products/new/app.py'])['allowed'])
        for path in ('../client.py', '/client.py', 'products/new/../../client.py'):
            self.assertFalse(self.decide(changed_paths=[path])['allowed'])

    def test_workspace_digest_is_order_independent_and_avoids_path_content_collisions(self):
        self.assertEqual(workspace_fingerprint({'a': 'x', 'b': 'y'}), workspace_fingerprint({'b': 'y', 'a': 'x'}))
        self.assertNotEqual(workspace_fingerprint({'ab': 'c'}), workspace_fingerprint({'a': 'bc'}))
        with self.assertRaises(ValueError):
            workspace_fingerprint({'../secret': 'x'})


if __name__ == '__main__':
    unittest.main()
