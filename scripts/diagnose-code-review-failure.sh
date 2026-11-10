#!/usr/bin/env bash
# Read-only. No repository source, reports, or credentials exported.
set -eu
umask 077
OUT="${1:-code-review-failure.log}"
{
 date -Iseconds
 docker inspect wharttest-backend --format 'image={{.Config.Image}} started={{.State.StartedAt}}'
 docker exec -i wharttest-backend /opt/venv/bin/python - <<'PY'
import os,json,re
os.environ.setdefault('DJANGO_SETTINGS_MODULE','wharttest_django.settings')
import django
django.setup()
from code_analysis.models import AnalysisTask,AnalysisTaskExecutionLog

def emit(d):
 s=json.dumps(d,ensure_ascii=False,default=str)
 s=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[REDACTED]@',s)
 s=re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._-]+',r'\1[REDACTED]',s)
 s=re.sub(r'\b(?:sk-|glpat-)[A-Za-z0-9_-]+','[REDACTED]',s)
 print(s)
for task in AnalysisTask.objects.order_by('-updated_at')[:5]:
 emit({'TASK':str(task.pk),'status':task.status,'step':task.current_step,'progress':task.progress,'error':task.error_message,'updated_at':task.updated_at,'celery_task_id':task.celery_task_id})
 for e in reversed(list(AnalysisTaskExecutionLog.objects.filter(task=task).order_by('-created_at')[:18])):
  detail=e.detail if isinstance(e.detail,dict) else {}
  # Only error metadata, never arbitrary context/diff payloads.
  emit({'EVENT':e.event,'time':e.created_at,'message':e.message,'errors':{k:detail[k] for k in ('error','error_type','reason') if k in detail}})
PY
} > "$OUT" 2>&1
printf '结果：%s（回传前检查错误信息是否含内网凭据）\n' "$OUT"
