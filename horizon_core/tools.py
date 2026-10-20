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
        self.evidence = []

    def path(self, name, write=False):
        if not isinstance(name, str) or not name or len(name) > 300:
            raise ValueError('Invalid path')
        parts = PurePosixPath(name).parts
        if '\\' in name or name.startswith('/') or any(p in ('.', '..') or p.startswith('.') for p in parts):
            raise ValueError('Hidden, absolute and parent paths are forbidden')
        if any(p.lower() in ('secrets', 'credentials', 'node_modules', 'runtime', '__pycache__') for p in parts):
            raise ValueError('Secret and runtime paths are forbidden')
        if write and parts[0] in ('agents', 'horizon_core', 'sandbox', 'android_tools', 'maestro_exec', 'executors', 'supervisor.py', 'GEMINI.md', 'AGENTS.md'):
            raise ValueError('A specialist cannot rewrite its control plane')
        if any(re.search(r'(^|[_.-])(token|secret|credential|password|private.?key)([_.-]|$)', p, re.I) for p in parts):
            raise ValueError('Secret path is forbidden')
        if parts[-1] == 'local.properties':
            raise ValueError('Local Android configuration is forbidden')
        if Path(name).suffix.lower() in ('.pem', '.key', '.sqlite', '.sqlite3', '.db', '.keystore', '.jks', '.env'):
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
            pattern = args.get('filter', '')
            limit = args.get('limit', 100)
            if not isinstance(pattern, str) or len(pattern) > 80 or (
                    type(limit) is not int or not 1 <= limit <= 200):
                raise ValueError('Bounded list_files filter/limit required')
            result = []
            for path in sorted(self.root.rglob('*')):
                if len(result) >= limit:
                    break
                if not path.is_file():
                    continue
                name = path.relative_to(self.root).as_posix()
                if pattern and pattern.casefold() not in name.casefold():
                    continue
                try:
                    self.path(name)
                except ValueError:
                    continue
                result.append(name)
            return {'files': result, 'limit': limit, 'filter': pattern}
        if action == 'read_file':
            path = self.path(args.get('path'))
            # Find a local symbol or string first, rather than asking an LLM to
            # read thousands of unrelated lines or make a remote API call.
            search = args.get('search')
            if search is not None:
                if (not isinstance(search, str) or not 2 <= len(search) <= 100 or
                        args.get('start_line') is not None or args.get('end_line') is not None):
                    raise ValueError('Search must be 2-100 chars without a line range')
                found = []
                context = 3
                if path.stat().st_size > 2_000_000:
                    raise ValueError('Source exceeds bounded local search')
                lines = path.read_text(encoding='utf-8').splitlines()
                matches = [i for i, line in enumerate(lines) if search.casefold() in line.casefold()]
                for match in matches[:8]:
                    start = max(0, match-context)
                    end = min(len(lines), match+context+1)
                    found.append({'start_line': start+1, 'end_line': end,
                                  'content': '\n'.join(lines[start:end])[:1100]})
                return {'path': args['path'], 'search': search, 'matches': found,
                        'total_matches': len(matches), 'truncated': len(matches)>8}
            # Read only the relevant excerpt when a bounded line range is supplied.
            start, end = args.get('start_line'), args.get('end_line')
            if start is not None or end is not None:
                if (not isinstance(start, int) or isinstance(start, bool) or start < 1 or
                    not isinstance(end, int) or isinstance(end, bool) or end < start or end - start > 199):
                    raise ValueError('Provide a valid 1-based range of at most 200 lines')
                with path.open(encoding='utf-8') as source:
                    selected = [line for number, line in enumerate(source, 1) if start <= number <= end]
                excerpt = ''.join(selected)
                if len(excerpt.encode('utf-8')) > 50000:
                    raise ValueError('Excerpt exceeds 50KB context budget')
                return {'path': args['path'], 'start_line': start, 'end_line': end,
                        'content': excerpt}
            if path.stat().st_size > 50000:
                raise ValueError('File exceeds 50KB context budget; request line range')
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
        if action == 'search_web':
            from .research import search_web
            return search_web(args.get('query'))
        if action == 'fetch_public':
            from .research import fetch_public
            return fetch_public(args.get('url'))
        raise ValueError('Unknown local tool')

    def evidence_image(self, index):
        if not isinstance(index,int) or isinstance(index,bool) or not 0 <= index < len(self.evidence):
            raise ValueError('Select an image index from the current browser check')
        item=self.evidence[index]
        path=Path(item['path'])
        data=path.read_bytes()
        if path.is_symlink() or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('Browser evidence changed after capture')
        return data

    def snapshot_files(self):
        """Exact credential-filtered full-root inputs used for runtime evidence."""
        files = {}
        size = 0
        for source in sorted(self.root.rglob('*')):
            if not source.is_file():
                continue
            name = source.relative_to(self.root).as_posix()
            try:
                self.path(name)
            except ValueError:
                continue
            data = source.read_bytes()
            size += len(data)
            if size > 20_000_000 or len(files) >= 2000:
                raise ValueError('Test snapshot exceeds safety bound')
            files[name] = data
        return files

    def _project_root(self, value):
        if value == '.':
            return self.root
        if not isinstance(value, str) or str(PurePosixPath(value)) != value:
            raise ValueError('project_dir must be a canonical repository-relative directory')
        result = self.path(value)
        if not result.is_dir():
            raise ValueError('project_dir is not an existing directory')
        return result

    def _evidence_directory(self, kind):
        target = self.root / 'runtime' / kind
        for component in (self.root / 'runtime', target):
            if component.is_symlink():
                raise ValueError('Evidence output cannot use linked directories')
        if not target.resolve().is_relative_to(self.root):
            raise ValueError('Evidence output must remain in the worktree')
        target.mkdir(parents=True, exist_ok=True)
        return target

    def run_check(self, args):
        name = args.get('name')
        if name not in ('python-unittest', 'node-test', 'android-gradle'):
            raise ValueError('Only approved isolated checks are available')
        project_dir = args.get('project_dir', '.')
        project_root = self._project_root(project_dir)
        from .publication_policy import workspace_fingerprint
        digest = workspace_fingerprint(self.snapshot_files())
        started = time.time()
        execution_id = uuid.uuid4().hex
        scoped = Tools(project_root, self.agent, sandbox_client=self.sandbox_client)
        if name == 'node-test':
            from .web_checks import run_web_check
            raw = run_web_check(project_root, scoped, name,
                evidence_dir=self._evidence_directory('web-evidence'))
            self.evidence.extend(raw.get('screenshots', []))
            backend = 'docker-web'
        elif name == 'android-gradle':
            from android_tools.check import isolated_check
            raw = isolated_check(project_root,
                artifacts_dir=self._evidence_directory('android-evidence') / execution_id)
            backend = 'docker-android'
        elif self.sandbox_client is not None:
            raw = self.sandbox_client.run_check(project_root, scoped)
            backend = raw.get('backend', 'railway-sdk')
        else:
            if not shutil.which('docker'):
                raise RuntimeError('Docker unavailable: untrusted tests cannot run on credentialed host')
            # Snapshot bytes captured before execution, not live credentialed host files.
            with tempfile.TemporaryDirectory(prefix='horizon-check-') as directory:
                snapshot = Path(directory)
                snapshot.chmod(0o755)
                for relative, data in scoped.snapshot_files().items():
                    target = snapshot / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    target.chmod(0o644)
                raw = self._docker_check(snapshot)
            backend = 'docker-python'
        if not isinstance(raw, dict):
            raise ValueError('Isolated checker returned an invalid result')
        output = raw.get('output', '')
        if not isinstance(output, str):
            raise ValueError('Isolated checker output must be text')
        count = raw.get('tests_run', raw.get('tests'))
        if count is None:
            counts = re.findall(r'^Ran (\d+) tests? in ', output, re.M)
            count = int(counts[-1]) if counts else 0
        if type(count) is not int or count < 0:
            count = 0
        timed_out = raw.get('timed_out', raw.get('timedOut', raw.get('timeout', False)))
        truncated = raw.get('truncated', False)
        cleanup = raw.get('cleanup_ok', raw.get('cleanup', False))
        exit_code = raw.get('exit_code', raw.get('exitCode'))
        unchanged = workspace_fingerprint(self.snapshot_files()) == digest
        result = {**raw, 'check': name, 'project_dir': project_dir, 'backend': backend,
                  'source': 'runtime', 'simulated': False, 'workspace_digest': digest,
                  'execution_id': execution_id, 'executed_at': started, 'tests_run': count, 'exit_code': exit_code,
                  'timed_out': timed_out is not False, 'truncated': truncated is not False,
                  'cleanup_ok': cleanup is True, 'workspace_unchanged': unchanged,
                  'passed': raw.get('passed') is True and type(exit_code) is int and exit_code == 0
                            and count > 0 and timed_out is False and truncated is False
                            and cleanup is True and unchanged}
        if not unchanged:
            result['reason'] = 'Workspace changed during check; rerun the final snapshot'
        return result

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
            cleanup = subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=10,
                           env={k: v for k, v in os.environ.items() if k in ('PATH', 'HOME')})
        return {'check': 'python-unittest', 'exit_code': exit_code,
                'passed': exit_code == 0, 'cleanup_ok': cleanup.returncode == 0 or (cleanup.returncode == 1 and b'No such' in cleanup.stderr),
                'timed_out': False, 'truncated': False, 'output': output.decode('utf-8', errors='replace')[-12000:]}
