#!/usr/bin/env bash
set -euo pipefail

VERIFY_OCR_LIVE="${VERIFY_OCR_LIVE:-1}"
TEST_HOSTNAME="${TEST_HOSTNAME:-www.test.sse.com.cn}"

expected=(
  'wharttest-backend|wharttest-250-backend:update-d595a628-review-fix-r5-arm64'
  'wharttest-frontend|wharttest-250-frontend:update-d595a628-review-fix-r5-arm64'
  'wharttest-vision-mcp|wharttest-250-vision-mcp:update-01339484-arm64-r2'
  'wharttest-mcp|wharttest-250-mcp-alpine:latest'
  'wharttest-qdrant|qdrant-kylin-arm64:v1.16.0-page64k'
  'wharttest-playwright-mcp|playwright-mcp-alpine:latest'
)

for entry in "${expected[@]}"; do
  IFS='|' read -r container image <<< "$entry"
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  [ "$actual" = "$image" ] || { echo "[失败] $container 应为 $image，实际为 $actual" >&2; exit 1; }
  state="$(docker inspect "$container" --format '{{.State.Status}}')"
  [ "$state" = "running" ] || { echo "[失败] $container 状态为 $state" >&2; exit 1; }
  echo "[通过] $container -> $actual"
done

# 生效代码必须来自镜像本体。d595a628-review-fix-r5 起 Backend 镜像已完整包含此前的全部
# 代码修复，不再需要 hotfix/ 挂载；若仍检测到代码覆盖层，说明生效的是挂载副本而不是镜像，
# 上面基于文件内容的断言就失去意义（无论镜像是否更新都可能通过），必须直接失败。
#
# 判据必须看挂载“源”是否落在 deploy_env/hotfix/ 下，不能只看挂载“目标”目录：
# 基础 compose 里本来就有一条 ./skills:/app/bundled_skills:ro（平台技能目录外置，用于技能
# 热插拔），而 hotfix 层里也有一条 hotfix/bundled_skills:/app/bundled_skills:ro —— 两者
# 目标完全相同、只有源不同。按目标匹配会把基础部署的这条合法挂载误报成覆盖层。
HOTFIX_MOUNT_PREFIX="/update_platform_version/deploy_env/hotfix/"
overlay_mounts="$(docker inspect wharttest-backend --format '{{range .Mounts}}{{println .Source " -> " .Destination}}{{end}}' \
  | grep -F "$HOTFIX_MOUNT_PREFIX" || true)"
if [ -n "$overlay_mounts" ]; then
  echo "[失败] Backend 仍叠加着 hotfix 代码覆盖层，生效内容不是镜像本体：" >&2
  echo "$overlay_mounts" >&2
  echo "       紧急热修复请显式叠加 docker-compose.hotfix.yml；正常升级应卸下覆盖层后重跑本脚本。" >&2
  exit 1
fi
echo "[通过] Backend 无 hotfix 代码覆盖层，生效代码全部来自镜像"

# /app/bundled_skills 在基础 compose 里就是外置挂载（技能热插拔），正常部署下生效的是宿主机
# 的 offline-images/skills 目录，既不是镜像本体也不是 hotfix 层。这里显式打印来源，避免把
# “技能目录外置”误判成“代码覆盖层”，也便于确认技能修复到底有没有同步到宿主机目录。
skills_source="$(docker inspect wharttest-backend --format '{{range .Mounts}}{{if eq .Destination "/app/bundled_skills"}}{{.Source}}{{end}}{{end}}')"
echo "[信息] /app/bundled_skills 来源：${skills_source:-（未挂载，使用镜像内副本）}"

curl -fsS http://127.0.0.1:8912/admin/login/ >/dev/null
curl -fsS http://127.0.0.1:8913/ >/dev/null
echo "[通过] Backend 和 Frontend HTTP 检查"

docker exec wharttest-backend /opt/venv/bin/python /app/manage.py migrate --check
echo "[通过] 数据库迁移状态"

docker exec wharttest-backend sh -c \
  'grep -q "Alpine Linux" /etc/os-release && ldd --version 2>&1 | grep -qi musl && command -v ocr >/dev/null && ocr --version && npm list -g --depth=0 @alibaba-group/open-code-review >/dev/null && grep -q -- "--concurrency=8" /app/supervisord.conf && grep -q "/app/data/logs" /app/supervisord.conf && ! grep -q "/var/log" /app/supervisord.conf'
