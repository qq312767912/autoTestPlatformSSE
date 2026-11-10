#!/usr/bin/env bash
# 只读诊断：代码审查任务长期处于 queued。不会重启服务、清理队列或修改数据库。
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_FILE="$SCRIPT_DIR/code-review-queue.log"
if ! : > "$OUTPUT_FILE"; then
  echo "无法写入日志文件：$OUTPUT_FILE" >&2
  exit 1
fi
printf '排查结果将保存到：%s\n' "$OUTPUT_FILE"
exec > "$OUTPUT_FILE" 2>&1

BACKEND_CONTAINER="${BACKEND_CONTAINER:-}"
TASK_ID="${1:-}"
LOG_LINES="${LOG_LINES:-180}"

section() { printf '\n===== %s =====\n' "$1"; }
run() { "$@" 2>&1 || printf '[命令失败，退出码 %s] %s\n' "$?" "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  echo '找不到 docker 命令；请在有 Docker 权限的内网宿主机运行。' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo '无法访问 Docker；请检查当前用户的 Docker 权限与服务状态。' >&2
  exit 1
fi

if [ -z "$BACKEND_CONTAINER" ]; then
  BACKEND_CONTAINER="$(docker ps --format '{{.Names}}' | awk '/^(wharttest-250-backend|wharttest-new-backend|wharttest-backend)$/ { print; exit }')"
fi
if [ -z "$BACKEND_CONTAINER" ]; then
  echo '未自动找到 Backend 容器。请运行：BACKEND_CONTAINER=实际容器名 bash diagnose_code_review_queue.sh [任务ID]' >&2
  exit 1
fi
if ! docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  echo "Backend 容器不存在：$BACKEND_CONTAINER" >&2
  exit 1
fi

section '诊断时间与容器状态'
date -Is
printf '宿主机当前目录：%s\n' "$(pwd)"
printf '脚本位置：%s\n' "$SCRIPT_DIR/$(basename "$0")"
printf '结果文件：%s\n' "$OUTPUT_FILE"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | awk 'NR == 1 || /backend|redis|postgres/'
printf 'Backend 容器：%s\n' "$BACKEND_CONTAINER"
section 'Backend 日志目录在宿主机的挂载位置'
run docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' "$BACKEND_CONTAINER"

section 'Backend 内进程和 Supervisor 状态'
run docker exec "$BACKEND_CONTAINER" sh -c 'ps -eo pid,ppid,etime,stat,args | grep -E "celery|supervisord|gunicorn" | grep -v grep | head -n 30'

section 'Celery Worker 检查（每项最多等待约 5 秒）'
run docker exec "$BACKEND_CONTAINER" celery -A wharttest_django inspect ping --timeout=5
run docker exec "$BACKEND_CONTAINER" celery -A wharttest_django inspect active --timeout=5
run docker exec "$BACKEND_CONTAINER" celery -A wharttest_django inspect reserved --timeout=5
run docker exec "$BACKEND_CONTAINER" celery -A wharttest_django inspect scheduled --timeout=5

section '数据库中的代码审查任务与最近执行记录（只读）'
run docker exec -i -e DIAG_TASK_ID="$TASK_ID" "$BACKEND_CONTAINER" python manage.py shell -c '
import os
from datetime import timedelta
from django.utils import timezone
from code_analysis.models import AnalysisTask, AnalysisTaskExecutionLog
now = timezone.now()
active = ["fetching", "machine_analyzing", "ai_analyzing", "generating_tests"]
print("current_time:", now.isoformat())
print("active_count:", AnalysisTask.objects.filter(status__in=active).count())
print("queued_count:", AnalysisTask.objects.filter(status="queued").count())
qs = AnalysisTask.objects.filter(status__in=active + ["queued"]).order_by("created_at")[:30]
for t in qs:
    print("TASK", str(t.id), "title", repr(t.title[:120]), "project", t.project_id, "status", t.status, "step", repr(t.current_step), "progress", t.progress, "age_minutes", round((now-t.created_at).total_seconds()/60), "update_age_minutes", round((now-t.updated_at).total_seconds()/60), "celery_id", t.celery_task_id)
    for e in AnalysisTaskExecutionLog.objects.filter(task=t).order_by("-created_at")[:5]:
        print("  EVENT", e.created_at.isoformat(), e.event, repr(e.message[:180]))
target = os.environ.get("DIAG_TASK_ID", "").strip()
if target:
    try:
        t = AnalysisTask.objects.get(pk=target)
        print("TARGET", str(t.id), "status", t.status, "step", repr(t.current_step), "error", repr(t.error_message[:1000]), "celery_id", t.celery_task_id)
        for e in AnalysisTaskExecutionLog.objects.filter(task=t).order_by("created_at"):
            print("  TARGET_EVENT", e.created_at.isoformat(), e.event, repr(e.message[:180]), "detail", repr(str(e.detail)[:500]))
    except (AnalysisTask.DoesNotExist, ValueError) as exc:
        print("TARGET_LOOKUP_FAILED", type(exc).__name__, str(exc))
'

section 'Redis 连接与队列长度'
REDIS_CONTAINER="$(docker ps --format '{{.Names}}' | awk '/^(wharttest-250-redis|wharttest-new-redis|wharttest-redis)$/ { print; exit }')"
if [ -n "$REDIS_CONTAINER" ]; then
  run docker exec "$REDIS_CONTAINER" redis-cli ping
  run docker exec "$REDIS_CONTAINER" redis-cli -n 0 llen celery
  run docker exec "$REDIS_CONTAINER" redis-cli -n 0 llen task_center
else
  echo '未找到 Redis 容器；请根据容器列表确认名称。'
fi

section 'Backend 最近日志（请检查并遮盖敏感信息后再回传）'
run docker logs --since 4h --tail "$LOG_LINES" "$BACKEND_CONTAINER"

section 'Celery Worker 专用日志（容器日志通常不包含这些内容）'
run docker exec "$BACKEND_CONTAINER" sh -c '
  found=0
  seen="|"
  for dir in /app/data/logs /app/logs /var/log /tmp; do
    [ -d "$dir" ] || continue
    for file in "$dir"/worker_out.log "$dir"/worker_err.log "$dir"/celery*.log "$dir"/*worker*.log; do
      [ -f "$file" ] || continue
      case "$seen" in *"|$file|"*) continue ;; esac
      seen="$seen$file|"
      found=1
      echo "容器内日志路径：$file"
      tail -n 160 "$file"
    done
  done
  [ "$found" -eq 1 ] || echo "未在常见目录找到 Worker 日志；请查看上方容器挂载位置或 Supervisor 配置。"
'

section '诊断结束'
echo '请回传脚本同级目录的 code-review-queue.log；尤其保留 TASK、EVENT、Celery inspect 和 Worker 日志。'
