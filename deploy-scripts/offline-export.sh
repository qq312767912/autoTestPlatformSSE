#!/usr/bin/env bash
# ============================================
# WHartTest-2.5.0 内网离线部署 - 外网导出脚本
# ============================================
# 在有互联网的机器上执行，导出所有 Docker 镜像（amd64 架构）
# 策略：
#   - backend/frontend/mcp 从 ghcr.io 拉取预构建 amd64 镜像（避免本地交叉编译 OOM）
#   - weixin-plugin-host 通过 buildx 交叉编译（轻量 Node.js 项目）
#   - 运行时镜像直接 docker pull --platform linux/amd64
# 使用 :amd64 标签，不影响本地已有的 ARM64 镜像
#
# 用法:
#   chmod +x deploy-scripts/offline-export.sh
#   ./deploy-scripts/offline-export.sh
#
# 产出: offline-images/ 目录（包含分卷压缩的镜像 + 导入脚本）
# 将此目录分批传到内网机器后执行 offline-import.sh
# ============================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="${PROJECT_DIR}/offline-images"
CHUNK_SIZE="280m"  # 每个分卷最大 280MB（留余量确保 < 300MB）
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

# 运行时镜像（直接拉取 amd64 版本）
# 格式: "官方镜像|镜像加速源"  (加速源为空则只用官方源)
RUNTIME_IMAGES=(
  "postgres:16-alpine|"
  "redis:7-alpine|"
  "qdrant/qdrant:latest|"
  "mcr.microsoft.com/playwright/mcp:latest|mcr.m.daocloud.io/playwright/mcp:latest"
)

# ghcr.io 预构建镜像（直接拉取 amd64，无需本地编译）
# 格式: "GHCR_SOURCE|LOCAL_AMD64_TAG"
GHCR_IMAGES=(
  "ghcr.io/mgdaaslab/wharttest-backend:latest|wharttest-250-backend:amd64"
  "ghcr.io/mgdaaslab/wharttest-frontend:latest|wharttest-250-frontend:amd64"
  "ghcr.io/mgdaaslab/wharttest-mcp:latest|wharttest-250-mcp:amd64"
)

# 需要本地交叉编译的镜像（轻量级）
# 格式: "AMD64_TAG|ORIGINAL_NAME|DOCKERFILE_DIR"
BUILD_IMAGES=(
  "wharttest-250-weixin-plugin-host:amd64|wharttest-250-weixin-plugin-host:latest|WHartTest_WeixinPluginHost"
)

# 完整的镜像映射表（用于导出和 manifest 生成）
# 格式: "AMD64_TAG|ORIGINAL_NAME"
IMAGE_MAP=(
  "postgres:16-alpine-amd64|postgres:16-alpine"
  "redis:7-alpine-amd64|redis:7-alpine"
  "qdrant/qdrant:latest-amd64|qdrant/qdrant:latest"
  "mcr.microsoft.com/playwright/mcp:latest-amd64|mcr.microsoft.com/playwright/mcp:latest"
  "wharttest-250-backend:amd64|wharttest-250-backend:latest"
  "wharttest-250-frontend:amd64|wharttest-250-frontend:latest"
  "wharttest-250-mcp:amd64|wharttest-250-mcp:latest"
  "wharttest-250-weixin-plugin-host:amd64|wharttest-250-weixin-plugin-host:latest"
)

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[导出]${NC} $1"; }
warn() { echo -e "${YELLOW}[警告]${NC} $1"; }
err()  { echo -e "${RED}[错误]${NC} $1"; }

# ---- 架构检测 ----
HOST_ARCH=$(uname -m)
log "宿主机架构: $HOST_ARCH"
log "目标平台: $TARGET_PLATFORM"

if [[ "$HOST_ARCH" == "arm64" || "$HOST_ARCH" == "aarch64" ]]; then
  warn "检测到 ARM 架构（Apple Silicon），将使用预构建 amd64 镜像 + 轻量交叉编译"
  warn "本地已有的 ARM64 镜像不会受影响"
fi

mkdir -p "$OUTPUT_DIR"
cd "$PROJECT_DIR"

