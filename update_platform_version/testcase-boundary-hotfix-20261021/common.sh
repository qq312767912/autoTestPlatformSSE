#!/usr/bin/env bash
# Shared helpers; requires Bash and Docker, no host Python dependency.
set -Eeuo pipefail
TARGET=/app/testcases/review_service.py
fail() { echo "[失败] $*" >&2; exit 1; }
choose_container() {
  CONTAINER="${1:-${BACKEND_CONTAINER:-}}"
  if [ -z "$CONTAINER" ]; then
    local found='' name
    for name in wharttest-backend wharttest-new-backend; do
      if docker inspect "$name" >/dev/null 2>&1; then
        [ -z "$found" ] || fail '检测到多个 Backend，请显式传入容器名'
        found="$name"
      fi
    done
    CONTAINER="$found"
  fi
  [ -n "$CONTAINER" ] || fail '请显式指定 Backend 容器名称'
  [ "$(docker inspect "$CONTAINER" --format '{{.State.Running}}')" = true ] || fail "容器未运行：$CONTAINER"
  PYTHON="$(docker exec "$CONTAINER" sh -c 'if [ -x /opt/venv/bin/python ]; then echo /opt/venv/bin/python; else command -v python3 || command -v python; fi')"
  [ -n "$PYTHON" ] || fail '容器内未找到 Python'
}
health_check() {
  local deadline=$((SECONDS + 300))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if docker exec "$CONTAINER" "$PYTHON" -c 'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/admin/login/",timeout=3); assert r.status==200' >/dev/null 2>&1; then
      local health
      health="$(docker inspect "$CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')"
      case "$health" in healthy|none) echo '[健康] HTTP 200，容器健康检查通过'; return 0;; esac
    fi
    sleep 3
  done
  echo '[失败] 300 秒内服务未恢复健康' >&2
  return 1
}
restore_file() {
  if [ -n "$HOST_TARGET" ]; then
    [ -f "$HOST_TARGET" ] || fail "宿主机挂载路径不存在：$HOST_TARGET"
    # Write through the existing inode so file bind mounts see the restored bytes.
    cat "$BACKUP/original.py" > "$HOST_TARGET"
  else
    docker cp "$BACKUP/original.py" "$CONTAINER:$TARGET"
  fi
}
