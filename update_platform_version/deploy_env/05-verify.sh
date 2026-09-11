#!/usr/bin/env bash
set -euo pipefail

VERIFY_OCR_LIVE="${VERIFY_OCR_LIVE:-1}"
TEST_HOSTNAME="${TEST_HOSTNAME:-www.test.sse.com.cn}"

expected=(
  'wharttest-backend|wharttest-250-backend:update-430787c8-r1-arm64'
  'wharttest-frontend|wharttest-250-frontend:update-ecff56e4-r1-arm64'
  'wharttest-vision-mcp|wharttest-250-vision-mcp:update-01339484-arm64-r2'
  'wharttest-mcp|wharttest-250-mcp-alpine:latest'
  'wharttest-qdrant|qdrant-kylin-arm64:v1.16.0-page64k'
  'wharttest-playwright-mcp|playwright-mcp-alpine:latest'
)

for entry in "${expected[@]}"; do
  IFS='|' read -r container image <<< "$entry"
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  [ "$actual" = "$image" ] || { echo "[失败] $container 应为 $image，实际为 $actual" >&2; exit 1; }
  state="$(docker inspect "$container" --format '{{.State.Status}}')"
  [ "$state" = "running" ] || { echo "[失败] $container 状态为 $state" >&2; exit 1; }
  echo "[通过] $container -> $actual"
done

curl -fsS http://127.0.0.1:8912/admin/login/ >/dev/null
curl -fsS http://127.0.0.1:8913/ >/dev/null
echo "[通过] Backend 和 Frontend HTTP 检查"

docker exec wharttest-backend /opt/venv/bin/python /app/manage.py migrate --check
echo "[通过] 数据库迁移状态"

docker exec wharttest-backend sh -c \
  'command -v ocr >/dev/null && ocr --version && grep -q -- "--concurrency=8" /app/supervisord.conf'
echo "[通过] Backend OpenCodeReview 与 Celery 并发 8"

celery_ping="$(docker exec wharttest-backend timeout 20 celery -A wharttest_django inspect ping --timeout=10)"
echo "$celery_ping" | grep -q 'pong' || { echo "[失败] Celery Worker 未响应 ping" >&2; exit 1; }
registered="$(docker exec wharttest-backend timeout 30 celery -A wharttest_django inspect registered --timeout=15)"
echo "$registered" | grep -q 'code_analysis.tasks.run_code_analysis' || { echo "[失败] Celery 未注册代码审查任务" >&2; exit 1; }
echo "$registered" | grep -q 'testcases.review_tasks.execute_testcase_review' || { echo "[失败] Celery 未注册用例审查任务" >&2; exit 1; }
echo "[通过] Celery Worker 在线，代码审查和用例审查任务已注册"

media_source='/projects/ai-test-platform/offline-images/data/media'
frontend_mounts="$(docker inspect wharttest-frontend --format '{{range .Mounts}}{{println .Source "|" .Destination "|" .RW}}{{end}}')"
echo "$frontend_mounts" | grep -F -- "$media_source | /app/data/media | false" >/dev/null || {
  echo "[失败] Frontend 未只读挂载 Backend 的共享媒体目录，请执行 15-fix-artifact-download-volume.sh" >&2
  exit 1
}
docker exec wharttest-frontend test -d /app/data/media
probe_name="deploy-verify-$$.txt"
docker exec wharttest-backend sh -c "mkdir -p /app/data/media/healthchecks && touch /app/data/media/healthchecks/$probe_name"
cleanup_probe() { docker exec wharttest-backend rm -f "/app/data/media/healthchecks/$probe_name" >/dev/null 2>&1 || true; }
trap cleanup_probe EXIT
curl -fsS "http://127.0.0.1:8913/media/healthchecks/$probe_name" >/dev/null
cleanup_probe
trap - EXIT
echo "[通过] Skill 交付物共享卷与 Frontend 实际下载链路"

docker exec wharttest-vision-mcp python -c \
  'import onnxruntime; from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print("Vision OCR runtime OK")'
echo "[通过] Vision MCP OCR 运行库可在当前宿主机加载"

for index in 01 02 03; do
  container="wharttest-actuator-$index"
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  [ "$actual" = 'wharttest-250-actuator:update-178fb3ed-arm64-r4' ] || { echo "[失败] $container 镜像错误：$actual" >&2; exit 1; }
  status="$(docker inspect "$container" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}')"
  case "$status" in
    running/healthy|running/no-healthcheck) echo "[通过] $container -> $status" ;;
    *) docker logs --tail 100 "$container"; echo "[失败] $container -> $status" >&2; exit 1 ;;
  esac
  docker exec -i "$container" python - "$TEST_HOSTNAME" <<'PY'
