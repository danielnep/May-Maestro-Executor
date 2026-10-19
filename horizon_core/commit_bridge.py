"""Chat -> versioned request -> private queue -> agents -> versioned result.

The controller runs from trusted main; task JSON is DATA, never code/policy.
New apps use apps/<project>/; existing products require exact authorized paths.
Existing-product changes remain on a work branch. No persistent PAT or CLI.
GitHub's short-lived workflow token publishes results and authorized branches.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time

from .gateway import ModelGateway
from .github_state import GitHubState, GitHubStateError
from .projects import prepare_project
from .publication_policy import restore_controller_baseline
from .publish import publish
from .railway import RailwayBroker
from .registry import AGENT_IDS
from .tools import Tools
from .worker import Worker

REPOSITORY = 'danielnep/May-Maestro-Executor'
OWNER = 'danielnep'
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9-]{0,69}')


def _validate_path_pattern(path, repo):
    if (not isinstance(path, str) or not path or len(path) > 300 or path.startswith('/')
            or '\\' in path or any(ord(char) < 32 for char in path)
            or str(PurePosixPath(path)) != path
            or any(part in ('.', '..') or part.startswith('.') for part in path.split('/'))):
        raise ValueError('Allowed paths must be canonical nonhidden relative paths')
    parts = path.split('/')
    # A concrete root prevents globs from reaching hidden/control-plane roots.
    if any(char in parts[0] for char in '*?['):
        raise ValueError('An authorized path requires a concrete root')
    forbidden = {'secrets', 'credentials', 'node_modules', 'runtime', '__pycache__'}
    if any(part.lower() in forbidden or re.search(
            r'(^|[_.-])(tokens?|secrets?|credentials?|passwords?|private.?key)([_.-]|$)', part, re.I)
            for part in parts):
        raise ValueError('Secrets and runtime paths cannot enter task scope')
    control = {'agents', 'horizon_core', 'sandbox', 'android_tools', 'maestro_exec', 'executors',
               'supervisor.py', 'GEMINI.md', 'AGENTS.md'}
    if repo == REPOSITORY:
        control |= {'android', 'chat_bridge', 'tasks', 'tests', 'benchmarks', 'requirements-postgres.txt'}
    if parts[0] in control or parts[-1] == 'local.properties' or Path(path).suffix.lower() in (
            '.pem', '.key', '.sqlite', '.sqlite3', '.db', '.keystore', '.jks', '.env'):
        raise ValueError('Controller and credential paths cannot enter task scope')
    return path


def validate_request(document, request_id, repo=REPOSITORY):
    if not isinstance(request_id, str) or not IDENTIFIER.fullmatch(request_id):
        raise ValueError('Invalid request identifier')
    fields = {'owner', 'repository', 'task_id', 'agent', 'project', 'objective', 'payload',
              'initiative', 'initiative_reason', 'delivery_type'}
    if not isinstance(document, dict) or set(document) - fields:
        raise ValueError('Request must contain only declared task data')
    target_repo = document.get('repository')
    if (repo != REPOSITORY or document.get('owner') != OWNER or not isinstance(target_repo, str)
            or not re.fullmatch(r'danielnep/[A-Za-z0-9_.-]+', target_repo)
            or target_repo.split('/')[1] in ('.', '..')):
        raise ValueError('Request owner/repository is not authorized')
    if document.get('task_id') != request_id:
        raise ValueError('Filename and request task_id must match')
    agent = document.get('agent', 'may-maestro')
    if agent not in AGENT_IDS:
        raise ValueError('Unknown specialist')
    project = document.get('project')
    if not isinstance(project, str) or not IDENTIFIER.fullmatch(project):
        raise ValueError('A valid application/project identifier is required')
    delivery_type = document.get('delivery_type', 'new_product')
    if delivery_type not in ('new_product', 'existing_product'):
        raise ValueError('delivery_type must be new_product or existing_product')
    objective = document.get('objective')
    if not isinstance(objective, str) or not 5 <= len(objective.strip()) <= 20000 or '\x00' in objective:
        raise ValueError('A bounded objective is required')
    payload = document.get('payload', {})
    if not isinstance(payload, dict) or set(payload) - {'branch', 'allowed_paths'}:
        raise ValueError('Task payload cannot override controller policy')
    namespace = 'apps/' + project if delivery_type == 'new_product' else None
    branch = 'agent/chat-' + request_id
    if 'branch' in payload and payload['branch'] != branch:
        raise ValueError('Work branch is assigned by the controller')
    if delivery_type == 'existing_product' and 'allowed_paths' not in payload:
        raise ValueError('Existing products require explicit allowed_paths')
    paths = payload.get('allowed_paths', [namespace + '/**'] if namespace else None)
    if (not isinstance(paths, list) or not paths or len(paths) > 100
            or not all(isinstance(path, str) for path in paths) or len(set(paths)) != len(paths)):
        raise ValueError('A bounded explicit allowlist is required')
    for path in paths:
        _validate_path_pattern(path, target_repo)
        if namespace and not path.startswith(namespace + '/'):
            raise ValueError('Allowed paths escape the authorized application namespace')
    initiative = document.get('initiative', False)
    reason = document.get('initiative_reason', '')
    if not isinstance(initiative, bool) or not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError('Invalid initiative declaration')
    if initiative and len(reason.strip()) < 10:
        raise ValueError('An initiative requires a concrete justification')
    task = {'task_id': 'chat-' + request_id, 'agent': agent,
            'project': 'chat:' + request_id, 'objective': objective,
            'payload': {'repository': target_repo, 'branch': branch, 'allowed_paths': paths,
                        **({'application_namespace': namespace} if namespace else {}), 'initiative': initiative,
                        **({'initiative_reason': reason} if initiative else {})}}
    canonical = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return {'id': request_id, 'task': task, 'request_digest': hashlib.sha256(canonical.encode()).hexdigest(),
            'namespace': namespace, 'branch': branch, 'delivery_type': delivery_type}


class ScopedState:
    """Claims are filtered atomically by the backend, not claim-then-requeue."""
    def __init__(self, state, project):
        self.state, self.project = state, project

    def claim(self, worker, now=None, lease_seconds=300):
        return self.state.claim(worker, now=now, lease_seconds=lease_seconds, project=self.project)

    def tasks(self, project=None):
        if project is not None and project != self.project:
            raise ValueError('Task listing outside execution scope')
        return self.state.tasks(project=self.project)

    def __getattr__(self, name):
        return getattr(self.state, name)


def import_request(state, validated):
    task = validated['task']
    previous = state.recall(task['project'], '__chat_controller__').get('request_digest')
    if previous is not None and previous != validated['request_digest']:
        raise ValueError('Immutable request changed; use a new task_id')
    # enqueue is itself idempotent and rejects changed specifications.
    state.enqueue(**task)
    state.remember(task['project'], '__chat_controller__', 'request_digest', validated['request_digest'])
    return task


def _reference(api, branch):
    try:
        return api._request('GET', '/git/ref/heads/' + branch)
    except GitHubStateError as error:
        if str(error) == 'GitHub HTTP 404':
            return None
        raise


def _checkout_existing_work_branch(root, branch):
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    base = ['git', '-c', 'credential.helper=', '-c', 'core.hooksPath=' + os.devnull, '-C', str(root)]
    exists = subprocess.run(base + ['show-ref', '--verify', '--quiet', 'refs/remotes/origin/' + branch],
                            env=env, capture_output=True, timeout=20)
    if exists.returncode == 0:
        switched = subprocess.run(base + ['checkout', '-B', branch, 'refs/remotes/origin/' + branch],
                                  env=env, capture_output=True, timeout=20)
        if switched.returncode:
            raise ValueError('Cannot restore published work branch')
    elif exists.returncode != 1:
        raise ValueError('Cannot inspect published work branch')


def summarize(request_id, tasks, publication=None):
    statuses = [task['status'] for task in tasks]
    status = 'pending' if any(value in ('queued', 'running') for value in statuses) else (
        'needs_attention' if any(value in ('failed', 'blocked') for value in statuses) else 'reported_complete')
    reports = []
    for task in tasks:
        result = task.get('result') or {}
        checks = []
        for check in result.get('verification', []):
            if not isinstance(check, dict):
                continue
            checks.append({key: check[key] for key in ('check', 'passed', 'exit_code', 'tests_run',
                'timed_out', 'truncated', 'cleanup_ok', 'backend', 'workspace_digest') if key in check})
        model = result.get('model') or {}
        model = model if isinstance(model, dict) else {}
        provider = model.get('provider')
        provider = provider if provider in ('gemini', 'deepseek') else None
        identifier = model.get('model')
        identifier = identifier if (isinstance(identifier, str) and
                                   re.fullmatch(r'[A-Za-z0-9_.-]{1,90}', identifier)) else None
        reports.append({'task_id': task['task_id'], 'agent': task['agent'], 'status': task['status'],
                        'attempts': task.get('attempts', 0), 'summary': str(result.get('summary', ''))[:12000],
                        'verification': checks,
                        'model_used': {'provider': provider, 'model': identifier} if provider else None})
    # A model's finish is not a verified delivery: failed or absent QA evidence
    # must remain actionable regardless of all specialists saying 'succeeded'.
    qa_reports = [row for row in reports if row['agent'] == 'qa-engineer']
    bad_checks = [dict(task_id=row['task_id'], check=check.get('check', 'unknown'),
                       exit_code=check.get('exit_code'), tests_run=check.get('tests_run', 0))
                  for row in reports for check in row['verification']
                  if check.get('passed') is not True or check.get('tests_run', 0) == 0]
    missing_qa_checks = [row['task_id'] for row in qa_reports if not row['verification']]
    quality_ok = not bad_checks and not missing_qa_checks
    if status == 'reported_complete' and not quality_ok:
        status = 'needs_attention'
    delivery = publication or {'published': False, 'reason': 'No publication attempted'}
    if status != 'pending' and delivery.get('awaiting_host_publication') is True:
        status = 'awaiting_host_publication'
    return {'request_id': request_id, 'status': status, 'tasks': reports, 'publication': delivery,
            'quality': {'approved': quality_ok, 'failed_checks': bad_checks,
                        'qa_missing_evidence': missing_qa_checks},
            'source': 'https://github.com/' + REPOSITORY + '/blob/main/tasks/chat/' + request_id + '.json',
            'delivery_mode': 'read_result_in_chat',
            'note': 'Relato concluído não comprova testes aprovados. Sem envio espontâneo ao chat ou deploy automático.'}


def publish_result(api, request_id, report, branch='main'):
    if not IDENTIFIER.fullmatch(request_id) or branch != 'main':
        raise ValueError('Result writer is restricted to tasks/results/<id>.json on main')
    path = 'tasks/results/' + request_id + '.json'
    endpoint = '/contents/' + path
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    sha = None
    try:
        previous = api._request('GET', endpoint + '?ref=main')
        sha = previous['sha']
        if base64.b64decode(previous['content']) == raw:
            return {'written': False, 'path': path}
    except GitHubStateError as error:
        if str(error) != 'GitHub HTTP 404':
            raise
    body = {'message': 'Horizon: resultado ' + request_id + ' [skip ci]', 'branch': branch,
            'content': base64.b64encode(raw).decode()}
    if sha is not None:
        body['sha'] = sha
    api._request('PUT', endpoint, body)
    return {'written': True, 'path': path}


def process_request(state, validated, *, api, session_seconds=1200, gateway_factory=ModelGateway,
                    prepare=prepare_project, worker_factory=Worker, publisher=publish):
    task = import_request(state, validated)
    scoped = ScopedState(state, task['project'])
    previous = state.recall(task['project'], '__chat_controller__')
    tasks = scoped.tasks()
    if previous.get('publication', {}).get('published') is True and tasks and all(row['status'] in ('succeeded', 'failed', 'blocked') for row in tasks):
        return summarize(validated['id'], tasks, previous.get('publication'))
    # This authorization originates from the controller, NEVER request JSON.
    target_repo = task['payload']['repository']
    existing_product = validated.get('delivery_type', 'new_product') == 'existing_product'
    policy = ({'delivery_type': 'existing_product_update', 'target_repo': target_repo,
               'target_branch': validated['branch'], 'allowed_paths': task['payload']['allowed_paths']}
              if existing_product else {'delivery_type': 'new_product', 'target_repo': target_repo,
               'target_branch': 'main', 'allow_new_product_main': True,
               'new_project_namespace': validated['namespace']})
    target_api = api if target_repo == REPOSITORY else GitHubState(target_repo)
    with prepare(target_repo, validated['branch'], operator_policy=policy, api=target_api) as project:
        default_branch = getattr(project, 'default_branch', 'main')
        if not existing_product:
            policy['target_branch'] = default_branch
        stored = state.recall(task['project'], '__operator__')
        baseline = restore_controller_baseline(stored['baseline']) if stored.get('baseline') else project.baseline
        if not stored.get('baseline'):
            state.remember(task['project'], '__operator__', 'baseline', baseline.as_dict())
            state.remember(task['project'], '__operator__', 'publication_policy', policy)
        _checkout_existing_work_branch(project.root, validated['branch'])
        sandbox = RailwayBroker(state) if os.getenv('RAILWAY_API_TOKEN') and os.getenv('RAILWAY_ENVIRONMENT_ID') else None
        worker = worker_factory(scoped, gateway_factory(state=state), project.root, sandbox_client=sandbox)
        started = time.monotonic()
        while time.monotonic() - started < session_seconds:
            worker.slice_seconds = min(900, max(1, session_seconds - (time.monotonic() - started)))
            if worker.run_one() is None:
                break
        # Main publication is conditional on current real checks and immutable
        # baseline. Work branch remains the recoverable fallback, never force-push.
        publication_target = validated['branch'] if existing_product else default_branch
        try:
            publication = publisher(state, project.root, target_repo, api=target_api, operator_policy=policy,
                                    baseline=baseline, target_branch=publication_target, project=task['project'])
            if not existing_product and not publication.get('published'):
                main_reason = publication.get('reason')
                publication_target = validated['branch']
                publication = publisher(state, project.root, target_repo, api=target_api, operator_policy={},
                                        target_branch=validated['branch'], project=task['project'])
                publication['main_pending_reason'] = main_reason
        except GitHubStateError as error:
            if target_repo == REPOSITORY or str(error) not in ('GitHub HTTP 403', 'GitHub HTTP 404'):
                raise
            files = []
            validator = Tools(project.root, {'tools': ['write_file']}, task['payload']['allowed_paths'], validated['branch'])
            for name, mutation in state.recall(task['project'], '__workspace__').items():
                _validate_path_pattern(name, target_repo)
                validator.path(name, write=True)
                if (mutation.get('branch') != validated['branch']
                        or not isinstance(mutation.get('content'), str)):
                    raise ValueError('Recovered host patch is outside authorized scope')
                files.append({'path': name, 'content': mutation['content'],
                              'expected_sha256': mutation.get('expected_sha256')})
            publication = {'published': False, 'awaiting_host_publication': bool(files),
                'reason': 'Workflow token cannot publish to this external repository',
                'target_repo': target_repo, 'target_branch': publication_target,
                'base_sha': baseline.base_sha, 'patch_files': files,
                'host_action': 'May must verify the remote base and publish via the connected GitHub tool; do not claim publication before confirmation.'}
        # Publishing source is a separate preview surface, never a main merge.
        # Switching Pages requires a Pages+Administration-write token.
        if publication.get('published') is True and publication.get('branch') == validated['branch']:
            from .pages_preview import sync_pages_source
            preview = sync_pages_source(target_repo, validated['branch'])
            publication['pages_preview'] = preview
            if preview['status'] not in ('verified', 'already_current', 'pages_not_enabled'):
                print(json.dumps({'request_id': validated['id'], 'pages_preview': preview}))
        state.remember(task['project'], '__chat_controller__', 'publication', publication)
        return summarize(validated['id'], scoped.tasks(), publication)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Versioned ChatGPT requests -> private specialist queue')
    parser.add_argument('--root', default='.')
    parser.add_argument('--session-seconds', type=int, default=1800)
    args = parser.parse_args(argv)
    if args.session_seconds < 1:
        parser.error('session-seconds must be positive')
    if os.getenv('GITHUB_REPOSITORY', REPOSITORY) != REPOSITORY:
        raise ValueError('This controller is bound to its own repository')
    from .handoff import ready_request_ids, write_marker
    backend = os.getenv('HORIZON_STATE_BACKEND', 'github-sqlite')
    if backend == 'github-sqlite':
        from .github_sqlite_state import GitHubSQLiteState
        state = GitHubSQLiteState()
        print(json.dumps({'state_backend': 'github-sqlite',
                          'legacy_sqlite_snapshot_restored': state.restored_legacy}))
    elif backend == 'embedded-postgres':
        from .embedded_state import HorizonEmbeddedState
        state = HorizonEmbeddedState()
    elif backend == 'railway-postgres':
        from .railway_postgres import RailwayPostgresState
        state = RailwayPostgresState()
    else:
        raise RuntimeError('Unsupported state backend; refusing implicit fallback')
    recovered = state.recover_provider_outages()
    if any(recovered.values()):
        print(json.dumps({'provider_recovery': recovered}))
    api = GitHubState(REPOSITORY)
    processed = rejected = 0
    started = time.monotonic()
    unprocessed = []
    try:
        directory = Path(args.root).resolve() / 'tasks' / 'chat'
        if not directory.exists():
            print(json.dumps({'processed': 0, 'rejected': 0}))
            return 0
        paths = sorted(directory.glob('*.json'))
        for index, path in enumerate(paths):
            if not IDENTIFIER.fullmatch(path.stem):
                rejected += 1
                continue
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 128000:
                    raise ValueError('Request file is not a bounded regular JSON file')
                validated = validate_request(json.loads(path.read_text(encoding='utf-8')), path.stem)
                remaining = args.session_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    # The next run must import requests we have not reached yet.
                    unprocessed = [item.stem for item in paths[index:]
                                   if IDENTIFIER.fullmatch(item.stem)]
                    break
                report = process_request(state, validated, api=api, session_seconds=remaining)
            except Exception as error:
                rejected += 1
                report = {'request_id': path.stem, 'status': 'blocked',
                          'error': {'kind': type(error).__name__},
                          'note': 'Erro sanitizado do controlador. O pedido não concede novas permissões.'}
            publish_result(api, path.stem, report)
            # Event-driven owner alert follows durable result publication, not
            # the five-minute recovery schedule or the ChatGPT task cadence.
            from .notify import notify
            try:
                alert = notify(report, os.getenv("GITHUB_TOKEN"))
            except RuntimeError:
                # Delivery state must survive an unavailable notification channel.
                alert = "notification_unavailable"
            print(json.dumps({"request_id": path.stem, "alert": alert}))
            processed += 1
        # Durable state has already been updated. Dispatch once, in a separate
        # workflow step, only when a task is immediately claimable or an incoming
        # request was not reached. Delayed retries use the scheduled watchdog.
        waiting = ready_request_ids(state, [path.stem for path in paths
                                             if IDENTIFIER.fullmatch(path.stem)])
        continuation = sorted(set(waiting + unprocessed))
        write_marker(continuation)
        print(json.dumps({'processed': processed, 'rejected': rejected,
                          'ready_for_continuation': len(continuation)}))
        return 1 if rejected else 0
    finally:
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
