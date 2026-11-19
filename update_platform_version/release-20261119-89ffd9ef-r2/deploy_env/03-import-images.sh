#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
(cd "$HERE" && sha256sum -c SHA256SUMS)
for component in backend frontend semgrep crg actuator; do
 archive="$IMAGE_DIR/$component-$REV-arm64.tar.gz"
 shopt -s nullglob
 parts=("$archive".part*)
 if [[ ${#parts[@]} -gt 0 ]]; then cat "${parts[@]}" | gzip -dc | docker load
 elif [[ -f "$archive" ]]; then gzip -dc "$archive" | docker load
 else echo "缺少镜像：$archive.part*";exit 1;fi
 image="wharttest-250-$component:update-$REV-arm64"
 [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" == linux/arm64 ]]
 [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$REV" ]]
done
# 必须在麒麟目标内核上实际执行，而非只验证镜像架构。
docker run --rm --entrypoint sh "wharttest-250-backend:update-$REV-arm64" -ec 'cat /etc/alpine-release; ls /lib/ld-musl-aarch64.so.1; python -V; node --version; command -v ocr; ocr --version; npm ls -g --depth=0 @alibaba-group/open-code-review; cd /app/ui_automation/recorder; node -e "require.resolve(\"playwright\")"; chromium-browser --version'
docker run --rm --entrypoint sh "wharttest-250-semgrep:update-$REV-arm64" -ec 'cat /etc/alpine-release; ls /lib/ld-musl-aarch64.so.1; semgrep --version'
docker run --rm --entrypoint sh "wharttest-250-actuator:update-$REV-arm64" -ec 'cat /etc/alpine-release; /usr/local/bin/python3.12 -V; /usr/bin/node --version; /usr/bin/chromium-browser --version'
docker run --rm --shm-size=512m --entrypoint node -v "$HERE/smoke_browser.js:/tmp/smoke_browser.js:ro" "wharttest-250-backend:update-$REV-arm64" /tmp/smoke_browser.js
docker run --rm --shm-size=512m --entrypoint /usr/local/bin/python3.12 -v "$HERE/smoke_actuator_browser.py:/tmp/smoke_actuator_browser.py:ro" "wharttest-250-actuator:update-$REV-arm64" /tmp/smoke_actuator_browser.py
docker run --rm --entrypoint python -v "$HERE/smoke_semgrep.py:/tmp/smoke_semgrep.py:ro" "wharttest-250-semgrep:update-$REV-arm64" /tmp/smoke_semgrep.py
docker run --rm --entrypoint python "wharttest-250-crg:update-$REV-arm64" -c 'from tree_sitter_language_pack import get_parser; tree=get_parser("python").parse(b"def probe():\n    return 1\n"); assert not tree.root_node.has_error; print("CRG native parser passed")'
printf '%s\n' "$REV" > "$HERE/.images-validated"
echo '导入及目标机CLI预检通过；接着执行02-backup.sh，再执行04-deploy.sh。'
