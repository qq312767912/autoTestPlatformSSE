#!/usr/bin/env bash
set -euo pipefail

BACKEND_IMAGE='wharttest-250-backend:update-ac65a6fb-v2.8-r3-kombu562-arm64'
FRONTEND_IMAGE='wharttest-250-frontend:update-ac65a6fb-v2.8-r1-arm64'
ACTUATOR_IMAGE='wharttest-250-actuator:update-ac65a6fb-v2.8-r3-auth-failfast-arm64'
REVISION='ac65a6fb-v2.8-r3-kombu562'

fail() { echo "[失败] $*" >&2; exit 1; }
ok() { echo "[通过] $*"; }

verify_container() {
  local container="$1" expected_image="$2"
  docker inspect "$container" >/dev/null 2>&1 || fail "缺少容器：$container"
  local actual state
  actual="$(docker inspect "$container" --format '{{.Config.Image}}')"
  state="$(docker inspect "$container" --format '{{.State.Status}}')"
  [ "$actual" = "$expected_image" ] || fail "$container 镜像错误：$actual"
  [ "$state" = running ] || fail "$container 状态为 $state"
  ok "$container -> $actual"
}

verify_container wharttest-backend "$BACKEND_IMAGE"
verify_container wharttest-frontend "$FRONTEND_IMAGE"

for index in 01 02 03; do
  verify_container "wharttest-actuator-$index" "$ACTUATOR_IMAGE"
done

backend_revision="$(docker image inspect "$BACKEND_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
[ "$backend_revision" = "$REVISION" ] || fail "Backend revision=$backend_revision，期望 $REVISION"

docker exec wharttest-backend /bin/sh -c '
  set -e
  grep -q "Alpine Linux" /etc/os-release
  ldd --version 2>&1 | grep -qi musl
  command -v ocr >/dev/null
  ocr --version
  npm list -g --depth=0 @alibaba-group/open-code-review >/dev/null
  test -x /usr/bin/chromium-browser
  cd /app/ui_automation/recorder
  node -e "require.resolve(\"playwright\")"
  test -f /app/testcases/review_service.py
  test -f /app/bundled_skills/test-case-clarity-review/SKILL.md
  grep -q -- "--pool=solo --concurrency=1" /app/supervisord.conf
  grep -q -- "-b redis://redis:6379/0 worker" /app/supervisord.conf
  python -c "import celery,kombu,redis; assert (celery.__version__,kombu.__version__,redis.__version__) == (\"5.4.0\",\"5.6.2\",\"5.2.0\")"
'
ok 'Backend Alpine/musl、OpenCodeReview/ocr CLI、录制器 Chromium/Playwright 与 Celery solo worker'

docker exec wharttest-backend getent hosts www.test.sse.com.cn >/dev/null
docker exec wharttest-backend /opt/venv/bin/python -c '
import urllib.request
r = urllib.request.urlopen("http://www.test.sse.com.cn", timeout=15)
print("Backend recorder target HTTP", r.status, r.url)
'
ok 'Backend 录制浏览器目标域名与 HTTP 连通性'

docker exec wharttest-backend /opt/venv/bin/python /app/manage.py migrate --check
ok '数据库迁移状态'

docker exec wharttest-backend /bin/sh -c '
  test -f /app/test_host_config/models.py
  test -s /run/secrets/test_host_sync_token
'
ok '测试域名配置后端模块与 Agent Token'

systemctl is-enabled --quiet wharttest-host-sync.timer || fail 'wharttest-host-sync.timer 未启用'
systemctl is-active --quiet wharttest-host-sync.timer || fail 'wharttest-host-sync.timer 未运行'
systemctl start wharttest-host-sync.service || {
  journalctl -u wharttest-host-sync.service -n 80 --no-pager || true
  fail '宿主机域名同步执行失败'
}
ok '宿主机域名同步服务'

docker exec wharttest-backend /opt/venv/bin/python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
from wharttest_django.celery import app
expected = "redis://redis:6379/0"
assert app.conf.broker_url == expected
assert app.conf.broker_read_url == expected
assert app.conf.broker_write_url == expected
'
ok 'Celery Redis broker/read/write URL'

curl -fsS http://127.0.0.1:8912/api/auth/login-key/ >/dev/null
curl -fsS http://127.0.0.1:8913/ >/dev/null
ok 'Backend/Frontend HTTP'

for index in 01 02 03; do
  container="wharttest-actuator-$index"
  docker exec "$container" /bin/sh -c '
    grep -q "Alpine Linux" /etc/os-release
    ldd --version 2>&1 | grep -qi musl
    test -x /usr/bin/chromium-browser
    python -c "import playwright; import websockets; import mcp"
    grep -q "执行器无法获取用例数据" /app/consumer.py
    test "$VISION_MCP_URL" = "http://vision-mcp:8010/mcp"
    test "$VISION_MCP_SHARED_DIR" = "/app/data/vision-mcp-captcha"
  '
done
ok '三个执行器均为 Alpine/musl，Chromium/Playwright/Vision MCP 客户端可用'

echo '升级验证完成。'
