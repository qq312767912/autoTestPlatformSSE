#!/usr/bin/env bash
# ============================================================================
# 构建 WHartTest 内网 ARM64 升级镜像，并产出可分卷传输的镜像归档。
# ============================================================================
# 用法：
#   bash build-images.sh backend     # 只重建 Backend（Dockerfile.alpine，Alpine/musl 完整构建）
#   bash build-images.sh frontend    # 只重建 Frontend
#   bash build-images.sh actuator    # 只重建 Actuator（Alpine/musl ARM64）
#   bash build-images.sh pack        # 只做 docker save + gzip + 分卷 + SHA256SUMS
#   bash build-images.sh all         # 全流程
#
# 约定：
#   REV   版本标识（默认由当前 Git HEAD 生成）。它会同时写入镜像 tag 后缀与
#         OCI 标签 org.opencontainers.image.revision，deploy_env/03-import-images.sh
#         会用同一字符串校验镜像版本，三者必须一致。
#   PART_SIZE 单个分卷大小（默认 280m），内网按 300MB 单文件上限传输。
#
# 产物：
#   update_platform_version/images/backend-<REV>-arm64.tar.gz.part*
#   update_platform_version/images/frontend-<REV>-arm64.tar.gz.part*
#   update_platform_version/images/actuator-<REV>-arm64.tar.gz.part*
#   update_platform_version/deploy_env/SHA256SUMS
#   update_platform_version/build-logs/*.log
#
# 注意：本脚本只负责构建与打包，不改 deploy_env 里的清单与断言。
#       镜像 tag/版本号变更后，需同步更新：
#         deploy_env/{IMAGE_MANIFEST.txt,VERSION,03-import-images.sh,05-verify.sh,
#                    docker-compose.update.yml,16-diagnose-backend-restart.sh}
#       并生成 deploy_env.zip。
# ============================================================================
set -euo pipefail

# macOS 上 Docker Desktop 的 CLI 常装在 /usr/local/bin 或应用包内，默认 PATH 往往不含它。
for candidate in /usr/local/bin /opt/homebrew/bin /Applications/Docker.app/Contents/Resources/bin; do
  case ":$PATH:" in
    *":$candidate:"*) ;;
    *) [ -d "$candidate" ] && PATH="$candidate:$PATH" ;;
  esac
done
export PATH

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REV="${REV:-$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD)-v2.8-r3-kombu562}"
BACKEND_IMAGE="wharttest-250-backend:update-${REV}-arm64"
FRONTEND_IMAGE="wharttest-250-frontend:update-${REV}-arm64"
ACTUATOR_IMAGE="wharttest-250-actuator:update-${REV}-arm64"
IMAGES_DIR="$REPO_ROOT/update_platform_version/images"
LOG_DIR="$REPO_ROOT/update_platform_version/build-logs"
DEPLOY_ENV_DIR="$REPO_ROOT/update_platform_version/deploy_env"
PART_SIZE="${PART_SIZE:-280m}"
PLATFORM="${PLATFORM:-linux/arm64}"

log() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { echo "[失败] $*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "找不到 docker 命令（macOS 上通常在 /usr/local/bin，可能需要加入 PATH）"
  docker info >/dev/null 2>&1 || fail "Docker 未运行"
  local actual
  actual="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
  case "$actual" in
    linux/aarch64|linux/arm64) ;;
    *) fail "当前 Docker 架构为 ${actual}，需要原生 linux/arm64 才能构建麒麟 ARM64 镜像" ;;
  esac
  log "Docker 就绪：$actual"
}

build_backend() {
  require_docker
  mkdir -p "$LOG_DIR"
  log "构建 Backend：${BACKEND_IMAGE}（Dockerfile.alpine / Alpine-musl）"
  docker buildx build \
    --platform "$PLATFORM" \
    --progress=plain \
    --build-arg OCI_REVISION="$REV" \
    -f "$REPO_ROOT/WHartTest_Django/Dockerfile.alpine" \
    -t "$BACKEND_IMAGE" \
    --load \
    "$REPO_ROOT/WHartTest_Django" \
    2>&1 | tee "$LOG_DIR/backend-full-${REV}-alpine-arm64.log"
  assert_backend
}

build_frontend() {
  require_docker
  mkdir -p "$LOG_DIR"
  log "构建 Frontend：$FRONTEND_IMAGE"
  docker buildx build \
    --platform "$PLATFORM" \
    --progress=plain \
    --build-arg OCI_REVISION="$REV" \
    -f "$REPO_ROOT/WHartTest_Vue/Dockerfile" \
    -t "$FRONTEND_IMAGE" \
    --load \
    "$REPO_ROOT/WHartTest_Vue" \
    2>&1 | tee "$LOG_DIR/frontend-full-${REV}-arm64.log"
  assert_frontend
}

build_actuator() {
  require_docker
  mkdir -p "$LOG_DIR"
  log "构建 Actuator：${ACTUATOR_IMAGE}（Dockerfile.alpine-arm64 / Alpine-musl）"
  docker buildx build \
    --platform "$PLATFORM" \
    --progress=plain \
    --build-arg BUILD_REVISION="$REV" \
    -f "$REPO_ROOT/WHartTest_Actuator/Dockerfile.alpine-arm64" \
    -t "$ACTUATOR_IMAGE" \
    --load \
    "$REPO_ROOT/WHartTest_Actuator" \
    2>&1 | tee "$LOG_DIR/actuator-full-${REV}-alpine-arm64.log"
  assert_actuator
}

