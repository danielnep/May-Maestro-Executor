"""Fixed offline Android checks; no model shell or credential-bearing host build."""
import argparse
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
import xml.etree.ElementTree as ET
import zipfile

IMAGE = 'horizon-android:agp8.7.3'


def inspect_results(directory):
    directory = Path(directory)
    reports = sorted((directory / 'tests').glob('TEST-*.xml'))
    count = failures = errors = skipped = 0
    for report in reports:
        if report.is_symlink() or report.stat().st_size > 2_000_000:
            raise ValueError('Invalid JUnit report')
        root = ET.parse(report).getroot()
        if root.tag != 'testsuite':
            raise ValueError('Invalid JUnit root')
        cases = list(root.iter('testcase'))
        if int(root.attrib.get('tests', '-1')) != len(cases):
            raise ValueError('JUnit case count mismatch')
        count += len(cases)
        failures += len(list(root.iter('failure')))
        errors += len(list(root.iter('error')))
        skipped += len(list(root.iter('skipped')))
        failures += int(root.attrib.get('failures', '0'))
        errors += int(root.attrib.get('errors', '0'))
    lint = directory / 'lint.xml'
    if lint.is_symlink() or lint.stat().st_size > 2_000_000:
        raise ValueError('Invalid lint report')
    lint_errors = sum(1 for issue in ET.parse(lint).getroot().iter('issue')
                      if issue.attrib.get('severity', '').lower() in ('error', 'fatal'))
    apk = directory / 'HorizonOfflineLab-debug.apk'
    if apk.is_symlink() or not 0 < apk.stat().st_size < 100_000_000:
        raise ValueError('Invalid APK artifact')
    with zipfile.ZipFile(apk) as package:
        names = set(package.namelist())
        if len(names) > 10000 or sum(info.file_size for info in package.infolist()) > 300_000_000:
            raise ValueError('APK decompression exceeds limit')
        if not {'AndroidManifest.xml', 'classes.dex'}.issubset(names):
            raise ValueError('APK is missing native manifest or bytecode')
        if package.testzip() is not None:
            raise ValueError('Corrupted APK')
    return {'tests_run': count, 'failures': failures, 'errors': errors, 'skipped': skipped,
            'lint_errors': lint_errors,
            'passed': count > 0 and failures == 0 and errors == 0 and skipped == 0 and lint_errors == 0}


def _snapshot(root, target):
    total = count = 0
    denied = {'build', '.gradle', '.git', 'node_modules', 'runtime', 'secrets', 'credentials'}
    for source in sorted(root.rglob('*')):
        relative = source.relative_to(root)
        if any(part.startswith('.') or part.lower() in denied for part in relative.parts):
            continue
        if source.is_symlink():
            raise ValueError('Symlinks are forbidden in Android inputs')
        if not source.is_file():
            continue
        if source.suffix.lower() in ('.key', '.pem', '.sqlite', '.sqlite3', '.db', '.keystore', '.jks'):
            continue
        if source.name == 'local.properties' or re.search(r'(^|[_.-])(secret|token|credential|password)([_.-]|$)', source.name, re.I):
            continue
        total += source.stat().st_size
        count += 1
        if total > 20_000_000 or count > 2000:
            raise ValueError('Android snapshot exceeds size limit')
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        dest.chmod(0o644)
    for required in ('settings.gradle', 'build.gradle', 'app/build.gradle'):
        if not (target / required).is_file():
            raise ValueError('Android Gradle project files are missing')


def isolated_check(project_root, artifacts_dir=None):
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError('Android project directory is missing')
    if not shutil.which('docker'):
        raise RuntimeError('Docker is required for isolated Android compilation')
    environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME')}
    image = subprocess.run(['docker', 'image', 'inspect', IMAGE], env=environment,
                           capture_output=True, timeout=15)
    if image.returncode:
        raise RuntimeError('Prepared Android image unavailable; build trusted android_tools/Dockerfile first')
    container = 'horizon-android-' + uuid.uuid4().hex
    output = bytearray()
    timeout = truncated = False
    process = None
    with tempfile.TemporaryDirectory(prefix='horizon-android-') as temporary:
        base = Path(temporary)
        base.chmod(0o755)
        source = base / 'input'
        source.mkdir(mode=0o755)
        _snapshot(root, source)
        result = base / 'output'
        result.mkdir(mode=0o777)
        result.chmod(0o777)
        command = ['docker', 'run', '--pull=never', '--name', container, '--rm', '--network=none',
                   '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                   '--pids-limit=512', '--memory=3g', '--cpus=2', '--user=10001:10001',
                   '--tmpfs=/tmp:rw,nosuid,size=256m',
                   '--tmpfs=/work:rw,exec,nosuid,size=3g,uid=10001,gid=10001',
                   '--mount', f'type=bind,src={source},dst=/input,readonly',
                   '--mount', f'type=bind,src={result},dst=/output', IMAGE]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment)
            deadline = time.monotonic() + 900
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        timeout = True
                        break
                    for key, _ in selector.select(timeout=1):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > 200_000:
                            truncated = True
                            break
                    if truncated:
                        break
            if timeout or truncated:
                process.kill()
            exit_code = process.wait(timeout=10)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            removal = subprocess.run(['docker', 'rm', '-f', container], env=environment,
                                     capture_output=True, timeout=20)
            probe = subprocess.run(['docker', 'container', 'inspect', container], env=environment,
                                   capture_output=True, timeout=10)
            cleanup = probe.returncode != 0 and (removal.returncode == 0 or b'No such container' in removal.stderr)
        summary = {'passed': False, 'tests_run': 0}
        if exit_code == 0 and not timeout and not truncated:
            try:
                summary = inspect_results(result)
            except (ValueError, OSError, ET.ParseError, zipfile.BadZipFile) as error:
                summary['artifact_error'] = str(error)
        summary.update(check='android-gradle', exit_code=exit_code, timeout=timeout,
                       truncated=truncated, cleanup=cleanup, output=output.decode('utf-8', errors='replace')[-16000:])
        summary['passed'] = bool(summary['passed'] and exit_code == 0 and cleanup and not timeout and not truncated)
        summary['artifacts'] = []
        if artifacts_dir is not None and summary['passed']:
            destination = Path(artifacts_dir).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            # Export only verifier-approved output paths, never arbitrary project outputs.
            files = [result / 'HorizonOfflineLab-debug.apk', result / 'lint.xml', *sorted((result / 'tests').glob('TEST-*.xml'))]
            for file in files:
                relative = file.relative_to(result)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                summary['artifacts'].append(str(target))
        return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='android_tools/lab')
    parser.add_argument('--artifacts', default=None)
    args = parser.parse_args()
    result = isolated_check(args.project, args.artifacts)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
