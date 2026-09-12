#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$UPDATE_COMPOSE"

required=(
  hotfix/requirements/services.py
  hotfix/testcases/review_service.py
  hotfix/testcases/views.py
  hotfix/testcases/management/commands/recover_stale_testcase_reviews.py
  hotfix/orchestrator_integration/builtin_tools/skill_tools.py
  hotfix/bundled_skills/test-case-clarity-review/SKILL.md
  hotfix/bundled_skills/test-case-clarity-review/references/review-rules.md
)
for relative_path in "${required[@]}"; do
  [ -f "$UPDATE_DIR/$relative_path" ] || fail "热修复文件缺失：$relative_path"
done

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
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
skill_tool = Path("/app/orchestrator_integration/builtin_tools/skill_tools.py").read_text(encoding="utf-8")
assert "chunk_size = 25" in service
assert "max_workers = min(2" in service
assert "config.request_timeout" in service
assert "max_retries=0" in service
assert "已内联参考文件" in skill_tool
assert Path("/app/bundled_skills/test-case-clarity-review/references/review-rules.md").is_file()
print("testcase review hotfix OK")
'

echo "[恢复] Backend 重建期间可能退出的三个执行器"
"${compose[@]}" up -d --no-deps actuator-01 actuator-02 actuator-03

echo "热修复已生效。请重新发起小用例审查；如仍失败，执行 18-diagnose-small-testcase-review.sh。"
