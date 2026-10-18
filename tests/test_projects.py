"""Project router checks using real local git clones and simulated public metadata."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from horizon_core.projects import prepare_project


class Metadata:
    def __init__(self, **updates):
        self.value = {'private': False, 'full_name': 'danielnep/fixture', 'default_branch': 'main', **updates}
    def _request(self, method, endpoint, body=None):
        if (method, endpoint) != ('GET', ''):
            raise AssertionError('Unexpected metadata operation')
        return self.value


class ProjectRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source'
        self.source.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.commands = []
        self.policy = {'delivery_type': 'new_product', 'target_repo': 'danielnep/fixture',
                       'target_branch': 'main', 'allow_new_product_main': True,
                       'new_project_namespace': 'apps/new-product'}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source), *args], capture_output=True, text=True, check=True).stdout.strip()

    def existing(self):
        (self.source / 'README.md').write_text('existing application\n')
        (self.source / 'package.json').write_text('{"scripts":{"postinstall":"touch MUST_NOT_EXECUTE"}}')
        self.git('add', '.')
        self.git('commit', '-m', 'baseline')
        return self.git('rev-parse', 'HEAD')

    def runner(self, command, **options):
        self.commands.append((command, options))
        actual = list(command)
        if 'clone' in actual:
            url = 'https://github.com/danielnep/fixture.git'
            self.assertIn(url, actual)
            actual[actual.index(url)] = str(self.source)
            result = subprocess.run(actual, **options)
            if result.returncode == 0:
                subprocess.run(['git', '-C', actual[-1], 'remote', 'set-url', 'origin', url],
                               env=options['env'], capture_output=True, text=True, check=True)
            return result
        return subprocess.run(actual, **options)

    def test_existing_public_repository_isolated_and_baseline_precedes_work_branch(self):
        head = self.existing()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'do-not-inherit', 'GITHUB_TOKEN': 'no-git-token'}):
            with prepare_project('danielnep/fixture', 'feat/new-product', self.policy, Metadata(), self.runner) as project:
                path = project.root
                self.assertTrue(path.exists())
                self.assertEqual(project.baseline.base_sha, head)
                self.assertIn('README.md', project.baseline.existing_paths)
                self.assertEqual(project.allowed_paths, ('apps/new-product/**',))
                branch = subprocess.run(['git', '-C', str(path), 'branch', '--show-current'], capture_output=True, text=True, check=True).stdout.strip()
                self.assertEqual(branch, 'feat/new-product')
                self.assertFalse((path / 'MUST_NOT_EXECUTE').exists())
        self.assertFalse(path.exists())
        for command, options in self.commands:
            self.assertNotIn('DEEPSEEK_API_KEY', options['env'])
            self.assertNotIn('GITHUB_TOKEN', options['env'])
            self.assertEqual(options['env']['GIT_TERMINAL_PROMPT'], '0')
            self.assertEqual(options['env']['GIT_CONFIG_NOSYSTEM'], '1')
            self.assertIn('credential.helper=', command)
            self.assertNotIn('no-git-token', ' '.join(command))

    def test_empty_public_repository_prepares_without_creating_a_commit(self):
        with prepare_project('danielnep/fixture', 'feat/first-product', self.policy, Metadata(), self.runner) as project:
            self.assertIsNone(project.baseline.base_sha)
            self.assertEqual(project.baseline.existing_paths, ())
            result = subprocess.run(['git', '-C', str(project.root), 'rev-parse', '--verify', 'HEAD'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_owner_privacy_target_and_main_branch_rejected_before_clone(self):
        for repo, branch, metadata, policy in (
            ('other/fixture', 'feat/work', Metadata(), self.policy),
            ('danielnep/fixture', 'main', Metadata(), self.policy),
            ('danielnep/fixture', 'feat/work', Metadata(private=True), self.policy),
            ('danielnep/fixture', 'feat/work', Metadata(), {**self.policy, 'target_repo': 'danielnep/client'}),
            ('danielnep/fixture', 'feat/../main', Metadata(), self.policy),
        ):
            with self.subTest(repo=repo, branch=branch, metadata=metadata.value):
                with self.assertRaises(ValueError):
                    with prepare_project(repo, branch, policy, metadata, self.runner):
                        pass
        self.assertEqual(self.commands, [])

    def test_existing_product_namespace_cannot_be_renamed_new(self):
        self.existing()
        (self.source / 'apps/new-product').mkdir(parents=True)
        (self.source / 'apps/new-product/app.py').write_text('existing')
        self.git('add', '.')
        self.git('commit', '-m', 'existing product')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            with prepare_project('danielnep/fixture', 'feat/work', self.policy, Metadata(), self.runner):
                pass

    def test_clone_failure_is_sanitized_and_does_not_execute_arbitrary_command(self):
        def fail(command, **options):
            return type('Result', (), {'returncode': 1, 'stdout': 'sensitive-fixture', 'stderr': 'sensitive-fixture'})()
        with self.assertRaises(ValueError) as caught:
            with prepare_project('danielnep/fixture', 'feat/work', self.policy, Metadata(), fail):
                pass
        self.assertNotIn('sensitive-fixture', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
