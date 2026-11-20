#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
[[ "$(uname -m)" == aarch64 ]] || { echo '必须在 ARM64 部署服务器执行';exit 1; }
command -v python3 >/dev/null
command -v timeout >/dev/null
docker compose version
docker info --format 'DockerRootDir={{.DockerRootDir}}'
getconf PAGESIZE
free -h
df -h
for c in wharttest-backend wharttest-frontend wharttest-postgres wharttest-redis wharttest-qdrant wharttest-mcp wharttest-playwright-mcp wharttest-vision-mcp wharttest-crg wharttest-semgrep wharttest-actuator-{01,02,03}; do docker inspect "$c" --format '{{.Name}} {{.Config.Image}} {{.State.Status}}'; done
# 新旧审查路由切换前，要求旧任务已全部完成；不清理 Redis。
timeout 40 docker exec -i wharttest-backend python - < "$HERE/check_queues.py"
echo '预检通过；请保持停止提交任务直到部署验收完成。'
