#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/fix-playwright-mcp-browser-$(date '+%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

echo "Playwright MCP 离线浏览器修复日志：$LOG_FILE"
[ -f "$BASE_COMPOSE" ] || { echo "找不到基础 YAML：$BASE_COMPOSE" >&2; exit 1; }
[ -f "$OVERRIDE_COMPOSE" ] || { echo "找不到升级 YAML：$OVERRIDE_COMPOSE" >&2; exit 1; }
[ -f "$UPDATE_DIR/playwright-mcp-entrypoint.sh" ] || { echo "缺少 Playwright MCP 启动脚本" >&2; exit 1; }
[ -f "$UPDATE_DIR/playwright-mcp-config.template.json" ] || { echo "缺少 Playwright MCP 配置模板" >&2; exit 1; }

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE")
"${compose[@]}" config >/dev/null

echo "只重建 playwright-mcp 容器..."
"${compose[@]}" up -d --no-deps --force-recreate playwright-mcp
sleep 10

docker inspect wharttest-playwright-mcp \
  --format 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} image={{.Config.Image}}'

echo "最近日志："
docker logs --tail 100 wharttest-playwright-mcp 2>&1

container_logs="$(docker logs wharttest-playwright-mcp 2>&1 || true)"
if ! grep -q 'Playwright MCP 使用浏览器:' <<< "$container_logs"; then
  echo "未检测到浏览器选择日志，请将本日志文件发回排查" >&2
  exit 1
fi

if ! grep -q 'Playwright MCP 使用入口:' <<< "$container_logs"; then
  echo "未检测到 Playwright MCP 入口选择日志，请将本日志文件发回排查" >&2
  exit 1
fi

echo "Playwright MCP 已改为使用镜像内现有浏览器，不会在内网运行时下载。"
