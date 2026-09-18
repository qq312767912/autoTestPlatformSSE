#!/usr/bin/env bash
set -euo pipefail

TARGET_IP="${TARGET_IP:-10.122.215.111}"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="${UPDATE_DIR:-/projects/ai-test-platform/update_platform_version/deploy_env}"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
DNS_COMPOSE="$UPDATE_DIR/docker-compose.internal-dns.yml"
LOG_DIR="/projects/ai-test-platform/update_platform_version/logs"
LOG_FILE="$LOG_DIR/fix-internal-dns-$(date '+%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

fail() {
  echo "[失败] $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未安装 Docker"
docker info >/dev/null 2>&1 || fail "Docker 未运行或当前用户无权访问"
[ -f "$BASE_COMPOSE" ] || fail "找不到基础 Compose：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级 Compose：$UPDATE_COMPOSE"

echo "日志文件：$LOG_FILE"
echo "目标 IP：$TARGET_IP"
echo "基础 Compose：$BASE_COMPOSE"
echo "升级 Compose：$UPDATE_COMPOSE"

echo
echo "[记录] 当前容器"
for container in wharttest-backend wharttest-playwright-mcp; do
  docker inspect "$container" --format \
    'name={{.Name}} image={{.Config.Image}} status={{.State.Status}} extraHosts={{json .HostConfig.ExtraHosts}}' \
    || fail "找不到容器：$container"
done

cat > "$DNS_COMPOSE" <<EOF
services:
  backend:
    extra_hosts:
      - "www.test.sse.com.cn:${TARGET_IP}"
      - "www.sse.com.cn:${TARGET_IP}"
      - "static.sse.com.cn:${TARGET_IP}"
      - "static.test.sse.com.cn:${TARGET_IP}"
      - "media.sseinfo.com:${TARGET_IP}"

  playwright-mcp:
    extra_hosts:
      - "www.test.sse.com.cn:${TARGET_IP}"
      - "www.sse.com.cn:${TARGET_IP}"
      - "static.sse.com.cn:${TARGET_IP}"
      - "static.test.sse.com.cn:${TARGET_IP}"
      - "media.sseinfo.com:${TARGET_IP}"
EOF

echo
echo "[写入] $DNS_COMPOSE"
sed -n '1,120p' "$DNS_COMPOSE"

compose=(
  docker compose -p offline-images
  -f "$BASE_COMPOSE"
  -f "$UPDATE_COMPOSE"
  -f "$DNS_COMPOSE"
)

"${compose[@]}" config >/dev/null
echo "[通过] Compose 合并配置可解析"

echo
echo "[重建] Backend 和 Playwright MCP（不删除数据卷）"
"${compose[@]}" up -d --no-deps --force-recreate backend playwright-mcp

wait_running() {
  local container="$1"
  local elapsed=0
  while [ "$elapsed" -lt 180 ]; do
    status="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    case "$status" in
      healthy|running)
        echo "[通过] $container: $status"
        return 0
        ;;
      unhealthy|exited|dead)
        docker logs --tail 100 "$container" || true
        fail "$container 状态异常：$status"
        ;;
    esac
    sleep 5
    elapsed=$((elapsed + 5))
  done
  docker logs --tail 100 "$container" || true
  fail "$container 启动超时"
}

wait_running wharttest-backend
wait_running wharttest-playwright-mcp

echo
echo "[验证] 域名解析"
for container in wharttest-backend wharttest-playwright-mcp; do
  docker exec "$container" getent hosts www.test.sse.com.cn \
    || fail "$container 仍无法解析 www.test.sse.com.cn"
done

echo
echo "[验证] HTTP 连通性"
docker exec wharttest-backend curl -I -L --connect-timeout 5 --max-time 15 \
  http://www.test.sse.com.cn || fail "Backend HTTP 访问失败"
docker exec wharttest-playwright-mcp wget -S --spider -T 15 \
  http://www.test.sse.com.cn || fail "Playwright MCP HTTP 访问失败"

echo
echo "[完成] 内网域名映射已生效。"
echo "请返回平台重新执行原测试。"
echo "日志文件：$LOG_FILE"
echo
echo "注意：以后使用 Compose 手工重建 Backend/Playwright MCP 时，需继续加上："
echo "  -f $DNS_COMPOSE"
