import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable, Writable } from 'node:stream';
import { createBridge, main, safePath } from './bridge.mjs';

const ENV = { RAILWAY_API_TOKEN: 'fixture-secret-key', RAILWAY_ENVIRONMENT_ID: 'environment-fixture',
  GEMINI_API_KEY: 'private-gemini-fixture', GITHUB_TOKEN: 'private-github-fixture' };
const OWNER = JSON.stringify({ owner: 'horizon-agent-core', environment_id: ENV.RAILWAY_ENVIRONMENT_ID, version: 1 });
const UUID = '12345678-1234-1234-1234-123456789abc';
const outcome = (updates = {}) => ({ exitCode: 0, timedOut: false, truncated: false,
  stdout: '', stderr: 'Ran 1 test in 0.001s\n\nOK\n', ...updates });
function namedError(name) { return Object.assign(new Error('SECRET UPSTREAM BODY fixture-secret-key'), { name }); }
function fixture() {
  const calls = [];
  const sandboxes = new Map();
  let number = 0;
  const make = (id, marker = OWNER, extra = {}) => {
    const writes = [];
    const sandbox = { id, status: 'RUNNING', environmentId: ENV.RAILWAY_ENVIRONMENT_ID,
      networkIsolation: 'ISOLATED', marker, writes, commands: [], heartbeats: 0,
      files: {
        async read(path) {
          assert.equal(path, '/tmp/horizon-runtime-owner.json');
          if (sandbox.marker === null || sandbox.marker === undefined) throw namedError('SandboxFileNotFoundError');
          return sandbox.marker;
        },
        async write(path, content) {
          writes.push({ path, content });
          if (path === '/tmp/horizon-runtime-owner.json') sandbox.marker = content;
        },
        async mkdir(path) { writes.push({ mkdir: path }); },
      },
      async heartbeat() { this.heartbeats++; },
      async exec(command, options) {
        this.commands.push({ command, options });
        return this.result || outcome();
      },
      ...extra };
    sandboxes.set(id, sandbox);
    return sandbox;
  };
  const Sandbox = {
    async list(options) { calls.push({ method: 'list', options }); return [...sandboxes.values()]; },
    async connect(id, options) {
      calls.push({ method: 'connect', id, options });
      if (!sandboxes.has(id)) throw namedError('SandboxNotFoundError');
      return sandboxes.get(id);
    },
    async create(options) {
      calls.push({ method: 'create', options });
      return make(`created-${++number}`, null);
    },
  };
  return { calls, Sandbox, sandboxes, make, handle: createBridge({ Sandbox, env: ENV, uuid: () => UUID }) };
}
const request = (id, updates = {}) => ({ operation: 'run-check', sandbox_id: id, task_id: 'task-fixture',
  check: 'python-unittest', files: [{ path: 'tests/test_fixture.py', content: 'import unittest\n' }], ...updates });

test('preflight returns IDs and statuses only, without credentials or commands', async () => {
  const f = fixture();
  f.make('unmanaged', null);
  const result = await f.handle({ operation: 'preflight' });
  assert.deepEqual(result, { ok: true, sandboxes: [{ id: 'unmanaged', status: 'RUNNING' }] });
  assert.equal(f.calls.length, 1);
  assert(!JSON.stringify(result).includes(ENV.RAILWAY_API_TOKEN));
});

test('ensure creates isolated VM with empty env and writes ownership marker', async () => {
  const f = fixture();
  const first = await f.handle({ operation: 'ensure' });
  assert.equal(first.created, true);
  const create = f.calls.find(call => call.method === 'create');
  assert.deepEqual(create.options.env, {});
  assert.equal(create.options.networkIsolation, 'ISOLATED');
  assert.equal(create.options.idleTimeoutMinutes, undefined);
  assert.equal(create.options.verbose, false);
  assert.equal(create.options.token, ENV.RAILWAY_API_TOKEN);
  assert(!JSON.stringify(create.options.env).includes('private'));
  const vm = f.sandboxes.get(first.sandbox_id);
  assert.deepEqual(JSON.parse(vm.marker), JSON.parse(OWNER));
  const second = await f.handle({ operation: 'ensure', sandbox_id: first.sandbox_id });
  assert.equal(second.reused, true);
  assert.equal(second.sandbox_id, first.sandbox_id);
  assert.equal(f.calls.filter(call => call.method === 'create').length, 1);
});

