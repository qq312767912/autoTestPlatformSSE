#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-wharttest-frontend}"
BACKUP_ROOT="$SCRIPT_DIR/backups"
SERVICES_PAYLOAD="$SCRIPT_DIR/payload/backend/services.py"
VIEWS_PAYLOAD="$SCRIPT_DIR/payload/backend/views.py"
FRONTEND_PAYLOAD="$SCRIPT_DIR/payload/frontend/frontend-html.tar.gz"

fail() { echo "[失败] $*" >&2; exit 1; }
container_running() {
  docker inspect "$1" >/dev/null 2>&1 &&
    [ "$(docker inspect "$1" --format '{{.State.Running}}')" = "true" ]
}
wait_container() {
  local container="$1" elapsed=0 health=""
  while [ "$elapsed" -lt 300 ]; do
    health="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
    case "$health" in
      healthy|running) return 0 ;;
      unhealthy|stopped) docker logs --tail 120 "$container" 2>&1 || true; return 1 ;;
    esac
    sleep 3
    elapsed=$((elapsed + 3))
  done
  return 1
}

command -v docker >/dev/null 2>&1 || fail "未安装 docker"
command -v sha256sum >/dev/null 2>&1 || fail "未安装 sha256sum"
(cd "$SCRIPT_DIR" && sha256sum -c SHA256SUMS) || fail "热修复载荷校验失败"
container_running "$BACKEND_CONTAINER" || fail "Backend 容器不存在或未运行：$BACKEND_CONTAINER"
container_running "$FRONTEND_CONTAINER" || fail "Frontend 容器不存在或未运行：$FRONTEND_CONTAINER"

grep -q 'def _prioritize_findings_for_report(findings, limit=None)' "$SERVICES_PAYLOAD" || fail "services.py 缺少完整三分类保留逻辑"
grep -q 'review_status' "$SERVICES_PAYLOAD" || fail "services.py 缺少逐文件审查状态"
grep -q 'AI未完成文件' "$VIEWS_PAYLOAD" || fail "views.py 缺少 Markdown 覆盖明细"

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$BACKUP_ROOT/$timestamp"
mkdir -p "$backup_dir/backend"
docker cp "$BACKEND_CONTAINER:/app/code_analysis/services.py" "$backup_dir/backend/services.py"
docker cp "$BACKEND_CONTAINER:/app/code_analysis/views.py" "$backup_dir/backend/views.py"
docker exec "$FRONTEND_CONTAINER" tar -C /usr/share/nginx/html -czf /tmp/code-review-coverage-frontend-backup.tar.gz .
docker cp "$FRONTEND_CONTAINER:/tmp/code-review-coverage-frontend-backup.tar.gz" "$backup_dir/frontend-html.tar.gz"
docker exec "$FRONTEND_CONTAINER" rm -f /tmp/code-review-coverage-frontend-backup.tar.gz

backend_mount="$(docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis"}}{{.Source}}{{end}}{{end}}')"
services_mount="$(docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis/services.py"}}{{.Source}}{{end}}{{end}}')"
views_mount="$(docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis/views.py"}}{{.Source}}{{end}}{{end}}')"
frontend_mount="$(docker inspect "$FRONTEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/usr/share/nginx/html"}}{{.Source}}{{end}}{{end}}')"

backend_mode="container"
services_target="/app/code_analysis/services.py"
views_target="/app/code_analysis/views.py"
if [ -n "$backend_mount" ]; then
  backend_mode="bind"
  services_target="$backend_mount/services.py"
  views_target="$backend_mount/views.py"
elif [ -n "$services_mount" ] || [ -n "$views_mount" ]; then
  [ -n "$services_mount" ] && [ -n "$views_mount" ] || fail "Backend 仅挂载了部分代码审查文件，拒绝不完整更新"
  backend_mode="bind"
  services_target="$services_mount"
  views_target="$views_mount"
fi

frontend_mode="container"
frontend_target="/usr/share/nginx/html"
if [ -n "$frontend_mount" ]; then
  frontend_mode="bind"
  frontend_target="$frontend_mount"
fi

