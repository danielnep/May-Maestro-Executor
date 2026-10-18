import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from horizon_core.tools import Tools
from horizon_core.web_checks import run_web_check


class WebCheckBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        (self.root / 'index.html').write_text('<html lang="en"><title>Fixture</title><body>Fixture app</body></html>')
        (self.root / '.env').write_text('do-not-copy')
        (self.root / 'credentials').mkdir()
        (self.root / 'credentials/key.txt').write_text('do-not-copy')
        self.tools = Tools(self.root, {'tools': ['run_check']})

    def tearDown(self):
        self.tmp.cleanup()

    def test_docker_absence_cannot_pass_or_claim_evidence(self):
        with patch('horizon_core.web_checks.shutil.which', return_value=None):
            result = run_web_check(self.root, self.tools)
        self.assertFalse(result['passed'])
        self.assertEqual(result['tests'], 0)
        self.assertEqual(result['screenshots'], [])

    def test_real_evidence_contract_and_sanitized_container_boundary(self):
        png = b'\x89PNG\r\n\x1a\nfixture-bytes'
        report = {'check': 'browser-playwright', 'tests': 4, 'passed': True, 'screenshots': [
            {'file': 'mobile-rendered.png', 'bytes': len(png), 'sha256': hashlib.sha256(png).hexdigest()}]}
        def execute(command, timeout, env):
            self.assertEqual(set(env) - {'PATH', 'HOME'}, set())
            for flag in ('--network=none', '--read-only', '--cap-drop=ALL', '--user=65534:65534'):
                self.assertIn(flag, command)
            mounts = [command[index + 1] for index, item in enumerate(command) if item == '--mount']
            project = Path(mounts[0].split('src=')[1].split(',')[0])
            evidence = Path(mounts[1].split('src=')[1].split(',')[0])
            self.assertTrue((project / 'index.html').exists())
            self.assertFalse((project / '.env').exists())
            self.assertFalse((project / 'credentials').exists())
            (evidence / 'mobile-rendered.png').write_bytes(png)
            (evidence / 'manifest.json').write_text(json.dumps(report))
            return 0, json.dumps(report)
        with patch('horizon_core.web_checks.shutil.which', return_value='/fake/docker'), patch(
                'horizon_core.web_checks._execute', side_effect=execute), patch(
                'horizon_core.web_checks.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            result = run_web_check(self.root, self.tools, evidence_dir=Path(self.tmp.name) / 'evidence')
        self.assertTrue(result['passed'])
        self.assertTrue(result['cleanup'])
        self.assertEqual(result['screenshots'][0]['sha256'], hashlib.sha256(png).hexdigest())
        self.assertEqual(Path(result['screenshots'][0]['path']).read_bytes(), png)
        self.assertTrue(Path(result['manifest_path']).exists())

    def test_zero_tests_and_failed_cleanup_never_pass(self):
        for tests, cleanup_code in ((0, 0), (3, 2)):
            with self.subTest(tests=tests), patch('horizon_core.web_checks.shutil.which', return_value='/fake/docker'), patch(
                    'horizon_core.web_checks._execute', return_value=(0, json.dumps({'check': 'node-test', 'tests': tests, 'passed': True}))), patch(
                    'horizon_core.web_checks.subprocess.run', return_value=subprocess.CompletedProcess([], cleanup_code, b'', b'cleanup failure')):
                result = run_web_check(self.root, self.tools, 'node-test')
            self.assertFalse(result['passed'])

    def test_project_dir_validation_and_unsupported_check(self):
        with self.assertRaises(ValueError):
            run_web_check(self.root, self.tools, project_dir='../outside')
        with self.assertRaises(ValueError):
            run_web_check(self.root, self.tools, 'shell')
        with self.assertRaises(ValueError):
            run_web_check(self.root, self.tools, project_dir='credentials')


if __name__ == '__main__':
    unittest.main()
