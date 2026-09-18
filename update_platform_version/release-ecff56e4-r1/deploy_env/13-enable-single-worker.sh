#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
SUPERVISOR_CONFIG="$UPDATE_DIR/supervisord.single-worker.conf"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/enable-single-worker-$(date '+%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

echo "后端单 worker 切换日志：$LOG_FILE"
[ -f "$BASE_COMPOSE" ] || { echo "找不到基础 YAML：$BASE_COMPOSE" >&2; exit 1; }
[ -f "$OVERRIDE_COMPOSE" ] || { echo "找不到升级 YAML：$OVERRIDE_COMPOSE" >&2; exit 1; }
[ -f "$SUPERVISOR_CONFIG" ] || { echo "找不到 Supervisor 配置：$SUPERVISOR_CONFIG" >&2; exit 1; }

grep -q -- '--workers=1' "$SUPERVISOR_CONFIG" || {
  echo "Supervisor 配置不是单 worker，拒绝执行" >&2
  exit 1
}

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE")
"${compose[@]}" config >/dev/null

echo "只重建 backend 容器..."
"${compose[@]}" up -d --no-deps --force-recreate backend

echo "等待后端 HTTP 服务真正就绪（最长 5 分钟）..."
backend_ready=0
for attempt in $(seq 1 60); do
  if docker exec wharttest-backend python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/admin/login/', timeout=3)" \
    >/dev/null 2>&1; then
    backend_ready=1
    echo "后端已就绪，共等待 $((attempt * 5)) 秒。"
    break
  fi
  sleep 5
done

if [ "$backend_ready" != "1" ]; then
  echo "后端 5 分钟内未就绪，最近 120 行日志：" >&2
  docker logs --tail 120 wharttest-backend 2>&1 || true
  exit 1
fi

echo "后端容器状态："
docker inspect wharttest-backend --format 'status={{.State.Status}} restarts={{.RestartCount}} image={{.Config.Image}}'

echo "Supervisor 实际配置："
docker exec wharttest-backend sh -c "grep -n 'command=uvicorn' /app/supervisord.conf"

echo "Uvicorn 进程："
docker exec wharttest-backend sh -c "ps -ef | grep '[u]vicorn' || true"

echo "重启三个执行器，使其立即注册到新的单 worker 后端..."
for index in 01 02 03; do
  docker restart "wharttest-actuator-$index"
done
sleep 15

echo "执行器容器状态："
for index in 01 02 03; do
  container="wharttest-actuator-$index"
  docker inspect "$container" --format '{{.Name}} status={{.State.Status}} restarts={{.RestartCount}}' || true
  docker logs --since 30s "$container" 2>&1 | grep -E '已连接到服务器|已发送执行器信息|连接失败|尝试重连' | tail -10 || true
done

echo "单 worker 切换完成。请刷新执行器页面验证。"
