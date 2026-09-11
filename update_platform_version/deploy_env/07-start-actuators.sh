#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="$(cd "$UPDATE_DIR/.." && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
SECRET_DIR="$UPDATE_DIR/secrets"
SECRET_FILE="$SECRET_DIR/actuator_api_password"
ACTUATOR_IMAGE="wharttest-250-actuator:update-178fb3ed-arm64-r4"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/start-actuators-$(date '+%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1
echo "执行器启动日志：$LOG_FILE"

[ -f "$BASE_COMPOSE" ] || { echo "找不到基础 YAML：$BASE_COMPOSE" >&2; exit 1; }

if ! docker image inspect "$ACTUATOR_IMAGE" >/dev/null 2>&1; then
  echo "本地缺少执行器镜像：$ACTUATOR_IMAGE" >&2
  echo "请先执行：" >&2
  echo "请先执行 deploy-actuator-r4.sh 导入并更新 R4 镜像。" >&2
  exit 1
fi

platform="$(docker image inspect "$ACTUATOR_IMAGE" --format '{{.Os}}/{{.Architecture}}')"
[ "$platform" = "linux/arm64" ] || {
  echo "执行器镜像架构错误：$platform，应为 linux/arm64" >&2
  exit 1
}

mkdir -p "$SECRET_DIR"
chmod 700 "$SECRET_DIR"

if [ ! -s "$SECRET_FILE" ]; then
  echo "执行器密钥文件缺失，优先从现有执行器恢复..."
  recovered=false
  for candidate in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
    if docker exec "$candidate" test -s /run/secrets/actuator_api_password >/dev/null 2>&1; then
      docker exec "$candidate" cat /run/secrets/actuator_api_password > "$SECRET_FILE"
      chmod 600 "$SECRET_FILE"
      recovered=true
      echo "已从 $candidate 恢复密钥（内容不会输出）。"
      break
    fi
  done
  if [ "$recovered" != true ]; then
    read -r -s -p "请输入平台账号 ${ACTUATOR_API_USERNAME:-admin} 的当前登录密码：" password
    echo
    [ -n "$password" ] || { echo "密码不能为空" >&2; exit 1; }
    printf '%s' "$password" > "$SECRET_FILE"
    unset password
    chmod 600 "$SECRET_FILE"
  fi
fi

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE")
"${compose[@]}" config >/dev/null

# 逐个删除、重建和检查，避免内网信创机器并发创建多个
# Playwright 容器时出现资源竞争，也防止旧容器残留旧的入口命令。
for index in 01 02 03; do
  container="wharttest-actuator-$index"
  service="actuator-$index"

  echo "[$container] 删除旧容器并逐个重建..."
  docker rm -f "$container" >/dev/null 2>&1 || true
  "${compose[@]}" up -d --no-deps "$service"
  sleep 12

  status="$(docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null || echo missing)"
  restarts="$(docker inspect "$container" --format '{{.RestartCount}}' 2>/dev/null || echo '-')"
  actual_image="$(docker inspect "$container" --format '{{.Config.Image}}' 2>/dev/null || echo '-')"
  actual_entrypoint="$(docker inspect "$container" --format '{{json .Config.Entrypoint}} {{json .Config.Cmd}}' 2>/dev/null || echo '-')"

  echo "$container -> status=$status restarts=$restarts"
  echo "  image=$actual_image"
  echo "  command=$actual_entrypoint"

  if [ "$status" != "running" ] || [ "$restarts" != "0" ]; then
    echo "[$container] 启动异常，最近 80 行日志：" >&2
    docker logs --tail 80 "$container" 2>&1 || true
  fi
done

echo "三个执行器已完成逐个重建。请刷新平台的执行器页面。"
