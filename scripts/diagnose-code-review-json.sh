#!/usr/bin/env bash
# Read-only snapshot. Run while/after the target task runs. No code or raw LLM payload export.
set -euo pipefail
container="${BACKEND_CONTAINER:-wharttest-backend}"
task_id="${1:-}"
out="${2:-code-review-json-diagnosis.log}"
if [ -n "$task_id" ] && ! [[ "$task_id" =~ ^[a-fA-F0-9-]{36}$ ]]; then echo '任务 ID 应为 UUID；留空则检查最近 5 个任务。' >&2; exit 1; fi
umask 077
{
 date -Iseconds
 docker inspect "$container" --format 'image={{.Config.Image}} started={{.State.StartedAt}}'
 docker exec -i "$container" /opt/venv/bin/python - "$task_id" <<'PY'
import os,sys,json,re,hashlib,pathlib,ast
os.environ.setdefault('DJANGO_SETTINGS_MODULE','wharttest_django.settings')
import django
django.setup()
from code_analysis.models import AnalysisTask,AnalysisTaskExecutionLog

def syntax_only(value):
 # Preserve punctuation needed to inspect JSON, remove all literal values/source text.
 return re.sub(r'[^\s{}\[\]:,\\"\x27()=;.+*/<>!?|-]', 'x',str(value))

def clean(value):
 if isinstance(value,dict):
  return {str(k):clean(v) for k,v in value.items() if str(k).lower() not in {'token','api_key','password','encrypted_api_key','prompt','response','raw_response','content','diff','source','evidence','supporting_evidence','counter_evidence','target_source_excerpt','related_source_context'}}
 if isinstance(value,list):return [clean(v) for v in value[:40]]
 if isinstance(value,str):
  value=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[REDACTED]@',value)
  value=re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._-]+',r'\1[REDACTED]',value)
  value=re.sub(r'\b(?:sk-|glpat-)[A-Za-z0-9_-]+','[REDACTED]',value)
  return value[:1800]
 return value

def emit(label,value):print(label,json.dumps(clean(value),ensure_ascii=False,default=str))
qs=AnalysisTask.objects.filter(pk=sys.argv[1]) if sys.argv[1] else AnalysisTask.objects.order_by('-updated_at')[:5]
for t in qs:
 emit('TASK',{'id':str(t.pk),'mode':t.mode,'status':t.status,'step':t.current_step,'progress':t.progress,'error':t.error_message,'ai_coverage':t.ai_coverage,'updated_at':t.updated_at,'celery_task_id':t.celery_task_id})
 report=t.change_report if isinstance(t.change_report,dict) else {}
 emit('SUMMARY',report.get('summary',{}))
 emit('CANDIDATE_AUDIT',report.get('candidate_audit',[]))
 emit('EXECUTION_NOTE',report.get('analysis_note',''))
 emit('AI_BATCHES',(report.get('coverage') or {}).get('ai_review',{}))
 emit('VERIFICATION_RETRY',report.get('verification_retry',{}))
 for item in report.get('findings') or []:
  if item.get('verification_status') in {'not_verified','verification_failed'}:
   emit('UNVERIFIED', {k:item.get(k) for k in ('key','file','disposition','severity','verification_status','verification_reason','missing_evidence')})
 for e in reversed(list(AnalysisTaskExecutionLog.objects.filter(task=t).order_by('-created_at')[:35])):
  # Stage-level metrics/errors only, never arbitrary payloads containing source.
  d=e.detail if isinstance(e.detail,dict) else {}
  allowed=('stage','status','error','error_type','reason','note','message','elapsed_ms','tokens','token_usage','total_tokens','candidate_count','completed_count','failed_count','batch','retry_count','candidate_keys','response_length','response_sha256','finish_reason','usage','line','column','position','syntax_window','cursor_in_window','raw_response_saved')
  emit('EVENT',{'time':e.created_at,'event':e.event,'message':e.message,'detail':{k:d[k] for k in allowed if k in d}})
try:
 from code_analysis.models import CodeAnalysisLLMConfig
 names={f.name for f in CodeAnalysisLLMConfig._meta.fields}
 allowed=[n for n in ('id','name','provider','model','model_name','is_active','max_tokens','max_output_tokens','timeout','temperature','request_timeout','max_retries') if n in names]
 emit('MODEL_SETTINGS',list(CodeAnalysisLLMConfig.objects.filter(is_active=True).values(*allowed)))
except Exception as exc:emit('MODEL_SETTINGS_ERROR',{'type':type(exc).__name__})
p=pathlib.Path('/app/code_analysis/services.py')
raw=p.read_bytes();tree=ast.parse(raw)
emit('SOURCE_VERSION',{'sha256':hashlib.sha256(raw).hexdigest(),'json_parser_lines':[n.lineno for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_json_from_response']})
print('=== Worker logs: fixed markers and error type/position only; no raw lines ===')
for name in ('worker_err.log','worker_out.log','django_err.log','celery.log'):
 p=pathlib.Path('/app/data/logs')/name
 if not p.is_file():continue
 with p.open('rb') as f:
  size=f.seek(0,2);f.seek(max(0,size-16*1024*1024));lines=f.read().decode('utf-8',errors='replace').splitlines()
 hits=[]
 for i,line in enumerate(lines):
  markers=[x for x in ('JSONDecodeError','Expecting','Unterminated string','LLM未返回JSON对象','反证','finish_reason','length','max_tokens','upstream_unavailable') if x in line]
  if not markers:continue
  # Only report generic JSON syntax error positions, never original response/stack source lines.
  position=re.search(r'line \d+ column \d+ \(char \d+\)',line)
  hits.append({'tail_line':i+1,'markers':markers,'position':position.group(0) if position else None})
 emit('LOG_MARKERS',{'file':name,'bytes':size,'tail_limit':'16MiB','hit_count':len(hits),'last_hits':hits[-45:]})
print('LIMITATION: 当前脚本不拦截模型调用；若运行版本未保存原始响应，不能还原出错字符。历史日志标记不可单独归因本次任务。')
print('PRIVACY: 未导出业务源码、提示词、模型原始响应或凭据。错误摘要仍请回传前检查。')
PY
} > "$out" 2>&1
printf '诊断结果：%s\n' "$out"
