import hashlib,json,pathlib,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parent
REPO=ROOT.parent.parent
IMAGE='wharttest-250-actuator:update-20261119-89ffd9ef-r2-arm64'
for _ in range(360):
 if subprocess.run(['docker','image','inspect',IMAGE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
 if 'ERROR: failed to build' in (ROOT/'build-logs/actuator.log').read_text():raise SystemExit('Actuator构建失败')
 time.sleep(5)
else:raise SystemExit('等待Actuator镜像超时')
with (ROOT/'build-logs/actuator-smoke.log').open('w') as log:
 commands=[['docker','run','--rm','--entrypoint','sh',IMAGE,'-ec','cat /etc/alpine-release; ls /lib/ld-musl-aarch64.so.1; cat /etc/apk/repositories; /usr/local/bin/python3.12 -V; /usr/bin/node --version; /usr/bin/chromium-browser --version'],['docker','run','--rm','--shm-size=512m','--entrypoint','/usr/local/bin/python3.12','-v',str(ROOT/'deploy_env/smoke_actuator_browser.py')+':/tmp/smoke_actuator_browser.py:ro',IMAGE,'/tmp/smoke_actuator_browser.py']]
 for cmd in commands:subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=180)
paths=['executor.py','consumer.py','main.py','models.py','websocket_client.py','login_state_capture.py']
code='import hashlib,json,pathlib; print(json.dumps({p:hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest() for p in '+repr(paths)+'}))'
actual=json.loads(subprocess.check_output(['docker','run','--rm','--entrypoint','/usr/local/bin/python3.12',IMAGE,'-c',code],text=True))
for name,digest in actual.items():assert hashlib.sha256((REPO/'WHartTest_Actuator'/name).read_bytes()).hexdigest()==digest,name
(ROOT/'build-logs/actuator-source-hashes.json').write_text(json.dumps(actual,indent=2))
(ROOT/'build-logs/actuator-validation.complete').write_text(json.dumps({'image_id':subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True).strip(),'passed':True}))
print('Actuator完整镜像验证通过')
