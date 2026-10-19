"""SQLite transactional worker with durable PostgreSQL snapshots on Horizon Site.

The agent queue remains fully compatible with State. Its snapshots live in the
existing Horizon Site PGlite database on a Railway persistent volume; nothing
is written to the Michelle database. GitHub Actions single-writer concurrency
and compare-and-swap revisions prevent stale overwrite.
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .state import State

URL = 'https://horizonsolucoes.com/api/internal/horizon-agent-state'
AUDIENCE = 'horizon-maestro-state'
MAX_BYTES = 20 * 1024 * 1024


class HorizonEmbeddedState(State):
    def __init__(self, *, url=URL, requester=urlopen):
        if url != URL:
            raise ValueError('Agent state endpoint cannot be changed by requests')
        self.requester = requester
        self.token = self._oidc_token()
        self.remote_version = 0
        self.workdir = tempfile.TemporaryDirectory(prefix='horizon-agent-state-')
        path = Path(self.workdir.name) / 'state.sqlite3'
        try:
            value = self._request('GET')
            version = value.get('revision')
            if not isinstance(version, int) or version < 0:
                raise RuntimeError('Agent state revision invalid')
            self.remote_version = version
            snapshot = value.get('snapshot')
            if version and not isinstance(snapshot, str):
                raise RuntimeError('Agent state snapshot missing')
            if snapshot is not None:
                raw = base64.b64decode(snapshot, validate=True)
                if len(raw) > MAX_BYTES or not raw.startswith(b'SQLite format 3\x00') or hashlib.sha256(raw).hexdigest() != value.get('checksum'):
                    raise RuntimeError('Agent state snapshot integrity failed')
                path.write_bytes(raw)
            super().__init__(path)
        except BaseException:
            self.workdir.cleanup()
            raise

    def _oidc_token(self):
        endpoint = os.getenv('ACTIONS_ID_TOKEN_REQUEST_URL')
        key = os.getenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
        if not endpoint or not key or not endpoint.startswith('https://'):
            raise RuntimeError('GitHub OIDC is required for Horizon state authentication')
        separator = '&' if '?' in endpoint else '?'
        req = Request(endpoint + separator + urlencode({'audience': AUDIENCE}),
                      headers={'Authorization': 'Bearer ' + key})
        with self.requester(req, timeout=20) as response:
            value = json.load(response)
        token = value.get('value')
        if not isinstance(token, str) or token.count('.') != 2:
            raise RuntimeError('Invalid GitHub OIDC token')
        return token

    def _request(self, method, body=None):
        raw = None if body is None else json.dumps(body, separators=(',', ':')).encode('utf8')
        req = Request(URL, data=raw, method=method, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'User-Agent': 'Horizon-Private-Agent-State'})
        try:
            with self.requester(req, timeout=90) as response:
                output = json.load(response)
            if not isinstance(output, dict) or 'revision' not in output:
                raise ValueError('Malformed state bridge response')
            return output
        except Exception:
            raise RuntimeError('Horizon state connection failed; execution suspended safely') from None

    def _persist(self):
        path = Path(self.workdir.name) / 'snapshot.sqlite3'
        with sqlite3.connect(path) as dest:
            self.db.backup(dest)
        raw = path.read_bytes()
        path.unlink(missing_ok=True)
        if len(raw) > MAX_BYTES:
            raise RuntimeError('Horizon agent checkpoint exceeded size limit')
        digest = hashlib.sha256(raw).hexdigest()
        result = self._request('POST', {'revision': self.remote_version,
            'snapshot': base64.b64encode(raw).decode('ascii'), 'checksum': digest})
        if result.get('checksum') != digest or result.get('revision') != self.remote_version + 1:
            raise RuntimeError('Horizon checkpoint was not acknowledged')
        self.remote_version += 1

    @contextmanager
    def _tx(self):
        with super()._tx():
            yield
        self._persist()

    def close(self):
        try:
            super().close()
        finally:
            self.workdir.cleanup()
