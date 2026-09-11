#!/usr/bin/env bash
set -euo pipefail

expected=(
  'wharttest-backend|wharttest-250-backend:update-ecff56e4-r1-arm64'
  'wharttest-frontend|wharttest-250-frontend:update-ecff56e4-r1-arm64'
  'wharttest-vision-mcp|wharttest-250-vision-mcp:update-01339484-arm64-r2'
  'wharttest-mcp|wharttest-250-mcp-alpine:latest'
  'wharttest-qdrant|qdrant-kylin-arm64:v1.16.0-page64k'
  'wharttest-playwright-mcp|playwright-mcp-alpine:latest'
)

for entry in "${expected[@]}"; do
  IFS='|' read -r container image <<< "$entry"
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  [ "$actual" = "$image" ] || { echo "[失败] $container 应为 $image，实际为 $actual" >&2; exit 1; }
  state="$(docker inspect "$container" --format '{{.State.Status}}')"
  [ "$state" = "running" ] || { echo "[失败] $container 状态为 $state" >&2; exit 1; }
  echo "[通过] $container -> $actual"
done

curl -fsS http://127.0.0.1:8912/admin/login/ >/dev/null
curl -fsS http://127.0.0.1:8913/ >/dev/null
echo "[通过] Backend 和 Frontend HTTP 检查"

media_source='/projects/ai-test-platform/offline-images/data/media'
frontend_mounts="$(docker inspect wharttest-frontend --format '{{range .Mounts}}{{println .Source "|" .Destination "|" .RW}}{{end}}')"
echo "$frontend_mounts" | grep -F -- "$media_source | /app/data/media | false" >/dev/null || {
  echo "[失败] Frontend 未只读挂载 Backend 的共享媒体目录，请执行 15-fix-artifact-download-volume.sh" >&2
  exit 1
}
docker exec wharttest-frontend test -d /app/data/media
echo "[通过] Skill 交付物下载媒体卷"

# 在真实信创宿主机内验证 OCR 的 ARM64 二进制依赖能够加载。
docker exec wharttest-vision-mcp python -c \
  'import onnxruntime; from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print("Vision OCR runtime OK")'
echo "[通过] Vision MCP OCR 运行库可在当前宿主机加载"

for index in 01 02 03; do
  container="wharttest-actuator-$index"
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  [ "$actual" = 'wharttest-250-actuator:update-178fb3ed-arm64-r4' ] || {
    echo "[失败] $container 镜像错误：$actual" >&2
    exit 1
  }
  status="$(docker inspect "$container" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}')"
  case "$status" in
    running/healthy|running/no-healthcheck) echo "[通过] $container -> $status" ;;
    *) docker logs --tail 100 "$container"; echo "[失败] $container -> $status" >&2; exit 1 ;;
  esac
done

echo "[提示] 请在平台“执行器”页面确认 actuator-01、02、03 均在线。"

docker ps --filter 'name=wharttest' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
