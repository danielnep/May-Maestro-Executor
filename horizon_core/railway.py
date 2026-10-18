"""Credential-scoped Python bridge to the official Railway SDK (not Railway CLI)."""
import json
import os
from pathlib import Path
import subprocess
import uuid


class RailwayError(RuntimeError):
    pass


class RailwayBroker:
    def __init__(self, state, persist=None, environ=None, runner=None):
        self.state = state
        self.persist = persist or (lambda: None)
        self.environ = os.environ if environ is None else environ
        self.runner = runner or subprocess.run
        self.bridge = Path(__file__).resolve().parents[1] / 'sandbox' / 'bridge.mjs'

    def request(self, payload):
        env = {key: self.environ[key] for key in ('PATH', 'HOME', 'NODE_EXTRA_CA_CERTS',
               'RAILWAY_API_TOKEN', 'RAILWAY_ENVIRONMENT_ID') if self.environ.get(key)}
        if not env.get('RAILWAY_API_TOKEN') or not env.get('RAILWAY_ENVIRONMENT_ID'):
            raise RailwayError('Railway token or environment configuration missing')
        try:
            result = self.runner(['node', str(self.bridge)], input=json.dumps(payload),
                                 text=True, capture_output=True, timeout=480, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise RailwayError('Railway SDK bridge unavailable or timed out') from None
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RailwayError('Railway SDK returned invalid protocol response') from None
        if not isinstance(value, dict):
            raise RailwayError('Railway SDK returned invalid protocol response')
        if result.returncode or value.get('ok') is not True or value.get('error'):
            # Never relay SDK error bodies or stderr, which may contain request URLs.
            error = value.get('error', {})
            known_types = {'BridgeError', 'RailwayAuthError', 'RailwayConnectionError',
                           'RailwayGraphQLError', 'SandboxNotFoundError', 'SandboxFileNotFoundError',
                           'SandboxFilesError', 'SandboxFailedError', 'SandboxTimeoutError',
                           'ExecInterruptedError', 'ExecControlUnsupportedError', 'ExecNotStartedError'}
            kind = error.get('type') if isinstance(error, dict) else None
            raise RailwayError('Railway SDK operation failed: ' + (kind if kind in known_types else 'operation_error'))
        return value

    def ensure(self):
        stored = self.state.recall('__runtime__', 'railway').get('sandbox', {})
        environment = self.environ.get('RAILWAY_ENVIRONMENT_ID')
        preferred = stored.get('id') if stored.get('environment_id') == environment else None
        result = self.request({'operation': 'ensure', **({'sandbox_id': preferred} if preferred else {})})
        if not isinstance(result.get('sandbox_id'), str) or not result['sandbox_id']:
            raise RailwayError('Railway did not return a sandbox identity')
        if result.get('status') != 'RUNNING':
            raise RailwayError('Railway sandbox is not RUNNING')
        self.state.remember('__runtime__', 'railway', 'sandbox',
                            {'id': result['sandbox_id'], 'environment_id': environment})
        self.persist()
        return result

    def run_check(self, root, tools):
        identity = self.ensure()
        files = []
        size = 0
        for source in Path(root).rglob('*'):
            if not source.is_file():
                continue
            name = source.relative_to(root).as_posix()
            try:
                tools.path(name)
            except ValueError:
                continue
            size += source.stat().st_size
            if size > 20_000_000 or len(files) >= 2000:
                raise RailwayError('Sandbox upload exceeds technical snapshot size')
            try:
                content = source.read_text(encoding='utf-8')
            except UnicodeError:
                raise RailwayError('Initial sandbox checker accepts text project files only') from None
            files.append({'path': name, 'content': content})
        result = self.request({'operation': 'run-check', 'sandbox_id': identity['sandbox_id'],
                               'files': files, 'task_id': uuid.uuid4().hex, 'check': 'python-unittest'})
        exit_code = result.get('exit_code', result.get('exitCode'))
        return {'check': 'python-unittest', 'backend': 'railway-sdk',
                'sandbox_id': identity['sandbox_id'], 'reused': identity.get('reused', False),
                'exit_code': exit_code,
                'passed': result.get('passed') is True and result.get('cleanup_ok') is True and isinstance(result.get('tests_run'), int) and result['tests_run'] > 0 and exit_code == 0 and not result.get('timedOut', result.get('timed_out', False)) and not result.get('truncated', False),
                'tests_run': result.get('tests_run', 0), 'cleanup_ok': result.get('cleanup_ok') is True,
                'timed_out': result.get('timedOut', result.get('timed_out', False)),
                'truncated': result.get('truncated', False),
                'output': (result.get('stdout', '') + result.get('stderr', ''))[-64000:]}
