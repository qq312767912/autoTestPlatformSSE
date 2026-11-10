#!/usr/bin/env bash
set -euo pipefail
# Only verify reused images; never pull or replace them.
for image in \
 wharttest-250-actuator:update-ac65a6fb-v2.8-r3-auth-failfast-arm64 \
 wharttest-250-vision-mcp:update-01339484-arm64-r2; do
 docker image inspect "$image" >/dev/null || { echo "缺少复用镜像：$image，请先保留/恢复现有内网镜像。" >&2; exit 1; }
 test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = linux/arm64
done
echo '复用镜像检查通过；Postgres/Redis/Qdrant/MCP/Playwright 保留当前版本。'
