#!/usr/bin/env bash
# ============================================
# WHartTest-2.5.0 内网离线部署 - 内网导入脚本
# ============================================
# 在无互联网的内网机器上执行
# 合并分卷文件 → 加载镜像 → 启动服务
#
# 前提：
#   1. offline-images/ 目录下所有文件已拷贝完成
#   2. Docker 和 Docker Compose 已安装
#
# 用法:
#   cd offline-images/
#   bash offline-import.sh
# ============================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGES_DIR="$SCRIPT_DIR"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[导入]${NC} $1"; }
err() { echo -e "${RED}[错误]${NC} $1"; exit 1; }
warn() { echo -e "${YELLOW}[提示]${NC} $1"; }

echo "============================================"
echo " WHartTest-2.5.0 离线镜像导入"
echo "============================================"
echo ""

# 检查 Docker
command -v docker &>/dev/null || err "未安装 Docker"
docker info &>/dev/null || err "Docker 服务未运行"
log "Docker 环境检查通过"

# 检查 manifest
MANIFEST="${IMAGES_DIR}/manifest.txt"
[ -f "$MANIFEST" ] || err "找不到 manifest.txt，请确认 offline-images 目录完整"

# ============================================
# 第一步：合并分卷 + 解压 + 加载镜像
# ============================================
echo ""
echo "============================================"
echo " 第一步: 加载 Docker 镜像"
echo "============================================"

load_image() {
  local loaded_tag="$1"   # 导出时的标签（如 :amd64）
  local prefix="$2"
  local parts="$3"
  local target_name="$4"  # 导入后还原的目标名称（如 :latest）
  local target_arch
  target_arch="$(docker image inspect "$target_name" --format '{{.Architecture}}' 2>/dev/null || true)"

  # 离线包全部面向 amd64。已有同名 ARM64 镜像不能复用，否则 Compose
  # 的 platform: linux/amd64 会触发联网拉取或直接架构不匹配。
  if [ "$target_arch" = "amd64" ]; then
    log "已存在: $target_name (amd64，跳过)"
    return 0
  elif [ -n "$target_arch" ]; then
    warn "已有镜像架构为 $target_arch，将用离线 amd64 镜像覆盖: $target_name"
  fi

  if [ "$parts" = "1" ]; then
    local gz_file="${IMAGES_DIR}/${prefix}"
    [ -f "$gz_file" ] || { warn "文件缺失: $gz_file"; return 1; }
    log "加载: $target_name"
    gunzip -c "$gz_file" | docker load
  else
    log "合并 ${parts} 个分卷: $target_name"
    local merged_file="${IMAGES_DIR}/_merged_$(echo "$prefix" | sed 's/.tar.gz.part//')"
    cat "${IMAGES_DIR}/${prefix}"??? > "$merged_file.tar.gz"
    gunzip -c "$merged_file.tar.gz" | docker load
    rm -f "$merged_file.tar.gz"
  fi

  # 如果加载后的标签与目标不同，进行 retag；覆盖可能存在的错误架构标签。
  if [ "$loaded_tag" != "$target_name" ]; then
    docker tag "$loaded_tag" "$target_name"
    docker rmi "$loaded_tag" 2>/dev/null || true
  fi

  log "  ✓ $target_name 加载成功"
}

# 读取 manifest 并逐个加载
while IFS='|' read -r loaded_tag prefix parts target_name; do
  [[ "$loaded_tag" =~ ^#.*$ ]] && continue
  [ -z "$loaded_tag" ] && continue
  # 兼容旧版 manifest（3字段，无 target_name）
  [ -z "$target_name" ] && target_name="$loaded_tag"

  load_image "$loaded_tag" "$prefix" "$parts" "$target_name"
done < "$MANIFEST"

# ============================================
# 第二步：验证所有镜像
# ============================================
echo ""
echo "============================================"
echo " 第二步: 验证镜像完整性"
echo "============================================"

EXPECTED_IMAGES=(
  "postgres:16-alpine"
  "redis:7-alpine"
  "qdrant/qdrant:latest"
  "mcr.microsoft.com/playwright/mcp"
  "wharttest-250-backend:latest"
  "wharttest-250-frontend:latest"
  "wharttest-250-mcp:latest"
  "wharttest-250-weixin-plugin-host:latest"
)

all_ok=true
for img in "${EXPECTED_IMAGES[@]}"; do
  if docker image inspect "$img" &>/dev/null; then
    arch="$(docker image inspect "$img" --format '{{.Architecture}}' 2>/dev/null || true)"
    [ "$arch" = "amd64" ] || err "✗ $img 架构为 $arch，期望 amd64"
    log "✓ $img ($arch)"
  else
    err "✗ 缺失: $img"
    all_ok=false
  fi
done

$all_ok || err "部分镜像缺失，请检查 offline-images 目录完整性"
log "所有镜像验证通过！"

# ============================================
# 第三步：部署项目
# ============================================
echo ""
echo "============================================"
echo " 第三步: 启动服务"
echo "============================================"

# 检查项目目录
COMPOSE_FILE=""
if [ -f "${IMAGES_DIR}/docker-compose.offline.yml" ]; then
  # compose 文件在同目录（导出脚本已复制）
  COMPOSE_FILE="${IMAGES_DIR}/docker-compose.offline.yml"
  cd "$IMAGES_DIR"
elif [ -f "${IMAGES_DIR}/../docker-compose.offline.yml" ]; then
  COMPOSE_FILE="${IMAGES_DIR}/../docker-compose.offline.yml"
  cd "${IMAGES_DIR}/.."
else
  warn "找不到 docker-compose.offline.yml"
  warn "请确保 offline-images 目录中包含 docker-compose.offline.yml"
  echo ""
  echo "手动启动命令:"
  echo "  cd <目录>"
  echo "  docker compose -f docker-compose.offline.yml up -d"
  exit 0
fi

log "工作目录: $(pwd)"
log "Compose 文件: $COMPOSE_FILE"

# 创建必要的数据目录
mkdir -p data/postgres data/redis data/qdrant data/playwright-screenshots

# 检查并停止旧容器
if docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | grep -q .; then
  warn "检测到正在运行的容器，停止中..."
  docker compose -f "$COMPOSE_FILE" down
fi

# 启动服务
log "启动所有服务..."
docker compose -f "$COMPOSE_FILE" up -d

# ============================================
# 第四步：检查服务状态
# ============================================
echo ""
echo "============================================"
echo " 第四步: 检查服务状态"
echo "============================================"

sleep 5
docker compose -f "$COMPOSE_FILE" ps

echo ""
echo "============================================"
echo " ✓ 离线部署完成！"
echo "============================================"
echo ""
echo " 前端地址: http://localhost:8913"
echo " 后端地址: http://localhost:8912"
echo " 管理员账号: admin"
echo " 管理员密码: admin123456"
echo ""
echo " 常用命令:"
echo "   查看日志:  docker compose -f docker-compose.offline.yml logs -f"
echo "   停止服务:  docker compose -f docker-compose.offline.yml down"
echo "   重启服务:  docker compose -f docker-compose.offline.yml restart"
echo "============================================"
