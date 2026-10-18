import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createPostgresBridge, loadTrustedSources, RPC_SCRIPT, STATE_ENVIRONMENT_ID } from './postgres_bridge.mjs';

const ENV = { RAILWAY_API_TOKEN: 'PRIVATE-RAILWAY-SECRET', RAILWAY_STATE_ENVIRONMENT_ID: STATE_ENVIRONMENT_ID,
  GITHUB_TOKEN: 'NEVER-TRANSMIT-GITHUB', GEMINI_API_KEY: 'NEVER-TRANSMIT-MODEL' };
const sources = { 'horizon_core/state.py': '# trusted-fixture', 'horizon_core/postgres_state.py': '# trusted-fixture', 'rpc.py': RPC_SCRIPT };
const result = (updates = {}) => ({ exitCode: 0, timedOut: false, truncated: false, stdout: '', stderr: '', ...updates });
function missing(name) { return Object.assign(new Error('SECRET-UPSTREAM-BODY'), { name }); }
function fixture() {
  const calls = [];
  const pool = new Map();
  const make = (id, extra = {}) => {
    const vm = { id, status: 'RUNNING', networkIsolation: 'PRIVATE', environmentId: STATE_ENVIRONMENT_ID,
      marker: null, writes: [], executions: [], rpcResult: { ok: true, result: { fixture: true } },
      files: {
        async read() { if (!vm.marker) throw missing('SandboxFileNotFoundError'); return vm.marker; },
        async write(path, data) { vm.writes.push({ path, data }); if (path.endsWith('horizon-postgres-owner.json')) vm.marker = data; },
      },
      async heartbeat() {},
      exec(command, options) {
        const record = { command, options, stdin: '' };
        vm.executions.push(record);
        if (!options.stdin) return Promise.resolve(vm.installResult || result());
        const promise = Promise.resolve(vm.execResult || result({ stdout: JSON.stringify(vm.rpcResult) }));
        promise.stdin = { async write(value) { record.stdin += value; }, async end() {} };
        return promise;
      }, ...extra };
    pool.set(id, vm);
    return vm;
  };
  const Sandbox = {
    async create(options) { calls.push({ method: 'create', options }); return make(`created-${pool.size + 1}`); },
    async list(options) { calls.push({ method: 'list', options }); return [...pool.values()]; },
    async connect(id, options) {
      calls.push({ method: 'connect', id, options });
      if (!pool.has(id)) throw missing('SandboxNotFoundError');
      return pool.get(id);
    },
  };
  return { calls, pool, make, Sandbox, handle: createPostgresBridge({ Sandbox, env: ENV, sources }) };
}

test('official source loader only reads fixed trusted runtime files', async () => {
  const loaded = await loadTrustedSources();
  assert.equal(loaded['rpc.py'], RPC_SCRIPT);
  assert.equal(typeof loaded['horizon_core/postgres_state.py'], 'string');
  assert.equal(typeof loaded['tests/test_postgres_state.py'], 'string');
  assert(!JSON.stringify(loaded).includes(ENV.RAILWAY_API_TOKEN));
});

test('new private controller gets server-resolved DB ref and no other secret', async () => {
  const f = fixture();
  const response = await f.handle({ operation: 'ensure' });
  assert.equal(response.ok, true);
  assert.equal(response.created, true);
  const create = f.calls.find(call => call.method === 'create').options;
  assert.equal(create.networkIsolation, 'PRIVATE');
  assert.equal(create.environmentId, STATE_ENVIRONMENT_ID);
  assert.equal(create.domains, undefined);
  assert.deepEqual(create.env, { HORIZON_DATABASE_URL: '${{Postgres.DATABASE_URL}}' });
  assert(!JSON.stringify(create.env).includes('NEVER-TRANSMIT'));
  const vm = f.pool.get(response.sandbox_id);
  assert(vm.executions[0].command.includes('env -u HORIZON_DATABASE_URL'));
  assert(!vm.executions[0].command.includes('DATABASE_URL=postgres'));
  const reused = await f.handle({ operation: 'ensure', sandbox_id: response.sandbox_id });
  assert.equal(reused.reused, true);
  assert.equal(f.calls.filter(call => call.method === 'create').length, 1);
});

test('isolated code sandbox cannot become a state controller', async () => {
  const f = fixture();
  const arbitrary = f.make('code-tests', { networkIsolation: 'ISOLATED' });
  const response = await f.handle({ operation: 'ensure', sandbox_id: arbitrary.id });
  assert.equal(response.ok, false);
  assert.equal(response.error.code, 'SANDBOX_NOT_MANAGED');
  assert.equal(arbitrary.writes.length, 0);
});

test('foreign or corrupted ownership marker never authorizes reuse', async () => {
  for (const marker of ['{bad', JSON.stringify({ owner: 'horizon-agent-core' })]) {
    const f = fixture();
    const vm = f.make('foreign', { marker });
    const response = await f.handle({ operation: 'ensure', sandbox_id: vm.id });
    assert.equal(response.ok, false);
    assert.equal(vm.executions.length, 0);
  }
});

