#!/usr/bin/env bash
set -euo pipefail
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
BACKUP_DIR="${1:-}"
[ -f "$BACKUP_DIR/BACKUP_COMPLETE" ] && [ -s "$BACKUP_DIR/image-baseline.txt" ] || { echo '用法：bash 06-rollback-app.sh 完整备份目录'; exit 1; }
echo '仅回退应用镜像；请先确认数据库迁移兼容。数据库和技能备份不会自动恢复。'
read -r -p '已核对数据库兼容性？输入 YES：' answer
[ "$answer" = YES ] || exit 1
rollback="$BACKUP_DIR/rollback-images.yml"
printf 'services:\n' > "$rollback"
services=()
new_services=()
for pair in backend:wharttest-backend frontend:wharttest-frontend semgrep-scanner:wharttest-semgrep crg-service:wharttest-crg; do
 service="${pair%%:*}"; container="${pair#*:}"
 image_id="$(awk -F '|' -v name="/$container" '$1==name {print $3}' "$BACKUP_DIR/image-baseline.txt")"
 if [ -z "$image_id" ]; then
  case "$service" in crg-service|semgrep-scanner) new_services+=("$service"); continue ;; *) echo "缺少 $container 原镜像 ID" >&2; exit 1 ;; esac
 fi
 services+=("$service")
 docker image inspect "$image_id" >/dev/null
 printf '  %s:\n    image: %s\n' "$service" "$image_id" >> "$rollback"
done
export CRG_INTERNAL_TOKEN="$(cat "$UPDATE_DIR/secrets/crg_internal_token")"
docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_DIR/docker-compose.update.yml" -f "$rollback" up -d --pull never --no-deps "${services[@]}"
if [ "${#new_services[@]}" -gt 0 ]; then
 docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_DIR/docker-compose.update.yml" stop "${new_services[@]}"
fi
echo '原镜像已恢复，保留新 compose 配置。若需完整恢复配置/数据库/技能，使用备份并人工核对。'
