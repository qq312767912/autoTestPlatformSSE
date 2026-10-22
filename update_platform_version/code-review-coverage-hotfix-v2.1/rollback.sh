#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -gt 1 ]; then echo "用法: bash rollback.sh [备份目录]" >&2; exit 2; fi
if [ "$#" -eq 1 ]; then
  backup_dir="$1"
else
  [ -s "$PACKAGE_DIR/backups/latest-backup.txt" ] || { echo "[失败] 未找到回滚记录" >&2; exit 1; }
  backup_dir="$(sed -n '1p' "$PACKAGE_DIR/backups/latest-backup.txt")"
fi
[ -f "$backup_dir/services.py" ] || { echo "[失败] 备份文件不存在: $backup_dir/services.py" >&2; exit 1; }
[ -f "$backup_dir/container.txt" ] && [ -f "$backup_dir/mode.txt" ] && [ -f "$backup_dir/host-path.txt" ] || { echo "[失败] 备份元数据不完整" >&2; exit 1; }
backend_container="$(sed -n '1p' "$backup_dir/container.txt")"
target_mode="$(sed -n '1p' "$backup_dir/mode.txt")"
target_host_path="$(sed -n '1p' "$backup_dir/host-path.txt")"

if [ "$target_mode" = bind ]; then
  [ -f "$target_host_path" ] || { echo "[失败] 挂载目标不存在: $target_host_path" >&2; exit 1; }
  cp "$backup_dir/services.py" "$target_host_path"
elif [ "$target_mode" = container ]; then
  docker cp "$backup_dir/services.py" "$backend_container:/app/code_analysis/services.py" >/dev/null
else
  echo "[失败] 未识别的备份类型: $target_mode" >&2
  exit 1
fi
docker restart "$backend_container" >/dev/null
elapsed=0
while [ "$elapsed" -lt 180 ]; do
  health="$(docker inspect "$backend_container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  if [ "$health" = healthy ] || [ "$health" = running ]; then
    if docker exec "$backend_container" /opt/venv/bin/python /app/manage.py check --deploy >/dev/null 2>&1; then
      echo "[完成] 已恢复备份 $backup_dir，Backend 检查通过"
      exit 0
    fi
  fi
  sleep 3
  elapsed=$((elapsed + 3))
done
echo "[失败] 文件已恢复，但 Backend 未通过健康检查；请查看 docker logs $backend_container" >&2
exit 1
