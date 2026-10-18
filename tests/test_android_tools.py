import tempfile
from pathlib import Path
import unittest
import zipfile

from android_tools.check import inspect_results, _snapshot


class AndroidArtifactBoundaryTests(unittest.TestCase):
    def fixture(self, root, cases='<testcase name="real"/>', counts='tests="1" failures="0" errors="0"', lint=''):
        (root / 'tests').mkdir(exist_ok=True)
        (root / 'tests/TEST-core.xml').write_text(f'<testsuite {counts}>{cases}</testsuite>')
        (root / 'lint.xml').write_text(f'<issues>{lint}</issues>')
        with zipfile.ZipFile(root / 'HorizonOfflineLab-debug.apk', 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest-fixture')
            archive.writestr('classes.dex', b'bytecode-fixture')

    def test_requires_nonzero_matching_tests_and_all_verifiers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            self.assertTrue(inspect_results(root)['passed'])
            self.fixture(root, '', 'tests="0" failures="0" errors="0"')
            self.assertFalse(inspect_results(root)['passed'])
            self.fixture(root, '<testcase><failure/></testcase>')
            self.assertFalse(inspect_results(root)['passed'])
            self.fixture(root, lint='<issue severity="Error"/>')
            self.assertFalse(inspect_results(root)['passed'])
            self.fixture(root, '<testcase><skipped/></testcase>')
            self.assertFalse(inspect_results(root)['passed'])
            self.fixture(root, '', 'tests="5"')
            with self.assertRaises(ValueError): inspect_results(root)

    def test_snapshot_rejects_links_and_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'project'; root.mkdir()
            output = Path(temp) / 'snapshot'; output.mkdir()
            for name in ('settings.gradle', 'build.gradle', 'app/build.gradle'):
                path = root / name; path.parent.mkdir(exist_ok=True); path.write_text('')
            (root / 'local.properties').write_text('sdk.dir=private')
            (root / 'api-token.txt').write_text('never-copy')
            _snapshot(root, output)
            self.assertFalse((output / 'api-token.txt').exists())
            self.assertFalse((output / 'local.properties').exists())
            (root / 'unsafe').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError): _snapshot(root, output)

if __name__ == '__main__': unittest.main()
