#!/usr/bin/env bash
set -euo pipefail

PLATFORM_DIR="${PLATFORM_DIR:-/projects/ai-test-platform}"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${UPDATE_COMPOSE:-${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.update.yml}"
MEDIA_SOURCE="${MEDIA_SOURCE:-${PLATFORM_DIR}/offline-images/data/media}"
FIX_COMPOSE="${FIX_COMPOSE:-${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.media-fix.yml}"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-wharttest-frontend}"
LOG_FILE="${PLATFORM_DIR}/update_platform_version/fix-artifact-download-$(date +%Y%m%d-%H%M%S).log"

exec > >(tee "$LOG_FILE") 2>&1

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

printf '开始修复 Skill 生成文件下载所需的前端媒体卷。\n'
printf '平台目录: %s\n' "$PLATFORM_DIR"
printf '基础 Compose: %s\n' "$BASE_COMPOSE"
printf '升级 Compose: %s\n' "$UPDATE_COMPOSE"
printf '媒体目录: %s\n' "$MEDIA_SOURCE"
printf '覆盖 Compose: %s\n' "$FIX_COMPOSE"

command -v docker >/dev/null 2>&1 || fail '未找到 docker 命令'
docker compose version >/dev/null 2>&1 || fail 'docker compose 不可用'
[ -f "$BASE_COMPOSE" ] || fail "基础 Compose 文件不存在: $BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "升级 Compose 文件不存在: $UPDATE_COMPOSE"

mkdir -p "$MEDIA_SOURCE"
chmod 775 "$MEDIA_SOURCE" 2>/dev/null || true
mkdir -p "$(dirname "$FIX_COMPOSE")"

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT
printf '%s\n' \
  'services:' \
  '  frontend:' \
  '    volumes:' \
  "      - ${MEDIA_SOURCE}:/app/data/media:ro" > "$tmp_file"
mv "$tmp_file" "$FIX_COMPOSE"
trap - EXIT

printf '\n已生成媒体卷覆盖配置：\n'
sed -n '1,20p' "$FIX_COMPOSE"

printf '\n校验 Compose 合并结果：\n'
docker compose \
  -f "$BASE_COMPOSE" \
  -f "$UPDATE_COMPOSE" \
  -f "$FIX_COMPOSE" \
  config >/dev/null

printf '\n只重新创建前端容器，不修改后端、数据库和数据卷：\n'
docker compose \
  -f "$BASE_COMPOSE" \
  -f "$UPDATE_COMPOSE" \
  -f "$FIX_COMPOSE" \
  up -d --no-deps --force-recreate frontend

printf '\n等待前端容器启动：\n'
for _ in $(seq 1 20); do
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$FRONTEND_CONTAINER" 2>/dev/null || true)"
  printf '%s: %s\n' "$FRONTEND_CONTAINER" "${state:-不存在}"
  if [ "$state" = 'healthy' ] || [ "$state" = 'running' ]; then
    break
  fi
  sleep 3
done

printf '\n验证前端挂载：\n'
mounts="$(docker inspect "$FRONTEND_CONTAINER" --format '{{range .Mounts}}{{println .Source "->" .Destination "| RW=" .RW}}{{end}}')"
printf '%s\n' "$mounts"
printf '%s\n' "$mounts" | grep -F -- "$MEDIA_SOURCE -> /app/data/media" >/dev/null \
  || fail '前端媒体卷未生效，请把本日志发回分析'

printf '\n验证前端容器媒体目录：\n'
docker exec "$FRONTEND_CONTAINER" sh -lc '
  ls -ld /app/data/media
  find /app/data/media/skill_runtime -type f 2>/dev/null | tail -20 || true
'

printf '\n验证 Nginx 媒体路由：\n'
docker exec "$FRONTEND_CONTAINER" sh -lc \
  'nginx -T 2>&1 | grep -n -A4 -B1 "location.*media" || true'

printf '\n修复完成。请回到平台重新生成一次文件，再点击下载。\n'
printf '注意：以后启动升级环境时，需要继续叠加此文件：%s\n' "$FIX_COMPOSE"
printf '完整启动命令：\n'
printf 'docker compose -f %q -f %q -f %q up -d\n' "$BASE_COMPOSE" "$UPDATE_COMPOSE" "$FIX_COMPOSE"
printf '\n日志文件: %s\n' "$LOG_FILE"