# ============================================
# 第一步：拉取 ghcr.io 预构建 amd64 镜像
# ============================================
echo ""
echo "============================================"
echo " 第一步: 拉取预构建 amd64 镜像 (ghcr.io)"
echo "============================================"

for entry in "${GHCR_IMAGES[@]}"; do
  IFS='|' read -r source local_tag <<< "$entry"
  # 如果已存在则跳过
  if docker image inspect "$local_tag" &>/dev/null; then
    log "跳过（已存在）: $local_tag"
    continue
  fi
  log "拉取 $source → $local_tag ..."
  docker pull --platform "$TARGET_PLATFORM" "$source" || { err "拉取 $source 失败"; exit 1; }
  docker tag "$source" "$local_tag"
  log "  ✓ $local_tag"
done

# ============================================
# 第二步：交叉编译轻量级镜像（仅 weixin-plugin-host）
# ============================================
echo ""
echo "============================================"
echo " 第二步: 交叉编译 ($TARGET_PLATFORM)"
echo "============================================"

export DOCKER_BUILDKIT=1

for entry in "${BUILD_IMAGES[@]}"; do
  IFS='|' read -r amd64_tag original_name build_dir <<< "$entry"
  # 如果已存在则跳过
  if docker image inspect "$amd64_tag" &>/dev/null; then
    log "跳过（已存在）: $amd64_tag"
    continue
  fi
  log "构建 $amd64_tag (from $build_dir) ..."
  docker buildx build --platform "$TARGET_PLATFORM" \
    -t "$amd64_tag" \
    -f "${build_dir}/Dockerfile" \
    --load "${build_dir}/" || { err "构建 $amd64_tag 失败"; exit 1; }
  log "  ✓ $amd64_tag"
done

# ============================================
# 第三步：拉取运行时镜像（amd64 版本，另存标签）
# ============================================
echo ""
echo "============================================"
echo " 第三步: 拉取运行时镜像 ($TARGET_PLATFORM)"
echo "============================================"

for entry in "${RUNTIME_IMAGES[@]}"; do
  IFS='|' read -r img mirror <<< "$entry"
  amd64_tag="${img}-amd64"

  # 如果 :amd64 标签已存在则跳过
  if docker image inspect "$amd64_tag" &>/dev/null; then
    log "跳过（已存在）: $amd64_tag"
    continue
  fi

  log "拉取 $img → $amd64_tag ..."
  if docker pull --platform "$TARGET_PLATFORM" "$img"; then
    docker tag "$img" "$amd64_tag"
    docker rmi "$img" 2>/dev/null || true
  elif [ -n "$mirror" ]; then
    warn "官方源失败，尝试镜像加速: $mirror"
    docker pull --platform "$TARGET_PLATFORM" "$mirror" || { err "拉取 $mirror 也失败"; exit 1; }
    docker tag "$mirror" "$amd64_tag"
    docker rmi "$mirror" 2>/dev/null || true
  else
    err "拉取 $img 失败"; exit 1
  fi
done

log "所有 amd64 镜像就绪"

# ============================================
# 第四步：导出 :amd64 镜像 → 压缩 → 分卷
# ============================================
echo ""
echo "============================================"
echo " 第四步: 导出镜像（压缩 + 分卷 ≤ 280MB）"
echo "============================================"

MANIFEST="${OUTPUT_DIR}/manifest.txt"
cat > "$MANIFEST" << EOF
# WHartTest-2.5.0 离线镜像清单
# 导出时间: $(date '+%Y-%m-%d %H:%M:%S')
# 目标平台: $TARGET_PLATFORM
# 导出机器: $(uname -a)
# 格式: 导出的标签|文件名前缀|分卷数|导入后还原为
EOF