test('ensure discovers owned VM and skips random running, private and wrong owner VMs', async () => {
  const f = fixture();
  f.make('random', null);
  f.make('private', OWNER, { networkIsolation: 'PRIVATE' });
  f.make('wrong-owner', JSON.stringify({ owner: 'another-controller' }));
  f.make('ours');
  const result = await f.handle({ operation: 'ensure' });
  assert.equal(result.sandbox_id, 'ours');
  assert.equal(result.reused, true);
  assert.equal(f.calls.filter(call => call.method === 'create').length, 0);
});

test('missing preferred ID can create, but known unowned ID cannot be adopted', async () => {
  const f = fixture();
  assert.equal((await f.handle({ operation: 'ensure', sandbox_id: 'missing' })).created, true);
  f.make('someone-else', null);
  const result = await f.handle({ operation: 'ensure', sandbox_id: 'someone-else' });
  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'SANDBOX_NOT_MANAGED');
});

test('marker malformed or environment mismatch never allows reuse', async () => {
  for (const marker of ['{broken', JSON.stringify({ owner: 'horizon-agent-core', environment_id: 'other', version: 1 })]) {
    const f = fixture();
    f.make('candidate', marker);
    assert.equal((await f.handle({ operation: 'ensure' })).created, true);
  }
  const f = fixture();
  f.make('bad-env', OWNER, { environmentId: 'other' });
  assert.equal((await f.handle({ operation: 'ensure' })).error.code, 'ENVIRONMENT_MISMATCH');
});

test('saved expired sandbox is replaced without deleting it and null saved ID is absent', async () => {
  const f = fixture();
  f.make('expired', OWNER, { status: 'DESTROYED' });
  const replacement = await f.handle({ operation: 'ensure', sandbox_id: 'expired' });
  assert.equal(replacement.created, true);
  assert.notEqual(replacement.sandbox_id, 'expired');
  assert(f.sandboxes.has('expired'));
  assert.equal((await f.handle({ operation: 'ensure', sandbox_id: null })).reused, true);
});

test('unknown statuses fail closed instead of creating or returning success', async () => {
  const f = fixture();
  f.make('invalid', OWNER, { status: 'not-a-status' });
  for (const operation of ['preflight', 'ensure']) {
    const result = await f.handle({ operation });
    assert.equal(result.ok, false);
    assert.equal(result.error.code, 'INVALID_STATUS');
  }
  assert(!f.calls.some(call => call.method === 'create'));
});

test('SDK permission failure is sanitized and does not silently create', async () => {
  const f = fixture();
  f.Sandbox.connect = async () => { throw namedError('RailwayAuthError'); };
  const result = await f.handle({ operation: 'ensure', sandbox_id: 'saved-id' });
  assert.deepEqual(result, { ok: false, error: { type: 'RailwayAuthError', code: 'SANDBOX_OPERATION_FAILED' } });
  assert(!JSON.stringify(result).includes('SECRET'));
  assert.equal(f.calls.length, 0);
});

test('path validation rejects traversal, hidden paths, secret and control files', () => {
  for (const value of ['../x', '/tmp/x', 'tests/../../x', 'tests\\x', '.git/config',
    'tests/.env', 'node_modules/a.js', 'runtime/x.py', 'secrets/a',
    'tests/private_key.py', 'app/credentials.json', 'id.key', 'x//y', 'x/./y', 'x\u0000y']) {
    assert.throws(() => safePath(value), undefined, value);
  }
  assert.equal(safePath('tests/test_fixture.py'), 'tests/test_fixture.py');
  assert.equal(safePath('horizon_core/app.py'), 'horizon_core/app.py');
  assert.equal(safePath('agents/frontend/instructions.md'), 'agents/frontend/instructions.md');
  assert.equal(safePath('src/cópia segura ; $.py'), 'src/cópia segura ; $.py');
});

test('run-check validates entire payload before uploads', async () => {
  const f = fixture();
  f.make('ours');
  const result = await f.handle(request('ours', { files: [
    { path: 'tests/test_fixture.py', content: 'safe' }, { path: '../bad', content: 'unsafe' }] }));
  assert.equal(result.ok, false);
  assert.equal(f.calls.length, 0);
  assert.equal(f.sandboxes.get('ours').writes.length, 0);
});

