#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# 用例审查「专用 LLM 配置（仅平台管理员可配置）」热修复
# ============================================================================
# 本次改动涉及：
#   新增  testcases/models.py            -> TestCaseReviewLLMConfig（单例 + Fernet 加密密钥）
#   新增  testcases/permissions.py       -> IsPlatformAdmin
#   新增  testcases/urls.py              -> review-llm-config 路由
#   新增  testcases/migrations/0025      -> 建表 + 从通用配置复制
#   新增  wharttest_django/urls.py       -> 挂载 api/testcases/
#   同步  testcases/{review_service,serializers,views}.py
#   行为变更：审查只认专用配置，未配置时创建/重试返回 409，不回退平台通用 LLM。
#
# 通道性质：
#   * 不换镜像、不联网；Backend 容器启动时 entrypoint.sh 第一步就是 migrate --noinput，
#     所以 --force-recreate 会顺带把 0025 迁移应用上去，无需手工 migrate。
#   * ⚠️ 生效的是 docker-compose.hotfix.yml 的挂载副本，覆盖镜像内同名文件。
#     因此本脚本跑完后 05-verify.sh 的「生效内容来自镜像」硬检查会【按设计故意失败】，
#     这是提醒你当前是挂载层生效、镜像尚未更新，不是脚本出错。
#     要让修复永久生效：重建 wharttest-250-backend 镜像并走 03/04 正常升级，
#     随后撤下本覆盖层（不要长期叠加）。
#
# 用法：
#   bash 28-apply-testcase-review-llm-hotfix.sh            # 应用
#   bash 28-apply-testcase-review-llm-hotfix.sh --revert   # 回退（不带覆盖层重建 Backend）
#
# 可用环境变量覆盖：
#   BASE_COMPOSE    内网基础 compose（默认 /projects/ai-test-platform/offline-images/docker-compose.offline.yml）
#   BACKEND_CONTAINER  后端容器名（默认 wharttest-backend）
#   BACKEND_URL      后端地址（默认 http://127.0.0.1:8912）
# ============================================================================

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
HOTFIX_COMPOSE="$UPDATE_DIR/docker-compose.hotfix.yml"
HOTFIX_DIR="$UPDATE_DIR/hotfix"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8912}"

REVERT=0
for arg in "$@"; do
  case "$arg" in
    --revert) REVERT=1 ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "[失败] 未知参数：$arg（可用：--revert / --help）" >&2; exit 1 ;;
  esac
done

fail() { echo "[失败] $*" >&2; exit 1; }

[ -f "$BASE_COMPOSE" ] || fail "找不到当前内网 YAML：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 YAML：$UPDATE_COMPOSE"
[ -f "$HOTFIX_COMPOSE" ] || fail "找不到热修复覆盖 YAML：$HOTFIX_COMPOSE"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE")
# 只在两者不同时叠加，避免 --help/日志里出现重复的 -f（BASE 被覆盖成 update.yml 时会撞车）
[ "$BASE_COMPOSE" = "$UPDATE_COMPOSE" ] || compose+=(-f "$UPDATE_COMPOSE")

echo "=========================================="
echo " 用例审查专用 LLM 热修复"
echo " 模式：$([ "$REVERT" = 1 ] && echo '回退' || echo '应用')"
echo " 后端容器：$BACKEND_CONTAINER"
echo " 覆盖层：$HOTFIX_COMPOSE"
echo "=========================================="
echo

# ---------------------------------------------------------------------------
# 回退：不带覆盖层重建 Backend，挂载消失，代码回到镜像版本
# ---------------------------------------------------------------------------
recreate_backend_revert() {
  "${compose[@]}" config --quiet
  echo "[回退] 不叠加覆盖层重新创建 Backend：代码回到镜像内版本"
  "${compose[@]}" up -d --no-deps --force-recreate backend
}
wait_backend() {
  local elapsed=0 health=""
  while [ "$elapsed" -lt 300 ]; do
    health="$(docker inspect "$BACKEND_CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
    case "$health" in
      healthy|running) return 0 ;;
      unhealthy|stopped)
        docker logs --tail 150 "$BACKEND_CONTAINER" || true
        fail "Backend 状态异常：$health"
        ;;
    esac
    sleep 5
    elapsed=$((elapsed + 5))
  done
  fail "Backend 启动超过 300 秒"
}
restore_actuators() {
  local services
  services="$("${compose[@]}" config --services 2>/dev/null | grep -x 'actuator-0[1-3]' | tr '\n' ' ' || true)"
  [ -n "$services" ] || return 0
  echo "[恢复] 确保执行器保持运行并重新连接 Backend：$services"
  # shellcheck disable=SC2086
  "${compose[@]}" up -d --no-deps $services
}

