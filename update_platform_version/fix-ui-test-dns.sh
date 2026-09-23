#!/usr/bin/env bash
# 修复 Backend 录制浏览器访问内网 SSE 域名时 ERR_NAME_NOT_RESOLVED。
set -euo pipefail

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.update.yml"
SSE_TEST_HOST_IP="${SSE_TEST_HOST_IP:-10.122.215.111}"
export SSE_TEST_HOST_IP

fail() { echo "[失败] $*" >&2; exit 1; }
[ -f "$BASE_COMPOSE" ] || fail "找不到 $BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到 $UPDATE_COMPOSE"
case "$SSE_TEST_HOST_IP" in
  *[!0-9.]*|'') fail "SSE_TEST_HOST_IP 格式不正确: $SSE_TEST_HOST_IP" ;;
esac

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
"${compose[@]}" config >/dev/null

echo "[修复] SSE 域名映射 IP: $SSE_TEST_HOST_IP"
echo "[修复] 重建 Backend 和 Playwright MCP，不重建数据库、Redis、Qdrant 和执行器..."
"${compose[@]}" up -d --pull never --no-deps --force-recreate backend playwright-mcp

echo "[等待] Backend 健康检查..."
for _ in $(seq 1 90); do
  health="$(docker inspect wharttest-backend --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
  case "$health" in
    healthy|running) break ;;
    unhealthy|exited|dead)
      docker logs --tail 150 wharttest-backend >&2 || true
      fail "Backend 状态异常: $health"
      ;;
  esac
  sleep 2
done
[ "${health:-}" = healthy ] || [ "${health:-}" = running ] || fail "Backend 启动超时: ${health:-unknown}"

echo "[验证] Backend 域名解析..."
docker exec wharttest-backend getent hosts www.test.sse.com.cn
resolved="$(docker exec wharttest-backend getent hosts www.test.sse.com.cn | awk 'NR==1 {print $1}')"
[ "$resolved" = "$SSE_TEST_HOST_IP" ] || fail "解析到 $resolved，期望 $SSE_TEST_HOST_IP"

echo "[验证] Backend 容器访问目标站点..."
docker exec wharttest-backend /opt/venv/bin/python -c '
import urllib.request
r = urllib.request.urlopen("http://www.test.sse.com.cn", timeout=15)
print("HTTP", r.status, r.url)
'

echo "[验证] Playwright MCP 域名解析..."
docker exec wharttest-playwright-mcp getent hosts www.test.sse.com.cn

echo "修复完成。请刷新页面后重新执行 UI 用例。"