echo "[通过] Backend Alpine/musl、OpenCodeReview（ocr CLI 可用）、Celery 并发 8，运行日志不写 /var"

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
assert "repository_count=Count" in view
assert "def _claim_global_slot" in tasks
assert "(\"degraded\", \"降级完成\")" in models
assert "class CodeAnalysisLLMConfig" in models
assert "class CodeAnalysisLLMConfigSerializer" in serializers
assert "repository_count = serializers.SerializerMethodField" in serializers
assert "router.register(\"llm-config\"" in urls
assert "def _get_code_analysis_llm_config" in service
assert "if task.mode != \"deep\"" in service
assert "if task.mode != \"deep\"" in view
assert "default_branch = models.CharField(max_length=255, default=\"master\")" in models
# 差异范围按共同祖先比较：缺了这些断言就说明挂载的还是两点比较的旧版，
# 基准分支上已修好的改动会被当成目标分支的删除，重复报出并不存在的问题。
assert "def merge_base" in service
assert "\"straight\": False" in service
assert "\"straight\": True" not in service
assert "compare_base" in service
assert ".merge_base(" in view
assert "compare_base_sha" in view
assert "基准与目标疑似颠倒" in view
'
echo "[通过] OpenCodeReview 单并发、超时诊断、分析模式、master 默认分支、专用 LLM、已有配置复制、GitLab 连接删除保护、差异按共同祖先比较"

docker exec wharttest-frontend sh -c \
  'grep -R -q "impact-module-grid.*grid-template-columns:minmax(0,1fr)" /usr/share/nginx/html/assets/*.css'
echo "[通过] 代码审查概览长模块名单列布局"

# 前端产物里必须带上报告导出与断点续审提示。这些字符串只出现在对应功能源码中，
# 命中即证明镜像里的是一版包含这些能力的前端产物，而不是旧镜像。
docker exec wharttest-frontend sh -c \
  'cd /usr/share/nginx/html && grep -R -q "代码审查报告_" assets/ && grep -R -q "测试分析报告_" assets/ && grep -R -q "导出报告" assets/ && grep -R -q "将从断点继续" assets/ && grep -R -q "coverage-warning" assets/'
echo "[通过] 代码审查 HTML/测试分析报告导出入口、用例审查断点续审提示与覆盖率告警"

# 报告命名口径必须是「<报告名>_<代码仓库名>」，而不是平台项目名：同一平台项目下可以有多个
# 代码仓库，用项目名会让不同仓库的报告同名。只断言字符串存在证明不了口径（旧版同样含
# 「代码审查报告_」），所以在前缀字面量之后的窗口里找 repository_name —— 旧版内联写法
# 紧跟的是 project_name，过不了这条断言。
frontend_bundle="$(docker exec wharttest-frontend sh -c 'ls -S /usr/share/nginx/html/assets/*.js | head -1')"
docker exec wharttest-frontend cat "$frontend_bundle" | awk -v n1='代码审查报告_' -v n2='测试分析报告_' -v field='repository_name' '
  { for (kind = 1; kind <= 2; kind++) {
      needle = (kind == 1 ? n1 : n2)
      at = index($0, needle)
      if (at > 0 && index(substr($0, at, 200), field) > 0) found[kind] = 1
    } }
  END { exit (found[1] && found[2]) ? 0 : 1 }' || {
  echo "[失败] 前端报告命名口径不是「报告名_代码仓库名」：$frontend_bundle" >&2
  echo "       前端产物缺少「报告名前缀 + repository_name」的组合，可能仍是按平台项目名命名的旧产物。" >&2
  exit 1
}
echo "[通过] 报告标题与下载文件名均为「报告名_代码仓库名」口径"

docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path
service = Path("/app/testcases/review_service.py").read_text(encoding="utf-8")
serializers = Path("/app/testcases/serializers.py").read_text(encoding="utf-8")
assert "TESTCASE_REVIEW_CHUNK_SIZE = 20" in service
assert "TESTCASE_REVIEW_MAX_WORKERS = 2" in service
assert "TESTCASE_REVIEW_CHUNK_ATTEMPTS = 3" in service
assert "def _chunk_attempt_limit" in service
assert "def _retry_delay" in service
assert "TESTCASE_REVIEW_CIRCUIT_BREAKER = 3" in service
assert "TESTCASE_REVIEW_TOTAL_BUDGET_SECONDS = 45 * 60" in service
assert "uncovered_chunks" in service
assert "ThreadPoolExecutor" in service
assert "串行小分片" not in service
assert "\"_checkpoint\"" in service
assert "def _is_case_header" in service
assert "config.request_timeout" in service
assert "max_retries=0" in service
assert "if key != \"_checkpoint\"" in serializers
rules = Path("/app/bundled_skills/test-case-clarity-review/references/review-rules.md")
skill_md = Path("/app/bundled_skills/test-case-clarity-review/SKILL.md")
# /app/bundled_skills 是基础 compose 的外置挂载，内容来自宿主机 offline-images/skills，
# 升级包不含该目录，所以缺文件时不能靠重装镜像解决，只能同步宿主机目录。
_hint = ("（修复：重跑 bash 04-deploy.sh 即会自动同步技能并刷新数据库；也可手工执行 "
         "bash 24-sync-bundled-skills.sh --apply --force，再执行 "
         "docker exec wharttest-backend /opt/venv/bin/python /app/manage.py init_skills）")
assert rules.is_file() and skill_md.is_file(), (
    f"缺少技能文件 {skill_md} 或 {rules}：/app/bundled_skills 由基础 compose 外置挂载到宿主机的 "
    "offline-images/skills 目录，不是镜像内副本。" + _hint)
# 只查文件存在不够：宿主机目录里可能残留精简兜底版（有文件但没规则明细），
# 那种情况下界面和审查行为都还是旧的。这里校验完整版独有的章节，保证装的确实是完整版。
rules_text = rules.read_text(encoding="utf-8")
skill_text = skill_md.read_text(encoding="utf-8")
assert "## 9. 严重程度参考" in rules_text, (
    "references/review-rules.md 不是完整版（缺第 9 条「严重程度参考」），"
    "宿主机技能目录里可能是精简兜底版。" + _hint)
assert "## 完成条件" in skill_text, (
    "SKILL.md 不是完整版（缺「完成条件」章节）。" + _hint)
'
echo "[通过] 用例审查表头识别、20行/2并发分片、重试退避与熔断、断点续审及完整版 Skill 内容校验"

# 宿主机目录到位 ≠ 审查读到新内容。审查送进模型的内容由 testcases/review_service.py 的
# build_skill_snapshot() 组装，两条路径都不是宿主机那个目录：
#   ① 选中了 Skill：DB 的 Skill.skill_content（=SKILL.md）+ MEDIA_ROOT 下该 Skill 的
#      references/*.md（init_skills 用 _sync_files 复制过去的那份副本）；
#   ② 未选中 Skill：才回退读 /app/bundled_skills（上面已校验）。
# 所以只补宿主机目录、不跑 init_skills，界面和审查结果仍是旧版。这一段校验的正是 ①。
docker exec -i wharttest-backend /opt/venv/bin/python - <<'PY'
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django

django.setup()
from skills.models import Skill

HINT = ("（修复：docker exec wharttest-backend /opt/venv/bin/python /app/manage.py init_skills；"
        "或直接重跑 bash 04-deploy.sh，它会在 Backend 健康后自动刷新）")

rows = list(Skill.objects.filter(name="test-case-clarity-review"))
if not rows:
    print("  [跳过] 数据库暂无 test-case-clarity-review 记录，审查将走 /app/bundled_skills 回退读取（已单独校验）")
for skill in rows:
    full_path = skill.get_full_path()
    rules = Path(full_path) / "references" / "review-rules.md" if full_path else None
    assert "## 完成条件" in skill.skill_content, (
        f"Skill[{skill.id}] 数据库里的 SKILL.md 不是完整版（缺「完成条件」章节），"
        "说明文件同步后没跑 init_skills。" + HINT)
    assert rules is not None and rules.is_file(), (
        f"Skill[{skill.id}] 媒体目录缺少 references/review-rules.md（{rules}），"
        "审查实际读的是这份副本，不是宿主机 offline-images/skills。" + HINT)
    rules_text = rules.read_text(encoding="utf-8")
    assert "## 9. 严重程度参考" in rules_text, (
        f"Skill[{skill.id}] 媒体目录里的 review-rules.md 不是完整版（缺第 9 条「严重程度参考」）。" + HINT)
    db_bytes = len(skill.skill_content.encode("utf-8"))
    print(f"  [通过] Skill[{skill.id}] project={skill.project_id}："
          f"DB SKILL.md {db_bytes} 字节，媒体目录 review-rules.md {rules.stat().st_size} 字节")
