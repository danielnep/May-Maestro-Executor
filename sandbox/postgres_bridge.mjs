/** Trusted state controller, NEVER the sandbox used to execute generated code.
 * Node22 + official railway SDK3.16; PRIVATE network and server-resolved DSN.
 * Dedicated environment: Agente Remoto in project caf3c2ea-f857-4242-8fa3-5453ea1462ca.
 * JSON stdin -> one sanitized JSON stdout. No arbitrary SQL, files or command API.
 */
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const STATE_ENVIRONMENT_ID = '11b303c5-e811-4fb2-9e77-514ad050697e';
const ROOT = fileURLToPath(new URL('../', import.meta.url));
const HOME = '/tmp/horizon-postgres-controller';
const MARKER = '/tmp/horizon-postgres-owner.json';
const OWNER = 'horizon-agent-state-postgres';
const VENV = `${HOME}/venv`;
const METHODS = new Set(['enqueue', 'claim', 'renew_lease', 'yield_task', 'checkpoint', 'complete',
  'fail', 'remember', 'recall', 'get', 'events', 'tasks', 'list_tasks_snapshot']);
const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;
const STATUS = new Set(['CREATING', 'RUNNING', 'DESTROYED', 'DESTROYING', 'FAILED']);
const ERROR_TYPES = new Set(['ValueError', 'LeaseError', 'PostgresStateError', 'RailwayAuthError',
  'RailwayConnectionError', 'RailwayGraphQLError', 'SandboxNotFoundError', 'SandboxFilesError',
  'SandboxFileNotFoundError', 'ExecInterruptedError', 'SandboxFailedError', 'SandboxTimeoutError']);

class StateBridgeError extends Error {
  constructor(code) { super(code); this.name = 'StateBridgeError'; this.code = code; }
}
function check(value, code = 'INVALID_REQUEST') { if (!value) throw new StateBridgeError(code); }
function failure(error) {
  return { ok: false, error: { type: error instanceof StateBridgeError ? 'StateBridgeError'
    : ERROR_TYPES.has(error?.name) ? error.name : 'Error',
  code: error instanceof StateBridgeError ? error.code : 'PRIVATE_STATE_FAILED' } };
}

// This script accepts DATA only on stdin and dispatches a fixed method allowlist.
// It imports only controller-owned source uploaded by trusted workflow code.
export const RPC_SCRIPT = String.raw`import inspect,json,os,sys,unittest,io
from urllib.parse import urlsplit,unquote
sys.path.insert(0, '${HOME}')
from horizon_core.postgres_state import PostgresState
allowed = ${JSON.stringify([...METHODS])}
state = None
dsn = os.environ.get('HORIZON_DATABASE_URL', '')
response = None
try:
    parts = urlsplit(dsn)
    if parts.scheme not in ('postgres','postgresql') or not parts.hostname or not parts.hostname.endswith('.railway.internal'):
        raise ValueError('Private database reference required')
    request = json.load(sys.stdin)
    schema = request.get('schema', 'horizon_agents')
    operation = request.get('operation')
    if operation == 'test_pg':
        os.environ['POSTGRES_TEST_DSN'] = dsn
        suite = unittest.defaultTestLoader.discover('${HOME}/tests', pattern='test_postgres_state.py')
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        response = {'ok': True, 'result': {'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful() and result.testsRun>0 and not result.skipped}}
    else:
        state = PostgresState(dsn, schema)
        if operation == 'health':
            response = {'ok': True, 'result': {'connected':True,'private_database':True,'schema':schema}}
        elif operation == 'rpc':
            method = request.get('method')
            if method not in allowed: raise ValueError('Method denied')
            args, kwargs = request.get('args', []), request.get('kwargs', {})
            if not isinstance(args,list) or not isinstance(kwargs,dict): raise ValueError('Arguments denied')
            fn = getattr(state, method)
            inspect.signature(fn).bind(*args, **kwargs)
            response = {'ok': True, 'result': fn(*args, **kwargs)}
        else:
            raise ValueError('Operation denied')
except BaseException as error:
    kind = type(error).__name__
    response = {'ok':False,'error':{'type':kind if kind in ('ValueError','LeaseError','PostgresStateError') else 'StateOperationError','code':'STATE_OPERATION_FAILED'}}
finally:
    if state is not None:
        try: state.close()
        except BaseException: response = {'ok':False,'error':{'type':'StateOperationError','code':'STATE_CLOSE_FAILED'}}
serialized = json.dumps(response,ensure_ascii=False,allow_nan=False)
for secret in (dsn,unquote(urlsplit(dsn).password or '')):
    if len(secret) >= 4:
        serialized = serialized.replace(json.dumps(secret,ensure_ascii=False)[1:-1],'[REDACTED]')
        serialized = serialized.replace(secret,'[REDACTED]')
print(serialized)
`;

