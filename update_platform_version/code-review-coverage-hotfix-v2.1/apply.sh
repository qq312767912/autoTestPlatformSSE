#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BACKUPS_DIR="$PACKAGE_DIR/backups"
TARGET_PATH="/app/code_analysis/services.py"
CHECK_ONLY=false
[ "${1:-}" = "--check" ] && CHECK_ONLY=true
[ "$#" -le 1 ] || { echo "用法: bash apply.sh [--check]" >&2; exit 2; }
[ "$#" -eq 0 ] || [ "$1" = "--check" ] || { echo "未知参数: $1" >&2; exit 2; }

fail() { echo "[失败] $*" >&2; exit 1; }
command -v docker >/dev/null || fail "缺少 docker"
command -v python3 >/dev/null || fail "缺少 python3"
command -v sha256sum >/dev/null || fail "缺少 sha256sum"
(cd "$PACKAGE_DIR" && sha256sum -c SHA256SUMS) || fail "热修复包校验失败"
docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1 || fail "找不到容器 $BACKEND_CONTAINER"
[ "$(docker inspect "$BACKEND_CONTAINER" --format '{{.State.Running}}')" = "true" ] || fail "Backend 容器未运行"

code_mount="$(docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis"}}{{.Source}}{{end}}{{end}}')"
file_mount="$(docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis/services.py"}}{{.Source}}{{end}}{{end}}')"
target_mode=container
target_host_path=""
if [ -n "$code_mount" ]; then
  target_mode=bind
  target_host_path="$code_mount/services.py"
elif [ -n "$file_mount" ]; then
  target_mode=bind
  target_host_path="$file_mount"
fi
[ -z "$target_host_path" ] || [ -f "$target_host_path" ] || fail "挂载目标文件不存在: $target_host_path"

work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
docker cp "$BACKEND_CONTAINER:$TARGET_PATH" "$work_dir/original.py" >/dev/null
python3 "$PACKAGE_DIR/patch_services.py" "$work_dir/original.py" "$PACKAGE_DIR/payload/reference-services.py" "$work_dir/patched.py"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -c 'import ast, pathlib; ast.parse(pathlib.Path("/app/code_analysis/services.py").read_text())'

if [ "$CHECK_ONLY" = true ]; then
  echo "[通过] 当前 services.py 是支持的内网 v3 基线，补丁可以生成；没有修改容器。"
  exit 0
fi

backup_dir="$BACKUPS_DIR/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$backup_dir"
cp "$work_dir/original.py" "$backup_dir/services.py"
printf '%s\n' "$BACKEND_CONTAINER" > "$backup_dir/container.txt"
printf '%s\n' "$target_mode" > "$backup_dir/mode.txt"
printf '%s\n' "$target_host_path" > "$backup_dir/host-path.txt"
printf '%s\n' "$backup_dir" > "$BACKUPS_DIR/latest-backup.txt"
echo "[备份] $backup_dir/services.py"

restore_on_error() {
  code=$?
  trap - EXIT
  [ "$code" -eq 0 ] && return
  echo "[回滚] 应用失败，正在恢复 $backup_dir" >&2
  bash "$PACKAGE_DIR/rollback.sh" "$backup_dir" || echo "[警告] 自动回滚未通过验证，请保留备份并检查容器日志" >&2
  exit "$code"
}
trap restore_on_error EXIT

echo "[应用] 更新 Backend 审查逻辑并校验 Django 加载"
if [ "$target_mode" = bind ]; then
  cp "$work_dir/patched.py" "$target_host_path"
else
  docker cp "$work_dir/patched.py" "$BACKEND_CONTAINER:$TARGET_PATH" >/dev/null
fi
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -m py_compile "$TARGET_PATH"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py check --deploy >/dev/null
docker restart "$BACKEND_CONTAINER" >/dev/null

elapsed=0
while [ "$elapsed" -lt 180 ]; do
  health="$(docker inspect "$BACKEND_CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  if [ "$health" = healthy ] || [ "$health" = running ]; then
    docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py check --deploy >/dev/null && break
  fi
  sleep 3
  elapsed=$((elapsed + 3))
done
[ "$elapsed" -lt 180 ] || fail "Backend 未在 180 秒内恢复并通过 Django 检查"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -c 'from pathlib import Path; s=Path("/app/code_analysis/services.py").read_text(); assert "# code-review-coverage-hotfix-v2" in s and "deferred_findings" in s'
trap - EXIT
rm -rf -- "$work_dir"
echo "[完成] 热修复已生效。回滚命令: bash $PACKAGE_DIR/rollback.sh"