PY
echo "[通过] 数据库技能快照与媒体目录规则副本均为完整版（审查实际读取的路径）"

celery_ping="$(docker exec wharttest-backend timeout 20 celery -A wharttest_django inspect ping --timeout=10)"
echo "$celery_ping" | grep -q 'pong' || { echo "[失败] Celery Worker 未响应 ping" >&2; exit 1; }
registered="$(docker exec wharttest-backend timeout 30 celery -A wharttest_django inspect registered --timeout=15)"
echo "$registered" | grep -q 'code_analysis.tasks.run_code_analysis' || { echo "[失败] Celery 未注册代码审查任务" >&2; exit 1; }
echo "$registered" | grep -q 'testcases.review_tasks.execute_testcase_review' || { echo "[失败] Celery 未注册用例审查任务" >&2; exit 1; }
echo "[通过] Celery Worker 在线，代码审查和用例审查任务已注册"

media_source='/projects/ai-test-platform/offline-images/data/media'
frontend_mounts="$(docker inspect wharttest-frontend --format '{{range .Mounts}}{{println .Source "|" .Destination "|" .RW}}{{end}}')"
echo "$frontend_mounts" | grep -F -- "$media_source | /app/data/media | false" >/dev/null || {
  echo "[失败] Frontend 未只读挂载 Backend 的共享媒体目录，请执行 15-fix-artifact-download-volume.sh" >&2
  exit 1
}
docker exec wharttest-frontend test -d /app/data/media
probe_name="deploy-verify-$$.txt"
docker exec wharttest-backend sh -c "mkdir -p /app/data/media/healthchecks && touch /app/data/media/healthchecks/$probe_name"
cleanup_probe() { docker exec wharttest-backend rm -f "/app/data/media/healthchecks/$probe_name" >/dev/null 2>&1 || true; }
trap cleanup_probe EXIT
curl -fsS "http://127.0.0.1:8913/media/healthchecks/$probe_name" >/dev/null
cleanup_probe
trap - EXIT
echo "[通过] Skill 交付物共享卷与 Frontend 实际下载链路"

docker exec wharttest-vision-mcp python -c \
  'import onnxruntime; from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print("Vision OCR runtime OK")'
echo "[通过] Vision MCP OCR 运行库可在当前宿主机加载"

for index in 01 02 03; do
  container="wharttest-actuator-$index"
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  [ "$actual" = 'wharttest-250-actuator:update-178fb3ed-arm64-r4' ] || { echo "[失败] $container 镜像错误：$actual" >&2; exit 1; }
  status="$(docker inspect "$container" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}')"
  case "$status" in
    running/healthy|running/no-healthcheck) echo "[通过] $container -> $status" ;;
    *) docker logs --tail 100 "$container"; echo "[失败] $container -> $status" >&2; exit 1 ;;
  esac
  docker exec -i "$container" python - "$TEST_HOSTNAME" <<'PY'
import socket
import sys
import urllib.error
import urllib.request
hostname = sys.argv[1]
addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname, 80, type=socket.SOCK_STREAM)})
if not addresses:
    raise SystemExit(f"无法解析 {hostname}")
try:
    with urllib.request.urlopen(f"http://{hostname}", timeout=15) as response:
        status = response.status
except urllib.error.HTTPError as exc:
    status = exc.code
if status >= 500:
    raise SystemExit(f"{hostname} 返回 HTTP {status}")
print(f"{hostname} -> {','.join(addresses)} HTTP {status}")
PY
done

online_count=0
for _attempt in $(seq 1 12); do
  actuator_payload="$(curl -fsS http://127.0.0.1:8912/api/ui-automation/actuators/list_actuators/ || true)"
  online_count="$(printf '%s' "$actuator_payload" | docker exec -i wharttest-backend /opt/venv/bin/python -c \
    'import json,sys; payload=json.load(sys.stdin); values=[]
