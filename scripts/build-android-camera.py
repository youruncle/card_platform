#!/usr/bin/env python3
"""Build a minimal personally signed USB-only camera APK; never install it."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,zipfile
root=Path(__file__).resolve().parents[1]
sdk=Path(os.environ.get('ANDROID_SDK_ROOT',str(Path.home()/'Library/Android/sdk')))
java=Path('/Applications/Android Studio.app/Contents/jbr/Contents/Home')
tools=sdk/'build-tools/36.1.0';android=sdk/'platforms/android-36.1/android.jar'
out=root/'.runtime/redmi-zoom-20261030/android-camera';out.mkdir(parents=True,exist_ok=True)
for folder in ['classes','dex']:
 if (out/folder).exists():shutil.rmtree(out/folder)
 (out/folder).mkdir()
env={**os.environ,'JAVA_HOME':str(java)}
def run(args):
 r=subprocess.run([str(a) for a in args],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode:
  raise RuntimeError(r.stderr.decode(errors='replace'))
run([java/'bin/javac','-source','8','-target','8','-bootclasspath',str(android)+os.pathsep+str(tools/'core-lambda-stubs.jar'),'-d',out/'classes',*sorted((root/'tools/android-camera/src').rglob('*.java'))])
run([tools/'d8','--min-api','30','--lib',android,'--output',out/'dex',*sorted((out/'classes').rglob('*.class'))])
run([tools/'aapt2','link','--manifest',root/'tools/android-camera/AndroidManifest.xml','-I',android,'-o',out/'base.apk'])
with zipfile.ZipFile(out/'base.apk','a',compression=zipfile.ZIP_DEFLATED) as z:z.write(out/'dex/classes.dex','classes.dex')
run([tools/'zipalign','-p','-f','4',out/'base.apk',out/'aligned.apk'])
keys=Path.home()/'Library/Application Support/CardPlatform/AndroidCamera';keys.mkdir(parents=True,exist_ok=True,mode=0o700);key=keys/'development.p12'
if not key.exists():
 run([java/'bin/keytool','-genkeypair','-keystore',key,'-storetype','PKCS12','-storepass','android','-keypass','android','-alias','cardsp-usb','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=CardSP USB Camera Development'])
 key.chmod(0o600)
apk=out/'CardSP-USB-Camera-1.0.3.apk'
run([tools/'apksigner','sign','--ks',key,'--ks-pass','pass:android','--out',apk,out/'aligned.apk'])
run([tools/'apksigner','verify','--verbose',apk])
permissions=subprocess.check_output([str(tools/'aapt'),'dump','permissions',str(apk)],text=True)
assert "android.permission.CAMERA" in permissions and 'android.permission.INTERNET' not in permissions and 'RECORD_AUDIO' not in permissions and 'STORAGE' not in permissions
receipt={'apk':str(apk),'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'bytes':apk.stat().st_size,'permissions':['android.permission.CAMERA'],'internet_permission':False,'signature_verified':True,'installed':False}
(out/'artifact.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
