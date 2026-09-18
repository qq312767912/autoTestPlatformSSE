#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# 前端热修复（累计产物）：① 报告导出标题 / 文件名改用代码仓库名
#                          ② 用例审查「审查模型配置」入口（仅平台管理员可见）
# ============================================================================
# 为什么需要单独一条通道：
#   报告导出有两处产物 ——
#     ① 前端：WHartTest_Vue/src/features/code-analysis/reportExport.ts 在浏览器里拼 HTML，
#        再 Blob 下载，报告顶部标题与下载文件名都由它决定；
#     ② 后端：code_analysis/views.py 的 Markdown 下载（本次未改动）。
#   本次改动落在 ①，属于前端产物。deploy_env/hotfix/ 那套 Python 代码覆盖层只能覆盖
#   Backend 容器内的文件，对前端无效；而 05-verify.sh 的前端断言检查的是容器内
#   /usr/share/nginx/html 的实际产物（nginx.conf 的 root）。
#   所以本脚本用「只读挂载已构建产物」让修复立即生效：不重建镜像、不联网、不改数据库。
#
# 关于产物是「累计」的：
#   每次重新构建 dist 都会把当时源码里的全部前端改动一起打进去。所以本包同时携带
#   ① 报告命名（3feae08a）与 ② 用例审查专用 LLM 入口（92cbc741）两项改动，
#   包名 frontend-report-title-dist.tar.gz 是历史命名，不代表只含报告命名。
#   判据也随之扩展：解包后会同时校验报告命名口径与用例审查配置入口，任一缺失即中止。
#
# 用法：
#   bash 25-apply-frontend-report-title-hotfix.sh            # 应用
#   bash 25-apply-frontend-report-title-hotfix.sh --revert   # 回退到镜像内产物
#
# 可用环境变量覆盖：
#   BASE_COMPOSE        内网基础 compose（默认 /projects/ai-test-platform/offline-images/docker-compose.offline.yml）
#   UPDATE_COMPOSE      升级覆盖 compose（默认 ../deploy_env/docker-compose.update.yml）
#   FRONTEND_CONTAINER  前端容器名（默认 wharttest-frontend）
#   FRONTEND_URL        前端地址（默认 http://127.0.0.1:8913/）
#
# ⚠️ 本通道是临时措施，不是发布通道：
#   * 生效的是挂载产物而不是镜像本体。要让修复永久生效，请重建 wharttest-250-frontend
#     镜像并走 03/04 正常升级。
#   * 下一次不带本覆盖层的 04-deploy.sh 重建 Frontend 时，挂载会自动消失、站点回到镜像内
#     产物（即旧口径）。这是这条通道的固有代价，不是故障。
#   * 届时可用 bash 05-verify.sh 确认站点产物到底是不是新口径（脚本内已含该断言）。
# ============================================================================

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${UPDATE_COMPOSE:-$UPDATE_DIR/../deploy_env/docker-compose.update.yml}"
HOTFIX_COMPOSE="$UPDATE_DIR/docker-compose.frontend-hotfix.yml"
PAYLOAD="$UPDATE_DIR/frontend-report-title-dist.tar.gz"
SUMS_FILE="$UPDATE_DIR/SHA256SUMS"
DIST_DIR="$UPDATE_DIR/dist"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-wharttest-frontend}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:8913/}"

REVERT=0
for arg in "$@"; do
  case "$arg" in
    --revert) REVERT=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "[失败] 未知参数：$arg（可用：--revert / --help）" >&2; exit 1 ;;
  esac
done

fail() { echo "[失败] $*" >&2; exit 1; }

# 报告命名口径判据：前缀字面量之后的窗口内必须出现 repository_name。
# 旧版内联写法紧跟的是 project_name，过不了这条；只查字符串存在则两种都能过。
# 与 05-verify.sh 中的断言保持同一判据，避免两处口径漂移。
check_report_naming() {  # $1 = 主包文件路径，- 表示标准输入
  awk -v n1='代码审查报告_' -v n2='测试分析报告_' -v field='repository_name' '
    { for (kind = 1; kind <= 2; kind++) {
        needle = (kind == 1 ? n1 : n2)
        at = index($0, needle)
        if (at > 0 && index(substr($0, at, 200), field) > 0) found[kind] = 1
      } }
    END { exit (found[1] && found[2]) ? 0 : 1 }' "$1"
}

# 用例审查专用 LLM 入口判据：主包必须同时含接口基址与两个专用文案。
# 区分度说明：仅报告命名的旧产物里，review-llm-config 与「用例审查专用 LLM」都不存在
# （只有「审查模型配置」——那是代码审查页原有的按钮文案），因此本判据能识别出旧产物。
# 判据与 05-verify.sh 的前端层断言逐字一致，避免两处口径漂移。
check_review_llm_entry() {  # $1 = 主包文件路径，- 表示标准输入
  awk -v a='review-llm-config' -v b='审查模型配置' -v c='用例审查专用 LLM' '
    { if (index($0, a) > 0) hitA = 1
      if (index($0, b) > 0) hitB = 1
      if (index($0, c) > 0) hitC = 1 }
    END { exit (hitA && hitB && hitC) ? 0 : 1 }' "$1"
}

