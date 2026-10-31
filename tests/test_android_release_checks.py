import tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from android_tools.check import inspect_results,_snapshot
from horizon_core.tools import Tools

class AndroidReleaseChecks(unittest.TestCase):
 def fixture(self,root,release,lint_error=False):
  (root/'tests').mkdir()
  (root/'lint.xml').write_text('<issues>'+('<issue severity="Error"/>' if lint_error else '')+'</issues>')
  with zipfile.ZipFile(root/('Android-release.apk' if release else 'HorizonOfflineLab-debug.apk'),'w') as z:
   z.writestr('AndroidManifest.xml',b'parser-fixture');z.writestr('classes.dex',b'dex-fixture')
 def test_release_reports_build_lint_without_inventing_unit_tests(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);self.fixture(root,True);result=inspect_results(root,release=True)
   self.assertTrue(result['passed']);self.assertEqual(result['tests_run'],0);self.assertFalse(result['unit_tests_executed']);self.assertFalse(result['signature_verified']);self.assertEqual(result['verification_kind'],'android-build-lint')
 def test_debug_still_requires_actual_junit_cases(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);self.fixture(root,False);self.assertFalse(inspect_results(root)['passed'])
 def test_release_rejects_lint_errors(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);self.fixture(root,True,lint_error=True);self.assertFalse(inspect_results(root,release=True)['passed'])
 def test_hybrid_snapshot_keeps_frontend_and_excludes_build_and_secrets(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'input';root.mkdir();target=Path(folder)/'copy';target.mkdir()
   for name in ['android/settings.gradle','android/build.gradle','android/app/build.gradle','src/app.js','assets/theme.css','index.html','artifacts/old.png','credentials/account.json','android/app/build/old.apk']:
    file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('fixture')
   _snapshot(root,target);self.assertTrue((target/'src/app.js').exists());self.assertTrue((target/'android/app/build.gradle').exists());self.assertFalse((target/'credentials').exists());self.assertFalse((target/'artifacts').exists());self.assertFalse((target/'android/app/build/old.apk').exists())
 def test_release_gate_is_not_a_general_zero_test_exception(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'source.txt').write_text('unchanged');tools=Tools(root,{'tools':['run_check']})
   raw={'passed':True,'tests_run':0,'checks_run':3,'verification_kind':'android-build-lint','exit_code':0,'cleanup':True,'timeout':False,'truncated':False,'output':''}
   with patch('android_tools.check.isolated_check',return_value=raw):
    self.assertTrue(tools.execute('run_check',{'name':'android-release'})['passed'])
    self.assertFalse(tools.execute('run_check',{'name':'android-gradle'})['passed'])
   raw.pop('checks_run')
   with patch('android_tools.check.isolated_check',return_value=raw):self.assertFalse(tools.execute('run_check',{'name':'android-release'})['passed'])
