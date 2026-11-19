#!/usr/bin/env bash
# 从版本化补丁文件生成带校验和的离线包，不携带现场、日志和备份。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=worker-runtime-20261119
DEST="${1:-$ROOT/update_platform_version/$NAME.tar.gz}"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME"
while IFS= read -r path; do
 relative="${path#update_platform_version/$NAME/}"
 mkdir -p "$STAGE/$NAME/$(dirname "$relative")"
 cp "$ROOT/$path" "$STAGE/$NAME/$relative"
done < <(git -C "$ROOT" ls-files "update_platform_version/$NAME/")
python3 - "$STAGE/$NAME" <<'PY'
import hashlib,pathlib,sys
root=pathlib.Path(sys.argv[1])
lines=[hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(root).as_posix() for p in sorted(root.rglob('*')) if p.is_file()]
(root/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
PY
tar -czf "$DEST" -C "$STAGE" "$NAME"
echo "$DEST"
