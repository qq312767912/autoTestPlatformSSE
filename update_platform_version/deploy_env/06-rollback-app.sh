#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
[ -f "$BASE_COMPOSE" ] || { echo "找不到旧版本 YAML：$BASE_COMPOSE" >&2; exit 1; }

echo "即将使用旧 YAML 恢复 Backend 和 Frontend。"
echo "注意：如果新版数据库迁移与旧版不兼容，应先停止操作并人工恢复 PostgreSQL 备份。"
read -r -p "确认只回退应用镜像？输入 YES：" answer
[ "$answer" = "YES" ] || { echo "已取消"; exit 1; }

docker compose -p offline-images -f "$BASE_COMPOSE" up -d --no-deps backend frontend

# Semgrep Scanner 是本次新增的无状态服务，旧版 Backend 不使用它；回退时停止即可。
if docker inspect wharttest-semgrep >/dev/null 2>&1; then
  docker stop -t 30 wharttest-semgrep >/dev/null
  echo "wharttest-semgrep -> stopped"
fi

# CRG 与旧版 Backend 解耦；回退时只停服务，保留持久化图谱便于排查或再升级。
if docker inspect wharttest-crg >/dev/null 2>&1; then
  docker stop -t 30 wharttest-crg >/dev/null
  echo "wharttest-crg -> stopped（图谱数据已保留）"
fi

# 新增执行器不属于旧版基线，回退应用时一并停止，但不删除容器和数据。
for container in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  if docker inspect "$container" >/dev/null 2>&1; then
    docker stop -t 30 "$container" >/dev/null
    echo "$container -> stopped"
  fi
done

for container in wharttest-backend wharttest-frontend; do
  echo "$container -> $(docker inspect "$container" --format '{{.Config.Image}} | {{.State.Status}}')"
done

echo "应用镜像已回退，Semgrep Scanner、CRG 和三个新增执行器已停止。请检查旧版登录及核心功能。Vision MCP 保留运行。"
