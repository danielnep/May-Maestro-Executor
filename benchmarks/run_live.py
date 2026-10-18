"""Real API-driven isolated repair benchmark. Reference is never repair context.

Generated code only executes in Docker. Offline controls execute trusted fixtures.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from horizon_core.gateway import ModelGateway
from horizon_core.registry import load_agent
from horizon_core.state import State
from horizon_core.tools import Tools
from horizon_core.worker import Worker

BENCHMARK = Path(__file__).resolve().parent / 'reservations_v1'
TASK_ID = 'reservations-v1-backend'
PROJECT = 'benchmark-reservations-v1-isolated'
BRANCH = 'feat/benchmark-reservations'


def bounded_process(command, *, timeout, env=None):
    """Drain without unbounded buffering; reject output floods instead of truncating."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise RuntimeError('benchmark subprocess timeout')
                for key, _ in selector.select(timeout=1):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > 64000:
                        raise RuntimeError('benchmark subprocess output exceeded safety bound')
        code = process.wait(timeout=5)
        return {'exit_code': code, 'passed': code == 0,
                'output': output.decode('utf-8', errors='replace')}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def evaluate_private(workspace, seed):
    """Held-out source is absent throughout reasoning and only mounted for scoring."""
    name = 'horizon-benchmark-' + uuid.uuid4().hex
    safe_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME')}
    with tempfile.TemporaryDirectory(prefix='horizon-private-evaluation-') as directory:
        evaluation = Path(directory)
        evaluation.chmod(0o755)
        candidate = evaluation / 'candidate'
        candidate.mkdir(mode=0o755)
        shutil.copyfile(workspace / 'engine.py', candidate / 'engine.py')
        (candidate / 'engine.py').chmod(0o644)
        private = evaluation / 'private'
        private.mkdir(mode=0o755)
        shutil.copyfile(BENCHMARK / 'heldout/evaluator.py', private / 'evaluator.py')
        (private / 'evaluator.py').chmod(0o644)
        command = ['docker', 'run', '--name', name, '--rm', '--network=none', '--read-only',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128',
                   '--memory=512m', '--cpus=2', '--user=65534:65534',
                   '--tmpfs=/tmp:rw,noexec,nosuid,size=128m',
                   '--mount', f'type=bind,src={candidate},dst=/project,readonly',
                   '--mount', f'type=bind,src={private},dst=/evaluation,readonly',
                   '--workdir=/project', '--env=PYTHONDONTWRITEBYTECODE=1',
                   '--env=ENGINE_TARGET=/project/engine.py', '--env=BENCHMARK_SEED=' + str(seed),
                   'python:3.12-slim', 'python', '/evaluation/evaluator.py']
        try:
            result = bounded_process(command, timeout=240, env=safe_env)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10, env=safe_env)
        lines = result['output'].strip().splitlines()
        if lines:
            try:
                result['summary'] = json.loads(lines[-1])
            except (json.JSONDecodeError, TypeError):
                pass
        # A zero process exit alone cannot count as private success without the oracle summary.
        summary = result.get('summary', {})
        result['passed'] = bool(result['passed'] and summary.get('suite') == 'reservations-v1-private'
                                and summary.get('tests') == 5 and summary.get('passed') == 5
                                and summary.get('failures') == 0 and summary.get('errors') == 0)
        return result


