#!/usr/bin/env bash
set -euo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$UPDATE_DIR/../images" ]; then
  IMAGES_DIR="$(cd "$UPDATE_DIR/../images" && pwd)"
else
  # 兼容旧包将 images 放在 deploy_env 内的目录布局。
  IMAGES_DIR="$UPDATE_DIR/images"
fi

cd "$UPDATE_DIR"
sha256sum -c SHA256SUMS

shopt -s nullglob
for archive in "$IMAGES_DIR"/*.tar.gz; do
  echo "[导入] $(basename "$archive")"
  gzip -dc "$archive" | docker load
done

for first_part in "$IMAGES_DIR"/*.tar.gz.part000; do
  archive_prefix="${first_part%.part000}"
  echo "[合并并导入] $(basename "$archive_prefix")"
  cat "$archive_prefix".part* | gzip -dc | docker load
done

images=(
  wharttest-250-backend:update-ecff56e4-r1-arm64
  wharttest-250-frontend:update-ecff56e4-r1-arm64
)

for image in "${images[@]}"; do
  platform="$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')"
  [ "$platform" = "linux/arm64" ] || { echo "镜像架构错误：$image ($platform)" >&2; exit 1; }
  revision="$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  expected_revision="ecff56e40673d502bd85e702dbf4e11ef969f2af"
  [ "$revision" = "$expected_revision" ] || { echo "镜像代码版本错误：$image ($revision)" >&2; exit 1; }
  echo "[通过] $image $platform $revision"
done
