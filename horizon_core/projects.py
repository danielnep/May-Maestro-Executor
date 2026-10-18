"""Prepare isolated public-project worktrees without inheriting controller secrets.

Only metadata reads and Git operations run on the host. No project scripts,
package hooks, credential helpers or submodules are executed. Each worktree is
removed on context exit; checkpoints and generated content belong in State.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .github_state import GitHubState
from .publication_policy import restore_controller_baseline


@dataclass(frozen=True)
class PreparedProject:
    root: Path
    repo: str
    branch: str
    default_branch: str
    baseline: object
    operator_policy: dict
    allowed_paths: tuple[str, ...]


def _work_branch(branch):
    return (isinstance(branch, str) and re.fullmatch(r'(feat|fix|agent|test)/[A-Za-z0-9_-][A-Za-z0-9_./-]*', branch)
            and '..' not in branch and '//' not in branch and not branch.endswith('/'))


@contextmanager
def prepare_project(repo, branch, operator_policy=None, api=None, runner=None):
    """Yield a temporary public danielnep/* project, captured before branch edits.

    `operator_policy` must originate from the operator/controller, never model JSON.
    Metadata is fetched through the GitHub connector transport; its token is never
    inherited by the Git subprocess or put into a URL/command. Existing projects
    default to branch-only updates. A new product in a nonempty repo must occupy a
    new explicitly authorized namespace; the rest of that repo stays read-only.
    """
    if not isinstance(repo, str) or not re.fullmatch(r'danielnep/[A-Za-z0-9_.-]+', repo) or repo.split('/')[1] in ('.', '..'):
        raise ValueError('Project router accepts only public danielnep repositories')
    if not _work_branch(branch):
        raise ValueError('Prepare projects on an authorized work branch')
    transport = api or GitHubState(repo)
    metadata = transport._request('GET', '')
    if not isinstance(metadata, dict) or metadata.get('private') is not False:
        raise ValueError('Project router requires confirmed public repository metadata')
    if metadata.get('full_name', repo).lower() != repo.lower():
        raise ValueError('Project metadata does not match the authorized repository')
    default = metadata.get('default_branch')
    if not isinstance(default, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*', default) or '..' in default or '//' in default or default.endswith('/'):
        raise ValueError('Repository default branch metadata is invalid')
    if branch == default:
        raise ValueError('Project work must use a separate branch')
    policy = dict(operator_policy or {})
    if policy and policy.get('target_repo') != repo:
        raise ValueError('Operator publication target does not match the prepared repository')
    invoke = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix='horizon-project-') as directory:
        root = Path(directory) / 'worktree'
        home = Path(directory) / 'home'
        home.mkdir()
        child_env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if os.environ.get(key)}
        child_env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                         GIT_TERMINAL_PROMPT='0', GIT_ASKPASS=os.devnull)
        def git(arguments, required=True):
            try:
                result = invoke(['git', '-c', 'credential.helper=', '-c', 'core.hooksPath=' + os.devnull, *arguments],
                                env=child_env, capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError('Public project Git operation unavailable or timed out') from None
            if required and result.returncode:
                raise ValueError('Public project Git operation failed')
            return result
        git(['clone', '--no-tags', '--no-recurse-submodules', 'https://github.com/' + repo + '.git', str(root)])
        remote_main = git(['-C', str(root), 'rev-parse', '--verify', 'refs/remotes/origin/' + default], required=False)
        if remote_main.returncode == 0:
            git(['-C', str(root), 'checkout', '--detach', 'refs/remotes/origin/' + default])
        elif git(['-C', str(root), 'rev-parse', '--verify', 'HEAD'], required=False).returncode == 0:
            raise ValueError('Repository default branch was not fetched')
        # Capture with the same credential-free child environment as clone.
        # The standalone policy helper intentionally uses ordinary local Git;
        # project routing must not inherit the controller environment at all.
        origin = git(['-C', str(root), 'remote', 'get-url', 'origin']).stdout.strip()
        if origin != 'https://github.com/' + repo + '.git':
            raise ValueError('Prepared worktree origin differs from its public target')
        current_head = git(['-C', str(root), 'rev-parse', '--verify', 'HEAD'], required=False)
        base_sha = current_head.stdout.strip() if current_head.returncode == 0 else None
        paths = set()
        if base_sha is not None:
            paths.update(filter(None, git(['-C', str(root), 'ls-tree', '-r', '--name-only', '-z', 'HEAD']).stdout.split('\x00')))
        else:
            refs = git(['-C', str(root), 'show-ref', '--head'], required=False)
            if refs.returncode not in (0, 1) or refs.stdout.strip():
                raise ValueError('Cannot verify an empty project baseline')
        for args in (['--cached'], ['--others', '--exclude-standard']):
            paths.update(filter(None, git(['-C', str(root), 'ls-files', *args, '-z']).stdout.split('\x00')))
        baseline = restore_controller_baseline({'repo': repo, 'base_sha': base_sha,
            'existing_paths': sorted(paths), 'source': 'git'})
        namespace = policy.get('new_project_namespace')
        if policy.get('delivery_type') == 'new_product' and baseline.existing_paths:
            if not isinstance(namespace, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', namespace):
                raise ValueError('A new product in an existing repo requires an authorized new namespace')
            if any(path == namespace or path.startswith(namespace + '/') for path in baseline.existing_paths):
                raise ValueError('Authorized new-project namespace already exists')
        git(['-C', str(root), 'checkout', '-b', branch])
        allowed = (namespace + '/**',) if isinstance(namespace, str) and namespace else tuple(policy.get('allowed_paths', ()))
        yield PreparedProject(root, repo, branch, default, baseline, policy, allowed)
