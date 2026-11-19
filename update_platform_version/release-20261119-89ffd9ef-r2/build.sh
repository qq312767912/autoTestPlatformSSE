#!/usr/bin/env bash
# 同版本完整源码构建；不以本地运行镜像为 Backend 基础层。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
REV=20261119-89ffd9ef-r2
cd "$ROOT"
python3 - "$HERE/deploy_env/source-manifest.json" "$ROOT" <<'PYVERIFY'
import hashlib,json,pathlib,sys
manifest=json.loads(pathlib.Path(sys.argv[1]).read_text());root=pathlib.Path(sys.argv[2])
for name,digest in manifest['files'].items():
 p=root/name
 if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
  raise SystemExit('源码与本次发布清单不符，请生成新版本：'+name)
PYVERIFY
mkdir -p "$HERE/build-logs"
components=("$@")
[[ ${#components[@]} -gt 0 ]] || components=(backend frontend semgrep crg actuator)
for component in "${components[@]}"; do
 args=(--platform linux/arm64 --progress plain --load)
 case "$component" in
  backend) context=WHartTest_Django; file=WHartTest_Django/Dockerfile.alpine; args+=(--build-arg ALPINE_MIRROR=https://mirrors.aliyun.com/alpine --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple --build-arg NPM_REGISTRY=https://registry.npmmirror.com --build-arg OCI_REVISION="$REV") ;;
  frontend) context=WHartTest_Vue; file=WHartTest_Vue/Dockerfile; args+=(--build-arg NPM_REGISTRY=https://registry.npmjs.org --build-arg OCI_REVISION="$REV") ;;
  semgrep) context=WHartTest_Semgrep; file=WHartTest_Semgrep/Dockerfile; args+=(--build-arg ALPINE_MIRROR=https://mirrors.aliyun.com/alpine --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple --build-arg OCI_REVISION="$REV") ;;
  crg) context=WHartTest_CRG; file=WHartTest_CRG/Dockerfile; args+=(--build-arg ALPINE_MIRROR=https://mirrors.aliyun.com/alpine --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple --build-arg OCI_REVISION="$REV") ;;
  actuator) context=WHartTest_Actuator; file=WHartTest_Actuator/Dockerfile.alpine-arm64; args+=(--build-arg PYTHON_BASE_IMAGE=python:3.12-alpine --build-arg ALPINE_MIRROR=https://mirrors.aliyun.com/alpine --build-arg BUILD_REVISION="$REV") ;;
  *) echo "未知组件：$component";exit 1 ;;
 esac
 docker buildx build "${args[@]}" -f "$file" -t "wharttest-250-$component:update-$REV-arm64" "$context" 2>&1 | tee "$HERE/build-logs/$component-rebuild.log"
done
