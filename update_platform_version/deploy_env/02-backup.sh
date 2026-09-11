#!/usr/bin/env bash
set -euo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date '+%Y%m%d-%H%M%S')"
BACKUP_DIR="$UPDATE_DIR/backups/$STAMP"
mkdir -p "$BACKUP_DIR"

echo "[备份] 保存当前容器及镜像基线"
docker ps -a --filter 'name=wharttest' --no-trunc > "$BACKUP_DIR/containers.txt"
for container in wharttest-backend wharttest-frontend wharttest-postgres wharttest-redis wharttest-qdrant wharttest-mcp wharttest-playwright-mcp wharttest-weixin-plugin-host; do
  docker inspect "$container" --format '{{.Name}}|{{.Config.Image}}|{{.Image}}' >> "$BACKUP_DIR/image-baseline.txt"
done

echo "[备份] PostgreSQL 逻辑备份"
docker exec wharttest-postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip -1 > "$BACKUP_DIR/postgres.sql.gz"
[ -s "$BACKUP_DIR/postgres.sql.gz" ] || { echo "PostgreSQL 备份失败" >&2; exit 1; }

data_source="$(docker inspect wharttest-backend --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}')"
[ -d "$data_source" ] || { echo "找不到 /app/data 宿主目录：$data_source" >&2; exit 1; }

stopped=()
restart_stopped() {
  if [ "${#stopped[@]}" -gt 0 ]; then
    docker start "${stopped[@]}" >/dev/null 2>&1 || true
  fi
}
trap restart_stopped EXIT

echo "[备份] 短暂停止写入 /app/data 的应用容器"
for container in wharttest-backend wharttest-mcp wharttest-weixin-plugin-host; do
  if [ "$(docker inspect "$container" --format '{{.State.Running}}')" = "true" ]; then
    docker stop -t 30 "$container" >/dev/null
    stopped+=("$container")
  fi
done

echo "[备份] 应用数据目录：$data_source"
tar -C "$(dirname "$data_source")" -czf "$BACKUP_DIR/app-data.tar.gz" "$(basename "$data_source")"
[ -s "$BACKUP_DIR/app-data.tar.gz" ] || { echo "应用数据备份失败" >&2; exit 1; }

restart_stopped
stopped=()
trap - EXIT

echo "$BACKUP_DIR" > "$UPDATE_DIR/.latest-backup"
echo "backup_complete=$STAMP" > "$BACKUP_DIR/BACKUP_COMPLETE"
echo "备份完成：$BACKUP_DIR"
