"""Publish recovered workspace content to its authorized work branch, never merge."""
import argparse
import json
import math
import os
import subprocess
from .github_state import GitHubState, GitHubStateError
from .state import State
from .tools import Tools
from .worker import Worker
from .publication_policy import decide_publication, restore_controller_baseline, workspace_fingerprint


def _git(root, *args, binary=False):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=not binary, timeout=30)
    if result.returncode:
        raise ValueError('Cannot inspect publication tree')
    return result.stdout


def candidate_snapshot(root, base_sha, elements):
    """Return final principal contents: observed base plus authorized mutations.

    Read exactly the files eligible for the test snapshot, without extracting a
    Git archive or invoking project code. Changes elsewhere on a feature branch
    cannot enter this candidate or its fingerprint.
    """
    files = {}
    validator = Tools(root, {'tools': ['read_file']})
    if base_sha is not None:
        tree = _git(root, 'ls-tree', '-r', '-z', base_sha, binary=True)
        for record in filter(None, tree.split(b'\x00')):
            entry, name = record.split(b'\t', 1)
            mode, kind, object_id = entry.split(b' ')
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                continue
            path = name.decode('utf-8')
            try:
                validator.path(path)
            except ValueError:
                continue
            files[path] = _git(root, 'cat-file', 'blob', object_id.decode('ascii'), binary=True)
    for path, element in elements.items():
        validator.path(path)
        files[path] = element['content'].encode('utf-8')
    return files


def _controller_records(state, active, operator_policy, baseline):
    records = [state.recall(project, '__operator__') for project in sorted({task['project'] for task in active})]
    policies = [record['publication_policy'] for record in records if record.get('publication_policy')]
    if operator_policy is None and policies:
        if any(value != policies[0] for value in policies):
            raise ValueError('Projects disagree on operator publication policy')
        operator_policy = policies[0]
    if baseline is None:
        values = [record['baseline'] for record in records if record.get('baseline')]
        if values:
            if any(value != values[0] for value in values):
                raise ValueError('Projects disagree on controller baseline')
            baseline = restore_controller_baseline(values[0])
    return operator_policy, baseline


def publish(state, root, repo, api=None, operator_policy=None, baseline=None, target_branch=None, project=None):
    transport = api or GitHubState(repo)
    tasks = state.tasks(project=project) if project is not None else state.tasks()
    active = []
    for task in tasks:
        operator = state.recall(task['project'], '__operator__')
        target = (operator.get('publication_policy') or {}).get('target_repo')
        if target is not None and target != repo:
            continue
        if state.recall(task['project'], '__workspace__'):
            active.append(task)
    if not active:
        return {'published': False, 'reason': 'No generated workspace files'}
    branches = {task['payload'].get('branch') for task in active}
    if len(branches) != 1:
        raise ValueError('A publication requires exactly one authorized work branch')
    work_branch = branches.pop()
    metadata = transport._request('GET', '')
    default = metadata.get('default_branch')
    if work_branch == default:
        raise ValueError('Cannot implement directly on repository default branch')
    operator_policy, baseline = _controller_records(state, active, operator_policy, baseline)
    policy = operator_policy or {}
    branch = target_branch or (policy.get('target_branch') if policy.get('allow_new_product_main') is True
                              or policy.get('allow_existing_product_main') is True else None) or work_branch
    if branch != default and branch != work_branch:
        raise ValueError('Publication target differs from the authorized work branch')
    # Source remains the separately authorized feature branch even for principal publication.
    elements = {}
    worker = Worker(state, None, root)
    for task in active:
        worker.restore_workspace(task)
        tools = worker.workspace_tools(task)
        for name in state.recall(task['project'], '__workspace__'):
            path = tools.path(name, write=True)
            content = path.read_text(encoding='utf-8')
            if name in elements and elements[name]['content'] != content:
                raise ValueError('Projects disagree on workspace content')
            elements[name] = {'path': name, 'mode': '100644', 'type': 'blob', 'content': content}
    if branch == default:
        return _publish_main(state, root, repo, transport, metadata, active, elements, policy, baseline)
    decision = decide_publication(repo=repo, branch=branch, default_branch=default)
    if not decision['allowed']:
        raise ValueError('Publication denied: ' + decision['reason'])
    base_sha = _git(root, 'rev-parse', 'HEAD').strip()
    observed = _reference(transport, branch)
    if observed is None:
        default_ref = _reference(transport, default)
        if default_ref is None or default_ref['object']['sha'] != base_sha:
            raise ValueError('New work branch requires the observed default-branch base')
        transport._request('POST', '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': base_sha})
    elif observed['object']['sha'] != base_sha:
        raise ValueError('Work branch changed remotely; refresh before publishing')
    commit = transport._request('GET', '/git/commits/' + base_sha)
    tree = transport._request('POST', '/git/trees', {'base_tree': commit['tree']['sha'], 'tree': list(elements.values())})
    if tree['sha'] == commit['tree']['sha']:
        return {'published': False, 'reason': 'Generated content already published'}
    created = transport._request('POST', '/git/commits', {'message': 'Horizon: checkpoint specialist workspace',
                                 'tree': tree['sha'], 'parents': [base_sha]})
    transport._request('PATCH', '/git/refs/heads/' + branch, {'sha': created['sha'], 'force': False})
    return {'published': True, 'branch': branch, 'commit': created['sha'], 'files': sorted(elements)}


