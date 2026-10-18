/**
 * One JSON request on stdin -> one JSON response on stdout, Node >=22.
 * {operation:'preflight'|'ensure'|'probe'|'run-check', sandbox_id?, files?, task_id?, check?}
 * Controller-only RAILWAY_API_TOKEN and RAILWAY_ENVIRONMENT_ID. No agent CLI.
 * Official API: docs.railway.com/sandboxes; railwayapp/railway-ts-sdk 3.16.0.
 * VM reuse is best-effort: plan default idle teardown remains enabled. Persist
 * returned sandbox_id outside this VM; checkpoints belong to the coordinator.
 */
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const OWNER = 'horizon-agent-core';
const MARKER = '/tmp/horizon-runtime-owner.json';
const ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$/;
const STATUSES = new Set(['CREATING', 'DESTROYED', 'DESTROYING', 'FAILED', 'RUNNING']);
const OPTIONS = Object.freeze({ timeoutSec: 300, maxOutputBytes: 64000 });
const SDK_ERRORS = new Set(['RailwayAuthError', 'RailwayConnectionError', 'RailwayGraphQLError',
  'SandboxNotFoundError', 'SandboxFileNotFoundError', 'SandboxFilesError', 'SandboxFailedError',
  'SandboxTimeoutError', 'ExecInterruptedError', 'ExecControlUnsupportedError', 'ExecNotStartedError']);

class BridgeError extends Error {
  constructor(code) { super(code); this.name = 'BridgeError'; this.code = code; }
}
function requireValue(condition, code = 'INVALID_INPUT') {
  if (!condition) throw new BridgeError(code);
}
export function safePath(value) {
  requireValue(typeof value === 'string' && value.length > 0 && value.length <= 300);
  const pieces = value.split('/');
  requireValue(!/[\\\u0000-\u001f\u007f]/.test(value));
  requireValue(pieces.every(p => p && !p.startsWith('.')
    && !['secrets', 'credentials', 'node_modules', 'runtime'].includes(p.toLowerCase())
    && !/(^|[_.-])(tokens?|secrets?|credentials?|passwords?|private.?key)([_.-]|$)/i.test(p)));
  requireValue(!/\.(pem|key|sqlite|sqlite3|db)$/i.test(value));
  return value;
}
function safeId(value) {
  requireValue(typeof value === 'string' && ID.test(value));
  return value;
}
function info(sandbox, environmentId) {
  requireValue(sandbox && typeof sandbox === 'object', 'INVALID_SANDBOX');
  safeId(sandbox.id);
  requireValue(STATUSES.has(sandbox.status), 'INVALID_STATUS');
  requireValue(sandbox.environmentId === environmentId, 'ENVIRONMENT_MISMATCH');
  return { id: sandbox.id, status: sandbox.status };
}
function redact(value, env) {
  let text = value;
  for (const [key, secret] of Object.entries(env)) {
    if (/(token|secret|password|api.?key)/i.test(key) && typeof secret === 'string' && secret.length >= 4) {
      text = text.split(secret).join('[REDACTED]');
    }
  }
  return text;
}
function execResult(value, env) {
  requireValue(value && typeof value === 'object'
    && (value.exitCode === null || Number.isInteger(value.exitCode))
    && typeof value.timedOut === 'boolean' && typeof value.truncated === 'boolean'
    && typeof value.stdout === 'string' && typeof value.stderr === 'string', 'INVALID_EXEC_RESULT');
  const stdout = Buffer.from(value.stdout).subarray(0, 64000).toString('utf8');
  const stderr = Buffer.from(value.stderr).subarray(0, 64000).toString('utf8');
  const truncated = value.truncated || Buffer.byteLength(value.stdout) > 64000 || Buffer.byteLength(value.stderr) > 64000;
  return { exitCode: value.exitCode, timedOut: value.timedOut,
    truncated,
    stdout: redact(stdout, env), stderr: redact(stderr, env),
    passed: value.exitCode === 0 && !value.timedOut && !truncated };
}
function errorResult(error) {
  return { ok: false, error: {
    type: error instanceof BridgeError ? 'BridgeError' : SDK_ERRORS.has(error?.name) ? error.name : 'Error',
    code: error instanceof BridgeError ? error.code : 'SANDBOX_OPERATION_FAILED',
  } };
}

