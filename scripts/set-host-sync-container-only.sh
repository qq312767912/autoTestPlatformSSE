#!/usr/bin/env bash
# Never reads, writes, removes, or restores /etc/hosts.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo '请用 sudo bash 执行'; exit 1; }
backup="/projects/ai-test-platform/backups/host-sync/container-only-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$backup"
cp -a /etc/wharttest/host-sync.env "$backup/host-sync.env"
systemctl stop wharttest-host-sync.timer
systemctl stop wharttest-host-sync.service || true
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/wharttest/host-sync.env')
lines=[x for x in p.read_text().splitlines() if not x.strip().startswith('HOST_SYNC_APPLY_HOST=')]
lines.append('HOST_SYNC_APPLY_HOST=false')
p.write_text('\n'.join(lines)+'\n');p.chmod(0o600)
PY
install -d -m 0755 /etc/systemd/system/wharttest-host-sync.service.d
file=/etc/systemd/system/wharttest-host-sync.service.d/99-container-only.conf
if [ -f "$file" ]; then cp -a "$file" "$backup/99-container-only.conf"; fi
cat > "$file" <<'CONF'
[Service]
ReadWritePaths=
ReadWritePaths=/var/lib/wharttest-host-sync /projects/ai-test-platform/backups/host-sync
ReadOnlyPaths=/etc/hosts
CONF
systemctl daemon-reload
systemctl start wharttest-host-sync.timer
systemctl start wharttest-host-sync.service
echo "已启用仅容器同步；宿主机 /etc/hosts 未修改。配置备份：$backup"
