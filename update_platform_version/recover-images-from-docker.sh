#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
timestamp="$(date +%Y%m%d-%H%M%S)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/images-recovered-$timestamp}"
PART_SIZE="${PART_SIZE:-280m}"
BACKUP_VOLUMES=false
if [ "${1:-}" = "--with-volumes" ]; then
  BACKUP_VOLUMES=true
elif [ "$#" -gt 0 ]; then
  echo "用法: $0 [--with-volumes]" >&2
  exit 2
fi

command -v docker >/dev/null 2>&1 || { echo "未找到 docker" >&2; exit 1; }
command -v gzip >/dev/null 2>&1 || { echo "未找到 gzip" >&2; exit 1; }
command -v split >/dev/null 2>&1 || { echo "未找到 split" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker 未运行或当前用户无权访问" >&2; exit 1; }

# 当前内网运行的 10 个唯一镜像。三个 Actuator 容器共用同一镜像，只导出一次。
images=(
  "wharttest-backend|backend-update-d595a628-review-fix-r5-arm64.tar.gz"
  "wharttest-frontend|frontend-update-d595a628-review-fix-r5-arm64.tar.gz"
  "wharttest-actuator-01|actuator-update-178fb3ed-arm64-r4.tar.gz"
  "wharttest-playwright-mcp|playwright-mcp-alpine-latest-arm64.tar.gz"
  "wharttest-vision-mcp|vision-mcp-update-01339484-arm64-r2.tar.gz"
  "wharttest-qdrant|qdrant-kylin-arm64-v1.16.0-page64k.tar.gz"
  "wharttest-mcp|mcp-alpine-latest-arm64.tar.gz"
  "wharttest-weixin-plugin-host|weixin-plugin-host-latest-arm64.tar.gz"
  "wharttest-postgres|postgres-16-alpine-arm64.tar.gz"
  "wharttest-redis|redis-7-alpine-arm64.tar.gz"
)

resolved_images=()
declared_images=()
for item in "${images[@]}"; do
  container="${item%%|*}"
  if ! docker container inspect "$container" >/dev/null 2>&1; then
    echo "无法恢复，找不到容器: $container" >&2
    exit 1
  fi
  source_image="$(docker container inspect "$container" --format '{{.Image}}')"
  if ! docker image inspect "$source_image" >/dev/null 2>&1; then
    echo "容器 $container 引用的镜像层已不存在: $source_image" >&2
    exit 1
  fi
  declared_image="$(docker container inspect "$container" --format '{{.Config.Image}}')"
  echo "[找到] $container -> $declared_image ($source_image)"
  resolved_images+=("$source_image")
  declared_images+=("$declared_image")
done

mkdir -p "$OUTPUT_DIR"
printf '恢复目录: %s\n' "$OUTPUT_DIR"
printf 'container\tdeclared_image\timage_id\tpackage_prefix\n' > "$OUTPUT_DIR/RECOVERY_MANIFEST.tsv"

for index in "${!images[@]}"; do
  item="${images[$index]}"
  container="${item%%|*}"
  filename="${item#*|}"
  source_image="${resolved_images[$index]}"
  declared_image="${declared_images[$index]}"
  prefix="$OUTPUT_DIR/$filename.part"
  echo "[导出] $container（镜像 ID: $source_image）"
  docker save "$source_image" | gzip -1 | split -b "$PART_SIZE" -d -a 3 - "$prefix"
  printf '%s\t%s\t%s\t%s.part*\n' \
    "$container" "$declared_image" "$source_image" "$filename" >> "$OUTPUT_DIR/RECOVERY_MANIFEST.tsv"
done

if [ "$BACKUP_VOLUMES" = true ]; then
  echo
  echo "[数据卷备份] 将短暂停止当前运行的容器，备份完成后自动启动。"
  mapfile -t running_containers < <(docker ps --format '{{.Names}}')
  mapfile -t named_volumes < <(
    docker inspect "${running_containers[@]}" \
      --format '{{range .Mounts}}{{if eq .Type "volume"}}{{println .Name}}{{end}}{{end}}' |
      awk 'NF' | sort -u
  )

  if [ "${#named_volumes[@]}" -eq 0 ]; then
    echo "[数据卷备份] 没有发现命名数据卷。"
  else
    printf 'volume\tarchive\n' > "$OUTPUT_DIR/VOLUME_MANIFEST.tsv"
    restart_needed=true
    restart_containers() {
      if [ "${restart_needed:-false}" = true ] && [ "${#running_containers[@]}" -gt 0 ]; then
        echo "[恢复服务] 启动原先运行的容器"
        docker start "${running_containers[@]}" >/dev/null || true
      fi
    }
    trap restart_containers EXIT INT TERM

    docker stop "${running_containers[@]}" >/dev/null
    for volume in "${named_volumes[@]}"; do
      safe_name="$(printf '%s' "$volume" | tr -c 'A-Za-z0-9_.-' '_')"
      archive="$safe_name.volume.tar.gz"
      echo "[备份数据卷] $volume -> $archive"
      docker run --rm \
        -v "$volume:/source:ro" \
        -v "$OUTPUT_DIR:/backup" \
        postgres:16-alpine \
        tar -czf "/backup/$archive" -C /source .
      printf '%s\t%s\n' "$volume" "$archive" >> "$OUTPUT_DIR/VOLUME_MANIFEST.tsv"
    done
    docker start "${running_containers[@]}" >/dev/null
    restart_needed=false
    trap - EXIT INT TERM
    echo "[数据卷备份] 完成，原运行容器已重新启动。"
  fi
fi

(
  cd "$OUTPUT_DIR"
  shopt -s nullglob
  artifacts=(./*.part* ./*.volume.tar.gz)
  sha256sum "${artifacts[@]}" > SHA256SUMS
)

echo
echo "恢复完成，文件如下："
ls -lh "$OUTPUT_DIR"
echo
echo "校验："
cat "$OUTPUT_DIR/SHA256SUMS"
echo
echo "注意：重新导出后压缩包 SHA256 会变化，但包内 Docker 镜像 ID 不变。"
