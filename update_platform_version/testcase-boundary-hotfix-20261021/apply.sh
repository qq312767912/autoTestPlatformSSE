#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
source "$ROOT/common.sh"
command -v docker >/dev/null || fail '未找到 docker 命令'
CONTAINER_ARG=''
CHECK_ONLY=false
for argument in "$@"; do
  case "$argument" in --check) CHECK_ONLY=true;; *) [ -z "$CONTAINER_ARG" ] || fail '仅接受一个容器名称'; CONTAINER_ARG="$argument";; esac
done
choose_container "$CONTAINER_ARG"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
BACKUP="$ROOT/backups/$STAMP"
STAGING="/tmp/wharttest-boundary-$STAMP"
mkdir -p "$BACKUP"
docker cp "$CONTAINER:$TARGET" "$BACKUP/original.py"
docker inspect "$CONTAINER" --format '{{json .Mounts}}' > "$BACKUP/mounts.json"
docker inspect "$CONTAINER" --format '{{.Name}} {{.Config.Image}} {{.Image}}' > "$BACKUP/container.txt"
docker exec "$CONTAINER" mkdir -p "$STAGING/payload"
for file in runtime.py; do docker cp "$ROOT/$file" "$CONTAINER:$STAGING/$file" >/dev/null; done
docker cp "$ROOT/payload/changes.json" "$CONTAINER:$STAGING/payload/changes.json" >/dev/null
docker cp "$BACKUP/mounts.json" "$CONTAINER:$STAGING/mounts.json" >/dev/null
# Verify payload checksums before preparing any candidate.
docker cp "$ROOT/SHA256SUMS" "$CONTAINER:$STAGING/SHA256SUMS" >/dev/null
docker exec "$CONTAINER" "$PYTHON" -c 'import hashlib, pathlib, sys; r=pathlib.Path(sys.argv[1]); items=[line.split(None,1) for line in (r/"SHA256SUMS").read_text().splitlines()]; checked=0
for expected,name in items:
 p=r/name.strip()
 if p.is_file():
  assert hashlib.sha256(p.read_bytes()).hexdigest()==expected, name
  checked+=1
assert checked==2, checked
print("[预检] 补丁载荷完整")' "$STAGING"
HOST_TARGET="$(docker exec "$CONTAINER" "$PYTHON" "$STAGING/runtime.py" host-target "$STAGING/mounts.json")"
if [ -n "$HOST_TARGET" ]; then [ -f "$HOST_TARGET" ] && [ -w "$HOST_TARGET" ] || fail "无权写入宿主机挂载源码：$HOST_TARGET"; fi
printf '%s\n' "$CONTAINER" > "$BACKUP/container.name"
printf '%s\n' "$HOST_TARGET" > "$BACKUP/host-target.path"
echo "[预检] 容器：$CONTAINER；备份：$BACKUP"
if ! docker exec -w /app "$CONTAINER" "$PYTHON" "$STAGING/runtime.py" prepare "$STAGING" > "$BACKUP/preflight.log" 2>&1; then
  cat "$BACKUP/preflight.log"
  fail "预检失败，未修改运行代码。诊断资料：$BACKUP"
fi
cat "$BACKUP/preflight.log"
if grep -q '^ALREADY_APPLIED$' "$BACKUP/preflight.log"; then echo '[完成] 补丁已存在且导出核验通过，无需重启'; exit 0; fi
docker cp "$CONTAINER:$STAGING/candidate.py" "$BACKUP/candidate.py" >/dev/null
docker cp "$CONTAINER:$STAGING/metadata.json" "$BACKUP/metadata.json" >/dev/null
if [ "$CHECK_ONLY" = true ]; then echo '[完成] 仅预检，未安装、未重启'; exit 0; fi
# Refuse to overwrite concurrent source edits made after the backup/preflight.
docker exec "$CONTAINER" "$PYTHON" -c 'from pathlib import Path; import hashlib,json,sys; m=json.loads(Path(sys.argv[1]).read_text()); assert hashlib.sha256(Path(m["target"]).read_bytes()).hexdigest()==m["original_sha256"], "运行代码在预检后发生变化，停止安装"' "$STAGING/metadata.json"
MUTATED=false
on_error() {
  local code=$?
  trap - EXIT
  if [ "$code" -ne 0 ] && [ "$MUTATED" = true ]; then
    echo '[回滚] 安装或健康检查失败，恢复原文件' >&2
    if restore_file && docker restart --timeout 60 "$CONTAINER" >/dev/null && health_check; then
      echo '[回滚完成] 原文件与服务均已恢复' >&2
    else
      echo "[回滚异常] 请执行 bash $ROOT/rollback.sh $BACKUP" >&2
    fi
  fi
  exit "$code"
}
trap on_error EXIT
echo '[安装] 将更新报告代码并重启 Backend；请在无执行任务时运行本脚本'
MUTATED=true
if [ -n "$HOST_TARGET" ]; then cat "$BACKUP/candidate.py" > "$HOST_TARGET"; else docker cp "$BACKUP/candidate.py" "$CONTAINER:$TARGET"; fi
# Verify that a bind mount or container-copy actually changed the exact target bytes.
docker exec "$CONTAINER" "$PYTHON" -c 'from pathlib import Path; import hashlib,json,sys; m=json.loads(Path(sys.argv[1]).read_text()); assert hashlib.sha256(Path(m["target"]).read_bytes()).hexdigest()==m["patched_sha256"]; compile(Path(m["target"]).read_text(),m["target"],"exec")' "$STAGING/metadata.json"
docker restart --timeout 60 "$CONTAINER" >/dev/null
health_check
docker exec -w /app "$CONTAINER" "$PYTHON" "$STAGING/runtime.py" smoke "$TARGET" > "$BACKUP/postcheck.log" 2>&1
cat "$BACKUP/postcheck.log"
printf '%s\n' "$BACKUP" > "$ROOT/backups/latest.path"
trap - EXIT
echo '[完成] 边界分析报告补丁已生效；请使用原始测试用例重新发起审查'
echo "[回滚] bash $ROOT/rollback.sh $BACKUP"
