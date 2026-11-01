#!/usr/bin/env python3
"""SDK/Lint/assinatura reais; não inicia emulador e não equivale a teste físico."""
from pathlib import Path
import argparse,subprocess,os,shutil,json,hashlib,re
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--sdk-root',default=os.environ.get('ANDROID_SDK_ROOT','/opt/horizon-mobile-app/android-sdk'))
p.add_argument('--gradle',default=os.environ.get('HORIZON_GRADLE','/opt/horizon-mobile-app/tools/gradle-8.9/bin/gradle'))
p.add_argument('--project-root',default=str(Path(__file__).resolve().parents[1]))
p.add_argument('--apk');p.add_argument('--package');p.add_argument('--lint',action='store_true');p.add_argument('--build',action='store_true');p.add_argument('--output')
a=p.parse_args();sdk=Path(a.sdk_root);root=Path(a.project_root);report={'physicalDeviceTested':False,'emulatorStarted':False,'checks':{}}
def run(label,cmd,timeout=60,env=None):
 result=subprocess.run([str(x) for x in cmd],capture_output=True,text=True,timeout=timeout,env=env)
 report['checks'][label]={'passed':result.returncode==0,'output':(result.stdout+result.stderr)[-5000:]}
 if result.returncode:raise RuntimeError(label+' falhou')
 return result.stdout+result.stderr
try:
 run('java',['java','-version']);run('adb',[sdk/'platform-tools/adb','version'])
 manager=next(sdk.glob('cmdline-tools/*/bin/sdkmanager'));run('sdkmanager',[manager,'--version'])
 run('gradle',[a.gradle,'--version'])
 build=sorted((sdk/'build-tools').iterdir(),key=lambda d:tuple(int(x) for x in d.name.split('.') if x.isdigit()))[-1]
 report['buildTools']=build.name;report['platforms']=sorted(p.name for p in (sdk/'platforms').iterdir())
 if a.lint or a.build:
  env={**os.environ,'ANDROID_SDK_ROOT':str(sdk),'ANDROID_HOME':str(sdk)}
  run('lintRelease',[a.gradle,'-p',root/'android','--no-daemon','--max-workers=2','-Dorg.gradle.jvmargs=-Xmx1536m','lintRelease'],timeout=1000,env=env)
 if a.build:run('signedBuild',['python3',root/'scripts/build-production-apk.py'],timeout=300)
 if a.apk:
  apk=Path(a.apk);report['apk']={'filename':apk.name,'bytes':apk.stat().st_size,'sha256':hashlib.sha256(apk.read_bytes()).hexdigest()}
  text=run('signature',[build/'apksigner','verify','--verbose','--print-certs',apk])
  badging=run('manifest',[build/'aapt2','dump','badging',apk]);report['apk']['packageLine']=next(x for x in badging.splitlines() if x.startswith('package:'))
  if a.package and "name='"+a.package+"'" not in report['apk']['packageLine']:raise RuntimeError('Pacote diferente do solicitado')
 report['passed']=True
except Exception as e:report['passed']=False;report['error']=str(e)
if a.output:Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['passed'] else 1)
