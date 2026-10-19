"""State RPC through a dedicated Railway PRIVATE sandbox, without a public DB.

Only Railway API credentials reach the trusted Node controller. The PostgreSQL
URL is resolved by Railway into the state sandbox; it never enters Actions,
model context or the ISOLATED code-test sandbox. No arbitrary SQL/exec API.
Each method is an independent PostgreSQL transaction. Transport failures are
ambiguous and are NEVER automatically retried, particularly claim/complete.
"""
import json
import os
from pathlib import Path
import re
import subprocess

from .state import LeaseError

STATE_ENVIRONMENT_ID = '11b303c5-e811-4fb2-9e77-514ad050697e'
METHODS = frozenset(('enqueue', 'claim', 'renew_lease', 'yield_task', 'checkpoint',
    'complete', 'fail', 'remember', 'recall', 'get', 'events', 'tasks', 'list_tasks_snapshot'))


class RailwayPostgresError(RuntimeError):
    """Sanitized RPC error; raw provider output and DSN are never included."""


class RailwayPostgresState:
    def __init__(self, *, environ=None, runner=None, bridge=None, node='node'):
        self.environ = dict(os.environ if environ is None else environ)
        self.runner = runner or subprocess.run
        self.bridge = Path(bridge or Path(__file__).resolve().parent.parent / 'sandbox' / 'postgres_bridge.mjs')
        self.node = node
        self.schema = self.environ.get('HORIZON_STATE_SCHEMA', 'horizon_agents')
        if not isinstance(self.schema, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', self.schema) or self.schema.startswith('pg_') or self.schema in ('public', 'information_schema'):
            raise ValueError('Use a dedicated application schema')
        self.sandbox_id = self.environ.get('RAILWAY_STATE_SANDBOX_ID') or None
        self.closed = False

    def _request(self, operation='rpc', method=None, args=(), kwargs=None):
        if self.closed:
            raise RailwayPostgresError('State client is closed')
        token = self.environ.get('RAILWAY_API_TOKEN')
        environment = self.environ.get('RAILWAY_STATE_ENVIRONMENT_ID', STATE_ENVIRONMENT_ID)
        if not token or environment != STATE_ENVIRONMENT_ID:
            raise RailwayPostgresError('Dedicated state environment and Railway API credential are required')
        request = {'operation': operation, 'schema': self.schema}
        if self.sandbox_id:
            request['sandbox_id'] = self.sandbox_id
        if method is not None:
            if method not in METHODS:
                raise ValueError('State method is not exposed by RPC')
            request.update(method=method, args=list(args), kwargs=kwargs or {})
        body = json.dumps(request, ensure_ascii=False, allow_nan=False)
        if len(body.encode()) > 25000000:
            raise RailwayPostgresError('State RPC request exceeds transport limit')
        # Never inherit LLM/GitHub keys, DB URLs, NODE_OPTIONS or alternate SDK
        # endpoints into the controller subprocess.
        env = {key: value for key, value in self.environ.items()
               if key in ('PATH', 'HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')}
        env.update(RAILWAY_API_TOKEN=token, RAILWAY_STATE_ENVIRONMENT_ID=environment)
        try:
            response = self.runner([self.node, str(self.bridge)], input=body, text=True,
                                   capture_output=True, timeout=420, env=env)
            if len(response.stdout.encode()) > 35000000:
                raise RailwayPostgresError('State RPC response exceeds transport limit')
            decoded = json.loads(response.stdout)
        except RailwayPostgresError:
            raise
        except Exception:
            raise RailwayPostgresError('Private state transport failed; outcome may be unknown; do not retry blindly') from None
        if not isinstance(decoded, dict) or not isinstance(decoded.get('ok'), bool):
            raise RailwayPostgresError('Private state returned an invalid response')
        if not decoded['ok']:
            kind = decoded.get('error', {}).get('type') if isinstance(decoded.get('error'), dict) else None
            if kind == 'LeaseError':
                raise LeaseError('Private state rejected an expired or unowned lease')
            if kind == 'ValueError':
                raise ValueError('Private state rejected invalid method arguments')
            raise RailwayPostgresError('Private state operation failed; inspect sanitized infrastructure status')
        if response.returncode != 0:
            raise RailwayPostgresError('Private state controller did not exit successfully')
        sandbox_id = decoded.get('sandbox_id')
        if not isinstance(sandbox_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', sandbox_id):
            raise RailwayPostgresError('Private state returned an invalid sandbox identifier')
        self.sandbox_id = sandbox_id
        return decoded.get('result')

    def bootstrap_existing(self):
        return self._request(operation='bootstrap_existing')

    def health(self):
        return self._request(operation='health')

    def close(self):
        # Closing a client never destroys the VM or persistent database.
        self.closed = True

    def enqueue(self, *args, **kwargs):
        return self._request(method='enqueue', args=args, kwargs=kwargs)

    def claim(self, *args, **kwargs):
        return self._request(method='claim', args=args, kwargs=kwargs)

    def renew_lease(self, *args, **kwargs):
        return self._request(method='renew_lease', args=args, kwargs=kwargs)

    def yield_task(self, *args, **kwargs):
        return self._request(method='yield_task', args=args, kwargs=kwargs)

    def checkpoint(self, *args, **kwargs):
        return self._request(method='checkpoint', args=args, kwargs=kwargs)

    def complete(self, *args, **kwargs):
        return self._request(method='complete', args=args, kwargs=kwargs)

    def fail(self, *args, **kwargs):
        return self._request(method='fail', args=args, kwargs=kwargs)

    def remember(self, *args, **kwargs):
        return self._request(method='remember', args=args, kwargs=kwargs)

    def recall(self, *args, **kwargs):
        return self._request(method='recall', args=args, kwargs=kwargs)

    def get(self, *args, **kwargs):
        return self._request(method='get', args=args, kwargs=kwargs)

    def events(self, *args, **kwargs):
        return self._request(method='events', args=args, kwargs=kwargs)

    def tasks(self, *args, **kwargs):
        return self._request(method='tasks', args=args, kwargs=kwargs)

    def list_tasks_snapshot(self):
        return self._request(method='list_tasks_snapshot')
