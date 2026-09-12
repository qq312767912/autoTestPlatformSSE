#!/usr/bin/env bash
set -uo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/small-testcase-review-$(date '+%Y%m%d-%H%M%S').log"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
REVIEW_ID="${REVIEW_ID:-}"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }

echo "小用例审查诊断日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"
echo "指定审查 ID：${REVIEW_ID:-未指定，将检查最新一条}"

section "Backend 状态"
docker inspect "$BACKEND_CONTAINER" --format \
  'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' || true

section "审查记录、源文件实际行数与分片估算"
docker exec -e REVIEW_ID="$REVIEW_ID" "$BACKEND_CONTAINER" \
  /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
from pathlib import Path
from openpyxl import load_workbook
from testcases.models import TestCaseReview

requested = (os.environ.get("REVIEW_ID") or "").strip()
query = TestCaseReview.objects.order_by("-created_at")
review = query.filter(pk=int(requested)).first() if requested.isdigit() else query.first()
if not review:
    print(json.dumps({"error": "没有找到用例审查记录"}, ensure_ascii=False))
else:
    nonempty_rows = 0
    sheet_rows = {}
    path = review.source_file.path
    if Path(path).suffix.lower() == ".xlsx" and Path(path).exists():
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                count = 0
                for values in sheet.iter_rows(values_only=True):
                    if any(str(value).strip() for value in values if value is not None):
                        count += 1
                sheet_rows[sheet.title] = count
                nonempty_rows += count
        finally:
            workbook.close()
    payload = {
        "id": review.id,
        "file": review.source_name,
        "file_size_bytes": Path(path).stat().st_size if Path(path).exists() else None,
        "status": review.status,
        "step": review.current_step,
        "progress": review.progress,
        "task_id": review.celery_task_id,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "started_at": review.started_at.isoformat() if review.started_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
        "completed_at": review.completed_at.isoformat() if review.completed_at else None,
        "error": review.error_message[:2000],
        "nonempty_rows": nonempty_rows,
        "nonempty_rows_by_sheet": sheet_rows,
        "old_backend_chunks_80_rows": (nonempty_rows + 79) // 80 if nonempty_rows else 0,
        "new_backend_chunks_25_rows": (nonempty_rows + 24) // 25 if nonempty_rows else 0,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
' || true

section "当前激活模型（不输出密钥）"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
from langgraph_integration.models import LLMConfig
config = LLMConfig.objects.filter(is_active=True).first()
print(json.dumps({
    "id": config.id if config else None,
    "config_name": config.config_name if config else None,
    "provider": config.provider if config else None,
    "model": config.name if config else None,
    "api_url": config.api_url if config else None,
    "request_timeout": config.request_timeout if config else None,
    "max_retries": config.max_retries if config else None,
    "api_key_configured": bool(config and config.api_key),
}, ensure_ascii=False, indent=2))
' || true

section "60 秒最小模型真实调用（零重试，只要求回复 OK）"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
import time
from openai import OpenAI
from langgraph_integration.models import LLMConfig

config = LLMConfig.objects.filter(is_active=True).first()
if not config:
    print(json.dumps({"ok": False, "error": "没有已激活的模型配置"}, ensure_ascii=False))
else:
    started = time.monotonic()
    try:
        client = OpenAI(
            api_key=config.api_key or "EMPTY",
            base_url=config.api_url,
            timeout=60.0,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=config.name,
            messages=[{"role": "user", "content": "只回复两个字母：OK"}],
            temperature=0,
            max_tokens=256,
        )
        message = response.choices[0].message if response.choices else None
        content = message.content if message else ""
        reasoning = getattr(message, "reasoning_content", None) if message else None
        if reasoning is None and message is not None:
            reasoning = (getattr(message, "model_extra", None) or {}).get("reasoning_content")
        print(json.dumps({
            "ok": bool(content),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content": content,
            "reasoning_content_length": len(reasoning or ""),
            "finish_reason": response.choices[0].finish_reason if response.choices else None,
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:3000],
        }, ensure_ascii=False, indent=2))
' || true

section "Celery 活跃和等待任务"
docker exec "$BACKEND_CONTAINER" timeout 30 celery -A wharttest_django inspect active --timeout=15 || true
docker exec "$BACKEND_CONTAINER" timeout 30 celery -A wharttest_django inspect reserved --timeout=15 || true

section "Worker 最近模型与用例审查日志"
docker exec "$BACKEND_CONTAINER" sh -c \
  'grep -Eai "testcase.review|测试用例审查|Initialized.*LLM|LLM 调用|timeout|timed out|upstream|429|401|403|500|502|503|504|traceback|error" /app/data/logs/worker_err.log /app/data/logs/worker_out.log 2>/dev/null | tail -n 800' || true

section "诊断结束"
echo "请将该日志文件返回：$LOG_FILE"
