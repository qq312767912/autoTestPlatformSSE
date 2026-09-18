#!/usr/bin/env bash
set -euo pipefail

PLATFORM_DIR="${PLATFORM_DIR:-/projects/ai-test-platform}"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${UPDATE_COMPOSE:-${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.update.yml}"
SSE_TEST_HOST_IP="${SSE_TEST_HOST_IP:-10.122.215.111}"
LOG_FILE="${PLATFORM_DIR}/update_platform_version/fix-actuator-dns-$(date +%Y%m%d-%H%M%S).log"
TEMP_COMPOSE="$(mktemp)"

cleanup() { rm -f "$TEMP_COMPOSE"; }
trap cleanup EXIT
exec > >(tee "$LOG_FILE") 2>&1

[ -f "$BASE_COMPOSE" ] || { echo "基础 Compose 不存在: $BASE_COMPOSE" >&2; exit 1; }
[ -f "$UPDATE_COMPOSE" ] || { echo "升级 Compose 不存在: $UPDATE_COMPOSE" >&2; exit 1; }

echo "目标 IP: $SSE_TEST_HOST_IP"
echo "本脚本只逐个重建三个执行器，不重启前后端、数据库或其他服务。"

{
  echo 'services:'
  for index in 01 02 03; do
    echo "  actuator-${index}:"
    echo '    extra_hosts:'
    echo "      - www.test.sse.com.cn:${SSE_TEST_HOST_IP}"
    echo "      - www.sse.com.cn:${SSE_TEST_HOST_IP}"
    echo "      - static.sse.com.cn:${SSE_TEST_HOST_IP}"
    echo "      - static.test.sse.com.cn:${SSE_TEST_HOST_IP}"
    echo "      - media.sseinfo.com:${SSE_TEST_HOST_IP}"
  done
} > "$TEMP_COMPOSE"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE" -f "$TEMP_COMPOSE")
"${compose[@]}" config >/dev/null

for index in 01 02 03; do
  service="actuator-${index}"
  container="wharttest-actuator-${index}"
  echo
  echo "[$container] 重建..."
  "${compose[@]}" up -d --no-deps --force-recreate "$service"

  for _ in $(seq 1 20); do
    state="$(docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null || true)"
    [ "$state" = running ] && break
    sleep 3
  done

  echo "[$container] 状态: ${state:-不存在}"
  [ "$state" = running ] || { docker logs --tail 100 "$container" || true; exit 1; }

  echo "[$container] /etc/hosts:"
  docker exec "$container" sh -lc \
    'grep -E "(www\.test\.sse\.com\.cn|www\.sse\.com\.cn|static\.sse\.com\.cn|static\.test\.sse\.com\.cn|media\.sseinfo\.com)" /etc/hosts'

  echo "[$container] Python DNS 验证:"
  docker exec "$container" /usr/local/bin/python3.12 -c \
    'import socket; names=["www.test.sse.com.cn","www.sse.com.cn","static.sse.com.cn","static.test.sse.com.cn","media.sseinfo.com"]; [print(f"{name} -> {socket.gethostbyname(name)}") for name in names]'
done

echo
echo "三个执行器的内网域名解析已修复。请刷新平台执行器页面，再运行 UI 用例。"
echo "日志文件: $LOG_FILE"
