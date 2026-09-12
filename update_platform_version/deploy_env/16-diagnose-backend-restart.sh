#!/usr/bin/env bash
set -uo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/backend-restart-$(date '+%Y%m%d-%H%M%S').log"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
EXPECTED_IMAGE="${EXPECTED_BACKEND_IMAGE:-wharttest-250-backend:update-430787c8-r1-arm64}"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }

echo "Backend 重启诊断日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"

section "Docker 与宿主机资源"
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}' 2>&1 || true
docker info --format 'arch={{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}} driver={{.Driver}}' 2>&1 || true
df -h / /var/lib/docker 2>&1 || df -h 2>&1 || true

section "Backend 容器状态"
docker ps -a --no-trunc --filter "name=^/${BACKEND_CONTAINER}$" 2>&1 || true
docker inspect "$BACKEND_CONTAINER" --format \
  'image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} running={{.State.Running}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} started={{.State.StartedAt}} finished={{.State.FinishedAt}} error={{.State.Error}}' 2>&1 || true

section "Backend 启动入口与端口"
docker inspect "$BACKEND_CONTAINER" --format \
  'entrypoint={{json .Config.Entrypoint}} command={{json .Config.Cmd}} ports={{json .NetworkSettings.Ports}}' 2>&1 || true
docker port "$BACKEND_CONTAINER" 2>&1 || true

section "Backend 挂载（不读取文件内容）"
docker inspect "$BACKEND_CONTAINER" --format \
  '{{range .Mounts}}{{println .Type .Source "->" .Destination "rw=" .RW}}{{end}}' 2>&1 || true

section "Backend 最近 500 行日志"
docker logs --timestamps --tail 500 "$BACKEND_CONTAINER" 2>&1 || true

section "Backend 上一次退出附近事件"
docker events --since 30m --until "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  --filter "container=$BACKEND_CONTAINER" 2>&1 | tail -100 || true

section "Compose 配置校验"
if [ -f "$BASE_COMPOSE" ] && [ -f "$UPDATE_COMPOSE" ]; then
  docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE" config --quiet 2>&1 \
    && echo "Compose 配置语法正常" || true
  docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE" ps -a 2>&1 || true
else
  echo "Compose 文件缺失：base=$BASE_COMPOSE update=$UPDATE_COMPOSE"
fi

section "数据库、Redis 与依赖容器"
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1 \
  | grep -Ei 'wharttest|postgres|redis|qdrant' || true

section "新 Backend 镜像基础检查"
docker image inspect "$EXPECTED_IMAGE" --format \
  'id={{.Id}} os={{.Os}} arch={{.Architecture}} size={{.Size}} entrypoint={{json .Config.Entrypoint}}' 2>&1 || true
docker run --rm --entrypoint /bin/sh "$EXPECTED_IMAGE" -c '
  echo "python=$(command -v python || true)";
  python --version 2>&1 || true;
  echo "supervisord=$(command -v supervisord || true)";
  supervisord --version 2>&1 || true;
  echo "ocr=$(command -v ocr || true)";
  ocr --version 2>&1 || true;
  echo "entrypoint:";
  ls -l /app/entrypoint.sh /app/manage.py /app/supervisord.conf 2>&1 || true;
  echo "celery config:";
  grep "command=celery" /app/supervisord.conf 2>&1 || true
' 2>&1 || true

section "诊断结束"
echo "请将该日志文件返回：$LOG_FILE"
