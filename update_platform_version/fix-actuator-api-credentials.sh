#!/usr/bin/env bash
# 修复执行器 API 账号密码失效导致的 UI 任务卡住。
set -euo pipefail

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
DEPLOY_DIR="${PLATFORM_DIR}/update_platform_version/deploy_env"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
OVERRIDE_COMPOSE="${DEPLOY_DIR}/docker-compose.update.yml"
SECRET_DIR="${DEPLOY_DIR}/secrets"
SECRET_FILE="${SECRET_DIR}/actuator_api_password"
ACTUATOR_IMAGE="${ACTUATOR_IMAGE:-wharttest-250-actuator:update-ac65a6fb-v2.8-r3-auth-failfast-arm64}"

fail() { echo "[失败] $*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail "找不到 docker"
docker inspect wharttest-backend >/dev/null 2>&1 || fail "找不到 wharttest-backend"
docker image inspect "$ACTUATOR_IMAGE" >/dev/null 2>&1 || fail "缺少镜像 $ACTUATOR_IMAGE"
[ -f "$BASE_COMPOSE" ] || fail "找不到 $BASE_COMPOSE"
[ -f "$OVERRIDE_COMPOSE" ] || fail "找不到 $OVERRIDE_COMPOSE"

echo "请先输入平台登录账号名（这里不是密码）。"
read -r -p "平台登录账号名 [admin]: " username
username="${username:-admin}"
echo "下一项才是密码；输入时屏幕不会显示字符。"
read -r -s -p "平台登录密码: " password
echo
[ -n "$password" ] || fail "密码不能为空"

mkdir -p "$SECRET_DIR"
chmod 700 "$SECRET_DIR"
candidate="${SECRET_FILE}.candidate.$$"
cleanup() { [ ! -e "$candidate" ] || unlink "$candidate"; }
trap cleanup EXIT
printf '%s' "$password" > "$candidate"
unset password
chmod 600 "$candidate"

network="$(docker inspect wharttest-backend --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}')"
[ -n "$network" ] || fail "无法确定 Backend Docker 网络"

echo "[校验] 用新凭据请求 Backend token（不输出密码）..."
docker run --rm \
  --network "$network" \
  -e ACTUATOR_API_USERNAME="$username" \
  -v "$candidate:/run/secrets/actuator_api_password:ro" \
  --entrypoint /usr/local/bin/python3.12 \
  "$ACTUATOR_IMAGE" -c '
import json, os, sys, urllib.error, urllib.request
password = open("/run/secrets/actuator_api_password", encoding="utf-8").read().strip()
payload = json.dumps({"username": os.environ["ACTUATOR_API_USERNAME"], "password": password}).encode()
request = urllib.request.Request("http://backend:8000/api/token/", data=payload, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode())
        token = ((body.get("data") or {}).get("access") or body.get("access"))
        if response.status != 200 or not token:
            raise RuntimeError(f"HTTP {response.status}: token missing")
        print("Backend token 校验成功")
except urllib.error.HTTPError as exc:
    print(f"Backend token 校验失败: HTTP {exc.code}, {exc.read().decode()[:500]}", file=sys.stderr)
    raise SystemExit(2)
' || fail "账号或密码不正确，未修改现有配置"

if [ -f "$SECRET_FILE" ]; then
  cp -p "$SECRET_FILE" "${SECRET_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
fi
mv "$candidate" "$SECRET_FILE"
trap - EXIT
chmod 600 "$SECRET_FILE"

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE")
ACTUATOR_API_USERNAME="$username" "${compose[@]}" config >/dev/null

echo "[修复] 清理失效 slot 租约..."
docker exec wharttest-redis redis-cli DEL ui_auto:slot_leases >/dev/null

echo "[修复] 将历史遗留的“执行中”用例收尾为失败..."
docker exec -i wharttest-backend /opt/venv/bin/python /app/manage.py shell <<'PY'
from ui_automation.models import UiTestCase
count = UiTestCase.objects.filter(status=1).update(
    status=3,
    error_message="执行器认证失效，任务未实际执行；凭据已修复，请重新执行",
)
print(f"已收尾 {count} 条卡住用例")
PY

echo "[重建] 使用新凭据重建三个执行器..."
ACTUATOR_API_USERNAME="$username" "${compose[@]}" up -d --pull never --no-deps --force-recreate actuator-01 actuator-02 actuator-03

for index in 01 02 03; do
  container="wharttest-actuator-$index"
  ready=false
  for _ in $(seq 1 30); do
    if docker logs --since 2m "$container" 2>&1 | grep -q '已连接到服务器'; then
      ready=true
      break
    fi
    sleep 2
  done
  [ "$ready" = true ] || { docker logs --tail 100 "$container" >&2; fail "$container 未连接 Backend"; }
  if docker logs --since 2m "$container" 2>&1 | grep -q '获取Token失败'; then
    docker logs --tail 100 "$container" >&2
    fail "$container 仍然认证失败"
  fi
  echo "[通过] $container 已连接 Backend"
done

echo "修复完成。请刷新页面后重新执行用例。"
