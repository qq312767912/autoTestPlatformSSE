#!/usr/bin/env bash
set -uo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/code-analysis-ocr-$(date '+%Y%m%d-%H%M%S').log"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
TASK_ID="${TASK_ID:-}"
RUN_LLM_PROBE="${RUN_LLM_PROBE:-1}"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }

echo "代码审查 OCR 诊断日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"
echo "指定任务 ID：${TASK_ID:-未指定，将检查最新任务}"

section "Backend 与 OpenCodeReview 状态"
docker inspect "$BACKEND_CONTAINER" --format \
  'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' || true
docker exec "$BACKEND_CONTAINER" sh -c \
  'command -v ocr; ocr --version; uname -m; df -h /app/data; test -w /app/data/code-analysis-repositories && echo "ocr_workspace=writable" || echo "ocr_workspace=not-writable"' || true

if [ -z "$TASK_ID" ]; then
  TASK_ID="$(docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c \
    'from code_analysis.models import AnalysisTask; item=AnalysisTask.objects.order_by("-created_at").first(); print(item.pk if item else "")' 2>/dev/null | tail -n 1 | tr -d '\r')"
fi
echo "实际检查任务 ID：${TASK_ID:-未找到}"

section "最近 10 条代码审查任务与 OCR 结果"
docker exec -e TASK_ID="$TASK_ID" "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
from code_analysis.models import AnalysisTask

def compact(item, include_details=False):
    report = item.change_report or {}
    ocr = report.get("ocr_status") or {}
    diagnostics = ocr.get("diagnostics") or {}
    return {
        "id": str(item.pk), "repository": item.repository.name,
        "status": item.status, "progress": item.progress, "step": item.current_step,
        "base_sha": item.base_sha, "head_sha": item.head_sha,
        "celery_task_id": item.celery_task_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "task_error": (item.error_message or "")[:3000],
        "ocr_status": ocr.get("status"), "ocr_message": (ocr.get("message") or "")[:3000],
        "ocr_coverage": ocr.get("coverage"),
        "failure_type_counts": diagnostics.get("failure_type_counts") or {},
        "failure_detail_count": len(diagnostics.get("failure_details") or []),
        "failure_details": (diagnostics.get("failure_details") or [])[:20] if include_details else [],
        "tool_failures": (diagnostics.get("tool_failures") or [])[:20] if include_details else [],
        "retry": diagnostics.get("retry") or {}, "resume": diagnostics.get("resume") or {},
        "timeout_budget": diagnostics.get("timeout_budget") or {},
    }

requested = (os.environ.get("TASK_ID") or "").strip()
tasks = list(AnalysisTask.objects.select_related("repository").order_by("-created_at")[:10])
print(json.dumps({
    "selected": compact(next((item for item in tasks if str(item.pk) == requested), tasks[0] if tasks else None), True) if tasks else None,
    "recent": [compact(item) for item in tasks],
}, ensure_ascii=False, indent=2, default=str))
' || true