main_bundle() {  # $1 = 目录；输出该目录下最大的 assets/*.js（即主包）
  ls -S "$1"/assets/*.js 2>/dev/null | head -1
}

wait_frontend_healthy() {
  local elapsed=0 health=""
  while [ "$elapsed" -lt 180 ]; do
    health="$(docker inspect "$FRONTEND_CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
    case "$health" in
      healthy|running) return 0 ;;
      unhealthy|stopped)
        docker logs --tail 80 "$FRONTEND_CONTAINER" || true
        fail "Frontend 状态异常：$health"
        ;;
    esac
    sleep 3
    elapsed=$((elapsed + 3))
  done
  fail "Frontend 启动超过 180 秒"
}

frontend_mount_sources() {
  docker inspect "$FRONTEND_CONTAINER" --format '{{range .Mounts}}{{println .Source}}{{end}}' 2>/dev/null || true
}

recreate_frontend() {  # $1 = 1 表示叠加本覆盖层，0 表示不叠加
  if [ "$1" = "1" ]; then
    FRONTEND_DIST_DIR="$DIST_DIR" "${compose[@]}" -f "$HOTFIX_COMPOSE" config --quiet
    echo "[应用] 重新创建 Frontend：站点根目录改为只读挂载已构建产物"
    FRONTEND_DIST_DIR="$DIST_DIR" "${compose[@]}" -f "$HOTFIX_COMPOSE" up -d --no-deps --force-recreate frontend
  else
    "${compose[@]}" config --quiet
    echo "[应用] 重新创建 Frontend：不带本覆盖层，恢复镜像内产物"
    "${compose[@]}" up -d --no-deps --force-recreate frontend
  fi
  wait_frontend_healthy
}

[ -f "$BASE_COMPOSE" ] || fail "找不到内网基础 compose：$BASE_COMPOSE"
[ -f "$UPDATE_COMPOSE" ] || fail "找不到升级覆盖 compose：$UPDATE_COMPOSE"
[ -f "$HOTFIX_COMPOSE" ] || fail "找不到热修复覆盖 compose：$HOTFIX_COMPOSE"
compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")

echo "=========================================="
echo " 前端热修复（累计产物）：① 报告命名 ② 用例审查专用 LLM 入口"
echo " 模式：$([ "$REVERT" = 1 ] && echo '回退' || echo '应用')"
echo " 前端容器：$FRONTEND_CONTAINER"
echo " 覆盖层：$HOTFIX_COMPOSE"
echo "=========================================="
echo

current_image="$(docker inspect "$FRONTEND_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo '(未取到)')"
echo "[信息] 当前 Frontend 镜像：$current_image"
echo

# ---------------------------------------------------------------------------
# 回退：不带覆盖层重建，挂载消失，站点回到镜像内产物
# ---------------------------------------------------------------------------
if [ "$REVERT" = 1 ]; then
  recreate_frontend 0

  if frontend_mount_sources | grep -Fxq "$DIST_DIR"; then
    fail "回退后仍检测到 $DIST_DIR 挂载，Frontend 未按预期重建"
  fi
  echo "[通过] Frontend 已不再挂载热修复产物"

  curl -fsS "$FRONTEND_URL" >/dev/null || fail "回退后前端不可访问：$FRONTEND_URL"
  echo "[通过] 前端 HTTP 可访问：$FRONTEND_URL"

  echo
  echo "[完成] 已回退到镜像内前端产物。"
  echo "       注意：若镜像尚未包含这两项改动，站点会退回 ——"
  echo "         ① 报告标题/文件名回到「<报告名>_<平台项目名>」的旧口径；"
  echo "         ② 用例审查页不再出现「审查模型配置」入口。"
  echo "       解包产物保留在 $DIST_DIR（可安全删除）。"
  echo "       复核：bash $UPDATE_DIR/../deploy_env/05-verify.sh（其中报告命名与用例审查入口断言此时应失败，属预期）"
  exit 0
fi

# ---------------------------------------------------------------------------
# 应用：校验产物 -> 解包 -> 预检 -> 挂载重建 -> 后置校验
# ---------------------------------------------------------------------------
[ -f "$PAYLOAD" ] || fail "缺少产物包：$PAYLOAD"
[ -f "$SUMS_FILE" ] || fail "缺少校验和文件：$SUMS_FILE"

echo "[校验] 产物包 SHA256"
if command -v sha256sum >/dev/null 2>&1; then
  ( cd "$UPDATE_DIR" && sha256sum -c "$(basename "$SUMS_FILE")" )
elif command -v shasum >/dev/null 2>&1; then
  ( cd "$UPDATE_DIR" && shasum -a 256 -c "$(basename "$SUMS_FILE")" )
else
  fail "环境里既没有 sha256sum 也没有 shasum，无法校验产物包"
fi

echo
echo "[解包] 展开已构建的前端产物"
rm -rf "$DIST_DIR"
tar -xzf "$PAYLOAD" -C "$UPDATE_DIR"
[ -f "$DIST_DIR/index.html" ] || fail "解包后缺少 $DIST_DIR/index.html，产物包不完整"
bundle="$(main_bundle "$DIST_DIR" || true)"
[ -n "$bundle" ] && [ -f "$bundle" ] || fail "解包后找不到前端主包（$DIST_DIR/assets/*.js）"
bundle_name="$(basename "$bundle")"

# 防御：macOS 打包可能带入 AppleDouble 垃圾文件，它们会被一并挂进站点根目录。
find "$DIST_DIR" \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -exec rm -rf {} + 2>/dev/null || true
chmod -R a+rX "$DIST_DIR"
echo "[通过] 产物已解包：$DIST_DIR（主包 $bundle_name，$(find "$DIST_DIR" -type f | wc -l | tr -d ' ') 个文件）"

echo
echo "[预检] 产物里的报告命名口径（容器动手之前先验，避免白重建一次）"
check_report_naming "$bundle" || fail \
  "产物不是「报告名_代码仓库名」口径：$(basename "$bundle") 的报告标题前缀后没有 repository_name"
echo "[通过] 产物为「<报告名>_<代码仓库名>」口径"

echo
echo "[预检] 产物里的用例审查专用 LLM 入口（仅平台管理员可见）"
check_review_llm_entry "$bundle" || fail \
  "产物不含用例审查专用 LLM 入口：$(basename "$bundle") 缺少 review-llm-config / 审查模型配置 / 用例审查专用 LLM。\
若产物是仅含报告命名的旧包，需重新构建 dist 并重打包（pack-frontend-hotfix.sh --build）"
echo "[通过] 产物含用例审查「审查模型配置」入口"

echo
recreate_frontend 1

echo
echo "[验证] 站点根目录确实来自热修复产物"
if ! frontend_mount_sources | grep -Fxq "$DIST_DIR"; then
  echo "[失败] Frontend 未挂载 $DIST_DIR，实际挂载：" >&2
  frontend_mount_sources >&2
  exit 1
fi
docker exec "$FRONTEND_CONTAINER" test -f /usr/share/nginx/html/index.html \
  || fail "容器内 /usr/share/nginx/html/index.html 不存在，站点根目录挂载异常"
echo "[通过] $DIST_DIR -> /usr/share/nginx/html（只读）"

echo
echo "[验证] 容器内实际产物为「<报告名>_<代码仓库名>」口径（与 05-verify.sh 同判据）"
# 按宿主机解包出来的主包文件名去容器里读，避免在 Nginx 镜像里依赖 ls -S / awk 等工具；
# 文件不存在时 cat 会失败，pipefail 会让整条流水线失败，不会静默通过。
docker exec "$FRONTEND_CONTAINER" cat "/usr/share/nginx/html/assets/$bundle_name" | check_report_naming - \
  || fail "容器内产物未按「报告名_代码仓库名」命名，或站点根目录不是热修复产物（assets/$bundle_name）"
echo "[通过] assets/$bundle_name"

echo
echo "[验证] 容器内实际产物含用例审查专用 LLM 入口（与 05-verify.sh 同判据）"
docker exec "$FRONTEND_CONTAINER" cat "/usr/share/nginx/html/assets/$bundle_name" | check_review_llm_entry - \
  || fail "容器内产物不含用例审查专用 LLM 入口（assets/$bundle_name），站点根目录可能不是热修复产物"
echo "[通过] assets/$bundle_name 含 review-llm-config / 审查模型配置 / 用例审查专用 LLM"

curl -fsS "$FRONTEND_URL" >/dev/null || fail "前端不可访问：$FRONTEND_URL"
echo "[通过] 前端 HTTP 可访问：$FRONTEND_URL"

echo
echo "=========================================="
echo "[完成] 前端两项改动已生效："
echo "       ① 报告导出标题与下载文件名 = 「代码审查报告_<仓库名>」「测试分析报告_<仓库名>」"
echo "       ② 用例审查页出现「审查模型配置」按钮（仅 is_staff 可见，用于配置专用 LLM）"
echo "       验证方式：① 对任一分析任务点「导出报告 → HTML」，看报告标题与下载文件名；"
echo "                 ② 以管理员登录「用例审查」页，点右上「审查模型配置」填写并启用模型。"
echo "       手工复核：bash $UPDATE_DIR/../deploy_env/05-verify.sh"
echo "       回退方式：bash $UPDATE_DIR/25-apply-frontend-report-title-hotfix.sh --revert"
echo
echo "[提醒] 本通道是临时措施：下一次执行 04-deploy.sh（不含本覆盖层）重建 Frontend 时，"
echo "       挂载会自动消失，站点回到镜像内产物。要让修复永久生效，请重建"
echo "       wharttest-250-frontend 镜像（wharttest-image-release 流程）并走 03/04 正常升级。"
echo "=========================================="
