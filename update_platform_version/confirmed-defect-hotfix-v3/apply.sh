#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PAYLOAD="$DIR/services.py"
CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
TARGET="/app/code_analysis/services.py"
BASE_SHA="a10722971762e38cdb8419a5a3ab40a8bc88dfb59e90186b43dccdb76409455f"
V2_SHA="c40c0916de5dc872445612322de9d3d16de8eb7e6dc81902da19788e42e31698"
PATCHED_SHA="8f208701e0e4d557b106b8a242a210654b7e2324e4025f6de792cc544be3b945"
BACKUP_DIR="$DIR/backups/$(date +%Y%m%d-%H%M%S)"

fail() { echo "[失败] $*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail "未安装 docker"
[ -f "$PAYLOAD" ] || fail "缺少 $PAYLOAD"
[ "$(sha256sum "$PAYLOAD" | awk '{print $1}')" = "$PATCHED_SHA" ] || fail "v3 文件校验失败"
docker inspect "$CONTAINER" >/dev/null 2>&1 || fail "找不到容器 $CONTAINER"

current_sha="$(docker exec "$CONTAINER" sha256sum "$TARGET" | awk '{print $1}')"
if [ "$current_sha" = "$PATCHED_SHA" ]; then
  echo "[完成] v3 已经生效，无需重复应用"
  exit 0
fi

mkdir -p "$BACKUP_DIR"
if [ "$current_sha" = "$BASE_SHA" ]; then
  echo "[基线] 当前容器使用内网镜像原始文件"
  docker cp "$CONTAINER:$TARGET" "$BACKUP_DIR/services.py"
elif [ "$current_sha" = "$V2_SHA" ]; then
  echo "[恢复] 检测到不兼容的 v2 文件，从 Backend 原始镜像提取基线"
  image_name="$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')"
  temp_container="extract-v3-base-$(date +%s)-$$"
  docker create --name "$temp_container" "$image_name" >/dev/null
  docker cp "$temp_container:$TARGET" "$BACKUP_DIR/services.py"
  docker rm "$temp_container" >/dev/null
  extracted_sha="$(sha256sum "$BACKUP_DIR/services.py" | awk '{print $1}')"
  [ "$extracted_sha" = "$BASE_SHA" ] || fail "镜像原始文件 SHA256 不匹配：$extracted_sha"
else
  fail "当前 services.py 是未知版本（SHA256=$current_sha），为防止覆盖其他修复已停止。"
fi
restore() {
  code=$?
  [ "$code" -eq 0 ] && return
  echo "[回滚] v3 应用失败，恢复原文件" >&2
  docker cp "$BACKUP_DIR/services.py" "$CONTAINER:$TARGET" >/dev/null 2>&1 || true
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  exit "$code"
}
trap restore EXIT

docker cp "$PAYLOAD" "$CONTAINER:$TARGET"
docker exec "$CONTAINER" /opt/venv/bin/python -m py_compile "$TARGET"
docker restart "$CONTAINER" >/dev/null

elapsed=0
while [ "$elapsed" -lt 300 ]; do
  health="$(docker inspect "$CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  case "$health" in
    healthy|running) break ;;
    stopped) fail "Backend 已停止" ;;
  esac
  sleep 5
  elapsed=$((elapsed + 5))
done
[ "$elapsed" -lt 300 ] || { docker logs --tail 150 "$CONTAINER" || true; fail "Backend 300 秒内未恢复"; }

actual="$(docker exec "$CONTAINER" sha256sum "$TARGET" | awk '{print $1}')"
[ "$actual" = "$PATCHED_SHA" ] || fail "重启后文件校验失败：$actual"
printf '%s\n' "$BACKUP_DIR/services.py" > "$DIR/latest-backup.txt"
trap - EXIT
echo "[完成] v3 最小热修复已生效，Backend=$health"
echo "[原始基线] $BACKUP_DIR/services.py"