section "目标任务执行记录"
docker exec -e TASK_ID="$TASK_ID" "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
from code_analysis.models import AnalysisTask, AnalysisTaskExecutionLog
def compact(value, depth=0):
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, dict):
        return {str(key): compact(item, depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return [compact(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:1000]
    return value
task_id = (os.environ.get("TASK_ID") or "").strip()
task = AnalysisTask.objects.filter(pk=task_id).first() if task_id else None
rows = [] if not task else [{
    "time": item.created_at.isoformat(), "event": item.event,
    "message": item.message, "detail": compact(item.detail),
} for item in AnalysisTaskExecutionLog.objects.filter(task=task).order_by("created_at")]
print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
' || true

section "OCR 结果文件结构（不输出代码正文）"
docker exec -e TASK_ID="$TASK_ID" "$BACKEND_CONTAINER" /opt/venv/bin/python -c '
import json
import os
from pathlib import Path
task_id = (os.environ.get("TASK_ID") or "").strip()
path = Path("/app/data/code-analysis-repositories") / task_id / ".ocr-review-result.json"
if not path.is_file():
    print(json.dumps({"path": str(path), "exists": False}, ensure_ascii=False))
else:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        payload = json.loads(text)
        coverage = ((payload.get("manifest") or {}).get("coverage") or {})
        print(json.dumps({
            "path": str(path), "exists": True, "size_bytes": path.stat().st_size,
            "valid_json": True, "status": payload.get("status"),
            "message": str(payload.get("message") or "")[:3000],
            "selected": len(coverage.get("selected") or []),
            "completed": len(coverage.get("completed") or []),
            "reused": len(coverage.get("reused") or []),
            "failed": len(coverage.get("failed") or []),
            "summary": payload.get("summary") or {},
            "retry_report": {
                "total_requests": (payload.get("retry_report") or {}).get("total_requests", 0),
                "retried_requests": (payload.get("retry_report") or {}).get("retried_requests", 0),
                "failed_requests": (payload.get("retry_report") or {}).get("failed_requests", 0),
                "sample_failures": [{
                    "file_path": item.get("file_path"), "task_type": item.get("task_type"),
                    "request_no": item.get("request_no"), "attempt_count": len(item.get("attempts") or []),
                    "status_codes": [attempt.get("status_code") for attempt in (item.get("attempts") or []) if attempt.get("status_code")],
                    "error_classes": sorted({str(attempt.get("error_class")) for attempt in (item.get("attempts") or []) if attempt.get("error_class")}),
                } for item in (payload.get("retry_report") or {}).get("requests", []) if item.get("outcome") == "failed"][:10],
            },
        }, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        print(json.dumps({
            "path": str(path), "exists": True, "size_bytes": path.stat().st_size,
            "valid_json": False, "error": f"{type(exc).__name__}: {exc}",
            "text_prefix": text[:500], "text_suffix": text[-1000:],
        }, ensure_ascii=False, indent=2))
' || true

section "Celery 任务与 OCR 进程"
docker exec "$BACKEND_CONTAINER" timeout 25 celery -A wharttest_django inspect active --timeout=15 || true
docker exec "$BACKEND_CONTAINER" timeout 25 celery -A wharttest_django inspect reserved --timeout=15 || true
docker exec "$BACKEND_CONTAINER" sh -c \
  'for file in /proc/[0-9]*/cmdline; do command=$(tr "\000" " " < "$file" 2>/dev/null || true); case "$command" in *"ocr review"*|*"code_analysis.tasks.run_code_analysis"*) echo "$file $command" ;; esac; done' || true

section "目标任务和 OCR 相关 Worker 日志"
docker exec -e TASK_ID="$TASK_ID" "$BACKEND_CONTAINER" sh -c '
pattern="${TASK_ID}|OpenCodeReview|OCR审查|OCR 不可用|code_analysis|timeout|timed out|Timeout|502|503|504|connection reset|Connection|JSON|Traceback|ERROR"
grep -Eai "$pattern" /app/data/logs/worker_err.log /app/data/logs/worker_out.log 2>/dev/null | tail -n 1200
' || true

section "当前模型配置（不输出密钥）"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
from langgraph_integration.models import LLMConfig
config = LLMConfig.objects.filter(is_active=True).first()
print(json.dumps({
    "id": config.id if config else None,
    "name": config.config_name if config else None,
    "provider": config.provider if config else None,
    "model": config.name if config else None,
    "api_url": config.api_url if config else None,
    "request_timeout": config.request_timeout if config else None,
    "max_retries": config.max_retries if config else None,
    "api_key_configured": bool(config and config.api_key),
}, ensure_ascii=False, indent=2))
' || true

if [ "$RUN_LLM_PROBE" = "1" ]; then
  section "60 秒最小模型真实调用"
  docker exec "$BACKEND_CONTAINER" timeout 75 /opt/venv/bin/python /app/manage.py shell -c '
import json
import time
from openai import OpenAI
from langgraph_integration.models import LLMConfig
config = LLMConfig.objects.filter(is_active=True).first()
started = time.monotonic()
try:
    if not config:
        raise RuntimeError("没有已激活的模型配置")
    response = OpenAI(
        api_key=config.api_key or "EMPTY", base_url=config.api_url,
        timeout=60.0, max_retries=0,
    ).chat.completions.create(
        model=config.name,
        messages=[{"role": "user", "content": "只回复：OK"}],
        temperature=0, max_tokens=32,
    )
    content = response.choices[0].message.content if response.choices else ""
    print(json.dumps({"ok": bool(content), "elapsed_seconds": round(time.monotonic()-started, 3), "content": content}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"ok": False, "elapsed_seconds": round(time.monotonic()-started, 3), "error_type": type(exc).__name__, "error": str(exc)[:3000]}, ensure_ascii=False))
' || true
else
  section "最小模型调用已跳过"
  echo "RUN_LLM_PROBE=$RUN_LLM_PROBE"
fi

section "诊断结束"
echo "请将该日志文件返回：$LOG_FILE"