def _reference(transport, branch):
    try:
        return transport._request('GET', '/git/ref/heads/' + branch)
    except GitHubStateError as exc:
        if str(exc) in ('GitHub HTTP 404', 'GitHub HTTP 409'):
            return None
        raise


def _main_head(transport, branch):
    value = _reference(transport, branch)
    return value['object']['sha'] if value is not None else None


def current_verification(state, tasks, workspace_digest):
    """Latest execution per check/configuration for exactly the final snapshot.

    Failed attempts on an older snapshot remain in task history but do not poison
    a corrected delivery forever. A later failure on the current snapshot always
    supersedes an earlier success. The controller's execution timestamp orders
    checks across specialists; legacy records use task completion time.
    """
    latest = {}
    for task in tasks:
        completions = [event for event in state.events(task['task_id']) if event['kind'] == 'succeeded']
        completion_time = max((event['created_at'] for event in completions), default=0)
        for index, check in enumerate((task.get('result') or {}).get('verification', [])):
            if not isinstance(check, dict) or check.get('workspace_digest') != workspace_digest:
                continue
            name = check.get('check') or check.get('name') or '__unnamed__'
            configuration = {key: check.get(key) for key in ('configuration', 'project_dir', 'directory', 'args') if key in check}
            key = json.dumps([name, configuration], sort_keys=True, separators=(',', ':'))
            stamp = check.get('executed_at', check.get('started_at', completion_time))
            if type(stamp) not in (int, float) or not math.isfinite(stamp):
                stamp = completion_time
            order = (stamp, task.get('seq', 0), index)
            if key not in latest or order >= latest[key][0]:
                latest[key] = (order, check)
    return [entry[1] for _, entry in sorted(latest.items())]


def _publish_main(state, root, repo, transport, metadata, active, elements, policy, baseline):
    branch = metadata['default_branch']
    base_sha = _main_head(transport, branch)
    files = candidate_snapshot(root, base_sha, elements)
    digest = workspace_fingerprint(files)
    checks = current_verification(state, active, digest)
    decision = decide_publication(repo=repo, branch=branch, default_branch=branch,
        operator_policy=policy, baseline=baseline, checks=checks,
        completed=all(task['status'] == 'succeeded' for task in active), head_sha=base_sha,
        changed_paths=sorted(elements), workspace_digest=digest)
    if not decision['allowed']:
        return {'published': False, 'branch': branch, 'reason': decision['reason'], 'policy': decision}
    body = {'tree': list(elements.values())}
    parent_tree = None
    if base_sha is not None:
        parent = transport._request('GET', '/git/commits/' + base_sha)
        parent_tree = parent['tree']['sha']
        body['base_tree'] = parent_tree
    tree = transport._request('POST', '/git/trees', body)
    if parent_tree is not None and tree['sha'] == parent_tree:
        return {'published': False, 'reason': 'Generated content already published', 'branch': branch}
    created = transport._request('POST', '/git/commits', {'message': 'Horizon: validated product delivery',
        'tree': tree['sha'], 'parents': [base_sha] if base_sha is not None else []})
    if _main_head(transport, branch) != base_sha:
        raise ValueError('Principal changed remotely; generated commit was not published')
    if base_sha is None:
        transport._request('POST', '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': created['sha']})
    else:
        transport._request('PATCH', '/git/refs/heads/' + branch, {'sha': created['sha'], 'force': False})
    return {'published': True, 'branch': branch, 'commit': created['sha'], 'files': sorted(elements),
            'policy': decision, 'workspace_digest': digest, 'production_deployed': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--db', default='runtime/state.sqlite3')
    parser.add_argument('--root', default='.')
    args = parser.parse_args()
    backend = os.getenv('HORIZON_STATE_BACKEND', 'sqlite')
    if backend == 'railway-postgres':
        from .railway_postgres import RailwayPostgresState
        state = RailwayPostgresState()
    elif backend == 'postgres':
        from .postgres_state import PostgresState
        state = PostgresState(os.environ.get('HORIZON_DATABASE_URL', ''))
    elif backend == 'sqlite':
        state = State(args.db)
    else:
        raise ValueError('Unknown state backend')
    try:
        print(json.dumps(publish(state, args.root, args.repo)))
    finally:
        state.close()


if __name__ == '__main__':
    main()
