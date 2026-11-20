#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
B="${1:-$(cat "$HERE/.latest-backup")}"
python3 "$HERE/prepare_assets.py" "$B" "$HERE"
python3 - "$B/compose.runtime.json" <<'PY'
import json,subprocess,sys
c=json.load(open(sys.argv[1]))
for key in ['actuator-01','actuator-02','actuator-03']:
 s=c['services'][key];env=s['environment'];target=env['WHARTTEST_ACTUATOR_API_PASSWORD_FILE'];m=next(m for m in s['volumes'] if m['target']==target)
 # Compose文件中的$$在真正启动时变为$，这里同样还原。
 username=env.get('WHARTTEST_ACTUATOR_API_USERNAME','admin').replace('$$','$')
 password=open(m['source'].replace('$$','$')).read().strip()
 code='import json,sys,urllib.request; p=json.load(sys.stdin); r=urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/api/token/",data=json.dumps(p).encode(),headers={"Content-Type":"application/json"}),timeout=20); b=json.load(r); assert (b.get("data") or b).get("access"), "未返回Token"; print("执行器凭据验证通过")'
 subprocess.run(['docker','exec','-i','wharttest-backend','python','-c',code],input=json.dumps({'username':username,'password':password}),text=True,check=True)
PY
project=$(cat "$B/project")
for n in 01 02 03; do
 docker compose -p "$project" -f "$B/compose.runtime.json" up -d --no-deps --pull never "actuator-$n"
 wait_healthy "wharttest-actuator-$n"
done
