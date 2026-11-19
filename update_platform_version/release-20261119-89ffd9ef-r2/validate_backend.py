import hashlib,json,pathlib,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parent
REPO=ROOT.parent.parent
IMAGE='wharttest-250-backend:update-20261119-89ffd9ef-r2-arm64'
for _ in range(180):
 if subprocess.run(['docker','image','inspect',IMAGE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
 if 'ERROR: failed to build' in (ROOT/'build-logs/backend.log').read_text():raise SystemExit('Backend构建失败')
 time.sleep(5)
else:raise SystemExit('等待Backend镜像超时')
with (ROOT/'build-logs/backend-smoke.log').open('w') as log:
 commands=[['docker','run','--rm','--entrypoint','sh',IMAGE,'-ec','cat /etc/alpine-release; ls /lib/ld-musl-aarch64.so.1; cat /etc/apk/repositories; python -V; node --version; command -v ocr; ocr --version; npm ls -g --depth=0 @alibaba-group/open-code-review; python manage.py check'],['docker','run','--rm','--shm-size=512m','--entrypoint','node','-v',str(ROOT/'deploy_env/smoke_browser.js')+':/tmp/smoke_browser.js:ro',IMAGE,'/tmp/smoke_browser.js']]
 for cmd in commands:subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=180)
paths=['wharttest_django/asgi.py','wharttest_django/web_runtime.py','wharttest_django/settings.py','supervisord.conf','code_analysis/tasks.py','code_analysis/services.py','testcases/review_tasks.py','testcases/review_service.py','ui_automation/tasks.py','accounts/login_crypto.py']
code='import hashlib,json,pathlib; print(json.dumps({p:hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest() for p in '+repr(paths)+'}))'
actual=json.loads(subprocess.check_output(['docker','run','--rm','--entrypoint','python',IMAGE,'-c',code],text=True))
for name,digest in actual.items():assert hashlib.sha256((REPO/'WHartTest_Django'/name).read_bytes()).hexdigest()==digest,name
(ROOT/'build-logs/backend-source-hashes.json').write_text(json.dumps(actual,indent=2))
# 镜像不打包测试文件，只在验证容器中只读注入测试代码。
command=['docker','run','--rm','--entrypoint','python','-e','DATABASE_TYPE=sqlite','-e','WHARTTEST_LOGS_DIR=/tmp/validation-logs']
for relative in ['wharttest_django/test_web_runtime.py','knowledge/test_worker_concurrency.py']:
 command+=['-v',str(REPO/'WHartTest_Django'/relative)+':/app/'+relative+':ro']
command += [IMAGE,'manage.py','test','wharttest_django.test_web_runtime','knowledge.test_worker_concurrency','--verbosity','2']
with (ROOT/'build-logs/backend-runtime-tests.log').open('w') as log:subprocess.run(command,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=180)
(ROOT/'build-logs/backend-validation.complete').write_text(json.dumps({'image_id':subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True).strip(),'passed':True}))
print('Backend完整镜像验证通过')
