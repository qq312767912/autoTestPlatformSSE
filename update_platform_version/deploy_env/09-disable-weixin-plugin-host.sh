#!/usr/bin/env bash
set -euo pipefail

# 内网环境未使用微信插件宿主。关闭容器的 Docker 自动重启策略后再停止，
# 避免 Docker daemon 或宿主机重启时把旧 Node.js 运行时重新拉起。
CONTAINER="${WEIXIN_PLUGIN_HOST_CONTAINER:-wharttest-weixin-plugin-host}"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "[通过] $CONTAINER 不存在，无需停用"
  exit 0
fi

echo "[停用] 关闭 $CONTAINER 的自动重启策略"
docker update --restart=no "$CONTAINER" >/dev/null

if [ "$(docker inspect "$CONTAINER" --format '{{.State.Running}}')" = "true" ]; then
  echo "[停用] 停止 $CONTAINER"
  docker stop -t "${WEIXIN_PLUGIN_HOST_STOP_TIMEOUT:-30}" "$CONTAINER" >/dev/null
fi

running="$(docker inspect "$CONTAINER" --format '{{.State.Running}}')"
restart_policy="$(docker inspect "$CONTAINER" --format '{{.HostConfig.RestartPolicy.Name}}')"

if [ "$running" != "false" ] || [ "$restart_policy" != "no" ]; then
  echo "[失败] $CONTAINER 停用验证失败：running=$running restart=$restart_policy" >&2
  exit 1
fi

echo "[通过] $CONTAINER 已停止，自动重启策略为 no"
