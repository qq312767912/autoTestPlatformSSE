#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: $0 <输出文件> <backend镜像> <frontend镜像> [actuator镜像] [vision-mcp镜像] [内网平台目录]" >&2
  exit 2
}

[ "$#" -ge 3 ] || usage

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$SCRIPT_DIR/templates/docker-compose.update.yml.tpl"
OUTPUT="$1"
BACKEND_IMAGE="$2"
FRONTEND_IMAGE="$3"
ACTUATOR_IMAGE="${4:-wharttest-250-actuator:update-01339484-arm64-r3}"
VISION_IMAGE="${5:-wharttest-250-vision-mcp:update-01339484-arm64-r2}"
PLATFORM_DIR="${6:-/projects/ai-test-platform}"

[ -f "$TEMPLATE" ] || { echo "模板不存在: $TEMPLATE" >&2; exit 1; }
for value in "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$ACTUATOR_IMAGE" "$VISION_IMAGE" "$PLATFORM_DIR"; do
  case "$value" in
    *'|'*|*$'\n'*) echo "参数包含不支持的字符: $value" >&2; exit 1 ;;
  esac
done

mkdir -p "$(dirname "$OUTPUT")"
TEMP_OUTPUT="$(mktemp)"
trap 'rm -f "$TEMP_OUTPUT"' EXIT

sed \
  -e "s|__BACKEND_IMAGE__|$BACKEND_IMAGE|g" \
  -e "s|__FRONTEND_IMAGE__|$FRONTEND_IMAGE|g" \
  -e "s|__ACTUATOR_IMAGE__|$ACTUATOR_IMAGE|g" \
  -e "s|__VISION_IMAGE__|$VISION_IMAGE|g" \
  -e "s|__PLATFORM_DIR__|$PLATFORM_DIR|g" \
  "$TEMPLATE" > "$TEMP_OUTPUT"

if grep -q '__[A-Z_]*__' "$TEMP_OUTPUT"; then
  echo "生成失败：配置中仍有未替换占位符" >&2
  exit 1
fi

# 固化历史部署修复：这些配置缺一不可。
grep -q '/offline-images/data/media:/app/data/media:ro' "$TEMP_OUTPUT" || { echo '缺少前端媒体卷' >&2; exit 1; }
[ "$(grep -c 'www.test.sse.com.cn:' "$TEMP_OUTPUT")" -ge 1 ] || { echo '缺少内网域名映射' >&2; exit 1; }
for service in actuator-01 actuator-02 actuator-03; do
  grep -q "^  ${service}:" "$TEMP_OUTPUT" || { echo "缺少服务: $service" >&2; exit 1; }
done

mv "$TEMP_OUTPUT" "$OUTPUT"
trap - EXIT

echo "已生成: $OUTPUT"
echo "Backend: $BACKEND_IMAGE"
echo "Frontend: $FRONTEND_IMAGE"
echo "Actuator: $ACTUATOR_IMAGE"
echo "Vision MCP: $VISION_IMAGE"
echo "已包含: 前端媒体卷、Backend/Playwright MCP/执行器统一域名映射、三个执行器配置。"
