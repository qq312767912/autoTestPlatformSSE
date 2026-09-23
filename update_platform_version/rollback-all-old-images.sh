#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/projects/ai-test-platform/update_platform_version}"
IMAGES_DIR="${IMAGES_DIR:-$BASE_DIR/images}"
DEPLOY_DIR="${DEPLOY_DIR:-$BASE_DIR/deploy_env}"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${UPDATE_COMPOSE:-$DEPLOY_DIR/docker-compose.update.yml}"
ROLLBACK_COMPOSE="$BASE_DIR/docker-compose.rollback-old-images.yml"

command -v docker >/dev/null 2>&1 || { echo "未找到 docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker 未运行或当前用户无权访问" >&2; exit 1; }
[ -d "$IMAGES_DIR" ] || { echo "找不到旧镜像分包目录: $IMAGES_DIR" >&2; exit 1; }
[ -f "$BASE_COMPOSE" ] || { echo "找不到基础 Compose: $BASE_COMPOSE" >&2; exit 1; }
[ -f "$UPDATE_COMPOSE" ] || { echo "找不到更新 Compose: $UPDATE_COMPOSE" >&2; exit 1; }

# 分包前缀|旧镜像标签|预期镜像 ID
old_images=(
  "backend-update-d595a628-review-fix-r5-arm64.tar.gz|wharttest-250-backend:update-d595a628-review-fix-r5-arm64|sha256:2c06269e5222984a32dc2eadbcc46142fa4bceb8ae4ed78c110cdadd530d12e1"
  "frontend-update-d595a628-review-fix-r5-arm64.tar.gz|wharttest-250-frontend:update-d595a628-review-fix-r5-arm64|sha256:1df1e8f36bd9484178216d53d25433b41a7f20c087bc7296d2a646504b645877"
  "actuator-update-178fb3ed-arm64-r4.tar.gz|wharttest-250-actuator:update-178fb3ed-arm64-r4|sha256:539272a5aa40000cdba316f9bb10f226de6cc3cf9c253a5d1e37bc57dda10b07"
  "playwright-mcp-alpine-latest-arm64.tar.gz|playwright-mcp-alpine:latest|sha256:65ef0f7880461b17259ae888657cc9034ac2831ddc13f41169f76d2b76c3d7e3"
  "vision-mcp-update-01339484-arm64-r2.tar.gz|wharttest-250-vision-mcp:update-01339484-arm64-r2|sha256:fa0338ba93e580af82c1f95efbed4e41c683f647d32f45f31d0791d64ffffedf"
  "qdrant-kylin-arm64-v1.16.0-page64k.tar.gz|qdrant-kylin-arm64:v1.16.0-page64k|sha256:65a7034584a0c3e165f132989cbafe8113a37407a42a66caed2f7dd6592e93c5"
  "mcp-alpine-latest-arm64.tar.gz|wharttest-250-mcp-alpine:latest|sha256:b23ea0875c0b5f2fa69710da84751c5f027c2fcaf3e74bc4528bc9b23d6f681c"
  "weixin-plugin-host-latest-arm64.tar.gz|wharttest-250-weixin-plugin-host:latest|sha256:2aab0f5bf7c607b6381c8808ef2808c203f96add71dda3a7367b78827a55d701"
  "postgres-16-alpine-arm64.tar.gz|postgres:16-alpine|sha256:7e7dbab8d3b431a20793a6d99cb5a6bc84e44914309917f1bf5589a7568cdefd"
  "redis-7-alpine-arm64.tar.gz|redis:7-alpine|sha256:80dd823f4d2bf93dd5e418a0ae2817319a1ba279953e234082e54a5a18306223"
)

echo "即将将整套服务回退到 2026-09-23 备份的 10 个旧镜像。"
echo "现有命名数据卷保持不变，不会恢复或删除卷数据。"
read -r -p "确认回退请输入 ROLLBACK: " answer
[ "$answer" = ROLLBACK ] || { echo "已取消"; exit 1; }

shopt -s nullglob
for item in "${old_images[@]}"; do
  prefix="${item%%|*}"
  remainder="${item#*|}"
  image="${remainder%%|*}"
  parts=("$IMAGES_DIR/$prefix".part*)
  [ "${#parts[@]}" -gt 0 ] || { echo "缺少旧镜像分包: $prefix.part*" >&2; exit 1; }
  echo "[导入旧镜像] $image <- $prefix.part*"
  cat "${parts[@]}" | gzip -dc | docker load
done

for item in "${old_images[@]}"; do
  remainder="${item#*|}"
  image="${remainder%%|*}"
  expected_id="${remainder#*|}"
  actual_id="$(docker image inspect "$image" --format '{{.Id}}')"
  [ "$actual_id" = "$expected_id" ] || {
    echo "旧镜像 ID 校验失败: $image" >&2
    echo "  预期: $expected_id" >&2
    echo "  实际: $actual_id" >&2
    exit 1
  }
  echo "[校验通过] $image $actual_id"
done

cat > "$ROLLBACK_COMPOSE" <<'EOF_COMPOSE'
services:
  backend:
    image: wharttest-250-backend:update-d595a628-review-fix-r5-arm64
  frontend:
    image: wharttest-250-frontend:update-d595a628-review-fix-r5-arm64
  actuator-01:
    image: wharttest-250-actuator:update-178fb3ed-arm64-r4
  actuator-02:
    image: wharttest-250-actuator:update-178fb3ed-arm64-r4
  actuator-03:
    image: wharttest-250-actuator:update-178fb3ed-arm64-r4
  playwright-mcp:
    image: playwright-mcp-alpine:latest
  vision-mcp:
    image: wharttest-250-vision-mcp:update-01339484-arm64-r2
  qdrant:
    image: qdrant-kylin-arm64:v1.16.0-page64k
  mcp:
    image: wharttest-250-mcp-alpine:latest
  weixin-plugin-host:
    image: wharttest-250-weixin-plugin-host:latest
  postgres:
    image: postgres:16-alpine
  redis:
    image: redis:7-alpine
EOF_COMPOSE

docker compose -p offline-images \
  -f "$BASE_COMPOSE" \
  -f "$UPDATE_COMPOSE" \
  -f "$ROLLBACK_COMPOSE" \
  config >/dev/null

docker compose -p offline-images \
  -f "$BASE_COMPOSE" \
  -f "$UPDATE_COMPOSE" \
  -f "$ROLLBACK_COMPOSE" \
  up -d --force-recreate

echo
echo "=== 回退后容器状态 ==="
docker ps -a --filter 'name=wharttest-' \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
echo
echo "旧镜像已全部恢复，数据卷保持现状。"
echo "如需回退数据，请使用 images/*.volume.tar.gz 单独执行，不要直接覆盖当前卷。"
