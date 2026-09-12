#!/usr/bin/env bash
set -uo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/testcase-review-$(date '+%Y%m%d-%H%M%S').log"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }

echo "用例审查诊断日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"

section "Backend 状态"
docker inspect "$BACKEND_CONTAINER" --format \
  'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' || true

section "最近 10 条用例审查记录"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
from testcases.models import TestCaseReview
rows = []
for item in TestCaseReview.objects.order_by("-created_at")[:10]:
    rows.append({
        "id": item.id,
        "file": item.source_name,
        "status": item.status,
        "step": item.current_step,
        "progress": item.progress,
        "task_id": item.celery_task_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "error": item.error_message[:1000],
    })
print(json.dumps(rows, ensure_ascii=False, indent=2))
' || true

section "Celery Worker 响应"
docker exec "$BACKEND_CONTAINER" timeout 20 celery -A wharttest_django inspect ping --timeout=10 || true

section "Celery 活跃任务"
docker exec "$BACKEND_CONTAINER" timeout 30 celery -A wharttest_django inspect active --timeout=15 || true

section "Celery 等待任务"
docker exec "$BACKEND_CONTAINER" timeout 30 celery -A wharttest_django inspect reserved --timeout=15 || true

section "Worker 最近日志"
docker exec "$BACKEND_CONTAINER" sh -c \
  'tail -n 500 /app/data/logs/worker_err.log 2>/dev/null; tail -n 500 /app/data/logs/worker_out.log 2>/dev/null' || true

section "与用例审查和模型调用相关的日志"
docker exec "$BACKEND_CONTAINER" sh -c \
  'grep -Eai "testcase.review|测试用例审查|LLM 调用|timeout|timed out|429|401|403|500|502|503|504|traceback|error" /app/data/logs/worker_err.log /app/data/logs/worker_out.log 2>/dev/null | tail -n 500' || true

section "诊断结束"
echo "请将该日志文件返回：$LOG_FILE"