# 构建后立即验证硬性要求，避免把不合格镜像打包装车。
assert_backend() {
  log "校验 Backend 镜像"
  docker run --rm --entrypoint /bin/sh "$BACKEND_IMAGE" -c '
    set -e
    grep -q "Alpine Linux" /etc/os-release
    ldd --version 2>&1 | grep -qi musl
    command -v ocr >/dev/null
    ocr --version
    npm list -g --depth=0 @alibaba-group/open-code-review >/dev/null
    test -x /usr/bin/chromium-browser
    cd /app/ui_automation/recorder
    node -e "require.resolve(\"playwright\")"
    [ -f /app/testcases/review_service.py ]
    [ -f /app/bundled_skills/test-case-clarity-review/references/review-rules.md ]
    python -c "import celery,kombu,redis; assert (celery.__version__,kombu.__version__,redis.__version__) == (\"5.4.0\",\"5.6.2\",\"5.2.0\")"
    echo "Alpine/musl + OpenCodeReview(ocr CLI) + UI 录制器 Chromium/Playwright + 用例审查 Skill 就绪"
  '
  local revision
  revision="$(docker image inspect "$BACKEND_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [ "$revision" = "$REV" ] || fail "镜像标签 revision=${revision}，期望 $REV"
  log "Backend 校验通过（revision=${revision}）"
}

assert_frontend() {
  log "校验 Frontend 镜像"
  docker run --rm --entrypoint sh "$FRONTEND_IMAGE" -c '
    set -e
    ls /usr/share/nginx/html/index.html >/dev/null
    echo "静态产物就绪"
  '
  local revision
  revision="$(docker image inspect "$FRONTEND_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [ "$revision" = "$REV" ] || fail "镜像标签 revision=${revision}，期望 $REV"
  log "Frontend 校验通过（revision=${revision}）"
}

assert_actuator() {
  log "校验 Actuator 镜像"
  docker run --rm --entrypoint /bin/sh "$ACTUATOR_IMAGE" -c '
    set -e
    grep -q "Alpine Linux" /etc/os-release
    ldd --version 2>&1 | grep -qi musl
    test -x /usr/bin/chromium-browser
    test -f /app/main.py
    python -c "import playwright; import websockets"
  '
  local revision
  revision="$(docker image inspect "$ACTUATOR_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [ "$revision" = "$REV" ] || fail "镜像标签 revision=${revision}，期望 $REV"
  log "Actuator 校验通过（revision=${revision}）"
}

# docker save → gzip → 按 PART_SIZE 分卷，删除中间整包。
pack_image() {
  local image="$1" name="$2"
  docker image inspect "$image" >/dev/null 2>&1 || fail "本地没有镜像：$image"
  local whole="$IMAGES_DIR/${name}.tar.gz"
  log "导出 $image → ${name}.tar.gz.part*"
  rm -f "$IMAGES_DIR/${name}.tar.gz".part*
  docker save "$image" | gzip -c > "$whole"
  split -b "$PART_SIZE" -d -a 3 "$whole" "$IMAGES_DIR/${name}.tar.gz.part"
  rm -f "$whole"
  ls -la "$IMAGES_DIR/${name}.tar.gz.part"* | awk '{printf "    %10.1f MB  %s\n", $5/1048576, $9}'
}

write_sha256sums() {
  log "生成 deploy_env/SHA256SUMS"
  local sums=""
  for name in "backend-${REV}-arm64" "frontend-${REV}-arm64" "actuator-${REV}-arm64"; do
    sum_prefix="../images/${name}.tar.gz"
    for part in "$IMAGES_DIR/${name}.tar.gz.part"*; do
      [ -e "$part" ] || continue
      local relative="${sum_prefix}$(basename "$part" | sed "s/^${name}\.tar\.gz//")"
      local digest
      if command -v sha256sum >/dev/null 2>&1; then
        digest="$(sha256sum "$part" | awk '{print $1}')"
      else
        digest="$(shasum -a 256 "$part" | awk '{print $1}')"
      fi
      sums="${sums}${digest}  ${relative}"$'\n'
    done
  done
  [ -n "$sums" ] || fail "没有可校验的分卷，请先执行 pack"
  printf '%s' "$sums" > "$DEPLOY_ENV_DIR/SHA256SUMS"
  cat "$DEPLOY_ENV_DIR/SHA256SUMS"
}

pack() {
  mkdir -p "$IMAGES_DIR"
  pack_image "$BACKEND_IMAGE"  "backend-${REV}-arm64"
  pack_image "$FRONTEND_IMAGE" "frontend-${REV}-arm64"
  pack_image "$ACTUATOR_IMAGE" "actuator-${REV}-arm64"
  write_sha256sums
  log "打包完成"
}

case "${1:-}" in
  backend)  build_backend ;;
  frontend) build_frontend ;;
  actuator) build_actuator ;;
  pack)     pack ;;
  all)      build_backend; build_frontend; build_actuator; pack ;;
  *)        sed -n '2,29p' "$0"; exit 1 ;;
esac