if [ "$REVERT" = 1 ]; then
  recreate_backend_revert
  wait_backend

  echo "[验证] Backend 已不再挂载 hotfix 代码"
  if docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{println .Source}}{{end}}' 2>/dev/null \
      | grep -Fq "/update_platform_version/deploy_env/hotfix/"; then
    fail "回退后仍检测到 deploy_env/hotfix/ 挂载，Backend 未按预期重建"
  fi
  echo "[通过] 已不挂载 deploy_env/hotfix/ 下的任何文件"
  restore_actuators

  echo
  echo "[完成] 已回退到镜像内后端代码。"
  echo "       注意：数据库里 testcases_testcasereviewllmconfig 表与 0025 的迁移记录会保留，"
  echo "             这不影响运行（Django 容忍已应用但代码中不存在的迁移）；若确认不再需要，"
  echo "             可由 DBA 手工 DROP TABLE testcases_testcasereviewllmconfig 并清理迁移记录。"
  echo "       复核：bash $UPDATE_DIR/05-verify.sh"
  exit 0
fi

# ---------------------------------------------------------------------------
# 应用：挂载源完整性 -> 文件级守门 -> 重建 -> 容器内断言 -> 接口探活
# ---------------------------------------------------------------------------
[ -d "$HOTFIX_DIR" ] || fail "缺少热修复目录：$HOTFIX_DIR"

echo "[检查] 热修复挂载源是否齐全（防止打包遗漏导致容器起不来）"
# 判据按 hotfix/ 之后的相对路径，与安装根目录解耦：compose 里的绝对路径前缀
# 只是内网约定（/projects/ai-test-platform/...），校验的是本目录下的实际副本。
missing=0
while IFS= read -r src; do
  [ -n "$src" ] || continue
  rel="${src#*/deploy_env/hotfix/}"
  if [ "$rel" = "$src" ]; then
    echo "  [异常] 挂载源不在 deploy_env/hotfix/ 下：$src" >&2
    missing=1
    continue
  fi
  if [ ! -e "$HOTFIX_DIR/$rel" ]; then
    echo "  [缺失] hotfix/$rel（覆盖层引用了 $src）" >&2
    missing=1
  fi
done < <(awk '/^[[:space:]]+-[[:space:]]*\// { line=$0; sub(/^[[:space:]]*-[[:space:]]*/, "", line); sub(/:.*$/, "", line); print line }' "$HOTFIX_COMPOSE")
[ "$missing" = 0 ] || fail "覆盖层引用的挂载源在本目录缺失，请确认 deploy_env/hotfix/ 已完整同步"
echo "[通过] 覆盖层引用的 $(awk '/^[[:space:]]+-[[:space:]]*\// { n++ } END { print n+0 }' "$HOTFIX_COMPOSE") 个挂载源全部存在"

echo
echo "[检查] 热修复文件内容守门（只看 ASCII 特征，避免依赖终端 locale）"
gate() {  # $1=文件 $2=特征 $3=说明
  [ -f "$1" ] || fail "缺少热修复文件：$1"
  grep -qF -- "$2" "$1" || fail "$3（缺少特征：$2）"
}
gate "$HOTFIX_DIR/testcases/models.py" "class TestCaseReviewLLMConfig" "专用配置模型未同步"
gate "$HOTFIX_DIR/testcases/models.py" "def set_api_key" "专用配置模型缺少密钥加密写入"
gate "$HOTFIX_DIR/testcases/models.py" "def _testcase_review_cipher" "专用配置模型缺少 Fernet 派生"
gate "$HOTFIX_DIR/testcases/permissions.py" "class IsPlatformAdmin" "缺少平台管理员权限类"
gate "$HOTFIX_DIR/testcases/urls.py" "review-llm-config" "缺少专用配置路由注册"
gate "$HOTFIX_DIR/testcases/views.py" "class TestCaseReviewLLMConfigViewSet" "缺少专用配置视图集"
gate "$HOTFIX_DIR/testcases/views.py" "class ReviewLLMNotConfigured" "缺少未配置时的 409 异常"
gate "$HOTFIX_DIR/testcases/views.py" "def _testcase_review_llm_ready" "缺少未配置前置校验"
gate "$HOTFIX_DIR/testcases/serializers.py" "class TestCaseReviewLLMConfigSerializer" "缺少专用配置序列化器"
gate "$HOTFIX_DIR/testcases/review_service.py" "def _get_testcase_review_llm_config" "审查服务未改为读取专用配置"
gate "$HOTFIX_DIR/testcases/migrations/0025_testcasereviewllmconfig.py" "0024_testcasereview_review_options" "0025 迁移未依赖前序迁移 0024"
gate "$HOTFIX_DIR/wharttest_django/urls.py" 'include("testcases.urls")' "根路由未挂载 api/testcases/"
# 关键反向守门：审查服务不得再回退平台通用 LLM 配置。
# 判据用 import 行，避免「TestCaseReviewLLMConfig」被子串误伤。
if grep -qF "from langgraph_integration.models import LLMConfig" "$HOTFIX_DIR/testcases/review_service.py"; then
  fail "审查服务仍在直接引用平台通用 LLMConfig（应通过 _get_testcase_review_llm_config 只读专用配置）"
