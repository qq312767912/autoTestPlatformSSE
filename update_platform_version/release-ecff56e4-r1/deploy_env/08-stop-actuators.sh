#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"

docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE" \
  stop actuator-01 actuator-02 actuator-03

echo "三个执行器已停止，平台其他服务未受影响。"
