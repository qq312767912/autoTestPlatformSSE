#!/usr/bin/env bash
# Read-only: no restart, stop, kill, or backup writes.
set -u
ROOT="${1:-/projects/ai-test-platform/update_platform_version}"
OUT="${2:-backup-progress.log}"
umask 077
{
 echo '=== 02 备份进度诊断（只读） ==='
 date -Iseconds
 echo "升级目录：$ROOT"
 echo '=== 磁盘与 inode ==='
 df -h "$ROOT"
 df -i "$ROOT"
 echo '=== 应用容器状态 ==='
 docker ps -a --format '{{.Names}} | {{.Status}}' | grep wharttest || true
 echo '=== /app/data 挂载源 ==='
 docker inspect wharttest-backend --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' 2>&1
 echo '=== 连续采样：共 3 次，间隔 10 秒 ==='
 for sample in 1 2 3; do
  echo "--- SAMPLE $sample ---"
  date -Iseconds
  echo '[备份进程：PID / PPID / 已运行时间 / 状态 / CPU / 等待点 / 命令]'
  ps -eo pid,ppid,etime,stat,pcpu,wchan:25,args | grep -E '[0]2-backup|[p]g_dump|[g]zip|[t]ar -|[d]ocker stop' || true
  echo '[最近备份目录中的文件字节数与完成标记]'
  python3 - "$ROOT" <<'PY'
import pathlib,sys,datetime
root=pathlib.Path(sys.argv[1])/'backups'
if not root.is_dir():
 print('未找到默认备份目录；若使用 BACKUP_ROOT，请另外检查该目录。')
else:
 dirs=sorted((p for p in root.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime,reverse=True)[:3]
 for p in dirs:
  print('DIRECTORY',p,'COMPLETE=',(p/'BACKUP_COMPLETE').exists())
  for name in ('postgres.sql.gz','app-data.tar.gz','BACKUP_COMPLETE'):
   f=p/name
   try:
    s=f.stat();print('FILE',name,'bytes=',s.st_size,'mtime=',datetime.datetime.fromtimestamp(s.st_mtime).isoformat())
   except FileNotFoundError:print('FILE',name,'NOT_CREATED')
PY
  if [ "$sample" != 3 ]; then sleep 10; fi
 done
 echo '=== 宿主机 I/O 概况 ==='
 if command -v vmstat >/dev/null 2>&1; then vmstat 1 3; fi
 echo '=== PostgreSQL pg_dump 进程（不查询数据库、不输出 SQL） ==='
 docker top wharttest-postgres -eo pid,ppid,etime,stat,pcpu,args 2>&1 | grep -E 'PID|[p]g_dump' || true
 echo '=== 检测结束 ==='
 echo '文件增长表示备份仍在推进；20 秒不增长不能单独认定卡死。'
 echo '本脚本未修改服务、配置或备份文件；未扫描业务代码。'
} > "$OUT" 2>&1
printf '诊断完成：%s\n' "$OUT"
