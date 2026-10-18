"""Deterministic branch/publication policy, independent of model judgments.

Operator config, captured baseline and runtime check records are separate trust
inputs. Never populate them from an LLM's tool args or summary. This policy grants
Git publication only; it never grants production deployment, database migration,
force push, deletion or rewriting existing commits.
"""
from dataclasses import dataclass, field
import hashlib
from pathlib import PurePosixPath
import re
import subprocess
from urllib.parse import urlparse

_RECEIPT = object()
_UNSET = object()
_REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
_WORK_BRANCH = re.compile(r'(feat|fix|agent|test)/[A-Za-z0-9_-][A-Za-z0-9_./-]*')
_DIGEST = re.compile(r'[0-9a-f]{64}')


@dataclass(frozen=True)
class RepositoryBaseline:
    repo: str
    base_sha: str | None
    existing_paths: tuple[str, ...]
    source: str = 'git'
    _receipt: object = field(default=None, init=False, repr=False, compare=False)

    def as_dict(self):
        return {'repo': self.repo, 'base_sha': self.base_sha,
                'existing_paths': list(self.existing_paths), 'source': self.source}


def _repo(value):
    return isinstance(value, str) and bool(_REPO.fullmatch(value)) and all(p not in ('.', '..') for p in value.split('/'))


def _path(value):
    if not isinstance(value, str) or not value or '\\' in value or value.startswith('/'):
        return False
    parts = PurePosixPath(value).parts
    return '..' not in parts and '.' not in parts and str(PurePosixPath(value)) == value


def _origin_repo(value):
    if value.startswith('git@github.com:'):
        path = value[len('git@github.com:'):]
    else:
        parsed = urlparse(value)
        if parsed.hostname not in ('github.com', 'git.chatgpt-team.site'):
            return None
        path = parsed.path.strip('/')
        if path.startswith('github.com/'):
            path = path[len('github.com/'):]
    return path[:-4] if path.endswith('.git') else path


def capture_git_baseline(root, repo):
    """Capture before implementation, from the actual authorized repository.

    No branch, file or remote is changed. The opaque receipt prevents a plain JSON
    model response from being accepted as baseline evidence. Persist as_dict only
    in controller-owned storage and restore through restore_controller_baseline.
    """
    if not _repo(repo):
        raise ValueError('A concrete owner/repository target is required')
    def git(*args):
        try:
            result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                    text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Cannot inspect repository baseline') from None
        return result
    origin = git('remote', 'get-url', 'origin')
    if origin.returncode or _origin_repo(origin.stdout.strip()) != repo:
        raise ValueError('Baseline worktree origin does not match the authorized repository')
    head = git('rev-parse', '--verify', 'HEAD')
    paths = set()
    if head.returncode == 0:
        base_sha = head.stdout.strip()
        tree = git('ls-tree', '-r', '--name-only', '-z', 'HEAD')
        if tree.returncode:
            raise ValueError('Cannot inspect baseline tree')
        paths.update(filter(None, tree.stdout.split('\x00')))
    else:
        # No HEAD is empty only if there are no refs. Other git failures fail closed.
        refs = git('show-ref', '--head')
        if refs.returncode not in (0, 1) or refs.stdout.strip():
            raise ValueError('Cannot establish an empty repository baseline')
        base_sha = None
    cached = git('ls-files', '--cached', '-z')
    if cached.returncode:
        raise ValueError('Cannot inspect baseline index')
    paths.update(filter(None, cached.stdout.split('\x00')))
    untracked = git('ls-files', '--others', '--exclude-standard', '-z')
    if untracked.returncode:
        raise ValueError('Cannot inspect baseline untracked paths')
    paths.update(filter(None, untracked.stdout.split('\x00')))
    record = RepositoryBaseline(repo, base_sha, tuple(sorted(paths)))
    object.__setattr__(record, '_receipt', _RECEIPT)
    return record


def restore_controller_baseline(record):
    """Restore ONLY from controller-owned state captured before task execution.

    This function is not a model tool and must never receive task payload fields,
    repository files or model JSON. State provenance is enforced by its caller.
    """
    if not isinstance(record, dict) or set(record) != {'repo', 'base_sha', 'existing_paths', 'source'}:
        raise ValueError('Invalid controller baseline record')
    if not _repo(record['repo']) or record['source'] != 'git':
        raise ValueError('Invalid controller baseline source')
    if record['base_sha'] is not None and not re.fullmatch(r'[0-9a-f]{40,64}', record['base_sha']):
        raise ValueError('Invalid baseline commit identity')
    paths = record['existing_paths']
    if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
        raise ValueError('Invalid baseline paths')
    value = RepositoryBaseline(record['repo'], record['base_sha'], tuple(paths), record['source'])
    object.__setattr__(value, '_receipt', _RECEIPT)
    return value


