import concurrent.futures,json,pathlib,subprocess,time,urllib.request,urllib.error,os
base=pathlib.Path(__file__).resolve().parent

def docker(*args,check=True):
 result=subprocess.run(['docker',*args],check=check,capture_output=True,text=True)
 return (result.stdout+result.stderr if args[0]=='logs' else result.stdout).strip()
info=json.loads(docker('inspect',os.environ.get('TEST_BACKEND_CONTAINER','wharttest-new-backend')))[0]
pg_info=json.loads(docker('inspect',os.environ.get('TEST_POSTGRES_CONTAINER','wharttest-new-postgres')))[0]
network=next(n for n in info['NetworkSettings']['Networks'] if n in pg_info['NetworkSettings']['Networks'])
auth_code='import os,json; os.environ.setdefault("DJANGO_SETTINGS_MODULE","wharttest_django.settings"); import django; django.setup(); from django.contrib.auth import get_user_model; from rest_framework_simplejwt.tokens import AccessToken; from projects.models import Project; project=Project.objects.first(); assert project; u=get_user_model().objects.filter(is_superuser=True,is_active=True).first(); assert u; print(json.dumps({"token":str(AccessToken.for_user(u)),"project_id":project.pk}))'
auth_output=docker('exec',os.environ.get('TEST_BACKEND_CONTAINER','wharttest-new-backend'),'python','-c',auth_code)
auth=next(json.loads(line) for line in auth_output.splitlines() if line.startswith('{'))

config=pathlib.Path('WHartTest_Vue/nginx.conf').read_text().replace('http://backend:', 'http://runtime-stage-backend:')
(base/'nginx.stage.conf').write_text(config)
for n in ['runtime-stage-frontend','runtime-stage-backend']:docker('rm','-f',n,check=False)
args=['run','-d','--name','runtime-stage-backend','--network',network]
for e in info['Config']['Env']:
 if not e.startswith('WHARTTEST_LOGS_DIR='):args+=['-e',e]
args+=['-e','WHARTTEST_LOGS_DIR=/tmp/runtime-stage-logs','--entrypoint','/bin/sh','wharttest-250-backend:update-20261119-89ffd9ef-r2-arm64','-c','uvicorn wharttest_django.asgi:application --host 0.0.0.0 --port 8000 --workers 1 --lifespan on & uvicorn wharttest_django.asgi:application --host 0.0.0.0 --port 8001 --workers 3 --lifespan on & wait']
try:
 docker(*args)
 docker('run','-d','--name','runtime-stage-frontend','--network',network,'-p','127.0.0.1:18913:80','-v',str(base/'nginx.stage.conf')+':/etc/nginx/conf.d/default.conf:ro', 'wharttest-250-frontend:update-20261119-89ffd9ef-r2-arm64')
 def request(path,timeout=10):
  start=time.monotonic()
  try:
   with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18913'+path,headers={'Authorization':'Bearer '+auth['token']}),timeout=timeout) as r: code=r.status;r.read()
  except urllib.error.HTTPError as e:code=e.code;e.read()
  return {'path':path,'status':code,'seconds':round(time.monotonic()-start,3)}
 for _ in range(90):
  try:
   if request('/api/auth/login-key/',2)['status']==200:break
  except Exception:pass
  time.sleep(1)
 else:raise AssertionError('staging startup failed')
 docker('exec','runtime-stage-frontend','nginx','-t')
 report={'baseline':request('/api/lg/chat/sessions/?project_id='+str(auth['project_id']))}
 assert report['baseline']['status']==200,report
 script="""import os,signal,pathlib
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  a=p.read_bytes().decode().split('\\0')
  if '--port' in a and a[a.index('--port')+1]=='8000' and any('uvicorn' in x for x in a[:2]):
   os.kill(int(p.parent.name),signal.SIGSTOP);print(p.parent.name)
 except (OSError,UnicodeError):pass
"""
 paused=docker('exec','runtime-stage-backend','python','-c',script).splitlines();assert paused
 try:
  try:request('/api/lg/chat/sessions/?project_id='+str(auth['project_id']),2)
  except (TimeoutError,OSError):report['stateful_suspended']=True
  else:raise AssertionError('stateful request did not block')
  paths=['/api/auth/login-key/','/api/projects/','/api/accounts/users/']
  with concurrent.futures.ThreadPoolExecutor(max_workers=35) as pool:
   results=list(pool.map(lambda i:request(paths[i%3]),range(35)))
  assert all(x['status']==200 for x in results),results
  report['concurrency']=35;report['success']=len(results);report['max_seconds']=max(x['seconds'] for x in results);report['requests']=results
 finally:
  for pid in paused:docker('exec','runtime-stage-backend','kill','-CONT',pid)
 (base/'release-integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in report.items() if k!='requests'},ensure_ascii=False))
finally:
 for n in ['runtime-stage-frontend','runtime-stage-backend']:
  (base/(n+'.log')).write_text(docker('logs',n,check=False))
  docker('rm','-f',n,check=False)
