"""Pinned, containerized Node/browser checks; never runs project code on host.

Build the trusted synthetic_visual/Dockerfile once before dispatching agents.
The Playwright profile currently targets the documented synthetic station contract;
this is not a universal audit or a substitute for WCAG contrast/manual review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import tempfile
import time
import uuid

IMAGE = 'horizon-web-proof:1.58.2'
CHECKS = {'node-test': 'node-proof.cjs', 'browser-playwright': 'browser-proof.cjs'}


def _execute(command, timeout, env):
    process = None
    output = bytearray()
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise RuntimeError('Web check timeout')
                for key, _ in selector.select(timeout=1):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > 64000:
                            raise RuntimeError('Web check output exceeded safety bound')
        return process.wait(timeout=5), output.decode('utf-8', errors='replace')
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()


def _snapshot(root, tools, destination):
    count = size = 0
    for source in Path(root).resolve().rglob('*'):
        if not source.is_file():
            continue
        name = source.relative_to(Path(root).resolve()).as_posix()
        try:
            tools.path(source.relative_to(Path(tools.root).resolve()).as_posix())
        except ValueError:
            continue
        if source.is_symlink():
            continue
        count += 1
        size += source.stat().st_size
        if count > 2000 or size > 20_000_000:
            raise ValueError('Web snapshot exceeds safety bound')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    return count


def _collect(evidence, target, manifest):
    entries = manifest.get('screenshots', [])
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError('Browser check produced no usable screenshot evidence')
    copied, size = [], 0
    for entry in entries:
        name = entry.get('file', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}\.png', name):
            raise ValueError('Unsafe screenshot filename')
        source = evidence / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Screenshot missing or linked')
        data = source.read_bytes()
        size += len(data)
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 5_000_000 or size > 20_000_000:
            raise ValueError('Invalid or oversized PNG evidence')
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.get('sha256') or entry.get('bytes') != len(data):
            raise ValueError('Screenshot evidence digest mismatch')
        destination = target / name
        shutil.copyfile(source, destination)
        copied.append({**entry, 'path': str(destination.resolve())})
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return copied


def run_web_check(root, tools, name='browser-playwright', *, evidence_dir=None, timeout=240, project_dir=None):
    """Return structured real execution/evidence. Unavailable checks never pass.

    tools.path(name) supplies the existing capability/secret-path boundary. Caller
    must authorize run_check; this helper never grants a specialist a capability.
    """
    if project_dir is not None:
        root = tools.path(project_dir)
        if not root.is_dir():
            raise ValueError('project_dir must be an existing project directory')
    if name not in CHECKS:
        raise ValueError('Unsupported web check')
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    result = {'check': name, 'kind': 'browser' if name == 'browser-playwright' else 'node',
              'passed': False, 'tests': 0, 'screenshots': [], 'cleanup': False,
              'image': IMAGE, 'playwright_version': '1.58.2', 'network': 'none'}
    if not shutil.which('docker'):
        return {**result, 'reason': 'Docker unavailable', 'cleanup': True}
    safe_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME')}
    container = 'horizon-web-' + uuid.uuid4().hex
    target = Path(evidence_dir or 'runtime/web-evidence').resolve() / container
    try:
        with tempfile.TemporaryDirectory(prefix='horizon-web-') as directory:
            parent = Path(directory)
            parent.chmod(0o755)
            snapshot, evidence = parent / 'project', parent / 'evidence'
            snapshot.mkdir(mode=0o755)
            evidence.mkdir(mode=0o777)
            evidence.chmod(0o777)
            result['snapshot_files'] = _snapshot(root, tools, snapshot)
            command = ['docker', 'run', '--name', container, '--rm', '--network=none', '--read-only',
                       '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=256',
                       '--memory=1g', '--cpus=2', '--shm-size=256m', '--user=65534:65534',
                       '--tmpfs=/tmp:rw,noexec,nosuid,size=256m',
                       '--mount', f'type=bind,src={snapshot},dst=/project,readonly',
                       '--mount', f'type=bind,src={evidence},dst=/evidence', '--workdir=/project',
                       '--env=HOME=/tmp', '--env=NODE_ENV=test', IMAGE,
                       'node', '/opt/horizon/' + CHECKS[name]]
            code, output = _execute(command, timeout, safe_env)
            result.update(exit_code=code, output=output[-12000:])
            try:
                report = json.loads(output.strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise RuntimeError('Check did not produce a JSON execution report') from None
            tests = report.get('tests', 0)
            if not isinstance(tests, int) or isinstance(tests, bool) or tests < 1:
                raise RuntimeError('Zero executed checks never counts as success')
            if report.get('check') != name:
                raise RuntimeError('Check report identity mismatch')
            result.update(tests=tests, passed=bool(code == 0 and report.get('passed') is True), report=report)
            if name == 'browser-playwright':
                manifest_path = evidence / 'manifest.json'
                if not manifest_path.is_file() or manifest_path.is_symlink() or manifest_path.stat().st_size > 64000:
                    raise RuntimeError('Browser manifest is missing or invalid')
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if manifest != report:
                    raise RuntimeError('Browser stdout and manifest disagree')
                target.mkdir(parents=True, exist_ok=False)
                result['screenshots'] = _collect(evidence, target, manifest)
                result['evidence_path'] = str(target)
                result['manifest_path'] = str(target / 'manifest.json')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result.update(passed=False, reason=type(error).__name__ + ': ' + str(error)[:200])
    finally:
        try:
            cleanup = subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=15, env=safe_env)
            # --rm may already have removed the container. Confirm absence separately.
            inspect = subprocess.run(['docker', 'inspect', container], capture_output=True, timeout=10, env=safe_env)
            result['cleanup'] = cleanup.returncode == 0 or (inspect.returncode == 1 and b'No such' in inspect.stderr)
        except (OSError, subprocess.SubprocessError):
            result['cleanup'] = False
        if not result['cleanup']:
            result.update(passed=False, cleanup_error='Container removal was not confirmed')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Real isolated Node and Playwright evidence, no agent CLI')
    parser.add_argument('--root', default='synthetic_visual/app')
    parser.add_argument('--check', choices=tuple(CHECKS), default='browser-playwright')
    parser.add_argument('--evidence', default='runtime/web-evidence')
    args = parser.parse_args(argv)
    from .registry import load_agent
    from .tools import Tools
    result = run_web_check(args.root, Tools(args.root, load_agent('qa-engineer')), args.check, evidence_dir=args.evidence)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
