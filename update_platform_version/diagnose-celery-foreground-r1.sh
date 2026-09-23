#!/usr/bin/env bash
# 在独立诊断队列以前台 DEBUG 模式启动 Celery，捕获启动阶段真实异常；不修改业务数据。
set -uo pipefail

CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
OUTPUT="${1:-celery-foreground-$(date '+%Y%m%d-%H%M%S').log}"
exec > >(tee "$OUTPUT") 2>&1

echo "[版本] diagnose-celery-foreground r1-20260923"
echo "[说明] 使用 celery_diagnostic 空队列和独立节点名，最长运行 50 秒。"

docker inspect "$CONTAINER" --format \
  'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} oom={{.State.OOMKilled}}' || true

echo "===== Celery 最终配置与 broker TCP/协议连接 ====="
docker exec "$CONTAINER" python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
from wharttest_django.celery import app
print("broker_url=", app.conf.broker_url)
print("broker_read_url=", app.conf.broker_read_url)
print("broker_write_url=", app.conf.broker_write_url)
conn = app.connection_for_read()
conn.ensure_connection(max_retries=0)
print("broker_connection=OK")
conn.release()
' || true

echo "===== 前台 Celery DEBUG（50 秒） ====="
set +e
docker exec "$CONTAINER" sh -c '
  timeout -s TERM 50 /opt/venv/bin/celery \
    -A wharttest_django \
    -b redis://redis:6379/0 \
    worker \
    -l DEBUG \
    --pool=solo \
    --concurrency=1 \
    --without-gossip \
    --without-mingle \
    --without-heartbeat \
    --hostname=diagnostic@%h \
    -Q celery_diagnostic
'
status=$?
set -e
echo "foreground_exit_status=$status"
case "$status" in
  124|143) echo "[结果] 前台 worker 持续运行到诊断超时，基础启动链正常。" ;;
  *) echo "[结果] 前台 worker 提前退出，以上 DEBUG 输出包含直接原因。" ;;
esac

echo "===== Supervisor 最近退出记录 ====="
docker logs --timestamps --tail 260 "$CONTAINER" || true
echo "请返回日志文件：$OUTPUT"