import socket
import sys
import urllib.error
import urllib.request
hostname = sys.argv[1]
addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname, 80, type=socket.SOCK_STREAM)})
if not addresses:
    raise SystemExit(f"无法解析 {hostname}")
try:
    with urllib.request.urlopen(f"http://{hostname}", timeout=15) as response:
        status = response.status
except urllib.error.HTTPError as exc:
    status = exc.code
if status >= 500:
    raise SystemExit(f"{hostname} 返回 HTTP {status}")
print(f"{hostname} -> {','.join(addresses)} HTTP {status}")
PY
done

online_count=0
for _attempt in $(seq 1 12); do
  actuator_payload="$(curl -fsS http://127.0.0.1:8912/api/ui-automation/actuators/list_actuators/ || true)"
  online_count="$(printf '%s' "$actuator_payload" | docker exec -i wharttest-backend /opt/venv/bin/python -c \
    'import json,sys; payload=json.load(sys.stdin); values=[]
while isinstance(payload,dict):
 values.append(payload.get("count")); payload=payload.get("data")
print(next((int(value) for value in values if value is not None),0))' 2>/dev/null || echo 0)"
  [ "$online_count" -ge 3 ] && break
  sleep 5
done
[ "$online_count" -ge 3 ] || { echo "[失败] Backend 仅识别到 $online_count 个在线执行器，期望至少 3 个" >&2; exit 1; }
echo "[通过] Backend WebSocket 注册表识别到 $online_count 个在线执行器"

if [ "$VERIFY_OCR_LIVE" = "1" ]; then
  echo "[检查] 使用当前激活 LLM 执行最小 OpenCodeReview 调用（最长 4 分钟）"
  docker exec -i wharttest-backend timeout 240 /opt/venv/bin/python - <<'PY'
import json
import os
import subprocess
import tempfile
from pathlib import Path
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from langgraph_integration.models import LLMConfig
config = LLMConfig.objects.filter(is_active=True).first()
if not config:
    raise SystemExit("没有已激活的 LLM 配置")
env = os.environ.copy()
env.update({"OCR_LLM_URL": config.api_url, "OCR_LLM_TOKEN": config.api_key, "OCR_LLM_MODEL": config.name, "OCR_LLM_PROTOCOL": "openai"})
with tempfile.TemporaryDirectory(prefix="ocr-verify-") as directory:
    root = Path(directory)
    def git(*args): subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    git("init"); git("config", "user.email", "verify@wharttest.local"); git("config", "user.name", "WHartTest Verify")
    source = root / "sample.py"
    source.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    git("add", "sample.py"); git("commit", "-m", "base")
    source.write_text("def divide(a, b):\n    return 0 if b == 0 else a / b\n", encoding="utf-8")
    git("add", "sample.py"); git("commit", "-m", "head")
    output = root / "result.json"
    result = subprocess.run(
        ["ocr", "review", "--from", "HEAD~1", "--to", "HEAD", "--format", "json", "--audience", "agent",
         "--concurrency", "1", "--timeout", "2", "--output", str(output)],
        cwd=root, env=env, capture_output=True, text=True, timeout=220,
    )
    text = output.read_text(encoding="utf-8") if output.exists() else result.stdout
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SystemExit("OpenCodeReview 未返回 JSON：" + (result.stderr or text)[-500:])
    payload = json.loads(text[start:end + 1])
    accepted = {"success", "complete", "completed", "partial", "partial_success", "completed_with_warnings"}
    if payload.get("status") not in accepted:
        raise SystemExit("OpenCodeReview 状态异常：" + str(payload.get("status") or payload.get("message")))
    print("OpenCodeReview 最小真实调用成功，状态=" + str(payload.get("status")))
PY
  echo "[通过] OpenCodeReview 到当前 LLM 的真实调用链路"
else
  echo "[跳过] OpenCodeReview 真实模型调用（VERIFY_OCR_LIVE=$VERIFY_OCR_LIVE）"
fi

echo "[全部通过] 容器、HTTP、迁移、Celery、OpenCodeReview、文件下载、Vision OCR、执行器注册与域名访问"
docker ps --filter 'name=wharttest' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
