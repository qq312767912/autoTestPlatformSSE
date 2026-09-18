#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
# 新版 Backend 镜像（update-d595a628-review-fix-r5 起）已内置全部代码修复，代码挂载已从
# docker-compose.update.yml 移出到本覆盖层；跑本脚本即等同“临时启用挂载层”。
HOTFIX_COMPOSE="$UPDATE_DIR/docker-compose.hotfix.yml"

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$UPDATE_COMPOSE"
[ -f "$HOTFIX_COMPOSE" ] || fail "找不到热修复覆盖 YAML：$HOTFIX_COMPOSE"

required=(
  hotfix/requirements/services.py
  hotfix/testcases/review_service.py
  hotfix/testcases/serializers.py
  hotfix/testcases/views.py
  hotfix/testcases/management/commands/recover_stale_testcase_reviews.py
  hotfix/orchestrator_integration/builtin_tools/skill_tools.py
  hotfix/bundled_skills/test-case-clarity-review/SKILL.md
  hotfix/bundled_skills/test-case-clarity-review/references/review-rules.md
)
for relative_path in "${required[@]}"; do
  [ -f "$UPDATE_DIR/$relative_path" ] || fail "热修复文件缺失：$relative_path"
done

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE" -f "$HOTFIX_COMPOSE")
"${compose[@]}" config --quiet

echo "[应用] 仅重新创建 Backend，数据库、Frontend、Vision MCP、执行器均不重启"
"${compose[@]}" up -d --no-deps --force-recreate backend

elapsed=0
while [ "$elapsed" -lt 300 ]; do
  backend_health="$(docker inspect wharttest-backend --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  case "$backend_health" in
    healthy|running) break ;;
    unhealthy|stopped)
      docker logs --tail 150 wharttest-backend || true
      fail "Backend 状态异常：$backend_health"
      ;;
  esac
  sleep 5
  elapsed=$((elapsed + 5))
done
[ "$elapsed" -lt 300 ] || fail "Backend 启动超过 300 秒"
echo "[通过] Backend 状态：$backend_health"

echo "[同步] 完整用例审查 Skill"
docker exec wharttest-backend /opt/venv/bin/python /app/manage.py init_skills

echo "[收尾] 标记超过 15 分钟无进度的旧审查任务"
docker exec wharttest-backend /opt/venv/bin/python /app/manage.py recover_stale_testcase_reviews --minutes 15

echo "[验证] 热修复代码与挂载"
docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path
service = Path("/app/testcases/review_service.py").read_text(encoding="utf-8")
serializers = Path("/app/testcases/serializers.py").read_text(encoding="utf-8")
skill_tool = Path("/app/orchestrator_integration/builtin_tools/skill_tools.py").read_text(encoding="utf-8")

# 1) 分片与并发：20 行/批 + 2 路并发（旧版为 10 行串行，48 批串行会顶到 Celery 软时限）
assert "TESTCASE_REVIEW_CHUNK_SIZE = 20" in service
assert "TESTCASE_REVIEW_MAX_WORKERS = 2" in service
assert "ThreadPoolExecutor" in service
assert "串行小分片" not in service, "仍挂载着旧版（10 行串行）review_service.py"

# 2) 重试与退避：次数取自 LLMConfig.max_retries 且带上下限，退避为指数且封顶
assert "TESTCASE_REVIEW_CHUNK_ATTEMPTS = 3" in service
assert "TESTCASE_REVIEW_MAX_ATTEMPTS = 5" in service
assert "TESTCASE_REVIEW_RETRY_BASE_DELAY = 2" in service
assert "TESTCASE_REVIEW_RETRY_MAX_DELAY = 30" in service
assert "def _chunk_attempt_limit" in service
assert "def _retry_delay" in service

# 3) 单批失败降级 + 网关整体故障熔断 + 总时间预算
assert "TESTCASE_REVIEW_CIRCUIT_BREAKER = 3" in service
assert "TESTCASE_REVIEW_TOTAL_BUDGET_SECONDS = 45 * 60" in service
assert "uncovered_chunks" in service
assert "未生成报告" in service

# 4) 既有能力不得回退：表头识别、断点续审、超时与 SDK 重试关闭
assert "def _is_case_header" in service
assert "\"_checkpoint\"" in service
assert "config.request_timeout" in service
assert "max_retries=0" in service

# 5) 列表接口不再回传运行中的 _checkpoint（否则 4 秒轮询会拉 MB 级响应）
assert "if key != \"_checkpoint\"" in serializers

assert "已内联参考文件" in skill_tool
assert Path("/app/bundled_skills/test-case-clarity-review/references/review-rules.md").is_file()

import re

def constant(name, text=service):
    # 只解析文本，不在 exec 环境导入 Django 应用（此处未初始化 settings）。
    match = re.search(rf"^{name}\s*=\s*(.+?)\s*(?:#.*)?$", text, re.MULTILINE)
    assert match, f"review_service.py 中找不到常量 {name}"
    return eval(match.group(1), {"__builtins__": {}}, {})   # noqa: S307 仅算术表达式

chunk_size = constant("TESTCASE_REVIEW_CHUNK_SIZE")
workers = constant("TESTCASE_REVIEW_MAX_WORKERS")
attempts = constant("TESTCASE_REVIEW_CHUNK_ATTEMPTS")
max_attempts = constant("TESTCASE_REVIEW_MAX_ATTEMPTS")
breaker = constant("TESTCASE_REVIEW_CIRCUIT_BREAKER")
budget_seconds = constant("TESTCASE_REVIEW_TOTAL_BUDGET_SECONDS")
base_delay = constant("TESTCASE_REVIEW_RETRY_BASE_DELAY")
max_delay = constant("TESTCASE_REVIEW_RETRY_MAX_DELAY")
assert chunk_size == 20, chunk_size
assert workers >= 2, workers
assert 3 <= attempts <= max_attempts, (attempts, max_attempts)
assert budget_seconds <= 50 * 60, budget_seconds        # 必须小于 Celery 55 分钟软时限
print(
    "review_service 生效参数："
    f"CHUNK_SIZE={chunk_size} WORKERS={workers} "
    f"ATTEMPTS={attempts}(上限{max_attempts}) "
    f"BACKOFF={base_delay}~{max_delay}s "
    f"BUDGET={budget_seconds // 60}min BREAKER={breaker}"
)
print("testcase review hotfix OK")
'

echo "[恢复] Backend 重建期间可能退出的三个执行器"
"${compose[@]}" up -d --no-deps actuator-01 actuator-02 actuator-03

echo "热修复已生效。请对失败记录点击重试；已完成分片会从断点继续。"
