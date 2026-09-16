#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
HOTFIX_FILE="$UPDATE_DIR/hotfix/code_analysis/services.py"
HOTFIX_VIEW="$UPDATE_DIR/hotfix/code_analysis/views.py"
HOTFIX_TASKS="$UPDATE_DIR/hotfix/code_analysis/tasks.py"
HOTFIX_MODELS="$UPDATE_DIR/hotfix/code_analysis/models.py"
HOTFIX_SERIALIZERS="$UPDATE_DIR/hotfix/code_analysis/serializers.py"
HOTFIX_URLS="$UPDATE_DIR/hotfix/code_analysis/urls.py"
HOTFIX_MIGRATION="$UPDATE_DIR/hotfix/code_analysis/migrations/0012_codeanalysisllmconfig.py"

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$UPDATE_COMPOSE"
[ -f "$HOTFIX_FILE" ] || fail "缺少代码审查热修复文件：$HOTFIX_FILE"
[ -f "$HOTFIX_VIEW" ] || fail "缺少代码审查取消接口热修复文件：$HOTFIX_VIEW"
[ -f "$HOTFIX_TASKS" ] || fail "缺少代码审查队列热修复文件：$HOTFIX_TASKS"
[ -f "$HOTFIX_MODELS" ] || fail "缺少代码审查状态热修复文件：$HOTFIX_MODELS"
[ -f "$HOTFIX_SERIALIZERS" ] || fail "缺少代码审查序列化热修复文件：$HOTFIX_SERIALIZERS"
[ -f "$HOTFIX_URLS" ] || fail "缺少代码审查路由热修复文件：$HOTFIX_URLS"
[ -f "$HOTFIX_MIGRATION" ] || fail "缺少代码审查专用 LLM 数据库迁移：$HOTFIX_MIGRATION"

grep -q 'OCR_CONCURRENCY = 1' "$HOTFIX_FILE" || fail "热修复文件不是单并发版本"
grep -q 'def _invalid_ocr_result_reason' "$HOTFIX_FILE" || fail "热修复文件缺少超时原因诊断"
grep -q 'def terminate_ocr_processes' "$HOTFIX_FILE" || fail "热修复文件缺少 OCR 子进程清理"
grep -q 'terminate=True' "$HOTFIX_VIEW" || fail "热修复文件缺少 Celery 运行任务终止"
grep -q 'def retry_ocr' "$HOTFIX_VIEW" || fail "热修复文件缺少 OCR 单独重试接口"
grep -q 'def copy_from_platform' "$HOTFIX_VIEW" || fail "热修复文件缺少已有 LLM 配置复制接口"
grep -q 'def _claim_global_slot' "$HOTFIX_TASKS" || fail "热修复文件缺少全局单任务队列"
grep -q '("degraded", "降级完成")' "$HOTFIX_MODELS" || fail "热修复文件缺少降级完成状态"
grep -q 'class CodeAnalysisLLMConfig' "$HOTFIX_MODELS" || fail "热修复文件缺少代码审查专用 LLM 模型"
grep -q 'class CodeAnalysisLLMConfigSerializer' "$HOTFIX_SERIALIZERS" || fail "热修复文件缺少代码审查专用 LLM API"
grep -q 'router.register("llm-config"' "$HOTFIX_URLS" || fail "热修复文件缺少代码审查专用 LLM 路由"

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

docker exec wharttest-backend /opt/venv/bin/python /app/manage.py migrate --noinput

echo "[验证] 容器内热修复代码"
docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path
service = Path("/app/code_analysis/services.py").read_text(encoding="utf-8")
view = Path("/app/code_analysis/views.py").read_text(encoding="utf-8")
tasks = Path("/app/code_analysis/tasks.py").read_text(encoding="utf-8")
models = Path("/app/code_analysis/models.py").read_text(encoding="utf-8")
serializers = Path("/app/code_analysis/serializers.py").read_text(encoding="utf-8")
urls = Path("/app/code_analysis/urls.py").read_text(encoding="utf-8")
assert "OCR_CONCURRENCY = 1" in service
assert "OCR_RESUME_CONCURRENCY = 1" in service
assert "def _invalid_ocr_result_reason" in service
assert "def terminate_ocr_processes" in service
assert "terminate=True" in view
assert "def retry_ocr" in view
assert "def copy_from_platform" in view
assert "def _claim_global_slot" in tasks
assert "(\"degraded\", \"降级完成\")" in models
assert "class CodeAnalysisLLMConfig" in models
assert "class CodeAnalysisLLMConfigSerializer" in serializers
assert "router.register(\"llm-config\"" in urls
assert "def _get_code_analysis_llm_config" in service
print("code analysis queue, OCR retry and cancellation hotfix OK")
'

echo "[恢复] 确保三个执行器保持运行并重新连接 Backend"
"${compose[@]}" up -d --no-deps actuator-01 actuator-02 actuator-03

echo "[完成] 代码审查已全局串行化；支持专用 LLM、降级完成、OCR 单独重试与取消进程清理。"
echo "如需诊断，执行：bash 21-diagnose-code-analysis-ocr.sh"