export function createBridge({ Sandbox, env = process.env, uuid = randomUUID }) {
  requireValue(Sandbox && typeof Sandbox.list === 'function');
  const environmentId = env.RAILWAY_ENVIRONMENT_ID;
  const config = () => {
    requireValue(typeof env.RAILWAY_API_TOKEN === 'string' && env.RAILWAY_API_TOKEN.trim()
      && typeof environmentId === 'string' && ID.test(environmentId), 'MISSING_CONFIGURATION');
    // Explicit endpoint prevents hostile ambient SDK endpoint overrides receiving keys.
    return { token: env.RAILWAY_API_TOKEN, authType: 'bearer', environmentId,
      endpoint: 'https://backboard.railway.com/graphql/v2',
      tcpProxyWsEndpoint: 'wss://ssh.railway.com:2226/ws/exec', verbose: false };
  };
  async function managed(sandbox) {
    info(sandbox, environmentId);
    if (sandbox.status !== 'RUNNING' || sandbox.networkIsolation !== 'ISOLATED') return false;
    let marker;
    try { marker = await sandbox.files.read(MARKER, { length: 2048 }); }
    catch (error) {
      if (error?.name === 'SandboxFileNotFoundError') return false;
      throw error;
    }
    try {
      const parsed = JSON.parse(marker);
      return parsed.owner === OWNER && parsed.environment_id === environmentId && parsed.version === 1;
    } catch { return false; }
  }
  async function connect(id) {
    safeId(id);
    return await Sandbox.connect(id, config());
  }
  async function ensure(sandboxId) {
    if (sandboxId !== undefined && sandboxId !== null) {
      try {
        const preferred = await connect(sandboxId);
        info(preferred, environmentId);
        if (preferred.status === 'RUNNING' && await managed(preferred)) {
          await preferred.heartbeat();
          return { sandbox: preferred, created: false, reused: true };
        }
        // Expired VMs may remain listed as DESTROYED: recover without touching
        // them. A running unowned VM is never permission to adopt or replace it.
        if (preferred.status === 'RUNNING') throw new BridgeError('SANDBOX_NOT_MANAGED');
        if (preferred.status === 'CREATING') throw new BridgeError('SANDBOX_NOT_READY');
      } catch (error) {
        if (error?.name !== 'SandboxNotFoundError') throw error;
      }
    }
    const listed = await Sandbox.list({ ...config(), first: 100 });
    requireValue(Array.isArray(listed), 'INVALID_SANDBOX_LIST');
    for (const candidate of listed) {
      info(candidate, environmentId);
      if (candidate.status !== 'RUNNING' || candidate.networkIsolation !== 'ISOLATED') continue;
      let sandbox;
      try { sandbox = await connect(candidate.id); }
      catch (error) { if (error?.name === 'SandboxNotFoundError') continue; throw error; }
      if (await managed(sandbox)) {
        await sandbox.heartbeat();
        return { sandbox, created: false, reused: true };
      }
    }
    const sandbox = await Sandbox.create({ ...config(), env: {}, networkIsolation: 'ISOLATED' });
    info(sandbox, environmentId);
    requireValue(sandbox.status === 'RUNNING' && sandbox.networkIsolation === 'ISOLATED', 'SANDBOX_NOT_READY');
    await sandbox.files.write(MARKER, JSON.stringify({ owner: OWNER, environment_id: environmentId, version: 1 }));
    return { sandbox, created: true, reused: false };
  }
  async function runCheck(request) {
    requireValue(request.check === 'python-unittest');
    requireValue(typeof request.task_id === 'string' && request.task_id.length > 0 && request.task_id.length <= 200);
    requireValue(Array.isArray(request.files) && request.files.length > 0 && request.files.length <= 2000);
    let size = 0;
    const seen = new Set();
    // Validate the complete payload before any connection, write or command.
    for (const file of request.files) {
      requireValue(file && typeof file === 'object');
      safePath(file.path);
      requireValue(!seen.has(file.path) && typeof file.content === 'string' && Buffer.byteLength(file.content) <= 20000000);
      seen.add(file.path);
      size += Buffer.byteLength(file.content);
    }
    requireValue(size <= 20000000);
    requireValue(request.files.some(file => file.path.startsWith('tests/') && file.path.endsWith('.py')), 'NO_TEST_FILES');
    const sandbox = await connect(request.sandbox_id);
    requireValue(await managed(sandbox), 'SANDBOX_NOT_MANAGED');
    const operationId = uuid();
    requireValue(/^[a-f0-9-]{36}$/.test(operationId), 'INVALID_OPERATION_ID');
    const workspace = `/tmp/horizon-workspaces/${operationId}`;
    const container = `horizon-check-${operationId}`;
    await sandbox.files.mkdir(workspace);
    let result;
    let cleanupOk = false;
    try {
      for (const file of request.files) await sandbox.files.write(`${workspace}/${file.path}`, file.content);
      // Only controller-generated UUID paths enter this fixed shell command.
      // Test contents and task_id are never interpolated or given host credentials.
      const command = `docker run --name ${container} --rm --network=none --read-only --cap-drop=ALL `
        + '--security-opt=no-new-privileges --pids-limit=128 --memory=512m --cpus=1 --user=65534:65534 '
        + '--tmpfs=/tmp:rw,noexec,nosuid,size=64m '
        + `--mount type=bind,src=${workspace},dst=/project,readonly --workdir=/project `
        + '--env PYTHONDONTWRITEBYTECODE=1 python:3.12-slim python -m unittest discover -s tests -v';
      result = execResult(await sandbox.exec(command, OPTIONS), env);
    } finally {
      // Never delete a sandbox. Only this operation's generated paths and container.
      try {
        const cleanup = execResult(await sandbox.exec(
          `docker info >/dev/null 2>&1 || exit 1; if docker inspect ${container} >/dev/null 2>&1; `
            + `then docker rm -f ${container} >/dev/null 2>&1 || exit 1; fi; rm -rf -- ${workspace}`,
          { timeoutSec: 30, maxOutputBytes: 2048 }), env);
        cleanupOk = cleanup.exitCode === 0 && !cleanup.timedOut;
      } catch { cleanupOk = false; }
    }
    const counts = [...(result.stdout + '\n' + result.stderr).matchAll(/^Ran (\d+) tests? in /gm)];
    const testsRun = counts.length ? Number(counts.at(-1)[1]) : 0;
    return { ok: true, sandbox_id: sandbox.id, check: request.check,
      ...result, tests_run: testsRun, cleanup_ok: cleanupOk, passed: result.passed && cleanupOk && testsRun > 0 };
  }
  return async function handle(request) {
    try {
      requireValue(request && typeof request === 'object' && !Array.isArray(request));
      config();
      if (request.operation === 'preflight') {
        const listed = await Sandbox.list({ ...config(), first: 100 });
        requireValue(Array.isArray(listed), 'INVALID_SANDBOX_LIST');
        return { ok: true, sandboxes: listed.map(item => info(item, environmentId)) };
      }
      if (request.operation === 'run-check') return await runCheck(request);
      requireValue(['ensure', 'probe'].includes(request.operation));
      const first = await ensure(request.sandbox_id);
      const response = { ok: true, sandbox_id: first.sandbox.id, status: first.sandbox.status,
        created: first.created, reused: first.reused };
      if (request.operation === 'ensure') return response;
      const probe = execResult(await first.sandbox.exec('python3 --version', { timeoutSec: 30, maxOutputBytes: 2048 }), env);
      const second = await ensure(first.sandbox.id);
      return { ...response, probe, reuse_confirmed: second.reused && second.sandbox.id === first.sandbox.id,
        passed: probe.passed && second.reused && second.sandbox.id === first.sandbox.id };
    } catch (error) { return errorResult(error); }
  };
}

export async function main(input = process.stdin, output = process.stdout, dependencies = {}) {
  let response;
  try {
    let body = '';
    let bytes = 0;
    for await (const chunk of input) {
      bytes += Buffer.byteLength(chunk);
      requireValue(bytes <= 40000000, 'REQUEST_TOO_LARGE');
      body += chunk;
    }
    const request = JSON.parse(body);
    const sdk = dependencies.Sandbox ? dependencies : await import('railway');
    response = await createBridge({ ...sdk, ...dependencies })(request);
  } catch (error) { response = errorResult(error); }
  output.write(JSON.stringify(response) + '\n');
  return response.ok ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