while isinstance(payload,dict):
 values.append(payload.get("count")); payload=payload.get("data")
print(next((int(value) for value in values if value is not None),0))' 2>/dev/null || echo 0)"
  [ "$online_count" -ge 3 ] && break
  sleep 5
done
[ "$online_count" -ge 3 ] || { echo "[失败] Backend 仅识别到 $online_count 个在线执行器，期望至少 3 个" >&2; exit 1; }
echo "[通过] Backend WebSocket 注册表识别到 $online_count 个在线执行器"

if [ "$VERIFY_OCR_LIVE" = "1" ]; then
  echo "[检查] 使用当前激活 LLM 执行最小 OpenCodeReview 调用（最长 4 分钟）"
  docker exec -i wharttest-backend timeout 240 /opt/venv/bin/python - <<'PY'
import json
import os
import subprocess
import tempfile
from pathlib import Path
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from code_analysis.models import CodeAnalysisLLMConfig
config = CodeAnalysisLLMConfig.objects.filter(is_active=True).first()
if not config:
    raise SystemExit("没有已启用的代码审查专用 LLM 配置")
env = os.environ.copy()
env.update({"OCR_LLM_URL": config.api_url, "OCR_LLM_TOKEN": config.api_key, "OCR_LLM_MODEL": config.name, "OCR_LLM_PROTOCOL": "openai"})
with tempfile.TemporaryDirectory(prefix="ocr-verify-") as directory:
    root = Path(directory)
    def git(*args): subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    git("init"); git("config", "user.email", "verify@wharttest.local"); git("config", "user.name", "WHartTest Verify")
    source = root / "UserExportVO.java"
    source.write_text(
        "import cn.afterturn.easypoi.excel.annotation.Excel;\n\n"
        "public class UserExportVO {\n"
        "    @Excel(name = \"姓名\")\n"
        "    private String name;\n"
        "}\n",
        encoding="utf-8",
    )
    git("add", "UserExportVO.java"); git("commit", "-m", "base")
    source.write_text(
        "import cn.afterturn.easypoi.excel.annotation.Excel;\n\n"
        "public class UserExportVO {\n"
        "    @Excel(name = \"姓名\")\n"
        "    private String name;\n\n"
        "    private String phone;\n"
        "}\n",
        encoding="utf-8",
    )
    git("add", "UserExportVO.java"); git("commit", "-m", "add export phone field")
    output = root / "result.json"
    result = subprocess.run(
        ["ocr", "review", "--from", "HEAD~1", "--to", "HEAD", "--format", "json", "--audience", "agent",
         "--concurrency", "1", "--timeout", "2", "--output", str(output)],
        cwd=root, env=env, capture_output=True, text=True, timeout=220,
    )
    text = output.read_text(encoding="utf-8") if output.exists() else result.stdout
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SystemExit("OpenCodeReview 未返回 JSON：" + (result.stderr or text)[-500:])
    payload = json.loads(text[start:end + 1])
    accepted = {"success", "complete", "completed", "partial", "partial_success", "completed_with_warnings"}
    if payload.get("status") not in accepted:
        raise SystemExit("OpenCodeReview 状态异常：" + str(payload.get("status") or payload.get("message")))
    comments = payload.get("comments") or []
    review_text = json.dumps(comments, ensure_ascii=False).lower()
    expected_terms = ("excel", "导出", "注解", "annotation")
    if not comments or not any(term in review_text for term in expected_terms):
        raise SystemExit("OpenCodeReview 调用成功，但未识别新增导出字段缺少 Excel 注解")
    print("OpenCodeReview 最小真实调用成功，并识别到 Excel 导出注解遗漏；状态=" + str(payload.get("status")))
PY
  echo "[通过] OpenCodeReview 到当前 LLM 的真实调用链路"
else
  echo "[跳过] OpenCodeReview 真实模型调用（VERIFY_OCR_LIVE=$VERIFY_OCR_LIVE）"
fi

