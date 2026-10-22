#!/usr/bin/env bash
set -euo pipefail

# 代码审查“确定缺陷”分类热修复。
# 仅替换 Backend 内的 /app/code_analysis/services.py，不修改数据库或其他容器。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PAYLOAD="$PACKAGE_ROOT/WHartTest_Django/code_analysis/services.py"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BACKUP_ROOT="${BACKUP_ROOT:-$SCRIPT_DIR/backups/code-review-confirmed-defect}"
EXPECTED_SHA256="c40c0916de5dc872445612322de9d3d16de8eb7e6dc81902da19788e42e31698"
TARGET_PATH="/app/code_analysis/services.py"

fail() { echo "[失败] $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "未安装 docker"
[ -f "$PAYLOAD" ] || fail "缺少热修复文件：$PAYLOAD"
docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1 || fail "找不到 Backend 容器：$BACKEND_CONTAINER"
[ "$(docker inspect "$BACKEND_CONTAINER" --format '{{.State.Running}}')" = "true" ] || fail "Backend 容器未运行"

actual_sha256="$(sha256sum "$PAYLOAD" | awk '{print $1}')"
[ "$actual_sha256" = "$EXPECTED_SHA256" ] || fail "热修复文件 SHA256 不匹配：$actual_sha256"

grep -q 'def _has_direct_target_defect_evidence' "$PAYLOAD" || fail "热修复文件缺少直接证据兜底"
grep -q '目标 Commit 反证核验未返回有效结论' "$PAYLOAD" || fail "热修复文件缺少核验失败降级逻辑"
grep -q '快速模式未执行 AI 反证审查' "$PAYLOAD" || fail "热修复文件缺少快速模式旁路修复"
grep -q '缺少直接源码证据的 AI 确定缺陷已降为待确认' "$PAYLOAD" || fail "热修复文件缺少模型配置失败旁路修复"

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$BACKUP_ROOT/$timestamp"
mkdir -p "$backup_dir"
docker cp "$BACKEND_CONTAINER:$TARGET_PATH" "$backup_dir/services.py"

mount_source="$(docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis/services.py"}}{{.Source}}{{end}}{{end}}')"
mode="container"
target="$TARGET_PATH"

restore_on_error() {
  status=$?
  if [ "$status" -eq 0 ]; then
    return
  fi
  echo "[回滚] 应用失败，正在恢复原文件..." >&2
  if [ "$mode" = "bind" ]; then
    cp "$backup_dir/services.py" "$target"
  else
    docker cp "$backup_dir/services.py" "$BACKEND_CONTAINER:$TARGET_PATH" >/dev/null 2>&1 || true
  fi
  docker restart "$BACKEND_CONTAINER" >/dev/null 2>&1 || true
  exit "$status"
}
trap restore_on_error EXIT

echo "[提醒] 重启 Backend 进程会中断正在执行的代码审查或其他后台任务。"
if [ -n "$mount_source" ]; then
  mode="bind"
  target="$mount_source"
  [ -f "$target" ] || fail "容器使用只读挂载，但宿主机源文件不存在：$target"
  echo "[应用] 检测到代码文件挂载，更新宿主机文件：$target"
  cp "$PAYLOAD" "$target"
else
  echo "[应用] 更新容器内文件：$BACKEND_CONTAINER:$TARGET_PATH"
  docker cp "$PAYLOAD" "$BACKEND_CONTAINER:$TARGET_PATH"
fi

docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -m py_compile "$TARGET_PATH"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -c '
from pathlib import Path
source = Path("/app/code_analysis/services.py").read_text(encoding="utf-8")
assert "def _has_direct_target_defect_evidence" in source
assert "目标 Commit 反证核验未返回有效结论" in source
assert "快速模式未执行 AI 反证审查" in source
assert "confirmed 必须同时指出目标版本仍然错误的具体代码" in source
print("confirmed defect verification hotfix content OK")
'

echo "[重启] Backend 容器（容器可写层和绑定挂载均会保留）"
docker restart "$BACKEND_CONTAINER" >/dev/null

elapsed=0
while [ "$elapsed" -lt 300 ]; do
  health="$(docker inspect "$BACKEND_CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
  case "$health" in
    healthy|running) break ;;
    unhealthy|stopped)
      docker logs --tail 150 "$BACKEND_CONTAINER" 2>&1 || true
      fail "Backend 状态异常：$health"
      ;;
  esac
  sleep 3
  elapsed=$((elapsed + 3))
done
[ "$elapsed" -lt 300 ] || fail "Backend 未在 300 秒内恢复运行"

docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -m py_compile "$TARGET_PATH"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python -c '
from pathlib import Path
source = Path("/app/code_analysis/services.py").read_text(encoding="utf-8")
assert "def _has_direct_target_defect_evidence" in source
assert "目标 Commit 反证核验未返回有效结论" in source
print("confirmed defect verification hotfix survived backend restart")
'

printf 'MODE=%s\nBACKUP=%s\nTARGET=%s\nCONTAINER=%s\n' \
  "$mode" "$backup_dir/services.py" "$target" "$BACKEND_CONTAINER" \
  > "$BACKUP_ROOT/latest.env"

trap - EXIT
echo "[完成] 代码审查确定缺陷分类热修复已生效。"
echo "[备份] $backup_dir/services.py"
echo "[回滚] bash $SCRIPT_DIR/rollback-code-review-confirmed-defect-hotfix.sh"
