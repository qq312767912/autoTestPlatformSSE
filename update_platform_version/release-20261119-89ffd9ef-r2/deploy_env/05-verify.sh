#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
for c in wharttest-backend wharttest-frontend wharttest-semgrep wharttest-crg wharttest-actuator-{01,02,03}; do
 wait_healthy "$c"
 [[ "$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$c")" == "$REV" ]]
done
docker exec wharttest-frontend nginx -t
docker exec wharttest-frontend sh -ec 'wget -q -O /dev/null -T 30 http://127.0.0.1/api/auth/login-key/'
docker exec wharttest-backend python -c '
import time
from wharttest_django.celery import app
expected={"default":4,"knowledge":2,"task_center":4,"ui_execution":4,"code_analysis":1,"testcase_review":1}
for attempt in range(12):
 r=app.control.inspect(timeout=15).stats() or {}
 actual={k.split("@")[0]:v["pool"].get("max-concurrency") for k,v in r.items()}
 if all(actual.get(k)==v for k,v in expected.items()):break
 time.sleep(5)
else:raise AssertionError(actual)
print(actual)
'
echo '健康、版本、前端代理和任务并发配置检查通过。'
expected=$(python3 - <<'PY'
import json,subprocess
names=['wharttest-actuator-%02d'%i for i in (1,2,3)]
objects=json.loads(subprocess.check_output(['docker','inspect']+names))
ids=[]
for obj in objects:
 env=dict(e.split('=',1) for e in obj['Config']['Env'] if '=' in e)
 assert env.get('WHARTTEST_ACTUATOR_ID'), '执行器未配置固定ID：'+obj['Name']
 ids.append(env['WHARTTEST_ACTUATOR_ID'])
print(json.dumps(ids))
PY
)
docker exec -i wharttest-backend python - "$expected" <<'PY'
import json,sys,time,urllib.request
expected=set(json.loads(sys.argv[1])); last=[]
for attempt in range(36):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000/api/ui-automation/actuators/list_actuators/',timeout=10) as r:
   last=json.load(r)['data']['items']
  online={str(a['id']) for a in last if a.get('is_open',True)}
  if expected <= online:
   print('三个执行器已通过WebSocket连接后端：',sorted(expected))
   break
 except Exception as exc:
  if attempt==35:raise
 time.sleep(5)
else:raise AssertionError('执行器未全部连接后端，在线ID='+str([a.get('id') for a in last]))
PY
