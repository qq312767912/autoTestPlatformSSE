#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="$SCRIPT_DIR/backups/latest.env"
[ -f "$STATE" ] || { echo "[失败] 没有找到可回滚记录：$STATE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$STATE"
[ -f "$BACKUP_DIR/backend/services.py" ] || { echo "[失败] services.py 备份不存在" >&2; exit 1; }
[ -f "$BACKUP_DIR/backend/views.py" ] || { echo "[失败] views.py 备份不存在" >&2; exit 1; }
[ -f "$BACKUP_DIR/frontend-html.tar.gz" ] || { echo "[失败] 前端备份不存在" >&2; exit 1; }

if [ "$BACKEND_MODE" = "bind" ]; then
  cp "$BACKUP_DIR/backend/services.py" "$SERVICES_TARGET"
  cp "$BACKUP_DIR/backend/views.py" "$VIEWS_TARGET"
else
  docker cp "$BACKUP_DIR/backend/services.py" "$BACKEND_CONTAINER:/app/code_analysis/services.py"
  docker cp "$BACKUP_DIR/backend/views.py" "$BACKEND_CONTAINER:/app/code_analysis/views.py"
fi

if [ "$FRONTEND_MODE" = "bind" ]; then
  find "$FRONTEND_TARGET" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -C "$FRONTEND_TARGET" -xzf "$BACKUP_DIR/frontend-html.tar.gz"
else
  docker cp "$BACKUP_DIR/frontend-html.tar.gz" "$FRONTEND_CONTAINER:/tmp/frontend-rollback.tar.gz"
  docker exec "$FRONTEND_CONTAINER" sh -c 'find /usr/share/nginx/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -C /usr/share/nginx/html -xzf /tmp/frontend-rollback.tar.gz && rm -f /tmp/frontend-rollback.tar.gz'
fi

docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -m py_compile /app/code_analysis/services.py /app/code_analysis/views.py
docker restart "$BACKEND_CONTAINER" "$FRONTEND_CONTAINER" >/dev/null
echo "[完成] 已回滚到应用热修复前的版本：$BACKUP_DIR"
