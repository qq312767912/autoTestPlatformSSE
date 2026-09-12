#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"

[ -f "$UPDATE_DIR/.latest-backup" ] || { echo "未发现升级前备份，请先执行 02-backup.sh" >&2; exit 1; }
backup_dir="$(cat "$UPDATE_DIR/.latest-backup")"
[ -f "$backup_dir/BACKUP_COMPLETE" ] || { echo "最近一次备份不完整：$backup_dir" >&2; exit 1; }

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE")
"${compose[@]}" config >/dev/null

wait_healthy() {
  local container="$1"
  local limit="${2:-180}"
  local elapsed=0
  while [ "$elapsed" -lt "$limit" ]; do
    status="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
    case "$status" in
      healthy|running) echo "[通过] $container: $status"; return 0 ;;
      unhealthy|stopped) docker logs --tail 100 "$container"; return 1 ;;
    esac
    sleep 5
    elapsed=$((elapsed + 5))
  done
  docker logs --tail 100 "$container" || true
  echo "$container 健康检查超时" >&2
  return 1
}

echo "[升级] 替换 Backend 和 Frontend（Backend 入口脚本会执行数据库迁移）"
"${compose[@]}" up -d --no-deps backend frontend
wait_healthy wharttest-backend 300
wait_healthy wharttest-frontend 180

echo "Backend 和 Frontend 升级完成；Vision MCP、Actuator 均保持运行。"
echo "请执行 05-verify.sh，并进行登录、代码审查、用例审查等人工验收。"
