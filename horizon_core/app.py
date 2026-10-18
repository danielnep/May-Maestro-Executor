"""Entry point for Actions today and the same worker on a VPS later."""
import argparse
import json
import os
from pathlib import Path
from .gateway import GeminiGateway
from .github_state import GitHubState
from .registry import AGENT_IDS, load_agent
from .state import State
from .worker import Worker


def import_tasks(state, document):
    if not isinstance(document, list) or len(document) > 100:
        raise ValueError('Task inbox must be a list of at most 100 authorized tasks')
    for item in document:
        load_agent(item['agent'])
        if not isinstance(item.get('payload', {}), dict):
            raise ValueError('Task payload must be an object')
        paths = item.get('payload', {}).get('allowed_paths', [])
        if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
            raise ValueError('allowed_paths must be a list of path patterns')
        state.enqueue(**item)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Horizon: API-driven specialists; no agent CLI')
    parser.add_argument('--db', default='runtime/state.sqlite3')
    parser.add_argument('--root', default='.')
    parser.add_argument('--tasks', default='tasks/inbox.json')
    parser.add_argument('--remote-repo')
    parser.add_argument('--max-tasks', type=int, default=3)
    parser.add_argument('--report', default='runtime/report.json')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps([{'id': a, 'scope': load_agent(a)['scope']} for a in AGENT_IDS], ensure_ascii=False))
        return 0
    if args.preflight:
        ready = {name: bool(os.getenv(name)) for name in ('GEMINI_API_KEY', 'GEMINI_MODEL')}
        ready['free_tier_attested'] = os.getenv('GEMINI_FREE_TIER_CONFIRMED', '').lower() == 'true'
        print(json.dumps({'configuration': ready, 'billing_verified': False, 'requests_sent': 0}))
        return 0 if all(ready.values()) else 2
    if not 1 <= args.max_tasks <= 20:
        parser.error('max-tasks must be between 1 and 20')
    remote = GitHubState(args.remote_repo) if args.remote_repo else None
    if remote:
        remote.pull(args.db)
    state = State(args.db)
    persist = (lambda: remote.push(args.db)) if remote else (lambda: None)
    try:
        import_tasks(state, json.loads(Path(args.tasks).read_text(encoding='utf-8')))
        persist()
        worker = Worker(state, GeminiGateway(), args.root, persist)
        results = []
        for _ in range(args.max_tasks):
            if worker.gateway.calls >= worker.gateway.max_calls:
                break
            result = worker.run_one()
            if result is None:
                break
            results.append({'task_id': result['task_id'], 'agent': result['agent'],
                            'status': result['status'], 'result': result.get('result'), 'error': result.get('error')})
        report = {'executed': results, 'provider_calls': worker.gateway.calls,
                  'states': [dict(row) for row in state.db.execute('SELECT task_id,agent,status,attempts FROM tasks ORDER BY seq')],
                  'note': 'succeeded means the specialist reported completion. Read verification entries for actual checks; this is not human approval.'}
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({'tasks_processed': len(results), 'provider_calls': worker.gateway.calls,
                          'statuses': [r['status'] for r in results]}))
        return 1 if any(r['status'] in ('failed', 'blocked') for r in results) else 0
    finally:
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
