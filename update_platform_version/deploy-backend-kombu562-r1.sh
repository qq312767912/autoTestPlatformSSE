#!/usr/bin/env bash
# 导入并部署修复 Celery/Kombu/redis-py 版本冲突的 Backend 镜像。
set -euo pipefail

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
IMAGES_DIR="${PLATFORM_DIR}/update_platform_version/images"
DEPLOY_DIR="${PLATFORM_DIR}/update_platform_version/deploy_env"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${DEPLOY_DIR}/docker-compose.update.yml"
ARCHIVE="backend-ac65a6fb-v2.8-r3-kombu562-arm64.tar.gz"
IMAGE="wharttest-250-backend:update-ac65a6fb-v2.8-r3-kombu562-arm64"
REVISION="ac65a6fb-v2.8-r3-kombu562"

fail() { echo "[失败] $*" >&2; exit 1; }
echo "[版本] deploy-backend-kombu562-r1 20260923"

[ -f "$BASE_COMPOSE" ] || fail "找不到基础 Compose: $BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级 Compose: $UPDATE_COMPOSE"
[ -f "$DEPLOY_DIR/SHA256SUMS" ] || fail "找不到 $DEPLOY_DIR/SHA256SUMS"

shopt -s nullglob
parts=("$IMAGES_DIR/$ARCHIVE".part*)
[ "${#parts[@]}" -gt 0 ] || fail "找不到镜像分包: $IMAGES_DIR/$ARCHIVE.part*"

echo "[校验] Backend 镜像分包 SHA-256..."
(cd "$DEPLOY_DIR" && grep "../images/$ARCHIVE.part" SHA256SUMS | sha256sum -c -)

echo "[导入] 合并 ${#parts[@]} 个分包并 docker load..."
cat "${parts[@]}" | gzip -dc | docker load

echo "[校验] 镜像架构、revision、Alpine/musl、OCR和Python依赖..."
[ "$(docker image inspect "$IMAGE" --format '{{.Os}}/{{.Architecture}}')" = linux/arm64 ] || fail "镜像不是 linux/arm64"
[ "$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$REVISION" ] || fail "镜像 revision 不正确"
docker run --rm --entrypoint /bin/sh "$IMAGE" -c '
  set -e
  grep -q "Alpine Linux" /etc/os-release
  ldd --version 2>&1 | grep -qi musl
  command -v ocr >/dev/null
  ocr --version
  npm list -g --depth=0 @alibaba-group/open-code-review >/dev/null
  python -c "import celery,kombu,redis; assert (celery.__version__,kombu.__version__,redis.__version__) == (\"5.4.0\",\"5.6.2\",\"5.2.0\"); print(\"celery=5.4.0 kombu=5.6.2 redis=5.2.0\")"
'

previous_image="$(docker inspect wharttest-backend --format '{{.Config.Image}}' 2>/dev/null || true)"
echo "[记录] 替换前 Backend 镜像: ${previous_image:-unknown}"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
"${compose[@]}" config >/dev/null
resolved_image="$("${compose[@]}" config --images | grep '^wharttest-250-backend:' | head -n 1)"
[ "$resolved_image" = "$IMAGE" ] || fail "Compose 解析到 $resolved_image，期望 $IMAGE；请确认已解压新版 deploy_env.zip"

echo "[部署] 仅重建 Backend，不改数据库、Redis、前端和执行器..."
"${compose[@]}" up -d --pull never --no-deps --force-recreate backend

echo "[等待] Backend HTTP 就绪（最长5分钟）..."
ready=0
for _ in $(seq 1 150); do
  if docker exec wharttest-backend python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/admin/login/', timeout=3)" \
      >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[ "$ready" = 1 ] || { docker logs --tail 200 wharttest-backend >&2 || true; fail "Backend HTTP 启动超时"; }

celery_pid() {
  docker exec wharttest-backend sh -c '
    for cmdline in /proc/[0-9]*/cmdline; do
      line=$(tr "\000" " " < "$cmdline" 2>/dev/null || true)
      case "$line" in
        "/opt/venv/bin/python /opt/venv/bin/celery -A wharttest_django -b redis://redis:6379/0 worker "*)
          basename "$(dirname "$cmdline")"; exit 0 ;;
      esac
    done
    exit 1
  '
}

echo "[验证] Celery依赖、Redis协议连接和进程稳定性..."
docker exec wharttest-backend python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import celery, kombu, redis
assert (celery.__version__, kombu.__version__, redis.__version__) == ("5.4.0", "5.6.2", "5.2.0")
from wharttest_django.celery import app
conn = app.connection_for_read()
conn.ensure_connection(max_retries=0)
print("broker_connection=OK", conn.as_uri())
conn.release()
'
pid_before="$(celery_pid || true)"
case "$pid_before" in ''|0|*[!0-9]*) fail "Celery worker 未运行" ;; esac
sleep 45
pid_after="$(celery_pid || true)"
[ "$pid_after" = "$pid_before" ] || { docker logs --tail 240 wharttest-backend >&2 || true; fail "Celery worker 发生重启: $pid_before -> $pid_after"; }

health="$(docker inspect wharttest-backend --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
[ "$health" = healthy ] || [ "$health" = starting ] || fail "Backend 健康状态异常: $health"

# Nginx 会在启动时解析 backend:8000 并缓存容器 IP；Backend 被重建后 IP 可能变化，
# 若不重启 Frontend，登录接口会继续访问旧 IP 并返回 502 Bad Gateway。
echo "[刷新] 重启 Frontend，使 Nginx 重新解析 Backend 容器地址..."
docker restart wharttest-frontend >/dev/null
frontend_ready=0
for _ in $(seq 1 60); do
  if docker exec wharttest-frontend wget -q -O /dev/null http://backend:8000/admin/login/ >/dev/null 2>&1 \
      && docker exec wharttest-frontend wget -q -O /dev/null http://127.0.0.1/ >/dev/null 2>&1; then
    frontend_ready=1
    break
  fi
  sleep 2
done
[ "$frontend_ready" = 1 ] || { docker logs --tail 120 wharttest-frontend >&2 || true; fail "Frontend 重启后仍无法访问 Backend"; }

echo "[完成] Backend=$IMAGE，Celery PID=$pid_after，health=$health。"
echo "可以重新执行 UI 自动化用例。"
