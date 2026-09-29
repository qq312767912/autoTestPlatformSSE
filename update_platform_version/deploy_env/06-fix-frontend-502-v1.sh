#!/usr/bin/env bash
# Backend 容器重建后 IP 可能改变，Frontend Nginx 仍持有旧解析时会导致
# /api/ 和 /ws/ 返回 502。本脚本先验证 Backend，再按需重启 Frontend 并复验。
#
# 用法：
#   bash 06-fix-frontend-502-v1.sh
set -euo pipefail

BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-wharttest-frontend}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8912/api/auth/login-key/}"
FRONTEND_PROXY_URL="${FRONTEND_PROXY_URL:-http://127.0.0.1:8913/api/auth/login-key/}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"

fail() { echo "[失败] $*" >&2; exit 1; }
ok() { echo "[通过] $*"; }
info() { echo "[信息] $*"; }

command -v docker >/dev/null 2>&1 || fail "未找到 docker"
command -v curl >/dev/null 2>&1 || fail "未找到 curl"

container_state() {
  docker inspect "$1" --format '{{.State.Status}}' 2>/dev/null || true
}

http_code() {
  curl -sS -o "$1" -m 15 -w '%{http_code}' "$2" 2>/dev/null || true
}

wait_container_ready() {
  local container="$1" limit="$2" elapsed=0 status=""
  while [ "$elapsed" -lt "$limit" ]; do
    status="$(docker inspect "$container" \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      2>/dev/null || true)"
    case "$status" in
      healthy|running) return 0 ;;
      unhealthy|exited|dead) return 1 ;;
    esac
    sleep 3
    elapsed=$((elapsed + 3))
  done
  return 1
}

BACKEND_BODY="$(mktemp)"
PROXY_BODY="$(mktemp)"
trap 'rm -f "$BACKEND_BODY" "$PROXY_BODY"' EXIT

[ "$(container_state "$BACKEND_CONTAINER")" = running ] \
  || fail "$BACKEND_CONTAINER 未运行，502 不能通过重启 Frontend 修复"
[ "$(container_state "$FRONTEND_CONTAINER")" = running ] \
  || fail "$FRONTEND_CONTAINER 未运行，请先恢复 Frontend 容器"

backend_code="$(http_code "$BACKEND_BODY" "$BACKEND_URL")"
if [[ ! "$backend_code" =~ ^2[0-9][0-9]$ ]]; then
  echo "Backend 响应体（最多 30 行）：" >&2
  sed -n '1,30p' "$BACKEND_BODY" >&2 || true
  docker logs --tail 100 "$BACKEND_CONTAINER" >&2 || true
  fail "Backend 直连接口返回 HTTP ${backend_code:-000}，请先修复 Backend"
fi
ok "Backend 直连接口正常（HTTP $backend_code）"

proxy_code="$(http_code "$PROXY_BODY" "$FRONTEND_PROXY_URL")"
if [[ "$proxy_code" =~ ^2[0-9][0-9]$ ]]; then
  ok "Frontend -> Backend API 代理已正常（HTTP $proxy_code），无需重启"
  exit 0
fi

info "Frontend API 代理当前返回 HTTP ${proxy_code:-000}，重启 Nginx 以重新解析 Backend IP"
docker restart "$FRONTEND_CONTAINER" >/dev/null \
  || fail "重启 $FRONTEND_CONTAINER 失败"

wait_container_ready "$FRONTEND_CONTAINER" "$WAIT_SECONDS" || {
  docker logs --tail 100 "$FRONTEND_CONTAINER" >&2 || true
  fail "$FRONTEND_CONTAINER 在 ${WAIT_SECONDS}s 内未就绪"
}
ok "$FRONTEND_CONTAINER 已就绪"

# healthcheck 可能只校验静态首页；继续轮询真实 API 代理路径。
elapsed=0
proxy_code=""
while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
  proxy_code="$(http_code "$PROXY_BODY" "$FRONTEND_PROXY_URL")"
  if [[ "$proxy_code" =~ ^2[0-9][0-9]$ ]]; then
    ok "Frontend -> Backend API 代理已恢复（HTTP $proxy_code）"
    echo "修复完成。建议继续执行：bash 05-verify-v2.sh"
    exit 0
  fi
  sleep 3
  elapsed=$((elapsed + 3))
done

echo "Frontend 代理响应体（最多 30 行）：" >&2
sed -n '1,30p' "$PROXY_BODY" >&2 || true
echo "Frontend 最近日志：" >&2
docker logs --tail 100 "$FRONTEND_CONTAINER" >&2 || true
fail "Frontend 重启后 API 代理仍返回 HTTP ${proxy_code:-000}"
