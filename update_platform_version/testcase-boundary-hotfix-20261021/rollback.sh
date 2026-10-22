#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
source "$ROOT/common.sh"
BACKUP="${1:-}"
if [ -z "$BACKUP" ]; then [ -f "$ROOT/backups/latest.path" ] || fail '请传入备份目录'; BACKUP="$(cat "$ROOT/backups/latest.path")"; fi
[ -f "$BACKUP/original.py" ] && [ -f "$BACKUP/metadata.json" ] || fail '备份目录缺少原文件或回滚元数据'
choose_container "$(cat "$BACKUP/container.name")"
HOST_TARGET="$(cat "$BACKUP/host-target.path")"
STAGING="/tmp/wharttest-boundary-rollback-$$"
docker exec "$CONTAINER" mkdir -p "$STAGING"
docker cp "$BACKUP/original.py" "$CONTAINER:$STAGING/original.py" >/dev/null
docker cp "$BACKUP/metadata.json" "$CONTAINER:$STAGING/metadata.json" >/dev/null
docker exec "$CONTAINER" "$PYTHON" -c 'import hashlib,json,sys; from pathlib import Path; r=Path(sys.argv[1]); m=json.loads((r/"metadata.json").read_text()); original=(r/"original.py").read_bytes(); assert hashlib.sha256(original).hexdigest()==m["original_sha256"], "备份校验失败"; current=hashlib.sha256(Path(m["target"]).read_bytes()).hexdigest(); assert current in (m["original_sha256"],m["patched_sha256"]), "当前源码存在后续改动，停止回滚以避免覆盖"; compile(original,m["target"],"exec")' "$STAGING"
restore_file
docker restart --timeout 60 "$CONTAINER" >/dev/null
health_check
echo '[完成] 已恢复安装前代码，报告生成逻辑已回滚'
