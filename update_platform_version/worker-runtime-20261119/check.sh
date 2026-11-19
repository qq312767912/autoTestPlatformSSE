#!/usr/bin/env bash
set -euo pipefail
C=wharttest-backend
# 冷启动含迁移、管理员与 Skills 初始化，最多等待十分钟。
for ((i=0;i<60;i++)); do
 status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$C")
 if [[ "$status" == healthy ]]; then break; fi
 echo "等待后端健康：$status ($((i+1))/60)"
 sleep 10
done
[[ "$status" == healthy ]] || { docker logs --tail 100 "$C"; exit 1; }
# solo 执行任务时不响应 inspect；通过进程参数核对消费者。
timeout 20 docker exec "$C" python -c '
from pathlib import Path
commands=[]
for p in Path("/proc").glob("[0-9]*/cmdline"):
 try: commands.append(p.read_bytes().replace(b"\x00",b" ").decode())
 except (OSError,UnicodeError): pass
for name,queue in {"default":"celery","code_analysis":"code_analysis","testcase_review":"testcase_review","knowledge":"knowledge","ui_execution":"ui_execution","task_center":"task_center"}.items():
 assert any(" worker " in c and "-n "+name+"@" in c and "-Q "+queue+" " in c for c in commands), name
print("六个独立消费者进程检查通过")
'
docker top "$C" -eo pid,ppid,pcpu,pmem,etime,args
docker exec "$C" python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8001/api/auth/login-key/",timeout=15).status)'
docker exec wharttest-frontend sh -c 'wget -q -O /dev/null -T 15 http://127.0.0.1/api/auth/login-key/'
