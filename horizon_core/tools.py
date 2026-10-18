"""Small capability boundary. Model output never becomes a host shell command."""
import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import uuid
import tempfile
import selectors
import time


class Tools:
    def __init__(self, root, agent, allowed_paths=(), branch=None, sandbox_client=None):
        self.root = Path(root).resolve()
        self.agent = agent
        if not isinstance(allowed_paths, (list, tuple)) or not all(isinstance(p, str) and p for p in allowed_paths):
            raise ValueError('allowed_paths must be a list of path patterns')
        self.allowed_paths = list(allowed_paths)
        self.branch = branch
        self.sandbox_client = sandbox_client

    def path(self, name, write=False):
        if not isinstance(name, str) or not name or len(name) > 300:
            raise ValueError('Invalid path')
        parts = PurePosixPath(name).parts
        if '\\' in name or name.startswith('/') or any(p in ('.', '..') or p.startswith('.') for p in parts):
            raise ValueError('Hidden, absolute and parent paths are forbidden')
        if any(p.lower() in ('secrets', 'credentials', 'node_modules', 'runtime') for p in parts):
            raise ValueError('Secret and runtime paths are forbidden')
        if write and parts[0] in ('agents', 'horizon_core', 'sandbox', 'maestro_exec', 'executors', 'supervisor.py', 'GEMINI.md', 'AGENTS.md'):
            raise ValueError('A specialist cannot rewrite its control plane')
        if any(re.search(r'(^|[_.-])(token|secret|credential|password|private.?key)([_.-]|$)', p, re.I) for p in parts):
            raise ValueError('Secret path is forbidden')
        if Path(name).suffix.lower() in ('.pem', '.key', '.sqlite', '.sqlite3', '.db'):
            raise ValueError('Sensitive file type')
        target = self.root.joinpath(*parts)
        if any(p.is_symlink() for p in [target, *target.parents] if p != self.root.parent):
            raise ValueError('Symlinks are forbidden')
        if not target.resolve().is_relative_to(self.root):
            raise ValueError('Path outside worktree')
        if write:
            if not self.branch or not re.fullmatch(r'(feat|fix|agent|test)/[a-zA-Z0-9_-][a-zA-Z0-9_./-]*', self.branch) or '..' in self.branch:
                raise ValueError('A work branch is required')
            current = subprocess.run(['git', '-C', str(self.root), 'branch', '--show-current'], capture_output=True, text=True, timeout=10)
            if current.returncode or current.stdout.strip() != self.branch:
                raise ValueError('Actual worktree branch does not match task branch')
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in self.allowed_paths):
                raise ValueError('Write outside task allowlist')
        return target

    def execute(self, action, args):
        if action not in self.agent['tools']:
            raise ValueError('Capability not granted to specialist')
        if not isinstance(args, dict):
            raise ValueError('Tool arguments must be an object')
        if action == 'list_files':
            result = []
            for path in sorted(self.root.rglob('*')):
                if len(result) >= 500:
                    break
                if not path.is_file():
                    continue
                name = path.relative_to(self.root).as_posix()
                try:
                    self.path(name)
                except ValueError:
                    continue
                result.append(name)
            return {'files': result, 'limit': 500}
        if action == 'read_file':
            path = self.path(args.get('path'))
            if path.stat().st_size > 50000:
                raise ValueError('File exceeds 50KB context budget')
            return {'path': args['path'], 'content': path.read_text(encoding='utf-8')}
        if action == 'write_file':
            path = self.path(args.get('path'), write=True)
            content = args.get('content')
            if not isinstance(content, str) or len(content.encode()) > 100000:
                raise ValueError('Write exceeds 100KB budget')
            # Compare before mutation. A retry can confirm an already applied write.
            previous = path.read_bytes() if path.exists() else b''
            expected = args.get('expected_sha256')
            new_hash = hashlib.sha256(content.encode()).hexdigest()
            if expected is not None and expected != hashlib.sha256(previous).hexdigest() and new_hash != hashlib.sha256(previous).hexdigest():
                raise ValueError('File changed since specialist read it')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            return {'path': args['path'], 'sha256': new_hash, 'bytes': len(content.encode())}
        if action == 'run_check':
            return self.run_check(args)
        raise ValueError('Unknown local tool')

    def run_check(self, args):
        if args.get('name') != 'python-unittest':
            raise ValueError('Only the approved python-unittest check is available')
        if self.sandbox_client is not None:
            return self.sandbox_client.run_check(self.root, self)
        if not shutil.which('docker'):
            raise RuntimeError('Docker unavailable: untrusted tests cannot run on credentialed host')
        # No host credentials, no Docker socket, no network, no shell interpolation.
        with tempfile.TemporaryDirectory(prefix='horizon-check-') as directory:
            snapshot = Path(directory)
            snapshot.chmod(0o755)
            size = 0
            count = 0
            for source in self.root.rglob('*'):
                if not source.is_file():
                    continue
                name = source.relative_to(self.root).as_posix()
                try:
                    self.path(name)
                except ValueError:
                    continue
                size += source.stat().st_size
                count += 1
                if size > 20_000_000 or count > 2000:
                    raise ValueError('Test snapshot exceeds budget')
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o644)
            return self._docker_check(snapshot)

    def _docker_check(self, snapshot):
        container = 'horizon-check-' + uuid.uuid4().hex
        command = ['docker', 'run', '--name', container, '--rm', '--network=none', '--read-only',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128',
                   '--memory=512m', '--cpus=1', '--user=65534:65534',
                   '--tmpfs=/tmp:rw,noexec,nosuid,size=64m',
                   '--mount', f'type=bind,src={snapshot},dst=/project,readonly',
                   '--workdir=/project', '--env=PYTHONDONTWRITEBYTECODE=1',
                   'python:3.12-slim', 'python', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        process = None
        output = bytearray()
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       env={k: v for k, v in os.environ.items() if k in ('PATH', 'HOME')})
            deadline = time.monotonic() + 90
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Test timeout')
                    for key, _ in selector.select(timeout=1):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > 64000:
                            raise RuntimeError('Test output budget exhausted')
            exit_code = process.wait(timeout=5)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=10,
                           env={k: v for k, v in os.environ.items() if k in ('PATH', 'HOME')})
        return {'check': 'python-unittest', 'exit_code': exit_code,
                'passed': exit_code == 0, 'output': output.decode('utf-8', errors='replace')[-12000:]}