def create_workspace(root):
    root.chmod(0o755)
    (root / 'tests').mkdir(mode=0o755)
    for source, target in ((BENCHMARK / 'starter/engine.py', root / 'engine.py'),
                           (BENCHMARK / 'starter/test_public.py', root / 'tests/test_public.py'),
                           (BENCHMARK / 'contract.md', root / 'contract.md')):
        shutil.copyfile(source, target)
        target.chmod(0o644)
    for command in (['git', 'init', str(root)],
                    ['git', '-C', str(root), 'switch', '-c', BRANCH],
                    ['git', '-C', str(root), 'add', 'engine.py', 'tests/test_public.py', 'contract.md'],
                    ['git', '-C', str(root), '-c', 'user.name=Horizon Benchmark', '-c',
                     'user.email=benchmark@example.invalid', 'commit', '-m', 'Isolated broken benchmark fixture']):
        subprocess.run(command, check=True, capture_output=True, timeout=20)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Repair an isolated synthetic SQLite engine through actual model APIs')
    parser.add_argument('--output', default='runtime')
    parser.add_argument('--session-seconds', type=int, default=1500)
    parser.add_argument('--seed', type=int, default=730517)
    parser.add_argument('--offline-controls', action='store_true', help='Trusted fixture controls only; no repair/model calls')
    args = parser.parse_args(argv)
    if args.session_seconds < 1:
        parser.error('session-seconds must be positive')
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / 'benchmark-report.json'
    report = {'benchmark': 'reservations-v1', 'mode': 'offline_controls' if args.offline_controls else 'live_api_repair',
              'status': 'started', 'passed': False, 'model_calls': 0,
              'reference_in_repair_context': False, 'real_projects_used': False}
    started = time.monotonic()
    state = gateway = None
    code = 2
    try:
        if args.offline_controls:
            command = [sys.executable, '-m', 'unittest', 'discover', '-s', str(REPOSITORY / 'tests'), '-p', 'test_benchmark.py', '-v']
            result = bounded_process(command, timeout=180, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            report['controls'] = result
            report['status'] = 'controls_passed' if result['passed'] else 'controls_failed'
            report['passed'] = result['passed']
            return 0 if result['passed'] else 1
        if not shutil.which('docker'):
            report.update(status='unavailable', reason='Docker is required before generated code can execute')
            return 2
        state = State(destination / 'benchmark-state.sqlite3')
        checkpoint = destination / 'benchmark-checkpoint.sqlite3'
        persist = lambda: state.backup(checkpoint)
        with tempfile.TemporaryDirectory(prefix='horizon-repair-workspace-') as directory:
            root = Path(directory)
            create_workspace(root)
            tests = Tools(root, load_agent('qa-engineer'))
            report['public_baseline'] = tests.execute('run_check', {'name': 'python-unittest'})
            if report['public_baseline'].get('exit_code') not in (0, 1):
                report.update(status='unavailable', reason='Docker/public test infrastructure could not execute')
                return 2
            objective = ('Repair engine.py against contract.md. Read the contract and source, diagnose causal bugs, '
                         'edit only engine.py, and run python-unittest public checks. Preserve SQLite durability, '
                         'tenant boundaries, payload idempotency, concurrent stock invariants, atomic outbox and '
                         'deadline transitions including callback-triggered process death. Tests are in tests/. '
                         'You may reason and call tools as much as required; no token/call/step rationing is imposed. '
                         'Public baseline failures: ' + report['public_baseline'].get('output', ''))
            if state.get(TASK_ID) is None:
                state.enqueue(TASK_ID, 'backend-engineer', PROJECT, objective,
                              payload={'branch': BRANCH, 'allowed_paths': ['engine.py']})
            persist()
            gateway = ModelGateway(state=state)
            worker = Worker(state, gateway, root, persist=persist, slice_seconds=args.session_seconds)
            existing = state.get(TASK_ID)
            if existing['status'] == 'succeeded':
                worker.restore_workspace(existing)
                result = existing
            else:
                result = worker.run_one() or state.get(TASK_ID)
                worker.restore_workspace(result)
            report['task'] = {'task_id': TASK_ID, 'agent': result['agent'], 'status': result['status'],
                              'result': result.get('result'), 'error': result.get('error'),
                              'checkpoint_step': (result.get('checkpoint') or {}).get('step', 0)}
            repaired = root / 'engine.py'
            output_engine = destination / 'benchmark-engine.py'
            shutil.copyfile(repaired, output_engine)
            report['candidate_sha256'] = hashlib.sha256(repaired.read_bytes()).hexdigest()
            changes = subprocess.run(['git', '-C', str(root), 'diff', '--name-only', 'HEAD'],
                                     capture_output=True, text=True, timeout=10)
            report['changed_files'] = changes.stdout.splitlines()
            if changes.returncode or any(path != 'engine.py' for path in report['changed_files']):
                report.update(status='invalid_patch', reason='Candidate changed files outside authorized engine.py')
                return 1
            if result['status'] in ('queued', 'running'):
                report.update(status='yielded_or_retry_pending', reason='Durable checkpoint retained; no delivery pass claimed')
                return 2
            if result['status'] != 'succeeded':
                report.update(status='repair_failed')
                return 1
            report['public_final'] = tests.execute('run_check', {'name': 'python-unittest'})
            # No further model call is made after the private evaluation mount exists.
            private_runs = [evaluate_private(root, seed) for seed in (args.seed, args.seed + 200214)]
            report['private'] = {'passed': all(run['passed'] for run in private_runs), 'runs': private_runs}
            report['passed'] = bool(report['public_final'].get('passed') and report['private']['passed'])
            report['status'] = 'passed' if report['passed'] else 'failed_acceptance'
            code = 0 if report['passed'] else 1
        return code
    except Exception as error:
        # Provider exceptions and arbitrary process output are not promoted to host diagnostics.
        report.update(status='runner_error', error_kind=type(error).__name__)
        return 2
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if gateway is not None:
            report['model_calls'] = gateway.calls
        if state is not None:
            try:
                recovered = state.recall(PROJECT, '__workspace__').get('engine.py')
                if recovered is not None:
                    (destination / 'benchmark-engine.py').write_text(recovered['content'], encoding='utf-8')
                state.backup(destination / 'benchmark-checkpoint.sqlite3')
            except Exception as error:
                report['checkpoint_error_kind'] = type(error).__name__
                report.update(status='checkpoint_error', passed=False)
            finally:
                state.close()
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'passed': report['passed'],
                          'model_calls': report['model_calls'], 'report': str(report_path)}))
        if report['status'] == 'checkpoint_error':
            return 2


if __name__ == '__main__':
    raise SystemExit(main())
