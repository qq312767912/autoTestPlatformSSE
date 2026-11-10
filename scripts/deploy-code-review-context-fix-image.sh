#!/usr/bin/env bash
# 内网专用：仅切换 Backend 到已构建的 contextfix1 镜像；失败保留现场并自动回滚。
set -euo pipefail

mode="${1:---preflight}"
case "$mode" in --preflight|--apply) ;; *) echo '用法：bash deploy-code-review-context-fix-image.sh [--preflight|--apply]' >&2; exit 2;; esac

container='wharttest-backend'
base_compose="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
update_compose="${UPDATE_COMPOSE:-/projects/ai-test-platform/update_platform_version/deploy_env/docker-compose.update.yml}"
old_tag='wharttest-250-backend:update-b6613a39-v2.8-r5-code-review-backend-hostcfg1-arm64'
old_id='sha256:7b982b8131bfd86cd7c71274321fbffc3fa6b060f58c413ad796630375a658fe'
new_tag='wharttest-250-backend:update-b6613a39-v2.8-r5-code-review-contextfix1-arm64'
new_id='sha256:cf2a472a4a09196c155f7c75ac0821b11c538b241b22b25e769143dbe7317040'
old_source='492f820e1495a884fd27d93e8e923be41ce8a91b0e42ef91cd41adee2fa7a07f'

command -v docker >/dev/null || { echo '未找到 Docker' >&2; exit 2; }
command -v python3 >/dev/null || { echo '未找到 Python 3' >&2; exit 2; }
[ -f "$base_compose" ] && [ -f "$update_compose" ] || { echo 'Compose 文件不存在，拒绝部署' >&2; exit 2; }
[ "$(docker image inspect --format '{{.Id}}' "$old_tag")" = "$old_id" ] || { echo '旧镜像 ID 不匹配' >&2; exit 2; }
[ "$(docker image inspect --format '{{.Id}}' "$new_tag")" = "$new_id" ] || { echo '修复镜像 ID 不匹配' >&2; exit 2; }
[ "$(docker inspect --type container --format '{{.Image}}' "$container")" = "$old_id" ] || { echo '当前运行容器不是预期旧镜像' >&2; exit 2; }
[ "$(docker inspect --type container --format '{{.State.Health.Status}}' "$container")" = healthy ] || { echo '当前 Backend 不健康，拒绝部署' >&2; exit 2; }
if ! docker inspect --type container wharttest-crg >/dev/null 2>&1; then
  echo '当前内网没有 wharttest-crg 容器，而磁盘 Compose 要求 CRG；此脚本不适用于现有拓扑，拒绝切换 Backend' >&2
  exit 2
fi
config_files="$(docker inspect --type container --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$container")"
python3 - "$config_files" "$base_compose" "$update_compose" <<'PY'
import os
import sys

actual = [os.path.realpath(p) for p in sys.argv[1].split(',') if p and p != '<no value>']
expected = [os.path.realpath(p) for p in sys.argv[2:]]
if actual != expected:
    raise SystemExit('当前 Backend 使用的 Compose 文件与脚本预期不一致；拒绝覆盖现有挂载或环境配置')
print('运行中 Backend 的 Compose 文件与脚本预期一致')
PY

source_hash="$(docker exec "$container" /opt/venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("/app/code_analysis/services.py", "rb").read()).hexdigest())')"
[ "$source_hash" = "$old_source" ] || { echo "当前运行源码 SHA256 不匹配：$source_hash" >&2; exit 2; }

# 在隔离容器内完成真实模块导入；失败时绝不改动在线服务。
docker run --rm --network none --env DJANGO_SETTINGS_MODULE=wharttest_django.settings \
  --entrypoint /opt/venv/bin/python "$new_tag" -c '
import django
django.setup()
from code_analysis import services
assert callable(services._repository_context_for_prompt)
assert services._repository_context_for_prompt(None)["items"] == []
print("修复镜像模块导入预检通过")
'

record_dir="$(mktemp -d "${TMPDIR:-/tmp}/whart-context-deploy.XXXXXX")"
chmod 700 "$record_dir"
printf '旧镜像=%s\n旧镜像ID=%s\n修复镜像=%s\n修复镜像ID=%s\n' "$old_tag" "$old_id" "$new_tag" "$new_id" > "$record_dir/rollback.txt"
echo "部署记录：$record_dir（可能包含环境配置，请勿原样分享）"
docker inspect "$container" > "$record_dir/before-container.json"

