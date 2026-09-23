#!/usr/bin/env bash
# 修复麒麟 ARM64/Alpine 环境中 Celery prefork 多进程反复退出并拖垮 Backend 健康检查。
set -euo pipefail

FIX_VERSION="r4-read-write-broker-20260923"
echo "[版本] fix-backend-celery-redis-r4 ${FIX_VERSION}"

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
DEPLOY_DIR="${PLATFORM_DIR}/update_platform_version/deploy_env"
UPDATE_COMPOSE="${DEPLOY_DIR}/docker-compose.update.yml"
SUPERVISOR_CONFIG="${DEPLOY_DIR}/supervisord.single-worker.conf"

fail() { echo "[失败] $*" >&2; exit 1; }
celery_worker_pid() {
  docker exec wharttest-backend sh -c '
    for cmdline in /proc/[0-9]*/cmdline; do
      command_line=$(tr "\000" " " < "$cmdline" 2>/dev/null || true)
      case "$command_line" in
        "/opt/venv/bin/python /opt/venv/bin/celery -A wharttest_django -b redis://redis:6379/0 worker "*)
          basename "$(dirname "$cmdline")"
          exit 0
          ;;
      esac
    done
    exit 1
  '
}
[ -f "$BASE_COMPOSE" ] || fail "找不到 $BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到 $UPDATE_COMPOSE"
[ -f "$SUPERVISOR_CONFIG" ] || fail "找不到 $SUPERVISOR_CONFIG"
grep -q -- '--pool=solo --concurrency=1' "$SUPERVISOR_CONFIG" || \
  fail "$SUPERVISOR_CONFIG 不是本次修复后的 solo 配置，请先上传新版 deploy_env.zip"
grep -q -- '-b redis://redis:6379/0 worker' "$SUPERVISOR_CONFIG" || \
  fail "$SUPERVISOR_CONFIG 未显式指定无密码 Redis broker，请先上传新版 deploy_env.zip"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
"${compose[@]}" config >/dev/null

echo "[修复] 仅重建 Backend；数据库、Redis、Qdrant、前端和执行器均保持不动..."
"${compose[@]}" up -d --pull never --no-deps --force-recreate backend

echo "[等待] Backend HTTP 和 Docker 健康检查..."
ready=0
for _ in $(seq 1 90); do
  state="$(docker inspect wharttest-backend --format '{{.State.Status}}' 2>/dev/null || true)"
  health="$(docker inspect wharttest-backend --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
  if [ "$state" = running ] && docker exec wharttest-backend python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/admin/login/', timeout=3)" \
      >/dev/null 2>&1; then
    ready=1
    break
  fi
  case "$state/$health" in
    exited/*|dead/*)
      docker logs --tail 160 wharttest-backend >&2 || true
      fail "Backend 状态异常: $state/$health"
      ;;
  esac
  sleep 2
done
[ "$ready" = 1 ] || { docker logs --tail 160 wharttest-backend >&2 || true; fail "Backend 启动超时"; }

echo "[验证] Celery 使用 solo 单进程并持续稳定 45 秒..."
docker exec wharttest-backend sh -c "grep -q -- '--pool=solo --concurrency=1' /app/supervisord.conf"
docker exec wharttest-backend sh -c "grep -q -- '-b redis://redis:6379/0 worker' /app/supervisord.conf"
docker exec wharttest-backend python -c '
import os
expected = "redis://redis:6379/0"
for name in ("CELERY_BROKER_URL", "CELERY_BROKER_READ_URL", "CELERY_BROKER_WRITE_URL"):
    actual = os.environ.get(name)
    assert actual == expected, f"{name}={actual!r}, expected={expected!r}"
    print(f"{name}={actual}")
'
docker exec wharttest-backend python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
from wharttest_django.celery import app
expected = "redis://redis:6379/0"
values = {
    "broker_url": app.conf.broker_url,
    "broker_read_url": app.conf.broker_read_url,
    "broker_write_url": app.conf.broker_write_url,
}
for name, actual in values.items():
    assert actual == expected, f"Celery {name}={actual!r}, expected={expected!r}"
    print(f"Celery {name}={actual}")
'
pid_before="$(celery_worker_pid || true)"
case "$pid_before" in
  ''|0|*[!0-9]*)
    docker logs --timestamps --tail 240 wharttest-backend >&2 || true
    docker exec wharttest-backend sh -c 'echo "===== worker_out.log ====="; tail -n 200 /app/data/logs/worker_out.log; echo "===== worker_err.log ====="; tail -n 200 /app/data/logs/worker_err.log' >&2 || true
    fail "Celery worker 未运行，PID=$pid_before"
    ;;
esac
sleep 45
pid_after="$(celery_worker_pid || true)"
[ "$pid_after" = "$pid_before" ] || {
  docker logs --timestamps --tail 240 wharttest-backend >&2 || true
  docker exec wharttest-backend sh -c 'echo "===== worker_out.log ====="; tail -n 200 /app/data/logs/worker_out.log; echo "===== worker_err.log ====="; tail -n 200 /app/data/logs/worker_err.log' >&2 || true
  fail "Celery worker 在验证期间重启: $pid_before -> $pid_after"
}

health="$(docker inspect wharttest-backend --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
[ "$health" = healthy ] || [ "$health" = starting ] || fail "Backend 健康状态仍异常: $health"
docker exec wharttest-backend sh -c 'tail -n 120 /app/data/logs/worker_out.log | grep -q "ready\."' || {
  docker exec wharttest-backend sh -c 'tail -n 200 /app/data/logs/worker_out.log; tail -n 200 /app/data/logs/worker_err.log' >&2 || true
  fail "Celery worker 未进入 ready 状态"
}
docker exec wharttest-backend getent hosts www.test.sse.com.cn >/dev/null || \
  fail "Backend 缺少 www.test.sse.com.cn 域名映射，请再执行 fix-ui-test-dns.sh"

echo "[完成] Backend HTTP 正常，Celery solo worker PID=$pid_after，状态=$health。"
echo "现在可以重新执行 UI 自动化用例。"
