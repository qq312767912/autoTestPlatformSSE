#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
HOTFIX_FILE="$UPDATE_DIR/hotfix/code_analysis/services.py"

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$UPDATE_COMPOSE"
[ -f "$HOTFIX_FILE" ] || fail "缺少代码审查热修复文件：$HOTFIX_FILE"

grep -q 'OCR_CONCURRENCY = 1' "$HOTFIX_FILE" || fail "热修复文件不是单并发版本"
grep -q 'def _invalid_ocr_result_reason' "$HOTFIX_FILE" || fail "热修复文件缺少超时原因诊断"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
"${compose[@]}" config --quiet

echo "[提醒] 重新创建 Backend 会中断当前正在运行的后台任务，请确认当前无必须保留的运行中任务。"
echo "[应用] 不更换镜像和数据卷，仅挂载代码审查热修复并重新创建 Backend"
"${compose[@]}" up -d --no-deps --force-recreate backend

elapsed=0
while [ "$elapsed" -lt 300 ]; do
  health="$(docker inspect wharttest-backend --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  case "$health" in
    healthy|running) break ;;
    unhealthy|stopped)
      docker logs --tail 150 wharttest-backend || true
      fail "Backend 状态异常：$health"
      ;;
  esac
  sleep 5
  elapsed=$((elapsed + 5))
done
[ "$elapsed" -lt 300 ] || fail "Backend 启动超过 300 秒"

echo "[验证] 容器内热修复代码"
docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path
service = Path("/app/code_analysis/services.py").read_text(encoding="utf-8")
assert "OCR_CONCURRENCY = 1" in service
assert "OCR_RESUME_CONCURRENCY = 1" in service
assert "def _invalid_ocr_result_reason" in service
print("code analysis OCR hotfix OK")
'

echo "[恢复] 确保三个执行器保持运行并重新连接 Backend"
"${compose[@]}" up -d --no-deps actuator-01 actuator-02 actuator-03

echo "[完成] 新任务将以单并发执行 OpenCodeReview；超时或空结果会保存明确原因。"
echo "如需诊断，执行：bash 21-diagnose-code-analysis-ocr.sh"
