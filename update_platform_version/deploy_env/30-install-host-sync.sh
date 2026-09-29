#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/host_sync"
INSTALL_DIR="/usr/local/lib/wharttest-host-sync"
CONFIG_DIR="/etc/wharttest"
STATE_DIR="/var/lib/wharttest-host-sync"
BACKUP_DIR="/projects/ai-test-platform/backups/host-sync"
TOKEN_SOURCE="$SCRIPT_DIR/secrets/test_host_sync_token"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 执行该脚本" >&2
  exit 1
fi
if [[ ! -s "$TOKEN_SOURCE" ]]; then
  echo "部署目录中的同步密钥缺失，尝试从正在运行的 Backend 恢复..."
  install -d -m 0700 "$(dirname "$TOKEN_SOURCE")"
  token_tmp="$(mktemp)"
  trap 'rm -f "$token_tmp"' EXIT
  if docker exec wharttest-backend test -s /run/secrets/test_host_sync_token >/dev/null 2>&1 && \
     docker exec wharttest-backend cat /run/secrets/test_host_sync_token > "$token_tmp" && \
     [[ -s "$token_tmp" ]]; then
    install -m 0600 "$token_tmp" "$TOKEN_SOURCE"
    echo "已从 Backend 恢复同步密钥（内容不会输出）。"
  else
    echo "无法从 Backend 恢复同步密钥。请先运行 04-deploy.sh，使 Backend 与新 Token 一起重建。" >&2
    exit 1
  fi
  rm -f "$token_tmp"
  trap - EXIT
fi

install -d -m 0755 "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR" "$BACKUP_DIR"
install -m 0755 "$SOURCE_DIR/wharttest_host_sync.py" "$INSTALL_DIR/wharttest_host_sync.py"
install -m 0600 "$TOKEN_SOURCE" "$CONFIG_DIR/test_host_sync_token"
cat > "$CONFIG_DIR/host-sync.env" <<'EOF'
WHARTTEST_BACKEND_URL=http://127.0.0.1:8912
TEST_HOST_SYNC_TOKEN_FILE=/etc/wharttest/test_host_sync_token
HOST_SYNC_BACKUP_ROOT=/projects/ai-test-platform/backups/host-sync
HOST_SYNC_STATE_FILE=/var/lib/wharttest-host-sync/state.json
EOF
chmod 0600 "$CONFIG_DIR/host-sync.env"
install -m 0644 "$SOURCE_DIR/wharttest-host-sync.service" /etc/systemd/system/wharttest-host-sync.service
install -m 0644 "$SOURCE_DIR/wharttest-host-sync.timer" /etc/systemd/system/wharttest-host-sync.timer
systemctl daemon-reload
systemctl enable --now wharttest-host-sync.timer
if ! systemctl start wharttest-host-sync.service; then
  echo "宿主机域名同步首次执行失败，诊断信息如下：" >&2
  systemctl --no-pager --full status wharttest-host-sync.service >&2 || true
  journalctl -u wharttest-host-sync.service -n 100 --no-pager >&2 || true
  exit 1
fi
systemctl --no-pager --full status wharttest-host-sync.service || true