rollback_on_error() {
  local status=$?
  [ "$status" -eq 0 ] && return
  echo "[回滚] 应用失败，恢复原文件..." >&2
  if [ "$backend_mode" = "bind" ]; then
    cp "$backup_dir/backend/services.py" "$services_target" || true
    cp "$backup_dir/backend/views.py" "$views_target" || true
  else
    docker cp "$backup_dir/backend/services.py" "$BACKEND_CONTAINER:/app/code_analysis/services.py" >/dev/null 2>&1 || true
    docker cp "$backup_dir/backend/views.py" "$BACKEND_CONTAINER:/app/code_analysis/views.py" >/dev/null 2>&1 || true
  fi
  if [ "$frontend_mode" = "bind" ]; then
    find "$frontend_target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + || true
    tar -C "$frontend_target" -xzf "$backup_dir/frontend-html.tar.gz" || true
  else
    docker cp "$backup_dir/frontend-html.tar.gz" "$FRONTEND_CONTAINER:/tmp/frontend-rollback.tar.gz" >/dev/null 2>&1 || true
    docker exec "$FRONTEND_CONTAINER" sh -c 'find /usr/share/nginx/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -C /usr/share/nginx/html -xzf /tmp/frontend-rollback.tar.gz && rm -f /tmp/frontend-rollback.tar.gz' || true
  fi
  docker restart "$BACKEND_CONTAINER" "$FRONTEND_CONTAINER" >/dev/null 2>&1 || true
  exit "$status"
}
trap rollback_on_error EXIT

echo "[提醒] 应用过程会重启 Backend 和 Frontend，请先确认没有正在执行的审查任务。"
if [ "$backend_mode" = "bind" ]; then
  [ -f "$services_target" ] && [ -f "$views_target" ] || fail "Backend 宿主机源码路径不存在"
  cp "$SERVICES_PAYLOAD" "$services_target"
  cp "$VIEWS_PAYLOAD" "$views_target"
else
  docker cp "$SERVICES_PAYLOAD" "$BACKEND_CONTAINER:/app/code_analysis/services.py"
  docker cp "$VIEWS_PAYLOAD" "$BACKEND_CONTAINER:/app/code_analysis/views.py"
fi

if [ "$frontend_mode" = "bind" ]; then
  [ -d "$frontend_target" ] || fail "Frontend 宿主机静态目录不存在：$frontend_target"
  tar -C "$frontend_target" -xzf "$FRONTEND_PAYLOAD"
else
  docker cp "$FRONTEND_PAYLOAD" "$FRONTEND_CONTAINER:/tmp/code-review-coverage-frontend.tar.gz"
  docker exec "$FRONTEND_CONTAINER" tar -C /usr/share/nginx/html -xzf /tmp/code-review-coverage-frontend.tar.gz
  docker exec "$FRONTEND_CONTAINER" rm -f /tmp/code-review-coverage-frontend.tar.gz
fi

docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -m py_compile /app/code_analysis/services.py /app/code_analysis/views.py
docker restart "$BACKEND_CONTAINER" "$FRONTEND_CONTAINER" >/dev/null
wait_container "$BACKEND_CONTAINER" || fail "Backend 重启后未恢复健康"
wait_container "$FRONTEND_CONTAINER" || fail "Frontend 重启后未恢复健康"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -c 'from pathlib import Path; s=Path("/app/code_analysis/services.py").read_text(); assert "def _prioritize_findings_for_report(findings, limit=None)" in s; assert "deferred_findings" in s; print("backend hotfix OK")'
docker exec "$FRONTEND_CONTAINER" test -s /usr/share/nginx/html/index.html

printf 'BACKUP_DIR=%q\nBACKEND_MODE=%q\nSERVICES_TARGET=%q\nVIEWS_TARGET=%q\nFRONTEND_MODE=%q\nFRONTEND_TARGET=%q\nBACKEND_CONTAINER=%q\nFRONTEND_CONTAINER=%q\n' \
  "$backup_dir" "$backend_mode" "$services_target" "$views_target" "$frontend_mode" "$frontend_target" "$BACKEND_CONTAINER" "$FRONTEND_CONTAINER" > "$BACKUP_ROOT/latest.env"
trap - EXIT
echo "[完成] 代码审查覆盖与三分类热修复已生效。"
echo "[备份] $backup_dir"
echo "[回滚] bash $SCRIPT_DIR/rollback.sh"
