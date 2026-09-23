#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
timestamp="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-$SCRIPT_DIR/docker-running-images-$timestamp.log}"

command -v docker >/dev/null 2>&1 || { echo "未找到 docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker 未运行或当前用户无权访问" >&2; exit 1; }

exec > >(tee "$LOG_FILE") 2>&1

echo "WHartTest 内网 Docker 运行镜像盘点"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "宿主机: $(hostname)"
echo "架构: $(uname -m)"
echo "Docker: $(docker version --format '{{.Server.Version}}')"

echo
echo "=== 1. 运行中容器 ==="
docker ps --no-trunc \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.ID}}'

echo
echo "=== 2. 容器声明镜像与实际镜像 ID ==="
running_ids="$(docker ps -q)"
if [ -z "$running_ids" ]; then
  echo "当前没有运行中容器"
  echo "日志文件: $LOG_FILE"
  exit 0
fi

docker inspect $running_ids \
  --format '{{.Name}}|{{.Config.Image}}|{{.Image}}|{{.State.Status}}' |
  sed 's#^/##' |
  sort

echo
echo "=== 3. 去重后的运行镜像 ID ==="
unique_ids="$(docker inspect $running_ids --format '{{.Image}}' | sort -u)"
printf '%s\n' "$unique_ids"

echo
echo "=== 4. 去重镜像数量 ==="
printf '%s\n' "$unique_ids" | awk 'NF {count++} END {print count+0}'

echo
echo "=== 5. 每个实际镜像 ID 在本机上的标签 ==="
while IFS= read -r image_id; do
  [ -n "$image_id" ] || continue
  tags="$(docker image inspect "$image_id" --format '{{join .RepoTags ","}}' 2>/dev/null || true)"
  [ -n "$tags" ] || tags="<none>"
  printf '%s|%s\n' "$image_id" "$tags"
done <<< "$unique_ids"

echo
echo "=== 6. Docker 镜像总览 ==="
docker image ls --no-trunc \
  --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}\t{{.Size}}'

echo
echo "盘点完成。"
echo "请把这个日志文件发回: $LOG_FILE"