fi
echo "[通过] 文件内容守门全部通过"

echo
echo "[提醒] 重新创建 Backend 会中断当前正在运行的后台任务，请确认当前无必须保留的运行中任务。"
"${compose[@]}" -f "$HOTFIX_COMPOSE" config --quiet
echo "[应用] 不更换镜像、不联网，仅叠加代码热修复层并重新创建 Backend（启动时自动 apply 0025 迁移）"
"${compose[@]}" -f "$HOTFIX_COMPOSE" up -d --no-deps --force-recreate backend
wait_backend

echo
echo "[验证] 容器内代码与数据库状态"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -c '
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()

# 1) 代码层：模型 / 权限 / 序列化器 / 视图 / 服务
from testcases.models import TestCaseReviewLLMConfig
from testcases.permissions import IsPlatformAdmin
from testcases.serializers import TestCaseReviewLLMConfigSerializer
from testcases.views import ReviewLLMNotConfigured, TestCaseReviewLLMConfigViewSet
from testcases.review_service import _get_testcase_review_llm_config

assert TestCaseReviewLLMConfig._meta.db_table == "testcases_testcasereviewllmconfig"
assert ReviewLLMNotConfigured.status_code == 409, "未配置应返回 409"
assert hasattr(TestCaseReviewLLMConfig, "set_api_key")
assert hasattr(TestCaseReviewLLMConfig, "get_api_key")

# 审查服务不得回退平台通用配置（按 import 行判定，避免子串误伤）
service_src = Path("/app/testcases/review_service.py").read_text(encoding="utf-8")
assert "from langgraph_integration.models import LLMConfig" not in service_src, \
    "审查服务仍直接引用平台通用 LLMConfig"
assert "_get_testcase_review_llm_config" in service_src

# 权限类语义：普通成员不放行，管理员放行
class _U:
    def __init__(self, staff, superuser):
        self.is_staff, self.is_superuser = staff, superuser
        self.is_authenticated = True
p = IsPlatformAdmin()
assert p.has_permission(_U(False, False), None) is False, "普通用户不应放行"
assert p.has_permission(_U(True, False), None) is True, "is_staff 应放行"
assert p.has_permission(_U(False, True), None) is True, "is_superuser 应放行"

# 2) 迁移层：0025 已应用且表已建立
from django.db import connection
tables = set(connection.introspection.table_names())
assert "testcases_testcasereviewllmconfig" in tables, "迁移未生效：表不存在"
with connection.cursor() as cur:
    cur.execute("SELECT name FROM django_migrations WHERE app = %s AND name LIKE %s", ["testcases", "0025%"])
    rows = [r[0] for r in cur.fetchall()]
assert rows, "迁移记录中没有 testcases.0025_*，migrate 未执行"

# 3) 路由层：URL 可反解
from django.urls import resolve
m = resolve("/api/testcases/review-llm-config/")
assert "TestCaseReviewLLMConfigViewSet" in str(m.func.cls), "路由未指向专用配置视图集"

count = TestCaseReviewLLMConfig.objects.count()
print("testcase review llm hotfix OK")
print(f"  单例表记录数：{count}（0 表示尚未由管理员配置，属正常初始状态）")
print(f"  已应用迁移：{rows}")
'

echo
echo "[验证] 接口已挂载且要求认证"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BACKEND_URL/api/testcases/review-llm-config/" || true)"
case "$code" in
  401|403) echo "[通过] HTTP $code（已挂载且要求认证）" ;;
  404) fail "接口返回 404：路由未生效" ;;
  000) fail "无法连接 $BACKEND_URL，请确认后端已就绪" ;;
  *) fail "接口返回 HTTP $code（期望 401/403）" ;;
esac

restore_actuators

echo
echo "=========================================="
echo "[完成] 用例审查已改为使用「专用 LLM 配置」，不再复用平台通用模型。"
echo "       后续动作（必须由管理员在页面上完成，否则用例审查会返回 409）："
echo "         以 is_staff 账号进入「用例审查」页 -> 右上「审查模型配置」->"
echo "         从已有平台配置复制或手工填写 -> 「测试连接」通过后保存并启用。"
echo
echo "[注意] 本通道是临时措施：现在 Backend 生效的是 deploy_env/hotfix/ 挂载副本，"
echo "       因此 05-verify.sh 的「生效内容来自镜像」硬检查会按设计故意失败。"
echo "       要让修复永久生效：重建 wharttest-250-backend 镜像（wharttest-image-release 流程）"
echo "       并走 03/04 正常升级，然后撤下本覆盖层。"
echo "       回退：bash $UPDATE_DIR/28-apply-testcase-review-llm-hotfix.sh --revert"
echo "       复核：bash $UPDATE_DIR/05-verify.sh"
echo "=========================================="