# 以运行中健康 Backend 实际使用的值为准，不依赖可能已丢失的部署包 secret 文件。
# 只在当前 shell 中传给 Compose，不写入新的明文凭据文件，也不打印。
CRG_INTERNAL_TOKEN="$(docker exec "$container" /opt/venv/bin/python -c 'import os; print(os.environ.get("CODE_REVIEW_GRAPH_TOKEN", ""))')"
[ -n "$CRG_INTERNAL_TOKEN" ] || { echo '运行中 Backend 未配置 CRG Token，无法安全复刻其配置' >&2; exit 2; }
export CRG_INTERNAL_TOKEN
compose=(docker compose -p offline-images -f "$base_compose" -f "$update_compose")
overlay="$record_dir/docker-compose.contextfix.yml"
printf 'services:\n  backend:\n    image: %s\n    pull_policy: never\n' "$new_tag" > "$overlay"
fixed_compose=("${compose[@]}" -f "$overlay")

# 拒绝意外覆盖服务参数或把源码文件挂载覆盖新镜像。
"${compose[@]}" config --format json > "$record_dir/original-compose.json"
"${fixed_compose[@]}" config --format json > "$record_dir/fixed-compose.json"
python3 - "$record_dir/original-compose.json" "$record_dir/fixed-compose.json" "$old_tag" "$new_tag" <<'PY'
import json
import sys

old, new = [json.load(open(p, encoding='utf-8')) for p in sys.argv[1:3]]
old_backend = old['services']['backend']
new_backend = new['services']['backend']
assert old_backend['image'] == sys.argv[3], old_backend['image']
assert new_backend['image'] == sys.argv[4], new_backend['image']
old_backend.pop('image')
new_backend.pop('image')
old_backend.pop('pull_policy', None)
new_backend.pop('pull_policy', None)
assert old_backend == new_backend, '修复镜像以外的 Backend Compose 配置发生变化'
for volume in new_backend.get('volumes', []):
    target = volume.get('target', '') if isinstance(volume, dict) else str(volume)
    assert not target.startswith('/app/code_analysis'), '源码目录被卷挂载覆盖'
print('Compose 差异预检通过：只变更 Backend 镜像')
PY

if [ "$mode" = --preflight ]; then
  echo '预检完成：原 Backend 仍在运行且健康，未切换服务。确认维护窗口后才可用 --apply 部署。'
  exit 0
fi

wait_healthy() {
  local phase="$1" limit="$2" elapsed=0 status
  while [ "$elapsed" -lt "$limit" ]; do
    status="$(docker inspect --type container --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo missing)"
    if [ "$status" = healthy ]; then
      echo "$phase：Backend healthy（约 ${elapsed} 秒）"
      return 0
    fi
    if [ $((elapsed % 30)) -eq 0 ]; then echo "$phase：$status（已等待 ${elapsed} 秒）"; fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  echo "$phase：等待 ${limit} 秒仍未健康" >&2
  return 1
}

capture_failure() {
  docker inspect "$container" > "$record_dir/failed-container.json" 2>&1 || true
  docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}开始={{.Start}} 结束={{.End}} 退出码={{.ExitCode}} 输出={{.Output}}{{println}}{{end}}{{end}}' "$container" > "$record_dir/failed-health.txt" 2>&1 || true
  docker logs --tail 500 "$container" > "$record_dir/failed-logs.txt" 2>&1 || true
  echo "失败现场已保存到 $record_dir；文件可能包含凭据，请先脱敏再分享" >&2
  echo '健康检查最近结果（仅退出码）：' >&2
  docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{.ExitCode}} {{end}}{{end}}' "$container" >&2 || true
}

changed=0
rollback() {
  local original_status=$?
  trap - ERR INT TERM
  if [ "$changed" -eq 1 ]; then
    capture_failure
    echo '开始只回滚 Backend 镜像；保留修复镜像和失败现场' >&2
    if "${compose[@]}" up -d --pull never --no-deps --force-recreate backend > "$record_dir/rollback-output.txt" 2>&1; then
      if wait_healthy '回滚验证' 180 && [ "$(docker inspect --type container --format '{{.Image}}' "$container")" = "$old_id" ]; then
        echo '原 Backend 镜像已恢复且健康' >&2
      else
        echo "紧急告警：回滚后 3 分钟内未确认健康；请检查 $record_dir" >&2
      fi
    else
      echo "紧急告警：回滚命令失败；请检查 $record_dir/rollback-output.txt" >&2
    fi
  fi
  exit "${original_status:-1}"
}
trap rollback ERR INT TERM

changed=1
"${fixed_compose[@]}" up -d --pull never --no-deps --force-recreate backend
wait_healthy '修复验证' 300
[ "$(docker inspect --type container --format '{{.Image}}' "$container")" = "$new_id" ] || { echo '容器镜像 ID 不是修复镜像' >&2; false; }
docker exec "$container" /opt/venv/bin/python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from code_analysis import services
assert callable(services._repository_context_for_prompt)
assert services._repository_context_for_prompt(None)["items"] == []
print("运行中 Backend 函数导入验证通过")
'
changed=0
trap - ERR INT TERM
echo '仅 Backend 定点修复已部署，容器健康且函数导入通过。请用原代码审查任务验证综合 AI 覆盖与风险结果。'
echo "部署记录：$record_dir"
