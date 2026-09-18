#!/usr/bin/env bash
set -euo pipefail

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
BASE_COMPOSE="${PLATFORM_DIR}/offline-images/docker-compose.offline.yml"
UPDATE_COMPOSE="${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.update.yml"
SECRET_FILE="${PLATFORM_DIR}/update_platform_version/deploy_env/secrets/actuator_api_password"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${PLATFORM_DIR}/update_platform_version/fix-actuator-credential-${STAMP}.log"
BACKUP_FILE="${SECRET_FILE}.bak-${STAMP}"

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$SECRET_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

if [[ ! -f "$BASE_COMPOSE" || ! -f "$UPDATE_COMPOSE" ]]; then
  echo "错误：找不到部署编排文件："
  echo "  $BASE_COMPOSE"
  echo "  $UPDATE_COMPOSE"
  exit 1
fi

echo "执行器后台凭据修复"
echo "账号默认使用 ACTUATOR_API_USERNAME，未配置时为 admin。"
echo "请输入该账号当前实际登录平台所用的密码。输入过程不会显示："
IFS= read -r -s NEW_PASSWORD
echo
if [[ -z "$NEW_PASSWORD" ]]; then
  echo "错误：密码不能为空。"
  exit 1
fi

if [[ -f "$SECRET_FILE" ]]; then
  cp -p "$SECRET_FILE" "$BACKUP_FILE"
  echo "旧密钥已备份（内容不会输出）：$BACKUP_FILE"
fi

umask 077
printf '%s' "$NEW_PASSWORD" > "$SECRET_FILE"
unset NEW_PASSWORD
chmod 600 "$SECRET_FILE"

COMPOSE=(docker compose -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")

echo "先重建执行器 01 并验证 Token 接口..."
"${COMPOSE[@]}" up -d --no-deps --force-recreate actuator-01

set +e
docker exec -i wharttest-actuator-01 python - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

username = os.environ.get("WHARTTEST_ACTUATOR_API_USERNAME", "admin")
with open("/run/secrets/actuator_api_password", "r", encoding="utf-8") as f:
    password = f.read().strip()
request = urllib.request.Request(
    "http://backend:8000/api/token/",
    data=json.dumps({"username": username, "password": password}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read())
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    access_token = (
        payload.get("access")
        or payload.get("token")
        or payload.get("access_token")
        or nested.get("access")
        or nested.get("token")
        or nested.get("access_token")
    )
    if not access_token:
        print("Token 接口响应成功，但未返回令牌。")
        sys.exit(2)
    print("Token 验证成功（令牌内容不输出）。")
except urllib.error.HTTPError as exc:
    print(f"Token 验证失败：HTTP {exc.code}")
    sys.exit(1)
except Exception as exc:
    print(f"Token 验证失败：{type(exc).__name__}: {exc}")
    sys.exit(1)
PY
VERIFY_STATUS=$?
set -e

if [[ "$VERIFY_STATUS" -ne 0 ]]; then
  echo "新凭据验证失败。"
  if [[ -f "$BACKUP_FILE" ]]; then
    cp -p "$BACKUP_FILE" "$SECRET_FILE"
    "${COMPOSE[@]}" up -d --no-deps --force-recreate actuator-01
    echo "已恢复旧密钥并重建执行器 01。"
  fi
  echo "请确认输入的是平台账号的当前密码。日志：$LOG_FILE"
  exit 1
fi

echo "凭据正确，继续逐个重建执行器 02、03..."
"${COMPOSE[@]}" up -d --no-deps --force-recreate actuator-02
"${COMPOSE[@]}" up -d --no-deps --force-recreate actuator-03

echo "等待执行器重新注册..."
sleep 5
docker ps --filter name=wharttest-actuator --format 'table {{.Names}}\t{{.Status}}'

echo "修复完成。请刷新执行器页面，并重新运行一条此前失败的用例。"
echo "日志文件：$LOG_FILE"
