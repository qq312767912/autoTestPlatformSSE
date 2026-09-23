#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/projects/ai-test-platform/update_platform_version}"
DEPLOY_DIR="$BASE_DIR/deploy_env"
timestamp="$(date +%Y%m%d-%H%M%S)"

command -v docker >/dev/null 2>&1 || { echo "未找到 docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker 未运行或当前用户无权访问" >&2; exit 1; }
mkdir -p "$DEPLOY_DIR"

backup_path() {
  path="$1"
  if [ -e "$path" ] || [ -L "$path" ]; then
    backup="$path.pre-recovery-$timestamp"
    echo "[备份异常路径] $path -> $backup"
    mv "$path" "$backup"
  fi
}

supervisord_file="$DEPLOY_DIR/supervisord.single-worker.conf"
playwright_entrypoint="$DEPLOY_DIR/playwright-mcp-entrypoint.sh"
playwright_config="$DEPLOY_DIR/playwright-mcp-config.template.json"
secret_dir="$DEPLOY_DIR/secrets"
secret_file="$secret_dir/actuator_api_password"

backup_path "$supervisord_file"
backup_path "$playwright_entrypoint"
backup_path "$playwright_config"

cat > "$supervisord_file" <<'EOF_SUPERVISORD'
[supervisord]
nodaemon=true
logfile=/app/data/logs/supervisord.log
pidfile=/app/data/run/supervisord.pid

[program:django]
command=uvicorn wharttest_django.asgi:application --host 0.0.0.0 --port 8000 --workers=1
directory=/app
autostart=true
autorestart=true
stderr_logfile=/app/data/logs/django_err.log
stdout_logfile=/app/data/logs/django_out.log

[program:celery_worker]
command=celery -A wharttest_django worker -l info --concurrency=8 -Q celery,task_center
directory=/app
autostart=true
autorestart=true
stderr_logfile=/app/data/logs/worker_err.log
stdout_logfile=/app/data/logs/worker_out.log

[program:celery_beat]
command=celery -A wharttest_django beat -l info --schedule=/app/data/celerybeat-schedule
directory=/app
autostart=true
autorestart=true
stderr_logfile=/app/data/logs/beat_err.log
stdout_logfile=/app/data/logs/beat_out.log
EOF_SUPERVISORD
chmod 644 "$supervisord_file"

cat > "$playwright_entrypoint" <<'EOF_PLAYWRIGHT'
#!/bin/sh
set -eu

TEMPLATE="${PLAYWRIGHT_MCP_CONFIG_TEMPLATE:-/opt/wharttest/playwright-mcp-config.template.json}"
RUNTIME_CONFIG="${PLAYWRIGHT_MCP_RUNTIME_CONFIG:-/tmp/playwright-mcp-config.json}"

find_browser() {
  for candidate in /usr/bin/chromium-browser /usr/bin/chromium /usr/bin/google-chrome; do
    if [ -x "$candidate" ]; then printf '%s\n' "$candidate"; return 0; fi
  done
  if [ -d /ms-playwright ]; then
    candidate="$(find /ms-playwright -type f \( -name chrome -o -name chrome-headless-shell \) 2>/dev/null | head -n 1)"
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then printf '%s\n' "$candidate"; return 0; fi
  fi
  return 1
}

find_mcp_cli() {
  for command_name in playwright-mcp playwright-mcp-server; do
    candidate="$(command -v "$command_name" 2>/dev/null || true)"
    if [ -n "$candidate" ]; then printf 'command:%s\n' "$candidate"; return 0; fi
  done
  if [ -f /app/cli.js ]; then printf 'node:%s\n' /app/cli.js; return 0; fi
  candidate="$(find /usr/local/lib/node_modules /usr/lib/node_modules /app /opt -type f -name 'cli.js' -path '*playwright*mcp*' 2>/dev/null | head -n 1)"
  if [ -n "$candidate" ]; then printf 'node:%s\n' "$candidate"; return 0; fi
  return 1
}

[ -f "$TEMPLATE" ] || { echo "Playwright MCP 配置模板不存在: $TEMPLATE" >&2; exit 1; }
BROWSER_EXECUTABLE="$(find_browser || true)"
[ -n "$BROWSER_EXECUTABLE" ] || { echo "镜像中未找到 Chromium/Chrome" >&2; exit 1; }
sed "s#__BROWSER_EXECUTABLE__#$BROWSER_EXECUTABLE#g" "$TEMPLATE" > "$RUNTIME_CONFIG"
MCP_CLI="$(find_mcp_cli || true)"
[ -n "$MCP_CLI" ] || { echo "镜像中未找到 Playwright MCP 启动命令" >&2; exit 1; }
MCP_CLI_TYPE="${MCP_CLI%%:*}"
MCP_CLI_PATH="${MCP_CLI#*:}"
if [ "$MCP_CLI_TYPE" = command ]; then exec "$MCP_CLI_PATH" "$@" --config "$RUNTIME_CONFIG"; fi
exec node "$MCP_CLI_PATH" "$@" --config "$RUNTIME_CONFIG"
EOF_PLAYWRIGHT
chmod 755 "$playwright_entrypoint"

cat > "$playwright_config" <<'EOF_CONFIG'
{
  "browser": {
    "browserName": "chromium",
    "isolated": true,
    "launchOptions": {"headless": true, "executablePath": "__BROWSER_EXECUTABLE__"},
    "contextOptions": {"viewport": {"width": 1920, "height": 1080}, "ignoreHTTPSErrors": true}
  },
  "server": {"port": 8931}
}
EOF_CONFIG
chmod 644 "$playwright_config"

if [ -e "$secret_dir" ] && [ ! -d "$secret_dir" ]; then
  backup_path "$secret_dir"
fi
mkdir -p "$secret_dir"
chmod 700 "$secret_dir"
if [ ! -f "$secret_file" ] || [ ! -s "$secret_file" ]; then
  backup_path "$secret_file"
  while true; do
    read -r -s -p '请输入原 Actuator API 密码: ' password_one
    echo
    read -r -s -p '请再次输入: ' password_two
    echo
    if [ -n "$password_one" ] && [ "$password_one" = "$password_two" ]; then break; fi
    echo "两次输入不一致或密码为空，请重试。" >&2
  done
  umask 077
  printf '%s' "$password_one" > "$secret_file"
  unset password_one password_two
fi
chmod 600 "$secret_file"

containers=(
  wharttest-backend
  wharttest-playwright-mcp
  wharttest-actuator-01
  wharttest-actuator-02
  wharttest-actuator-03
)

for container in "${containers[@]}"; do
  docker container inspect "$container" >/dev/null 2>&1 || {
    echo "找不到容器: $container" >&2
    exit 1
  }
done

echo "[启动] wharttest-backend"
docker start wharttest-backend >/dev/null
echo "[启动] wharttest-playwright-mcp"
docker start wharttest-playwright-mcp >/dev/null
echo "[启动] Actuator 01/02/03"
docker start wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03 >/dev/null

sleep 8
echo
echo "=== 恢复结果 ==="
docker ps -a --filter 'name=wharttest-backend' --filter 'name=wharttest-playwright-mcp' \
  --filter 'name=wharttest-actuator-' \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

failed=false
for container in "${containers[@]}"; do
  running="$(docker inspect "$container" --format '{{.State.Running}}')"
  if [ "$running" != true ]; then
    failed=true
    echo "[失败] $container 未运行，最近日志：" >&2
    docker logs --tail 40 "$container" >&2 || true
  fi
done

if [ "$failed" = true ]; then
  exit 1
fi
echo "5 个容器已全部恢复运行。"
