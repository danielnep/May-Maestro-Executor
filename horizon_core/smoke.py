"""Live GitHub/Docker infrastructure proof with synthetic data and zero model calls."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from .github_state import GitHubState
from .state import State
from .tools import Tools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('prepare', 'resume'))
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run-id', required=True, type=int)
    args = parser.parse_args()
    remote = GitHubState(args.repo, branch='agent-state/smoke-' + str(args.run_id))
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / 'state.sqlite3'
        found = remote.pull(database)
        state = State(database)
        try:
            if args.phase == 'prepare':
                if found:
                    raise RuntimeError('Synthetic smoke state already exists; use a fresh run')
                state.enqueue('synthetic-resume', 'qa-engineer', 'synthetic-public-proof', 'Resume a real remote checkpoint')
                claimed = state.claim('first-runner', now=1000, lease_seconds=10)
                assert claimed['task_id'] == 'synthetic-resume'
                state.checkpoint(claimed['task_id'], 'first-runner', {'step': 2, 'evidence': 'synthetic'}, now=1001)
                state.remember('synthetic-public-proof', 'qa-engineer', 'decision', 'preserve synthetic checkpoint')
                remote.push(database)
                print(json.dumps({'phase': 'prepare', 'checkpoint_published': True, 'branch': remote.branch, 'model_calls': 0}))
            else:
                if not found:
                    raise RuntimeError('Remote checkpoint was not recovered')
                task = state.claim('second-runner', now=1020, lease_seconds=300)
                assert task['checkpoint']['step'] == 2 and task['attempts'] == 2
                assert state.recall('synthetic-public-proof', 'qa-engineer')['decision'] == 'preserve synthetic checkpoint'
                project = Path(directory) / 'fixture'
                (project / 'tests').mkdir(parents=True)
                (project / 'README.md').write_text('Synthetic smoke proof')
                (project / '.env').write_text('SYNTHETIC_ONLY=not-a-credential')
                (project / 'tests' / 'test_boundary.py').write_text(
                    'import os,unittest\nfrom pathlib import Path\n'
                    'class Boundary(unittest.TestCase):\n'
                    ' def test_no_credentials_or_git(self):\n'
                    '  self.assertFalse(Path(".env").exists())\n'
                    '  self.assertFalse(Path(".git").exists())\n'
                    '  self.assertFalse(os.getenv("GITHUB_TOKEN"))\n'
                    '  self.assertFalse(os.getenv("GEMINI_API_KEY"))\n'
                    '  self.assertTrue(Path("README.md").exists())\n')
                check = Tools(project, {'tools': ['run_check']}).execute('run_check', {'name': 'python-unittest'})
                if not check.get('passed'):
                    print(json.dumps(check))
                    raise RuntimeError('Live Docker check failed')
                state.complete(task['task_id'], 'second-runner', {'remote_resume': True, 'docker': check}, now=1021)
                remote.push(database)
                print(json.dumps({'phase': 'resume', 'remote_resume': True, 'docker_passed': True,
                                  'memory_recovered': True, 'branch': remote.branch, 'model_calls': 0}))
        finally:
            state.close()


if __name__ == '__main__':
    main()
