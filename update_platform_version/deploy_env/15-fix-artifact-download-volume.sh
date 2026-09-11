#!/usr/bin/env bash
set -euo pipefail

PLATFORM_DIR="${PLATFORM_DIR:-/projects/ai-test-platform}"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
MEDIA_SOURCE="${PLATFORM_DIR}/offline-images/data/media"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/fix-artifact-download-$(date '+%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

fail() {
  echo "错误：$1" >&2
  exit 1
}

[ -f "$BASE_COMPOSE" ] || fail "找不到基础 Compose：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到更新 Compose：$UPDATE_COMPOSE"

# Backend 将 Skill 生成物写入 offline-images/data/media；Frontend/Nginx
# 必须只读挂载同一目录，才能通过受控媒体路由提供下载。
mkdir -p "$MEDIA_SOURCE"
chmod 775 "$MEDIA_SOURCE" 2>/dev/null || true

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
"${compose[@]}" config >/dev/null

echo "只重建 Frontend 以应用媒体卷，不重启 Backend、数据库或执行器。"
"${compose[@]}" up -d --no-deps --force-recreate frontend

for _ in $(seq 1 30); do
  state="$(docker inspect wharttest-frontend --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
  case "$state" in
    healthy|running) break ;;
    unhealthy|exited|dead) docker logs --tail 100 wharttest-frontend; fail "Frontend 状态异常：$state" ;;
  esac
  sleep 2
done

mounts="$(docker inspect wharttest-frontend --format '{{range .Mounts}}{{println .Source "|" .Destination "|" .RW}}{{end}}')"
echo "$mounts"
echo "$mounts" | grep -F -- "$MEDIA_SOURCE | /app/data/media | false" >/dev/null \
  || fail "Frontend 未以只读方式挂载共享媒体目录"

docker exec wharttest-frontend test -d /app/data/media \
  || fail "Frontend 容器内不存在 /app/data/media"

echo "文件下载媒体卷修复完成。日志：$LOG_FILE"
