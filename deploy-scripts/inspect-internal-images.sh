#!/usr/bin/env bash
# 盘点内网 WHartTest 当前使用的 Docker 镜像，便于后续离线替换。
# 本脚本只执行只读 Docker 命令，不打印容器环境变量，也不会修改镜像、容器或数据卷。
#
# 用法：
#   bash deploy-scripts/inspect-internal-images.sh
#   bash deploy-scripts/inspect-internal-images.sh ./docker-compose.offline.yml
#   bash deploy-scripts/inspect-internal-images.sh ./docker-compose.offline.yml ./docker-compose.vision.yml
#
# 报告默认写入当前目录：wharttest-image-report-YYYYmmdd-HHMMSS.txt

set -euo pipefail

command -v docker >/dev/null 2>&1 || {
  echo "错误：未安装 Docker，或 docker 不在 PATH 中。" >&2
  exit 1
}

docker info >/dev/null 2>&1 || {
  echo "错误：Docker 服务未运行，或当前用户无权访问 Docker。" >&2
  exit 1
}

REPORT_FILE="${REPORT_FILE:-./wharttest-image-report-$(date '+%Y%m%d-%H%M%S').txt}"

# 允许传入多个 Compose 文件；不传时自动选择当前目录已有配置。
COMPOSE_ARGS=()
if [ "$#" -gt 0 ]; then
  for compose_file in "$@"; do
    if [ ! -f "$compose_file" ]; then
      echo "错误：找不到 Compose 文件：$compose_file" >&2
      exit 1
    fi
    COMPOSE_ARGS+=("-f" "$compose_file")
  done
elif [ -f ./docker-compose.offline.yml ]; then
  COMPOSE_ARGS+=("-f" "./docker-compose.offline.yml")
  [ -f ./docker-compose.vision.yml ] && COMPOSE_ARGS+=("-f" "./docker-compose.vision.yml")
elif [ -f ./docker-compose.yml ]; then
  COMPOSE_ARGS+=("-f" "./docker-compose.yml")
  [ -f ./docker-compose.vision.yml ] && COMPOSE_ARGS+=("-f" "./docker-compose.vision.yml")
fi

short_id() {
  printf '%s' "$1" | sed 's/^sha256://' | cut -c1-12
}

inspect_image() {
  local image_ref="$1"
  local image_id arch os created size repo_digests revision source

  if ! docker image inspect "$image_ref" >/dev/null 2>&1; then
    printf '%-48s %-12s %-10s %-21s %12s %s\n' \
      "$image_ref" "缺失" "-" "-" "-" "-"
    return
  fi

  image_id="$(docker image inspect "$image_ref" --format '{{.Id}}')"
  arch="$(docker image inspect "$image_ref" --format '{{.Os}}/{{.Architecture}}')"
  created="$(docker image inspect "$image_ref" --format '{{.Created}}' | cut -c1-19)"
  size="$(docker image inspect "$image_ref" --format '{{.Size}}')"
  size="$((size / 1024 / 1024)) MB"
  repo_digests="$(docker image inspect "$image_ref" --format '{{join .RepoDigests ","}}')"
  revision="$(docker image inspect "$image_ref" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)"
  source="$(docker image inspect "$image_ref" --format '{{index .Config.Labels "org.opencontainers.image.source"}}' 2>/dev/null || true)"

  [ -n "$repo_digests" ] || repo_digests="无（本地构建或未保留摘要）"
  [ -n "$revision" ] || revision="无"
  [ -n "$source" ] || source="无"

  printf '%-48s %-12s %-10s %-21s %12s %s\n' \
    "$image_ref" "$(short_id "$image_id")" "$arch" "$created" "$size" "$repo_digests"
  printf '  Git revision: %s\n  OCI source:   %s\n' "$revision" "$source"
}

{
  echo "============================================================"
  echo " WHartTest 内网镜像盘点报告"
  echo "============================================================"
  echo "生成时间：$(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "主机名称：$(hostname)"
  echo "宿主架构：$(uname -m)"
  echo "Docker：  $(docker version --format 'Client {{.Client.Version}} / Server {{.Server.Version}}' 2>/dev/null || docker --version)"
  echo

  echo "[1] WHartTest 相关容器（包括已停止容器）"
  printf '%-31s %-20s %-48s %-13s %s\n' "容器" "状态" "镜像标签" "镜像ID" "创建时间"
  docker ps -a \
    --filter 'name=wharttest' \
    --format '{{.Names}}|{{.Status}}|{{.Image}}|{{.ID}}|{{.CreatedAt}}' \
    | while IFS='|' read -r name status image_ref container_id created_at; do
        current_image_id="$(docker inspect "$container_id" --format '{{.Image}}' 2>/dev/null || true)"
        printf '%-31s %-20s %-48s %-13s %s\n' \
          "$name" "${status:0:20}" "$image_ref" "$(short_id "$current_image_id")" "$created_at"
      done
  echo

  echo "[2] 部署所需镜像明细"
  printf '%-48s %-12s %-10s %-21s %12s %s\n' "镜像标签" "镜像ID" "平台" "创建时间" "大小" "RepoDigest"

  EXPECTED_IMAGES=(
    "wharttest-250-backend:latest"
    "wharttest-250-frontend:latest"
    "wharttest-250-mcp:latest"
    "wharttest-250-weixin-plugin-host:latest"
    "wharttest-250-vision-mcp:latest"
    "postgres:16-alpine"
    "redis:7-alpine"
    "qdrant/qdrant:latest"
    "mcr.microsoft.com/playwright/mcp:latest"
  )

  for image_ref in "${EXPECTED_IMAGES[@]}"; do
    inspect_image "$image_ref"
  done
  echo

  if [ "${#COMPOSE_ARGS[@]}" -gt 0 ]; then
    echo "[3] Compose 实际解析出的服务与镜像"
    echo "Compose 参数：docker compose ${COMPOSE_ARGS[*]}"
    if docker compose "${COMPOSE_ARGS[@]}" config --images >/dev/null 2>&1; then
      while IFS= read -r image_ref; do
        [ -n "$image_ref" ] && inspect_image "$image_ref"
      done < <(docker compose "${COMPOSE_ARGS[@]}" config --images | sort -u)
    else
      echo "警告：Compose 配置解析失败，请在项目部署目录执行并检查配置文件。"
    fi
  else
    echo "[3] 未找到 Compose 文件，已跳过 Compose 服务检查。"
  fi
  echo

  echo "[4] WHartTest 容器的数据卷和目录挂载（不显示环境变量）"
  docker ps -a --filter 'name=wharttest' --format '{{.ID}}' | while IFS= read -r container_id; do
    [ -n "$container_id" ] || continue
    docker inspect "$container_id" --format \
      '{{.Name}}|{{range .Mounts}}{{.Type}}:{{.Name}}{{.Source}} -> {{.Destination}} (RW={{.RW}}); {{end}}'
  done
  echo

  echo "[5] 全部本地镜像（用于发现自定义标签）"
  docker image ls --digests --no-trunc \
    --format '{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Digest}}|{{.CreatedSince}}|{{.Size}}' \
    | sort
  echo

  echo "替换前建议："
  echo "1. 保存本报告，并另外执行 docker compose ps。"
  echo "2. 新镜像使用明确版本标签，不要直接覆盖 latest。"
  echo "3. 只重建容器，不执行 docker compose down -v，避免删除数据卷。"
  echo "4. 替换后再次运行本脚本，对比镜像 ID、平台和创建时间。"
} | tee "$REPORT_FILE"

echo
echo "报告已保存：$REPORT_FILE"