def workspace_fingerprint(files):
    """Hash exact path/content pairs; runtime stamps a check before test execution.

    Use a full product snapshot (including tests), not just changed files. Call
    again before publication so a later write invalidates previous evidence.
    """
    if not isinstance(files, dict) or not all(_path(p) and isinstance(v, (str, bytes)) for p, v in files.items()):
        raise ValueError('Workspace fingerprint requires canonical paths and contents')
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        data = content.encode('utf-8') if isinstance(content, str) else content
        label = name.encode('utf-8')
        digest.update(len(label).to_bytes(8, 'big') + label)
        digest.update(len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()


def _valid_checks(checks, digest):
    if not isinstance(checks, (list, tuple)) or not checks or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        return False
    for check in checks:
        if not isinstance(check, dict) or check.get('source') != 'runtime' or check.get('simulated') is not False:
            return False
        if check.get('passed') is not True or type(check.get('exit_code')) is not int or check['exit_code'] != 0:
            return False
        if type(check.get('tests_run')) is not int or check['tests_run'] < 1:
            return False
        if check.get('timed_out') is not False or check.get('truncated') is not False:
            return False
        if check.get('backend') == 'railway-sdk' and check.get('cleanup_ok') is not True:
            return False
        if not isinstance(check.get('execution_id'), str) or not check['execution_id']:
            return False
        if check.get('workspace_digest') != digest:
            return False
    return True


def decide_publication(*, repo, branch, default_branch='main', operator_policy=None,
                       baseline=None, checks=(), completed=False, head_sha=_UNSET,
                       changed_paths=(), workspace_digest=None):
    """Return an explicit allow/deny result; never performs Git or deployment writes.

    head_sha is the observed CURRENT target default-branch commit (None for an
    actually empty repository). It must still match the pre-work baseline.
    Work-branch publication remains authorized independently of main validation.
    """
    policy = operator_policy if isinstance(operator_policy, dict) else {}
    delivery_type = policy.get('delivery_type', 'existing_product_update')
    def decision(allowed, reason):
        return {'allowed': allowed, 'reason': reason, 'delivery_type': delivery_type,
                'repo': repo, 'branch': branch, 'production_deploy_allowed': False,
                'force_push_allowed': False}
    if not _repo(repo) or not isinstance(branch, str) or not isinstance(default_branch, str) or not default_branch:
        return decision(False, 'invalid_publication_target')
    if branch != default_branch:
        if _WORK_BRANCH.fullmatch(branch) and '..' not in branch and '//' not in branch and not branch.endswith('/'):
            return decision(True, 'authorized_work_branch_requires_report')
        return decision(False, 'target_is_not_an_authorized_work_branch')
    if policy.get('target_repo') != repo or policy.get('target_branch') != branch:
        return decision(False, 'main_target_requires_operator_binding')
    if delivery_type not in ('new_product', 'existing_product_update'):
        return decision(False, 'delivery_type_requires_operator_declaration')
    if completed is not True or not _valid_checks(checks, workspace_digest):
        return decision(False, 'main_requires_completed_delivery_and_real_current_checks')
    if delivery_type == 'existing_product_update':
        if (policy.get('allow_existing_product_main') is True
                and isinstance(policy.get('approval_id'), str) and policy['approval_id'].strip()
                and policy.get('approved_workspace_digest') == workspace_digest
                and head_sha is not _UNSET and policy.get('approved_base_sha', _UNSET) == head_sha):
            return decision(True, 'existing_product_main_explicitly_approved')
        return decision(False, 'existing_product_requires_branch_and_explicit_main_approval')
    if policy.get('allow_new_product_main') is not True:
        return decision(False, 'new_product_main_requires_operator_authorization')
    if not isinstance(baseline, RepositoryBaseline) or baseline._receipt is not _RECEIPT or baseline.repo != repo:
        return decision(False, 'new_product_requires_controller_baseline')
    if head_sha != baseline.base_sha:
        return decision(False, 'target_head_changed_since_baseline')
    if not isinstance(changed_paths, (list, tuple)) or not changed_paths or not all(_path(p) for p in changed_paths):
        return decision(False, 'new_product_requires_exact_changed_paths')
    if not baseline.existing_paths:
        return decision(True, 'validated_new_product_in_empty_repository')
    namespace = policy.get('new_project_namespace')
    if not _path(namespace):
        return decision(False, 'existing_repository_requires_authorized_new_project_namespace')
    prefix = namespace + '/'
    if any(path == namespace or path.startswith(prefix) for path in baseline.existing_paths):
        return decision(False, 'project_namespace_already_exists')
    if not all(path.startswith(prefix) for path in changed_paths):
        return decision(False, 'changed_paths_escape_authorized_new_project_namespace')
    return decision(True, 'validated_new_product_in_authorized_new_namespace')
