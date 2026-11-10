#!/usr/bin/env bash
# Read only. Never execute the sync agent or print credentials.
set -u
umask 077
OUT="${1:-host-sync-diagnosis.log}"
{
 date -Iseconds
 echo '=== 容器状态 ==='
 docker ps -a --format '{{.Names}} | {{.Image}} | {{.Status}}' | grep wharttest || true
 echo '=== 同步服务状态 ==='
 systemctl --no-pager --full status wharttest-host-sync.service 2>&1 || true
 echo '=== 同步状态文件：仅提取结果，不输出域名配置 ==='
 python3 - <<'PY'
import json,pathlib
p=pathlib.Path('/var/lib/wharttest-host-sync/state.json')
try:
 d=json.loads(p.read_text())
 print('state_keys=',list(d) if isinstance(d,dict) else type(d).__name__)
 if isinstance(d,dict):
  for key in ('nodes','results'):
   rows=d.get(key,[])
   if isinstance(rows,list):
    for row in rows:
     if isinstance(row,dict):print({k:row.get(k) for k in ('node_id','node_type','status','message')})
except Exception as e:print(type(e).__name__,str(e))
PY
 echo '=== 最近服务日志 ==='
 journalctl -u wharttest-host-sync.service -n 30 --no-pager 2>&1 || true
 echo '=== 后端端口映射 ==='
 docker port wharttest-backend 2>&1 || true
 echo '=== 完成：未修改或重启服务，未读取/输出 Token ==='
} > "$OUT" 2>&1
printf '诊断结果：%s\n' "$OUT"
