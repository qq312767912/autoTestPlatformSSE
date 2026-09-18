#!/bin/sh
set -eu

TEMPLATE="${PLAYWRIGHT_MCP_CONFIG_TEMPLATE:-/opt/wharttest/playwright-mcp-config.template.json}"
RUNTIME_CONFIG="${PLAYWRIGHT_MCP_RUNTIME_CONFIG:-/tmp/playwright-mcp-config.json}"

find_browser() {
  for candidate in /usr/bin/chromium-browser /usr/bin/chromium /usr/bin/google-chrome; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if [ -d /ms-playwright ]; then
    candidate="$(find /ms-playwright -type f \( -name chrome -o -name chrome-headless-shell \) 2>/dev/null | head -n 1)"
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  return 1
}

find_mcp_cli() {
  for command_name in playwright-mcp playwright-mcp-server; do
    candidate="$(command -v "$command_name" 2>/dev/null || true)"
    if [ -n "$candidate" ]; then
      printf 'command:%s\n' "$candidate"
      return 0
    fi
  done

  if [ -f /app/cli.js ]; then
    printf 'node:%s\n' /app/cli.js
    return 0
  fi

  candidate="$(
    find /usr/local/lib/node_modules /usr/lib/node_modules /app /opt \
      -type f -name 'cli.js' -path '*playwright*mcp*' 2>/dev/null | head -n 1
  )"
  if [ -n "$candidate" ]; then
    printf 'node:%s\n' "$candidate"
    return 0
  fi

  return 1
}

[ -f "$TEMPLATE" ] || {
  echo "Playwright MCP 配置模板不存在: $TEMPLATE" >&2
  exit 1
}

BROWSER_EXECUTABLE="$(find_browser || true)"
[ -n "$BROWSER_EXECUTABLE" ] || {
  echo "镜像中未找到可执行的 Chromium/Chrome，离线环境无法在运行时安装" >&2
  exit 1
}

sed "s#__BROWSER_EXECUTABLE__#$BROWSER_EXECUTABLE#g" "$TEMPLATE" > "$RUNTIME_CONFIG"
echo "Playwright MCP 使用浏览器: $BROWSER_EXECUTABLE"

MCP_CLI="$(find_mcp_cli || true)"
[ -n "$MCP_CLI" ] || {
  echo "镜像中未找到 Playwright MCP 启动命令或 cli.js" >&2
  echo "可用命令：" >&2
  ls -la /usr/local/bin /usr/bin 2>/dev/null | grep -E 'playwright|mcp' >&2 || true
  exit 1
}

MCP_CLI_TYPE="${MCP_CLI%%:*}"
MCP_CLI_PATH="${MCP_CLI#*:}"
echo "Playwright MCP 使用入口: $MCP_CLI_PATH"

if [ "$MCP_CLI_TYPE" = "command" ]; then
  exec "$MCP_CLI_PATH" "$@" --config "$RUNTIME_CONFIG"
fi
exec node "$MCP_CLI_PATH" "$@" --config "$RUNTIME_CONFIG"
