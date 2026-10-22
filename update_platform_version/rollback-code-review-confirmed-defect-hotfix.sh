#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-$SCRIPT_DIR/backups/code-review-confirmed-defect}"
STATE_FILE="$BACKUP_ROOT/latest.env"

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$STATE_FILE" ] || fail "找不到热修复状态文件：$STATE_FILE"
mode="$(sed -n 's/^MODE=//p' "$STATE_FILE")"
backup="$(sed -n 's/^BACKUP=//p' "$STATE_FILE")"
target="$(sed -n 's/^TARGET=//p' "$STATE_FILE")"
container="$(sed -n 's/^CONTAINER=//p' "$STATE_FILE")"

[ -f "$backup" ] || fail "找不到备份文件：$backup"
docker inspect "$container" >/dev/null 2>&1 || fail "找不到 Backend 容器：$container"

if [ "$mode" = "bind" ]; then
  [ -n "$target" ] || fail "状态文件缺少宿主机目标路径"
  cp "$backup" "$target"
elif [ "$mode" = "container" ]; then
  docker cp "$backup" "$container:/app/code_analysis/services.py"
else
  fail "未知热修复模式：$mode"
fi

docker exec "$container" /opt/venv/bin/python -m py_compile /app/code_analysis/services.py
docker restart "$container" >/dev/null

elapsed=0
while [ "$elapsed" -lt 300 ]; do
  health="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  case "$health" in
    healthy|running) break ;;
    unhealthy|stopped)
      docker logs --tail 150 "$container" 2>&1 || true
      fail "Backend 状态异常：$health"
      ;;
  esac
  sleep 3
  elapsed=$((elapsed + 3))
done
[ "$elapsed" -lt 300 ] || fail "Backend 未在 300 秒内恢复运行"
echo "[完成] 已恢复热修复前的 services.py。"