test('RPC transmits JSON data on stdin, never interpolates arguments in commands', async () => {
  const f = fixture();
  const response = await f.handle({ operation: 'rpc', method: 'remember',
    args: ['fixture', 'backend', 'x', '$(cat /proc/self/environ); pretend-code'], kwargs: {} });
  assert.equal(response.ok, true);
  const vm = f.pool.get(response.sandbox_id);
  const rpc = vm.executions.find(call => call.options.stdin);
  assert.equal(rpc.command, '/tmp/horizon-postgres-controller/venv/bin/python -I /tmp/horizon-postgres-controller/rpc.py');
  assert.equal(rpc.options.timeoutSec, 120);
  assert.equal(JSON.parse(rpc.stdin).args[3], '$(cat /proc/self/environ); pretend-code');
  assert(!rpc.command.includes('pretend-code'));
  assert.equal(rpc.options.env, undefined);
  assert(!rpc.stdin.includes(ENV.RAILWAY_API_TOKEN));
});

test('arbitrary SQL, files, commands and schemas rejected before provisioning', async () => {
  for (const request of [{ operation: 'exec' }, { operation: 'rpc', method: 'execute', args: ['DROP TABLE tasks'], kwargs: {} },
    { operation: 'rpc', method: 'get', args: [], kwargs: {}, schema: 'public' }]) {
    const f = fixture();
    const response = await f.handle(request);
    assert.equal(response.ok, false);
    assert.equal(f.calls.length, 0);
  }
});

test('state exceptions are sanitized and preserve lease/value error identity', async () => {
  const f = fixture();
  const ensured = await f.handle({ operation: 'ensure' });
  const vm = f.pool.get(ensured.sandbox_id);
  for (const type of ['LeaseError', 'ValueError', 'PostgresStateError', 'SECRET-CLASS']) {
    vm.rpcResult = { ok: false, error: { type, code: 'SECRET-PASSWORD', message: 'SECRET-DSN' } };
    const response = await f.handle({ operation: 'rpc', sandbox_id: vm.id, method: 'get', args: ['fixture'], kwargs: {} });
    assert.equal(response.ok, false);
    assert(!JSON.stringify(response).includes('SECRET'));
    if (type !== 'SECRET-CLASS') assert.equal(response.error.type, type);
  }
});

test('truncated, failed or timed out execution never returns successful state', async () => {
  for (const updates of [{ timedOut: true }, { truncated: true }, { exitCode: null }, { exitCode: 1 }, { stdout: '{SECRET-BROKEN' }]) {
    const f = fixture();
    const ensured = await f.handle({ operation: 'ensure' });
    const vm = f.pool.get(ensured.sandbox_id);
    vm.execResult = result({ stdout: '{"ok":true,"result":"pretended"}', ...updates });
    const response = await f.handle({ operation: 'health', sandbox_id: vm.id });
    assert.equal(response.ok, false);
    assert(!JSON.stringify(response).includes('SECRET'));
  }
});

test('untrusted environment cannot redirect creation into customer environment', async () => {
  const f = fixture();
  const handler = createPostgresBridge({ Sandbox: f.Sandbox, sources,
    env: { ...ENV, RAILWAY_STATE_ENVIRONMENT_ID: 'customer-environment' } });
  assert.equal((await handler({ operation: 'health' })).ok, false);
  assert.equal(f.calls.length, 0);
});

test('real PostgreSQL suite is a separate fixed trusted operation', async () => {
  const f = fixture();
  const response = await f.handle({ operation: 'test_pg' });
  assert.equal(response.ok, true);
  const vm = f.pool.get(response.sandbox_id);
  const request = JSON.parse(vm.executions.find(item => item.options.stdin).stdin);
  assert.equal(request.operation, 'test_pg');
  assert.equal(request.command, undefined);
  assert(RPC_SCRIPT.includes("os.environ['POSTGRES_TEST_DSN'] = dsn"));
  assert(RPC_SCRIPT.includes("pattern='test_postgres_state.py'"));
});

test('embedded trusted Python RPC runs data-only dispatch and redacts DSN/password', () => {
  const prelude = `import sys,types\ncore=types.ModuleType('horizon_core')\npg=types.ModuleType('horizon_core.postgres_state')\nclass FakeState:\n def __init__(self,dsn,schema): pass\n def close(self): pass\n def remember(self,*args,**kwargs): return args[-1]\npg.PostgresState=FakeState\nsys.modules['horizon_core']=core\nsys.modules['horizon_core.postgres_state']=pg\n`;
  const dsn = 'postgresql://user:private%22password@postgres.railway.internal/database';
  const execution = spawnSync('python3', ['-c', prelude + RPC_SCRIPT], {
    input: JSON.stringify({ operation: 'rpc', method: 'remember', args: ['p', 'a', 'k',
      { url: dsn, password: 'private"password', nested: ['synthetic'] }], kwargs: {} }),
    encoding: 'utf8', env: { ...process.env, HORIZON_DATABASE_URL: dsn },
  });
  assert.equal(execution.status, 0, execution.stderr);
  const response = JSON.parse(execution.stdout);
  assert.equal(response.ok, true);
  assert.equal(response.result.url, '[REDACTED]');
  assert.equal(response.result.password, '[REDACTED]');
  assert.deepEqual(response.result.nested, ['synthetic']);
  const invalid = spawnSync('python3', ['-c', prelude + RPC_SCRIPT], {
    input: JSON.stringify({ operation: 'rpc', method: '__getattribute__', args: [], kwargs: {} }),
    encoding: 'utf8', env: { ...process.env, HORIZON_DATABASE_URL: dsn },
  });
  assert.equal(JSON.parse(invalid.stdout).error.type, 'ValueError');
  assert(!invalid.stdout.includes(dsn));
});