for entry in "${IMAGE_MAP[@]}"; do
  IFS='|' read -r amd64_img original_name <<< "$entry"

  safe_name=$(echo "$amd64_img" | tr '/:' '_')
  tar_file="${OUTPUT_DIR}/${safe_name}.tar"
  gz_file="${tar_file}.gz"

  echo ""
  log "导出: $amd64_img (→ $original_name)"

  docker save "$amd64_img" -o "$tar_file"
  raw_size=$(du -h "$tar_file" | cut -f1)
  log "  原始大小: $raw_size"

  log "  压缩中..."
  gzip -f "$tar_file"
  gz_size=$(du -h "$gz_file" | cut -f1)
  log "  压缩后: $gz_size"

  gz_bytes=$(stat -c%s "$gz_file" 2>/dev/null || stat -f%z "$gz_file" 2>/dev/null)
  chunk_bytes=$((280 * 1024 * 1024))

  if [ "$gz_bytes" -gt "$chunk_bytes" ]; then
    log "  文件超过 280MB，执行分卷..."
    split -b "$CHUNK_SIZE" -d -a 3 "$gz_file" "${gz_file}.part"
    rm -f "$gz_file"

    part_count=$(ls "${gz_file}.part"* | wc -l | tr -d ' ')
    log "  已分为 ${part_count} 个卷:"
    ls -lh "${gz_file}.part"* | awk '{print "    " $NF " (" $5 ")"}'
    echo "${amd64_img}|${safe_name}.tar.gz.part|${part_count}|${original_name}" >> "$MANIFEST"
  else
    log "  无需分卷（${gz_size}）"
    echo "${amd64_img}|${safe_name}.tar.gz|1|${original_name}" >> "$MANIFEST"
  fi
done

# ============================================
# 第五步：清理本地 :amd64 标签（不影响运行中的容器）
# ============================================
echo ""
echo "============================================"
echo " 第五步: 清理临时 :amd64 标签"
echo "============================================"

for entry in "${IMAGE_MAP[@]}"; do
  IFS='|' read -r amd64_img _ <<< "$entry"
  docker rmi "$amd64_img" 2>/dev/null && log "  已移除: $amd64_img" || true
done

# 清理拉取的 ghcr.io 源镜像
for entry in "${GHCR_IMAGES[@]}"; do
  IFS='|' read -r source _ <<< "$entry"
  docker rmi "$source" 2>/dev/null && log "  已移除: $source" || true
done

# ============================================
# 第六步：复制部署文件
# ============================================
echo ""
echo "============================================"
echo " 第六步: 复制离线部署文件"
echo "============================================"

cp "${PROJECT_DIR}/deploy-scripts/offline-import.sh" "${OUTPUT_DIR}/"
cp "${PROJECT_DIR}/docker-compose.offline.yml" "${OUTPUT_DIR}/"

mkdir -p "${OUTPUT_DIR}/config"
cp "${PROJECT_DIR}/WHartTest_MCP/playwright-mcp-config.json" "${OUTPUT_DIR}/config/" 2>/dev/null || true
cp "${PROJECT_DIR}/WHartTest_MCP/playwright-mcp-entrypoint.sh" "${OUTPUT_DIR}/config/"
cp "${PROJECT_DIR}/WHartTest_MCP/playwright-mcp-config.template.json" "${OUTPUT_DIR}/config/"

if [ -d "${PROJECT_DIR}/WHartTest_Skills" ]; then
  mkdir -p "${OUTPUT_DIR}/skills"
  cp "${PROJECT_DIR}/WHartTest_Skills/"*.zip "${OUTPUT_DIR}/skills/" 2>/dev/null || true
  cp "${PROJECT_DIR}/WHartTest_Skills/manifest.json" "${OUTPUT_DIR}/skills/" 2>/dev/null || true
  log "Skills 目录已复制"
fi

log "部署文件已复制"

# ---- 汇总 ----
echo ""
echo "============================================"
echo " 导出完成！"
echo "============================================"
echo ""
echo "输出目录: $OUTPUT_DIR"
total_size=$(du -sh "$OUTPUT_DIR" | cut -f1)
file_count=$(find "$OUTPUT_DIR" -type f | wc -l | tr -d ' ')
echo "总大小: $total_size"
echo "文件数: $file_count"
echo ""
echo "文件清单:"
find "$OUTPUT_DIR" -type f | sort | while read f; do
  size=$(du -h "$f" | cut -f1)
  echo "  $(basename "$f") ($size)"
done

echo ""
echo "============================================"
echo " 内网部署步骤:"
echo "============================================"
echo " 1. 将 offline-images/ 目录分批传到内网机器"
echo "    （每个文件 ≤ 280MB，可分多次传输）"
echo " 2. 在内网机器上执行:"
echo "    cd <offline-images目录>"
echo "    bash offline-import.sh"
echo "============================================"
