#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# 【联网构建机专用】打包前端热修复产物。
# 内网（离线）不执行本脚本，只需执行 25-apply-frontend-report-title-hotfix.sh。
# ============================================================================
# 用法：
#   bash pack-frontend-hotfix.sh              # 只重新打包现有 dist
#   bash pack-frontend-hotfix.sh --build      # 先构建 dist 再打包
#
# 说明：
#   * 构建命令必须与前端镜像 Dockerfile 一致：`vite build --mode production`。
#     不要用 npm run build —— 那是 `vue-tsc -b && vite build`，类型检查会挡住构建。
#   * 用 Python 生成 tar，避免 macOS 把 AppleDouble（._*）资源叉文件带进产物包；
#     这些垃圾文件会被一并挂进站点根目录。
# ============================================================================

HOTFIX_DIR="$(cd "$(dirname "$0")" && pwd)"
VUE_DIR="$(cd "$HOTFIX_DIR/../../WHartTest_Vue" && pwd)"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
PAYLOAD="$HOTFIX_DIR/frontend-report-title-dist.tar.gz"

fail() { echo "[失败] $*" >&2; exit 1; }

if [ "${1:-}" = "--build" ]; then
  [ -n "$NODE_BIN" ] || fail "找不到 node，请用 NODE_BIN=/path/to/node 指定"
  echo "[构建] vite build --mode production（与前端镜像 Dockerfile 同一命令）"
  rm -rf "$VUE_DIR/dist"
  ( cd "$VUE_DIR" && "$NODE_BIN" node_modules/vite/bin/vite.js build --mode production )
else
  echo "[跳过] 未指定 --build，直接使用现有 $VUE_DIR/dist"
fi

[ -f "$VUE_DIR/dist/index.html" ] || fail "缺少 $VUE_DIR/dist/index.html，请先加 --build 构建"
[ -n "$(ls -S "$VUE_DIR"/dist/assets/*.js 2>/dev/null | head -1)" ] || fail "dist 里没有 assets/*.js"

chmod -R u+w "$VUE_DIR/dist"
find "$VUE_DIR/dist" \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -exec rm -rf {} + 2>/dev/null || true

echo "[打包] $PAYLOAD"
PY_BIN="${PY_BIN:-$(command -v python3 || true)}"
[ -n "$PY_BIN" ] || fail "找不到 python3，请用 PY_BIN=/path/to/python3 指定"
"$PY_BIN" - "$VUE_DIR/dist" "$HOTFIX_DIR" <<'PY'
import gzip, hashlib, io, sys, tarfile
from pathlib import Path

dist, hotfix = Path(sys.argv[1]), Path(sys.argv[2])
out = hotfix / "frontend-report-title-dist.tar.gz"

# 逐文件写入：路径固定为 dist/...，属主与 mtime 归一化，便于比对与复现。
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w") as tar:
    def add(path: Path, arcname: str) -> None:
        info = tar.gettarinfo(str(path), arcname=arcname)
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = 0
        info.mode = 0o755 if path.is_dir() else 0o644
        if path.is_file():
            with path.open("rb") as handle:
                tar.addfile(info, handle)
        else:
            tar.addfile(info)

    add(dist, "dist")
    for path in sorted(dist.rglob("*")):
        add(path, "dist/" + path.relative_to(dist).as_posix())

with gzip.GzipFile(filename="", mode="wb", fileobj=out.open("wb"), mtime=0) as gz:
    gz.write(raw.getvalue())

digest = hashlib.sha256(out.read_bytes()).hexdigest()
sums = hotfix / "SHA256SUMS"
sums.write_text(f"{digest}  {out.name}\n", encoding="utf-8")
files = sum(1 for p in dist.rglob("*") if p.is_file())
print(f"  产物包 {out.name}：{out.stat().st_size} 字节 / {files} 个文件")
print(f"  SHA256 {digest}")
print(f"  校验和已写入 {sums.name}")
PY
