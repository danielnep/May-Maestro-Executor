"""Publish recovered workspace content to its authorized work branch, never merge."""
import argparse
import json
import subprocess
from .github_state import GitHubState
from .state import State
from .tools import Tools
from .worker import Worker


def publish(state, root, repo, api=None):
    transport = api or GitHubState(repo)
    tasks = [state._decode(row) for row in state.db.execute('SELECT * FROM tasks ORDER BY seq')]
    active = [task for task in tasks if state.recall(task['project'], '__workspace__')]
    if not active:
        return {'published': False, 'reason': 'No generated workspace files'}
    branches = {task['payload'].get('branch') for task in active}
    if len(branches) != 1:
        raise ValueError('A publication requires exactly one authorized work branch')
    branch = branches.pop()
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
    metadata = transport._request('GET', '')
    if branch == metadata.get('default_branch'):
        raise ValueError('Cannot publish to repository default branch')
    base = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10)
    if base.returncode:
        raise ValueError('Cannot read worktree base')
    base_sha = base.stdout.strip()
    observed = transport._request('GET', '/git/ref/heads/' + branch)
    if observed['object']['sha'] != base_sha:
        raise ValueError('Work branch changed remotely; refresh before publishing')
    commit = transport._request('GET', '/git/commits/' + base_sha)
    tree = transport._request('POST', '/git/trees', {'base_tree': commit['tree']['sha'], 'tree': list(elements.values())})
    if tree['sha'] == commit['tree']['sha']:
        return {'published': False, 'reason': 'Generated content already published'}
    created = transport._request('POST', '/git/commits', {'message': 'Horizon: checkpoint specialist workspace',
                                 'tree': tree['sha'], 'parents': [base_sha]})
    transport._request('PATCH', '/git/refs/heads/' + branch, {'sha': created['sha'], 'force': False})
    return {'published': True, 'branch': branch, 'commit': created['sha'], 'files': sorted(elements)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--db', default='runtime/state.sqlite3')
    parser.add_argument('--root', default='.')
    args = parser.parse_args()
    state = State(args.db)
    try:
        print(json.dumps(publish(state, args.root, args.repo)))
    finally:
        state.close()


if __name__ == '__main__':
    main()