export async function loadTrustedSources() {
  const sources = {};
  for (const name of ['horizon_core/state.py', 'horizon_core/postgres_state.py',
    'horizon_core/registry.py', 'tests/test_postgres_state.py']) {
    sources[name] = await readFile(`${ROOT}${name}`, 'utf8');
  }
  sources['horizon_core/__init__.py'] = '"""Trusted private state RPC package."""\n';
  sources['rpc.py'] = RPC_SCRIPT;
  return sources;
}

export function createPostgresBridge({ Sandbox, env = process.env, sources }) {
  check(Sandbox && typeof Sandbox.create === 'function');
  check(sources && sources['rpc.py'] === RPC_SCRIPT, 'TRUSTED_SOURCE_REQUIRED');
  const digest = createHash('sha256').update(JSON.stringify(sources)).digest('hex');
  const config = () => {
    const environmentId = env.RAILWAY_STATE_ENVIRONMENT_ID || STATE_ENVIRONMENT_ID;
    check(environmentId === STATE_ENVIRONMENT_ID && typeof env.RAILWAY_API_TOKEN === 'string'
      && env.RAILWAY_API_TOKEN.trim(), 'DEDICATED_CONFIGURATION_REQUIRED');
    return { token: env.RAILWAY_API_TOKEN, authType: 'bearer', environmentId,
      endpoint: 'https://backboard.railway.com/graphql/v2',
      tcpProxyWsEndpoint: 'wss://ssh.railway.com:2226/ws/exec', verbose: false };
  };
  function validateInfo(vm) {
    check(vm && ID.test(vm.id) && STATUS.has(vm.status), 'INVALID_SANDBOX_STATUS');
    check(vm.environmentId === STATE_ENVIRONMENT_ID, 'ENVIRONMENT_MISMATCH');
  }
  async function managed(vm) {
    validateInfo(vm);
    if (vm.status !== 'RUNNING' || vm.networkIsolation !== 'PRIVATE') return false;
    try {
      const marker = JSON.parse(await vm.files.read(MARKER, { length: 2048 }));
      return marker.owner === OWNER && marker.environment_id === STATE_ENVIRONMENT_ID
        && marker.source_digest === digest && marker.version === 1;
    } catch (error) {
      if (error instanceof SyntaxError || error?.name === 'SandboxFileNotFoundError') return false;
      throw error;
    }
  }
  function validExec(result) {
    check(result && result.exitCode === 0 && result.timedOut === false && result.truncated === false
      && typeof result.stdout === 'string', 'PRIVATE_EXEC_FAILED');
  }
  async function ensure(id) {
    if (id !== undefined && id !== null) {
      check(typeof id === 'string' && ID.test(id));
      try {
        const vm = await Sandbox.connect(id, config());
        validateInfo(vm);
        if (await managed(vm)) { await vm.heartbeat(); return { vm, created: false, reused: true }; }
        if (vm.status === 'RUNNING') throw new StateBridgeError('SANDBOX_NOT_MANAGED');
        if (vm.status === 'CREATING') throw new StateBridgeError('SANDBOX_NOT_READY');
      } catch (error) { if (error?.name !== 'SandboxNotFoundError') throw error; }
    }
    const list = await Sandbox.list({ ...config(), first: 100 });
    check(Array.isArray(list), 'INVALID_SANDBOX_LIST');
    for (const entry of list) {
      validateInfo(entry);
      if (entry.status !== 'RUNNING' || entry.networkIsolation !== 'PRIVATE') continue;
      let vm;
      try { vm = await Sandbox.connect(entry.id, config()); }
      catch (error) { if (error?.name === 'SandboxNotFoundError') continue; throw error; }
      if (await managed(vm)) { await vm.heartbeat(); return { vm, created: false, reused: true }; }
    }
    const vm = await Sandbox.create({ ...config(), networkIsolation: 'PRIVATE',
      env: { HORIZON_DATABASE_URL: '${{Postgres.DATABASE_URL}}' } });
    validateInfo(vm);
    check(vm.status === 'RUNNING' && vm.networkIsolation === 'PRIVATE', 'PRIVATE_SANDBOX_NOT_READY');
    for (const [path, content] of Object.entries(sources)) await vm.files.write(`${HOME}/${path}`, content);
    // The package installer receives no DB credential; DSN stays only with RPC.
    const installed = await vm.exec(`env -u HORIZON_DATABASE_URL python3 -m venv ${VENV} && `
      + `env -u HORIZON_DATABASE_URL ${VENV}/bin/python -m pip install --quiet --disable-pip-version-check --no-input 'psycopg[binary]==3.2.10'`,
    { timeoutSec: 240, maxOutputBytes: 2048 });
    validExec(installed); // Never return package logs or upstream stderr.
    await vm.files.write(MARKER, JSON.stringify({ owner: OWNER, environment_id: STATE_ENVIRONMENT_ID,
      source_digest: digest, version: 1 }));
    return { vm, created: true, reused: false };
  }
  return async function handle(request) {
    try {
      check(request && typeof request === 'object' && !Array.isArray(request));
      check(['ensure', 'health', 'rpc', 'test_pg'].includes(request.operation));
      const schema = request.schema || 'horizon_agents';
      check(typeof schema === 'string' && /^[a-z][a-z0-9_]{0,62}$/.test(schema)
        && !schema.startsWith('pg_') && !['public', 'information_schema'].includes(schema));
      if (request.operation === 'rpc') {
        check(METHODS.has(request.method));
        check(Array.isArray(request.args) && request.kwargs && typeof request.kwargs === 'object' && !Array.isArray(request.kwargs));
      }
      config();
      const { vm, created, reused } = await ensure(request.sandbox_id);
      if (request.operation === 'ensure') return { ok: true, sandbox_id: vm.id, created, reused,
        result: { private_network: true, initialized: true } };
      const wire = { operation: request.operation, schema };
      if (request.operation === 'rpc') Object.assign(wire, { method: request.method, args: request.args, kwargs: request.kwargs });
      const handle = vm.exec(`${VENV}/bin/python -I ${HOME}/rpc.py`,
        { stdin: true, timeoutSec: 120, maxOutputBytes: 35000000 });
      await handle.stdin.write(JSON.stringify(wire));
      await handle.stdin.end();
      const result = await handle;
      validExec(result);
      let response;
      try { response = JSON.parse(result.stdout); }
      catch { throw new StateBridgeError('INVALID_PRIVATE_RESPONSE'); }
      check(response && typeof response === 'object' && typeof response.ok === 'boolean', 'INVALID_PRIVATE_RESPONSE');
      if (!response.ok) {
        const type = response.error?.type;
        return { ok: false, sandbox_id: vm.id, error: { type: ERROR_TYPES.has(type) ? type : 'StateOperationError', code: 'STATE_OPERATION_FAILED' } };
      }
      return { ok: true, sandbox_id: vm.id, created, reused, result: response.result };
    } catch (error) { return failure(error); }
  };
}

export async function main(input = process.stdin, output = process.stdout, dependencies = {}) {
  let response;
  try {
    let body = '';
    let size = 0;
    for await (const chunk of input) {
      size += Buffer.byteLength(chunk);
      check(size <= 25000000, 'REQUEST_TOO_LARGE');
      body += chunk;
    }
    const sdk = dependencies.Sandbox ? dependencies : await import('railway');
    const sources = dependencies.sources || await loadTrustedSources();
    response = await createPostgresBridge({ ...sdk, ...dependencies, sources })(JSON.parse(body));
  } catch (error) { response = failure(error); }
  output.write(JSON.stringify(response) + '\n');
  return response.ok ? 0 : 1;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = await main();
