"""Entry point for Actions today and the same worker on a VPS later."""
import argparse
import json
import os
import time
from pathlib import Path
from .gateway import ModelGateway, DEFAULT_DEEPSEEK_MODEL
from .github_state import GitHubState
from .registry import AGENT_IDS, load_agent
from .state import State
from .worker import Worker
from .railway import RailwayBroker


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
    parser.add_argument('--max-tasks', type=int)
    parser.add_argument('--session-seconds', type=int, default=2400)
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
        deepseek = {'api_key_present': bool(os.getenv('DEEPSEEK_API_KEY')),
                    'model': os.getenv('DEEPSEEK_MODEL') or DEFAULT_DEEPSEEK_MODEL}
        railway = {'api_token_present': bool(os.getenv('RAILWAY_API_TOKEN')),
                   'environment_present': bool(os.getenv('RAILWAY_ENVIRONMENT_ID'))}
        print(json.dumps({'configuration': ready, 'deepseek': deepseek, 'railway': railway,
                          'billing_verified': False, 'requests_sent': 0}))
        return 0 if all(ready.values()) or deepseek['api_key_present'] else 2
    if args.max_tasks is not None and args.max_tasks < 1:
        parser.error('max-tasks must be positive when explicitly set')
    if args.session_seconds < 1:
        parser.error('session-seconds must be positive')
    backend = os.getenv('HORIZON_STATE_BACKEND', 'sqlite')
    remote = GitHubState(args.remote_repo) if args.remote_repo and backend == 'sqlite' else None
    if remote:
        remote.pull(args.db)
    if backend == 'postgres':
        from .postgres_state import PostgresState
        state = PostgresState(os.environ.get('HORIZON_DATABASE_URL', ''))
    elif backend == 'railway-postgres':
        from .railway_postgres import RailwayPostgresState
        state = RailwayPostgresState()
    elif backend == 'sqlite':
        state = State(args.db)
    else:
        raise ValueError('Unknown state backend')
    persist = (lambda: remote.push(args.db)) if remote else (lambda: None)
    try:
        import_tasks(state, json.loads(Path(args.tasks).read_text(encoding='utf-8')))
        persist()
        sandbox = RailwayBroker(state, persist) if os.getenv('RAILWAY_API_TOKEN') and os.getenv('RAILWAY_ENVIRONMENT_ID') else None
        worker = Worker(state, ModelGateway(state=state), args.root, persist, sandbox_client=sandbox)
        results = []
        started = time.monotonic()
        while args.max_tasks is None or len(results) < args.max_tasks:
            remaining = args.session_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            worker.slice_seconds = min(1500, remaining)
            result = worker.run_one()
            if result is None:
                break
            results.append({'task_id': result['task_id'], 'agent': result['agent'],
                            'status': result['status'], 'result': result.get('result'), 'error': result.get('error')})
        report = {'executed': results, 'provider_calls': worker.gateway.calls,
                  'states': state.list_tasks_snapshot(),
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