test('run-check uploads fresh UUID workspace, fixed Docker command, no host secrets', async () => {
  const f = fixture();
  const vm = f.make('ours');
  const result = await f.handle(request('ours', { task_id: '$(bad-command); unsafe input' }));
  assert.equal(result.passed, true);
  assert.equal(result.tests_run, 1);
  assert.equal(vm.writes[0].mkdir, `/tmp/horizon-workspaces/${UUID}`);
  assert.equal(vm.writes[1].path, `/tmp/horizon-workspaces/${UUID}/tests/test_fixture.py`);
  const { command, options } = vm.commands[0];
  assert(command.includes('--network=none --read-only --cap-drop=ALL'));
  assert(command.includes('--user=65534:65534'));
  assert(command.includes('--mount type=bind,src=/tmp/horizon-workspaces/'));
  assert(!command.includes('bad-command'));
  assert(!JSON.stringify(vm.commands).includes(ENV.RAILWAY_API_TOKEN));
  assert.deepEqual(options, { timeoutSec: 300, maxOutputBytes: 64000 });
  assert(vm.commands[1].command.includes(`rm -rf -- /tmp/horizon-workspaces/${UUID}`));
  assert(!vm.commands[1].command.includes('destroy'));
});

test('run-check cannot connect arbitrary unowned sandbox', async () => {
  const f = fixture();
  const vm = f.make('random', null);
  const result = await f.handle(request('random'));
  assert.equal(result.error.code, 'SANDBOX_NOT_MANAGED');
  assert.equal(vm.commands.length, 0);
  assert.equal(vm.writes.length, 0);
});

test('timeouts, truncation, nonzero/null exit, zero tests and malformed results never pass', async () => {
  for (const result of [outcome({ timedOut: true }), outcome({ truncated: true }),
    outcome({ exitCode: 1 }), outcome({ exitCode: null }), outcome({ stderr: 'Ran 0 tests in 0.000s\nOK' }),
    { exitCode: 0 }, outcome({ stdout: 'x'.repeat(65000) })]) {
    const f = fixture();
    const vm = f.make('ours', OWNER, { result });
    const response = await f.handle(request('ours'));
    assert.notEqual(response.passed, true);
    assert.equal(vm.commands.length, 2); // Cleanup runs even after invalid result.
  }
});

test('cleanup failure prevents a false pass', async () => {
  const f = fixture();
  const vm = f.make('ours');
  vm.exec = async (command) => command.startsWith('docker run') ? outcome() : outcome({ exitCode: 1 });
  const response = await f.handle(request('ours'));
  assert.equal(response.passed, false);
  assert.equal(response.cleanup_ok, false);
});

test('probe verifies Python and a second ensure reuses the same sandbox', async () => {
  const f = fixture();
  const result = await f.handle({ operation: 'probe' });
  assert.equal(result.created, true);
  assert.equal(result.passed, true);
  assert.equal(result.reuse_confirmed, true);
  assert.equal(f.sandboxes.get(result.sandbox_id).commands[0].command, 'python3 --version');
});

test('known secret values are redacted from output while SDK errors omit bodies', async () => {
  const f = fixture();
  f.make('ours', OWNER, { result: outcome({ stdout: Object.values(ENV).join(' ') }) });
  const response = await f.handle(request('ours'));
  for (const key of ['RAILWAY_API_TOKEN', 'GITHUB_TOKEN', 'GEMINI_API_KEY']) {
    assert(!JSON.stringify(response).includes(ENV[key]));
  }
});

test('JSON stdin/stdout emits exactly one response and malformed JSON is sanitized', async () => {
  const f = fixture();
  let output = '';
  const stream = new Writable({ write(chunk, encoding, done) { output += chunk; done(); } });
  assert.equal(await main(Readable.from(['{"operation":"preflight"}']), stream,
    { Sandbox: f.Sandbox, env: ENV }), 0);
  assert.equal(output.trim().split('\n').length, 1);
  assert.equal(JSON.parse(output).ok, true);
  output = '';
  assert.equal(await main(Readable.from(['{ secret-invalid-json']), stream, { Sandbox: f.Sandbox, env: ENV }), 1);
  assert.equal(JSON.parse(output).ok, false);
  assert(!output.includes('secret-invalid-json'));
});
