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
  echo "缺少同步密钥: $TOKEN_SOURCE" >&2
  echo "可执行: openssl rand -hex 32 > '$TOKEN_SOURCE' && chmod 600 '$TOKEN_SOURCE'" >&2
  exit 1
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
systemctl start wharttest-host-sync.service
systemctl --no-pager --full status wharttest-host-sync.service || true
