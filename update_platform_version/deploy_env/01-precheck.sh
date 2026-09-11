#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"

fail() { echo "[失败] $*" >&2; exit 1; }
ok() { echo "[通过] $*"; }

command -v docker >/dev/null 2>&1 || fail "未安装 Docker"
docker info >/dev/null 2>&1 || fail "Docker 未运行或当前用户无访问权限"

case "$(uname -m)" in
  aarch64|arm64) ok "宿主机架构为 $(uname -m)" ;;
  *) fail "宿主机不是 ARM64：$(uname -m)" ;;
esac

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$OVERRIDE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$OVERRIDE_COMPOSE"
ok "Compose 文件存在"

# Compose 解析 secret 时要求文件已存在。这里只创建空占位文件，真正密码由
# 07-start-actuators.sh 以隐藏输入方式写入，绝不写进镜像或 YAML。
secret_dir="$UPDATE_DIR/secrets"
secret_file="$secret_dir/actuator_api_password"
mkdir -p "$secret_dir"
chmod 700 "$secret_dir"
if [ ! -e "$secret_file" ]; then
  : > "$secret_file"
  chmod 600 "$secret_file"
fi

for container in wharttest-backend wharttest-frontend wharttest-postgres wharttest-redis wharttest-qdrant wharttest-mcp wharttest-playwright-mcp; do
  docker inspect "$container" >/dev/null 2>&1 || fail "缺少当前容器：$container"
  running="$(docker inspect "$container" --format '{{.State.Running}}')"
  [ "$running" = "true" ] || fail "容器未运行：$container"
done
ok "当前核心容器均在运行"

qdrant_image="$(docker inspect wharttest-qdrant --format '{{.Config.Image}}')"
[ "$qdrant_image" = "qdrant-kylin-arm64:v1.16.0-page64k" ] || \
  fail "Qdrant 镜像不是已验证的 64KB 页版本：$qdrant_image"
ok "Qdrant 保持麒麟 ARM64 64KB 页镜像"

mcp_image="$(docker inspect wharttest-mcp --format '{{.Config.Image}}')"
[ "$mcp_image" = "wharttest-250-mcp-alpine:latest" ] || \
  fail "MCP 镜像与基线报告不一致：$mcp_image"
ok "MCP 使用现有 Alpine 镜像"

for volume in offline-images_postgres-data offline-images_redis-data offline-images_qdrant-data offline-images_backend-static; do
  docker volume inspect "$volume" >/dev/null 2>&1 || fail "缺少数据卷：$volume"
done
ok "现有数据卷均存在"

available_kb="$(df -Pk "$UPDATE_DIR" | awk 'NR==2 {print $4}')"
[ "$available_kb" -ge 10485760 ] || fail "可用磁盘不足 10GB"
ok "可用磁盘空间满足升级要求"

docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE" config >/dev/null
ok "合并后的 Compose 配置可以解析"

echo "预检查完成，可以执行 02-backup.sh"
