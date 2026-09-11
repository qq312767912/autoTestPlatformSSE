#!/usr/bin/env bash
set -euo pipefail

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
IMAGE_DIR="${PLATFORM_DIR}/update_platform_version/images"
BASE_COMPOSE="${PLATFORM_DIR}/offline-images/docker-compose.offline.yml"
UPDATE_COMPOSE="${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.update.yml"
CHECKSUM_FILE="${PLATFORM_DIR}/update_platform_version/deploy_env/actuator-update-178fb3ed-arm64-r4.sha256"
PART_PREFIX="${IMAGE_DIR}/actuator-update-178fb3ed-arm64-r4.tar.gz.part"
START_SCRIPT="${PLATFORM_DIR}/update_platform_version/deploy_env/07-start-actuators.sh"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${PLATFORM_DIR}/update_platform_version/deploy-actuator-r4-${STAMP}.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

for required in "$BASE_COMPOSE" "$UPDATE_COMPOSE" "$CHECKSUM_FILE" "$START_SCRIPT"; do
  if [[ ! -f "$required" ]]; then
    echo "错误：缺少文件 $required"
    exit 1
  fi
done

echo "1/5 校验镜像分卷"
cd "$PLATFORM_DIR"
shasum -a 256 -c "$CHECKSUM_FILE"

echo "2/5 合并并导入镜像（不生成超大中间文件）"
cat "${PART_PREFIX}"* | gzip -dc | docker load

echo "3/5 验证镜像"
docker image inspect wharttest-250-actuator:update-178fb3ed-arm64-r4 \
  --format '架构={{.Architecture}} 大小={{.Size}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'

echo "4/5 逐个更新三个执行器，不重启其他服务"
# 密钥恢复、架构校验与逐个重建统一由 07 脚本负责，避免多处逻辑漂移。
BASE_COMPOSE="$BASE_COMPOSE" bash "$START_SCRIPT"

echo "5/5 验证状态、镜像、DNS 和 HTTP"
for index in 01 02 03; do
  name="wharttest-actuator-${index}"
  docker inspect "$name" --format '{{.Name}} 状态={{.State.Status}} 镜像={{.Config.Image}}'
  docker exec "$name" getent hosts www.test.sse.com.cn
  docker exec "$name" python -c 'import urllib.request; r=urllib.request.urlopen("http://www.test.sse.com.cn", timeout=15); print("HTTP", r.status, r.url)'
done

echo "部署完成。请刷新执行器页面并重新运行一条用例。"
echo "日志文件：$LOG_FILE"
