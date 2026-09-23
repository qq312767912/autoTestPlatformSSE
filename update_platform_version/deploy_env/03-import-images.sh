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

# images/ 可能同时保留旧镜像和 *.volume.tar.gz 数据卷备份。
# 只导入本次升级的三个镜像，避免把数据卷 tar 误交给 docker load。
archive_names=(
  backend-ac65a6fb-v2.8-r3-kombu562-arm64.tar.gz
  frontend-ac65a6fb-v2.8-r1-arm64.tar.gz
  actuator-ac65a6fb-v2.8-r3-auth-failfast-arm64.tar.gz
)

shopt -s nullglob
for archive_name in "${archive_names[@]}"; do
  archive_prefix="$IMAGES_DIR/$archive_name"
  parts=("$archive_prefix".part*)
  if [ "${#parts[@]}" -gt 0 ]; then
    echo "[合并并导入] $archive_name"
    cat "${parts[@]}" | gzip -dc | docker load
  elif [ -f "$archive_prefix" ]; then
    echo "[导入] $archive_name"
    gzip -dc "$archive_prefix" | docker load
  else
    echo "缺少本次升级镜像分包: $archive_name.part*" >&2
    exit 1
  fi
done

images=(
  wharttest-250-backend:update-ac65a6fb-v2.8-r3-kombu562-arm64
  wharttest-250-frontend:update-ac65a6fb-v2.8-r1-arm64
  wharttest-250-vision-mcp:update-01339484-arm64-r2
  wharttest-250-actuator:update-ac65a6fb-v2.8-r3-auth-failfast-arm64
)

for image in "${images[@]}"; do
  platform="$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')"
  [ "$platform" = "linux/arm64" ] || { echo "镜像架构错误：$image ($platform)" >&2; exit 1; }
  case "$image" in
    wharttest-250-backend:*)
      echo "[验证] Backend Alpine、OpenCodeReview、Celery 与持久化日志配置"
      docker run --rm --entrypoint /bin/sh "$image" -c \
        'grep -q "Alpine Linux" /etc/os-release && ldd --version 2>&1 | grep -qi musl && command -v ocr >/dev/null && ocr --version && npm list -g --depth=0 @alibaba-group/open-code-review >/dev/null && test -x /usr/bin/chromium-browser && cd /app/ui_automation/recorder && node -e "require.resolve(\"playwright\")" && grep -q -- "--pool=solo --concurrency=1" /app/supervisord.conf && python -c "import celery,kombu,redis; assert (celery.__version__,kombu.__version__,redis.__version__) == (\"5.4.0\",\"5.6.2\",\"5.2.0\")" && grep -q "/app/data/logs" /app/supervisord.conf && ! grep -q "/var/log" /app/supervisord.conf'
      revision="$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
      [ "$revision" = "ac65a6fb-v2.8-r3-kombu562" ] || { echo "镜像代码版本错误：$image ($revision)" >&2; exit 1; }
      echo "[通过] $image $platform $revision（Alpine/musl、OpenCodeReview、ocr CLI、录制器 Chromium/Playwright、Celery/Kombu/Redis 兼容组合、日志不写 /var）"
      continue
      ;;
    wharttest-250-frontend:*) expected_revision="ac65a6fb-v2.8-r1" ;;
    wharttest-250-actuator:*) expected_revision="ac65a6fb-v2.8-r3-auth-failfast" ;;
    *) expected_revision="01339484c66c281fdddaea22683a68c40b7835bd" ;;
  esac
  revision="$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [ "$revision" = "$expected_revision" ] || { echo "镜像代码版本错误：$image ($revision)" >&2; exit 1; }
  echo "[通过] $image $platform $revision"
done
