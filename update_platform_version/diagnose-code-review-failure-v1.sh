#!/usr/bin/env bash
# Read-only diagnosis for an intranet code-review failure. No restart, rerun or DB write.
set -u

BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BASE_SHA_PREFIX="${BASE_SHA_PREFIX:-36dfb85}"
HEAD_SHA_PREFIX="${HEAD_SHA_PREFIX:-7bb5309}"
OUTPUT_DIR="${OUTPUT_DIR:-$(pwd)}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT_FILE="$OUTPUT_DIR/code-review-failure-$STAMP.log"

mkdir -p "$OUTPUT_DIR" || exit 1
if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker 命令" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到宿主机 python3，无法安全过滤日志" >&2
  exit 1
fi
if ! docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  echo "找不到容器 $BACKEND_CONTAINER；可设置 BACKEND_CONTAINER=实际容器名" >&2
  exit 1
fi

TEMP_FILE="$(mktemp)" || exit 1
RAW_FILE="$(mktemp)" || exit 1
trap 'rm -f "$TEMP_FILE" "$RAW_FILE"' EXIT

{
  echo "代码审查失败诊断（只读）"
  echo "采集时间：$(date '+%F %T %z')"
  echo "目标 Commit 前缀：$BASE_SHA_PREFIX -> $HEAD_SHA_PREFIX"
  echo "后端容器：$BACKEND_CONTAINER"
  echo
  echo "===== 容器状态 ====="
  docker inspect "$BACKEND_CONTAINER" --format 'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' 2>&1 || true
  echo
  echo "===== 任务数据库记录和执行事件 ====="
  docker exec -i -e BASE_SHA_PREFIX="$BASE_SHA_PREFIX" -e HEAD_SHA_PREFIX="$HEAD_SHA_PREFIX" "$BACKEND_CONTAINER" sh -c 'cd /app && /opt/venv/bin/python -' <<'PY' 2>&1 || true
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from code_analysis.models import AnalysisTask, AnalysisTaskExecutionLog

base = os.environ.get("BASE_SHA_PREFIX", "").lower()
head = os.environ.get("HEAD_SHA_PREFIX", "").lower()
tasks = list(AnalysisTask.objects.select_related("repository").order_by("-created_at")[:500])
matched = [task for task in tasks if task.base_sha.lower().startswith(base) and task.head_sha.lower().startswith(head)]
print("total_recent_tasks_checked=", len(tasks), "matched=", len(matched))
for task in matched[:10]:
    report = task.change_report or {}
    data = {
        "id": str(task.pk), "repository": task.repository.name,
        "title": task.title, "mode": task.mode, "status": task.status,
        "progress": task.progress, "step": task.current_step,
        "error_message": (task.error_message or "")[:4000],
        "base_sha": task.base_sha, "head_sha": task.head_sha,
        "celery_task_id": task.celery_task_id,
        "created_at": task.created_at, "updated_at": task.updated_at,
        "completed_at": task.completed_at,
        "report_schema_version": report.get("schema_version"),
        "ocr_status": report.get("ocr_status"),
    }
    print("TASK", json.dumps(data, ensure_ascii=False, default=str))
    events = AnalysisTaskExecutionLog.objects.filter(task=task).order_by("created_at")
    for event in events[:100]:
        print("EVENT", json.dumps({
            "time": event.created_at, "event": event.event,
            "message": event.message, "detail": event.detail,
        }, ensure_ascii=False, default=str)[:5000])
if not matched:
    print("未匹配任务；最近失败任务摘要：")
    for task in [item for item in tasks if item.status == "failed"][:8]:
        print(json.dumps({"id": str(task.pk), "repository": task.repository.name,
                          "title": task.title, "base_sha": task.base_sha,
                          "head_sha": task.head_sha, "step": task.current_step,
                          "error_message": (task.error_message or "")[:1500]},
                         ensure_ascii=False))
PY
  echo
  echo "===== 近 48 小时后端容器日志（仅错误相关行，含上下文） ====="
  docker logs --timestamps --since 48h --tail 15000 "$BACKEND_CONTAINER" 2>&1 > "$TEMP_FILE" || true
  python3 - "$TEMP_FILE" "$BASE_SHA_PREFIX" "$HEAD_SHA_PREFIX" <<'PY' || true
import re
import sys

path, base, head = sys.argv[1:4]
with open(path, encoding="utf-8", errors="replace") as stream:
    lines = stream.readlines()
pattern = re.compile(r"traceback|exception|error|failed|code.analysis|code_analysis|代码审查|" +
                     re.escape(base) + r"|" + re.escape(head), re.I)
indices = set()
for index, line in enumerate(lines):
    if pattern.search(line):
        indices.update(range(max(0, index - 2), min(len(lines), index + 9)))
selected = sorted(indices)[-1800:]
print("container_log_lines=", len(lines), "selected_lines=", len(selected))
for index in selected:
    line = lines[index][:3000]
    line = re.sub(r"(?i)(bearer\s+)[a-z0-9._~+/-]+", r"\1[REDACTED]", line)
    line = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)\s*[:=]\s*[\"']?)[^\s,\"']+", r"\1[REDACTED]", line)
    print(f"{index + 1}: {line}", end="" if line.endswith("\n") else "\n")
PY
  echo
  echo "===== 完成 ====="
} > "$RAW_FILE" 2>&1

python3 - "$RAW_FILE" "$OUTPUT_FILE" <<'PY'
import re
import sys

source, target = sys.argv[1:3]
with open(source, encoding="utf-8", errors="replace") as stream:
    content = stream.read()
content = re.sub(r"(?i)(bearer\s+)[a-z0-9._~+/-]+", r"\1[REDACTED]", content)
content = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s,\"']+", r"\1[REDACTED]", content)
with open(target, "w", encoding="utf-8") as stream:
    stream.write(content)
PY

echo "诊断日志已生成：$OUTPUT_FILE"
echo "发送前请快速检查是否含有内部地址、仓库名或其他不宜外发的信息。"
