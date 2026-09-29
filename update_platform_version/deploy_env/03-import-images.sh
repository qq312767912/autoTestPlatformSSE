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
# 只导入本次升级的应用镜像，避免把数据卷 tar 误交给 docker load。
archive_names=(
  backend-b6613a39-v2.8-r5-code-review-backend-hostcfg1-arm64.tar.gz
  frontend-b6613a39-v2.8-r5-code-review-arm64.tar.gz
  semgrep-b6613a39-v2.8-r5-code-review-semgrep-musl1-arm64.tar.gz
  crg-b6613a39-v2.8-r5-code-review-crg1-arm64.tar.gz
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
  wharttest-250-backend:update-b6613a39-v2.8-r5-code-review-backend-hostcfg1-arm64
  wharttest-250-frontend:update-b6613a39-v2.8-r5-code-review-arm64
  wharttest-250-semgrep:update-b6613a39-v2.8-r5-code-review-semgrep-musl1-arm64
  wharttest-250-crg:update-b6613a39-v2.8-r5-code-review-crg1-arm64
)

for image in "${images[@]}"; do
  platform="$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')"
  [ "$platform" = "linux/arm64" ] || { echo "镜像架构错误：$image ($platform)" >&2; exit 1; }
  case "$image" in
    wharttest-250-backend:*)
      echo "[验证] Backend Alpine、OpenCodeReview、Celery 与持久化日志配置"
      docker run --rm --entrypoint /bin/sh "$image" -c \
        'grep -q "Alpine Linux" /etc/os-release && ldd --version 2>&1 | grep -qi musl && command -v ocr >/dev/null && ocr --version && npm list -g --depth=0 @alibaba-group/open-code-review >/dev/null && test -x /usr/bin/chromium-browser && cd /app/ui_automation/recorder && node -e "require.resolve(\"playwright\")" && test -f /app/test_host_config/models.py && test -f /app/test_host_config/migrations/0001_initial.py && cd /app && python manage.py check && grep -q -- "--pool=solo --concurrency=1" /app/supervisord.conf && python -c "import celery,kombu,redis; assert (celery.__version__,kombu.__version__,redis.__version__) == (\"5.4.0\",\"5.6.2\",\"5.2.0\")" && grep -q "/app/data/logs" /app/supervisord.conf && ! grep -q "/var/log" /app/supervisord.conf'
      revision="$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
      [ "$revision" = "b6613a39-v2.8-r5-code-review-backend-hostcfg1" ] || { echo "镜像代码版本错误：$image ($revision)" >&2; exit 1; }
      echo "[通过] $image $platform $revision（Alpine/musl、OpenCodeReview、ocr CLI、录制器 Chromium/Playwright、Celery/Kombu/Redis 兼容组合、日志不写 /var）"
      continue
      ;;
    wharttest-250-frontend:*) expected_revision="b6613a39-v2.8-r5-code-review" ;;
    wharttest-250-semgrep:*)
      expected_revision="b6613a39-v2.8-r5-code-review-semgrep-musl1"
      docker run --rm --entrypoint /bin/sh "$image" -c \
        'grep -q "Alpine Linux" /etc/os-release && ldd --version 2>&1 | grep -qi musl && semgrep --version >/dev/null && test -d /app/rules'
      ;;
    wharttest-250-crg:*)
      expected_revision="b6613a39-v2.8-r5-code-review-crg1"
      docker run --rm --entrypoint /bin/sh "$image" -c \
        'grep -q "Alpine Linux" /etc/os-release && ldd --version 2>&1 | grep -qi musl && test "$(id -u)" = "10001" && code-review-graph --version | grep -q "2.3.9" && python -c "import fastapi,uvicorn; import app"'
      ;;
    *) echo "未识别镜像：$image" >&2; exit 1 ;;
  esac
  revision="$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [ "$revision" = "$expected_revision" ] || { echo "镜像代码版本错误：$image ($revision)" >&2; exit 1; }
  echo "[通过] $image $platform $revision"
done
