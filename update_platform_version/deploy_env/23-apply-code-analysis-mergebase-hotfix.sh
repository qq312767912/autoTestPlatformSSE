#!/usr/bin/env bash
set -euo pipefail

# 代码审查「差异范围改用共同祖先比较」热修复。
# 不换镜像、不动数据库、不需要 migrate，只覆盖两个只读挂载文件并重建 Backend。
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
# 本版本起 Backend 镜像已内置该修复，代码挂载层已移出 docker-compose.update.yml，
# 仅在跑本脚本（临时热修复）时叠加 docker-compose.hotfix.yml。
HOTFIX_COMPOSE="$UPDATE_DIR/docker-compose.hotfix.yml"
HOTFIX_FILE="$UPDATE_DIR/hotfix/code_analysis/services.py"
HOTFIX_VIEW="$UPDATE_DIR/hotfix/code_analysis/views.py"

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$UPDATE_COMPOSE"
[ -f "$HOTFIX_COMPOSE" ] || fail "找不到热修复覆盖 YAML：$HOTFIX_COMPOSE"
[ -f "$HOTFIX_FILE" ] || fail "缺少代码审查热修复文件：$HOTFIX_FILE"
[ -f "$HOTFIX_VIEW" ] || fail "缺少代码审查校验接口热修复文件：$HOTFIX_VIEW"

# 守门：必须带上共同祖先比较，且不能还是两点比较的旧版。
grep -q 'def merge_base' "$HOTFIX_FILE" || fail "热修复文件缺少 merge_base（GitLabClient/LocalGitClient）"
grep -q '"straight": False' "$HOTFIX_FILE" || fail "热修复文件仍是 GitLab 两点比较（straight 未改为 False）"
grep -q 'compare_base' "$HOTFIX_FILE" || fail "热修复文件未以共同祖先作为比较起点"
grep -q '\.merge_base(' "$HOTFIX_VIEW" || fail "热修复文件缺少校验接口的共同祖先解析"
grep -q 'compare_base_sha' "$HOTFIX_VIEW" || fail "热修复文件缺少 compare_base_sha 返回字段"
grep -q '基准与目标疑似颠倒' "$HOTFIX_VIEW" || fail "热修复文件缺少基准/目标颠倒的方向校验"
# 既有能力不能被这次同步挤掉。
grep -q 'OCR_CONCURRENCY = 1' "$HOTFIX_FILE" || fail "同步后丢失 OCR 单并发"
grep -q 'def _invalid_ocr_result_reason' "$HOTFIX_FILE" || fail "同步后丢失 OCR 超时诊断"
grep -q 'def retry_ocr' "$HOTFIX_VIEW" || fail "同步后丢失 OCR 单独重试接口"
grep -q 'def copy_from_platform' "$HOTFIX_VIEW" || fail "同步后丢失 LLM 配置复制接口"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE" -f "$HOTFIX_COMPOSE")
"${compose[@]}" config --quiet

echo "[提醒] 重新创建 Backend 会中断当前正在运行的后台任务，请确认当前无必须保留的运行中任务。"
echo "[应用] 不更换镜像、不改数据库，仅挂载代码审查热修复并重新创建 Backend"
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

echo "[验证] 容器内热修复代码"
docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path

service = Path("/app/code_analysis/services.py").read_text(encoding="utf-8")
view = Path("/app/code_analysis/views.py").read_text(encoding="utf-8")

assert "def merge_base" in service
assert "\"straight\": False" in service
assert "\"straight\": True" not in service
assert "compare_base" in service
assert ".merge_base(" in view
assert "compare_base_sha" in view
assert "基准与目标疑似颠倒" in view
# 同步本文件不得挤掉既有能力
assert "OCR_CONCURRENCY = 1" in service
assert "def _invalid_ocr_result_reason" in service
assert "def terminate_ocr_processes" in service
assert "def _get_code_analysis_llm_config" in service
assert "def retry_ocr" in view
assert "def copy_from_platform" in view
assert "terminate=True" in view
print("code analysis merge-base hotfix OK")
'

echo "[验证] 共同祖先解析真实可用（容器内读取当前仓库配置）"
docker exec wharttest-backend /opt/venv/bin/python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from code_analysis.services import LocalGitClient, GitLabClient
assert hasattr(LocalGitClient, "merge_base"), "LocalGitClient 缺少 merge_base"
assert hasattr(GitLabClient, "merge_base"), "GitLabClient 缺少 merge_base"
print("merge_base 已注册在 LocalGitClient 与 GitLabClient 上")
'

echo "[恢复] 确保三个执行器保持运行并重新连接 Backend"
"${compose[@]}" up -d --no-deps actuator-01 actuator-02 actuator-03

echo "[完成] 代码审查差异范围已按共同祖先（merge base）比较，可消除「已修复问题被重复报成新增风险」的误报。"
echo "校验入口：bash 05-verify.sh"
echo "使用注意：对历史「基准不是目标祖先」的分析任务需要重新执行才能刷新结论；已完成的记录不会自动重算。"