# 用例审查专用 LLM（testcases.TestCaseReviewLLMConfig）：
#   ① 代码层：专用配置模型 / 序列化器 / 管理员权限 / 路由 / 前置校验齐全，
#      且审查服务**不再回退**平台通用配置（回退会让专用配置形同虚设）；
#   ② 迁移层：0025 已应用（专用配置表已建立），且保持单例；
#   ③ 路由层：新接口已挂载 —— 未认证应返回 401/403，返回 404 说明路由没进镜像；
#   ④ 前端层：用例审查页的配置入口已进产物。
docker exec wharttest-backend /opt/venv/bin/python - <<'PY'
import re
from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


models = read("/app/testcases/models.py")
serializers = read("/app/testcases/serializers.py")
views = read("/app/testcases/views.py")
permissions = read("/app/testcases/permissions.py")
service = read("/app/testcases/review_service.py")
urls = read("/app/testcases/urls.py")
root_urls = read("/app/wharttest_django/urls.py")

assert "class TestCaseReviewLLMConfig" in models, "缺少用例审查专用 LLM 配置模型"
assert "encrypted_api_key" in models and "def set_api_key" in models, "专用配置缺少加密密钥实现"
assert "class TestCaseReviewLLMConfigSerializer" in serializers, "缺少专用配置序列化器"
assert "class IsPlatformAdmin" in permissions, "缺少管理员专用权限类"
assert "class TestCaseReviewLLMConfigViewSet" in views and "IsPlatformAdmin" in views, (
    "专用配置视图未收紧到平台管理员")
assert "def _get_testcase_review_llm_config" in service, "审查服务未切换到专用配置"
# 判据必须排除 TestCaseReviewLLMConfig 这个前缀，否则专用配置自己的 ORM 调用
# 也会命中「LLMConfig.objects...」子串，把正常实现误报成回退。
assert "langgraph_integration.models import LLMConfig" not in service, (
    "审查服务仍导入平台通用 LLM 配置：专用配置形同虚设")
assert not re.search(r"(?<!TestCaseReview)LLMConfig\.objects", service), (
    "审查服务仍会回退平台通用配置：专用配置形同虚设")
assert "def _testcase_review_llm_ready" in views, "缺少审查创建/重试的前置条件校验"
assert "review-llm-config" in urls and 'include("testcases.urls")' in root_urls, "专用配置路由未挂载"
print("  [通过] 专用配置模型 / 序列化器 / 管理员权限 / 路由 / 前置校验齐全")
PY

docker exec -i wharttest-backend /opt/venv/bin/python - <<'PY'
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django

django.setup()
from django.db import connection
from testcases.models import TestCaseReviewLLMConfig

assert "testcases_testcasereviewllmconfig" in connection.introspection.table_names(), (
    "0025 迁移未应用：专用配置表不存在（修复：docker exec wharttest-backend "
    "/opt/venv/bin/python /app/manage.py migrate）")
rows = list(TestCaseReviewLLMConfig.objects.all())
assert len(rows) <= 1, f"专用配置应为单例，实际有 {len(rows)} 条"
if rows:
    row = rows[0]
    print(f"  [通过] 专用配置表已建立；config_name={row.config_name!r} "
          f"is_active={row.is_active} has_api_key={row.has_api_key}")
else:
    print("  [提示] 专用配置表已建立但暂无记录：需管理员在「用例审查 → 审查模型配置」中填写")
PY

llm_config_code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8912/api/testcases/review-llm-config/)"
case "$llm_config_code" in
  401|403) echo "[通过] 用例审查专用 LLM 接口已挂载且要求认证（HTTP $llm_config_code）" ;;
  404) echo "[失败] /api/testcases/review-llm-config/ 返回 404：路由未随镜像生效" >&2; exit 1 ;;
  *)   echo "[失败] 用例审查专用 LLM 接口返回 HTTP $llm_config_code（期望 401/403）" >&2; exit 1 ;;
esac

docker exec wharttest-frontend sh -c \
  'cd /usr/share/nginx/html && grep -R -q "review-llm-config" assets/ && grep -R -q "审查模型配置" assets/ && grep -R -q "用例审查专用 LLM" assets/'
echo "[通过] 前端产物已包含用例审查的模型配置入口"

echo "[全部通过] 容器、HTTP、迁移、Celery、OpenCodeReview、文件下载、Vision OCR、执行器注册与域名访问"
docker ps --filter 'name=wharttest' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
