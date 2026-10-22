#!/usr/bin/env bash
set -euo pipefail

# 从 Backend 当前使用的不可变镜像中提取原始 code_analysis/services.py。
# 不启动临时容器，不修改运行中的 Backend，也不访问数据库。

BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
OUTPUT_FILE="${OUTPUT_FILE:-/projects/ai-test-platform/services-image-original.py}"
TEMP_CONTAINER="extract-services-$(date +%s)-$$"

fail() { echo "[失败] $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "未安装 docker"
docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1 || fail "找不到 Backend 容器：$BACKEND_CONTAINER"

image_name="$(docker inspect "$BACKEND_CONTAINER" --format '{{.Config.Image}}')"
[ -n "$image_name" ] || fail "无法读取 Backend 镜像名称"

cleanup() {
  docker rm -f "$TEMP_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[信息] Backend 容器：$BACKEND_CONTAINER"
echo "[信息] Backend 镜像：$image_name"
echo "[创建] 创建不启动的临时容器：$TEMP_CONTAINER"
docker create --name "$TEMP_CONTAINER" "$image_name" >/dev/null

mkdir -p "$(dirname "$OUTPUT_FILE")"
echo "[提取] /app/code_analysis/services.py -> $OUTPUT_FILE"
docker cp "$TEMP_CONTAINER:/app/code_analysis/services.py" "$OUTPUT_FILE"

[ -s "$OUTPUT_FILE" ] || fail "提取结果不存在或为空：$OUTPUT_FILE"
python3 -m py_compile "$OUTPUT_FILE" 2>/dev/null || echo "[提醒] 宿主机 Python 无法完成语法检查，请仍将文件返回分析。"

size_bytes="$(wc -c < "$OUTPUT_FILE" | tr -d ' ')"
sha256="$(sha256sum "$OUTPUT_FILE" | awk '{print $1}')"

echo "[完成] 原始镜像文件已提取"
echo "[文件] $OUTPUT_FILE"
echo "[大小] $size_bytes 字节"
echo "[SHA256] $sha256"

if grep -q 'def _has_direct_target_defect_evidence' "$OUTPUT_FILE"; then
  echo "[警告] 镜像本身已经包含确定缺陷分类修复；请仍将文件返回分析。"
else
  echo "[确认] 文件不包含 v2 热修复标记，应为镜像原始版本。"
fi
