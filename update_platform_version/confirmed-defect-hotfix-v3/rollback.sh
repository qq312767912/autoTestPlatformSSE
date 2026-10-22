#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
STATE="$DIR/latest-backup.txt"
[ -f "$STATE" ] || { echo "[失败] 找不到 $STATE" >&2; exit 1; }
BACKUP="$(sed -n '1p' "$STATE")"
[ -f "$BACKUP" ] || { echo "[失败] 找不到备份 $BACKUP" >&2; exit 1; }
docker cp "$BACKUP" "$CONTAINER:/app/code_analysis/services.py"
docker exec "$CONTAINER" /opt/venv/bin/python -m py_compile /app/code_analysis/services.py
docker restart "$CONTAINER" >/dev/null
echo "[完成] 已恢复 v3 应用前文件"
