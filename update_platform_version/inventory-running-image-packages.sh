#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${IMAGES_DIR:-}" ]; then
  IMAGES_DIR="$(cd "$IMAGES_DIR" && pwd)"
elif [ -d "$SCRIPT_DIR/../images" ]; then
  IMAGES_DIR="$(cd "$SCRIPT_DIR/../images" && pwd)"
else
  IMAGES_DIR="$SCRIPT_DIR/images"
fi

scope="running"
if [ "${1:-}" = "--all" ]; then
  scope="all"
elif [ "$#" -gt 0 ]; then
  echo "用法: $0 [--all]" >&2
  exit 2
fi

command -v docker >/dev/null 2>&1 || { echo "未找到 docker" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "未找到 python3" >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "未找到 tar" >&2; exit 1; }
[ -d "$IMAGES_DIR" ] || { echo "找不到镜像分包目录: $IMAGES_DIR" >&2; exit 1; }

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/wharttest-image-inventory.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
containers_tsv="$work_dir/containers.tsv"
packages_tsv="$work_dir/packages.tsv"
: > "$containers_tsv"
: > "$packages_tsv"

if [ "$scope" = "all" ]; then
  container_ids="$(docker ps -aq)"
else
  container_ids="$(docker ps -q)"
fi

if [ -n "$container_ids" ]; then
  docker inspect $container_ids \
    --format '{{.Name}}|{{.Config.Image}}|{{.Image}}|{{.State.Status}}' |
    sed 's#^/##' | awk -F '|' 'BEGIN {OFS="\t"} {print $1, $2, $3, $4}' |
    sort -u > "$containers_tsv"
fi

read_manifest() {
  archive_label="$1"
  python3 -c '
import json, sys
label = sys.argv[1]
for item in json.load(sys.stdin):
    config = item.get("Config", "")
    config_id = "sha256:" + config.rsplit("/", 1)[-1].removesuffix(".json")
    tags = ",".join(item.get("RepoTags") or ["<none>"])
    print(f"{config_id}\t{label}\t{tags}")
' "$archive_label" >> "$packages_tsv"
}

scan_direct() {
  archive="$1"
  label="$(basename "$archive")"
  echo "[解析] $label" >&2
  gzip -dc "$archive" | tar -xOf - manifest.json | read_manifest "$label"
}

scan_parts() {
  prefix="$1"
  label="$(basename "$prefix").part*"
  echo "[解析] $label" >&2
  cat "$prefix".part* | gzip -dc | tar -xOf - manifest.json | read_manifest "$label"
}

shopt -s nullglob
for archive in "$IMAGES_DIR"/*.tar.gz; do
  scan_direct "$archive"
done
for first_part in "$IMAGES_DIR"/*.tar.gz.part000; do
  scan_parts "${first_part%.part000}"
done
for first_part in "$IMAGES_DIR"/*.tar.gz.partaa; do
  scan_parts "${first_part%.partaa}"
done
sort -u -o "$packages_tsv" "$packages_tsv"

printf '\n=== 当前%s容器使用的镜像 ===\n' "$([ "$scope" = running ] && echo '运行中' || echo '全部')"
printf '%-32s %-48s %-14s %s\n' '容器' '声明镜像' '状态' '镜像ID'
while IFS=$'\t' read -r name image image_id status; do
  [ -n "$name" ] || continue
  printf '%-32s %-48s %-14s %s\n' "$name" "$image" "$status" "$image_id"
done < "$containers_tsv"

printf '\n=== 当前镜像对应的上传分包 ===\n'
while IFS=$'\t' read -r name image image_id status; do
  [ -n "$name" ] || continue
  matches="$(awk -F '\t' -v id="$image_id" '$1 == id {print $2 "\t" $3}' "$packages_tsv")"
  if [ -n "$matches" ]; then
    while IFS=$'\t' read -r package tags; do
      printf '[在用] %-28s -> %-55s 包内标签: %s\n' "$name" "$package" "$tags"
    done <<< "$matches"
  else
    printf '[未匹配] %-26s -> 镜像ID %s（分包目录中未找到）\n' "$name" "$image_id"
  fi
done < "$containers_tsv"

printf '\n=== 未被当前容器使用的分包（候选整理，不会自动删除） ===\n'
while IFS=$'\t' read -r image_id package tags; do
  if ! awk -F '\t' -v id="$image_id" '$3 == id {found=1} END {exit !found}' "$containers_tsv"; then
    printf '%-58s 镜像ID: %s  包内标签: %s\n' "$package" "$image_id" "$tags"
  fi
done < "$packages_tsv"

printf '\n说明：匹配使用 Docker archive 内 manifest.json 的 Config ID，不依赖分包文件名或镜像标签。\n'
