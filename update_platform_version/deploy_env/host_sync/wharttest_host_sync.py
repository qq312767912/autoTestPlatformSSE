#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BEGIN_MARKER = "# BEGIN WHARTTEST MANAGED HOSTS"
END_MARKER = "# END WHARTTEST MANAGED HOSTS"
ACTUATOR_PATTERN = re.compile(r"^wharttest-actuator-[A-Za-z0-9_.-]+$")
FIXED_CONTAINERS = {
    "wharttest-backend": ("backend", "Backend / Celery / Recorder"),
    "wharttest-playwright-mcp": ("playwright", "Playwright MCP"),
}


def canonical_payload(mappings):
    return json.dumps(mappings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify_checksum(mappings, expected):
    actual = hashlib.sha256(canonical_payload(mappings).encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(f"checksum 校验失败: expected={expected}, actual={actual}")


def render_managed_block(mappings):
    lines = [BEGIN_MARKER]
    for item in sorted(mappings, key=lambda row: row["hostname"]):
        ipv4 = item["ipv4"]
        hostname = item["hostname"]
        socket.inet_aton(ipv4)
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", hostname):
            raise ValueError(f"非法域名: {hostname}")
        lines.append(f"{ipv4}\t{hostname}")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def replace_managed_block(original, block):
    begin = original.find(BEGIN_MARKER)
    end = original.find(END_MARKER)
    if (begin == -1) != (end == -1):
        raise ValueError("hosts 中的 WHartTest 受管标记不完整")
    if begin != -1:
        if end < begin:
            raise ValueError("hosts 中的 WHartTest 受管标记顺序错误")
        end += len(END_MARKER)
        prefix = original[:begin].rstrip("\n")
        suffix = original[end:].strip("\n")
        parts = [part for part in (prefix, block.rstrip("\n"), suffix) if part]
        return "\n\n".join(parts) + "\n"
    prefix = original.rstrip("\n")
    return (prefix + "\n\n" if prefix else "") + block


class HostSyncAgent:
    def __init__(self):
        self.backend_url = os.environ.get("WHARTTEST_BACKEND_URL", "http://127.0.0.1:8912").rstrip("/")
        self.token_file = Path(os.environ.get("TEST_HOST_SYNC_TOKEN_FILE", "/etc/wharttest/test_host_sync_token"))
        self.hosts_file = Path(os.environ.get("HOST_SYNC_HOSTS_FILE", "/etc/hosts"))
        self.backup_root = Path(os.environ.get("HOST_SYNC_BACKUP_ROOT", "/projects/ai-test-platform/backups/host-sync"))
        self.state_file = Path(os.environ.get("HOST_SYNC_STATE_FILE", "/var/lib/wharttest-host-sync/state.json"))
        self.docker_bin = os.environ.get("HOST_SYNC_DOCKER_BIN", "docker")

    def _token(self):
        token = self.token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("同步 Token 文件为空")
        return token

    def _request(self, path, method="GET", data=None):
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.backend_url + path, method=method, headers=headers, data=body)
        with urlopen(request, timeout=10) as response:
            if response.status == 204:
                return None
            return json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))

    def fetch_snapshot(self):
        return self._request("/api/test-host-config/agent/export/")

    def backup_host_file(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target_dir = self.backup_root / stamp
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / "hosts"
        shutil.copy2(self.hosts_file, target)
        return target

    def apply_host(self, block):
        original = self.hosts_file.read_text(encoding="utf-8")
        updated = replace_managed_block(original, block)
        if updated == original:
            return False
        self.backup_host_file()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.hosts_file.parent, delete=False) as handle:
            handle.write(updated)
            temp_path = Path(handle.name)
        try:
            os.chmod(temp_path, self.hosts_file.stat().st_mode & 0o777)
            os.replace(temp_path, self.hosts_file)
        finally:
            temp_path.unlink(missing_ok=True)
        return True

    def discover_containers(self):
        command = [self.docker_bin, "ps", "--format", "{{.Names}}"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        targets = []
        for name in names:
            if name in FIXED_CONTAINERS:
                node_type, display_name = FIXED_CONTAINERS[name]
                targets.append((name, node_type, display_name))
            elif ACTUATOR_PATTERN.fullmatch(name):
                targets.append((name, "actuator", name))
        return sorted(targets)

    def apply_container(self, container, block):
        script = r'''set -eu
begin_count="$(grep -Fxc '# BEGIN WHARTTEST MANAGED HOSTS' /etc/hosts || true)"
end_count="$(grep -Fxc '# END WHARTTEST MANAGED HOSTS' /etc/hosts || true)"
if [ "$begin_count" -ne "$end_count" ] || [ "$begin_count" -gt 1 ]; then
  echo 'WHartTest 受管标记不完整或重复' >&2
  exit 4
fi
tmp="$(mktemp)"
awk '
  $0 == "# BEGIN WHARTTEST MANAGED HOSTS" { managed=1; next }
  $0 == "# END WHARTTEST MANAGED HOSTS" { managed=0; next }
  !managed { print }
' /etc/hosts > "$tmp"
printf '\n' >> "$tmp"
cat >> "$tmp"
cat "$tmp" > /etc/hosts
rm -f "$tmp"
'''
        subprocess.run(
            [self.docker_bin, "exec", "-i", "-u", "0", container, "/bin/sh", "-c", script],
            input=block, text=True, check=True, capture_output=True, timeout=20,
        )

    def report(self, nodes):
        self._request("/api/test-host-config/agent/report/", method="POST", data={"nodes": nodes})

    def save_state(self, snapshot, nodes):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": snapshot["version"], "checksum": snapshot["checksum"],
            "updated_at": datetime.now(timezone.utc).isoformat(), "nodes": nodes,
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_file.parent, delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            temp_path = Path(handle.name)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, self.state_file)

    def run(self):
        snapshot = self.fetch_snapshot()
        if snapshot is None:
            return 0
        verify_checksum(snapshot["mappings"], snapshot["checksum"])
        block = render_managed_block(snapshot["mappings"])
        version = snapshot["version"]
        checksum = snapshot["checksum"]
        nodes = []
        try:
            changed = self.apply_host(block)
            nodes.append({
                "node_id": "host", "node_type": "host", "display_name": socket.gethostname(),
                "applied_version": version, "applied_checksum": checksum, "status": "synced",
                "message": "已更新" if changed else "已是最新配置", "details": {},
            })
        except Exception as exc:
            nodes.append({
                "node_id": "host", "node_type": "host", "display_name": socket.gethostname(),
                "applied_version": None, "applied_checksum": "", "status": "failed",
                "message": str(exc)[:500], "details": {},
            })
        for container, node_type, display_name in self.discover_containers():
            try:
                self.apply_container(container, block)
                nodes.append({
                    "node_id": container, "node_type": node_type, "display_name": display_name,
                    "applied_version": version, "applied_checksum": checksum, "status": "synced",
                    "message": "同步成功", "details": {},
                })
            except Exception as exc:
                nodes.append({
                    "node_id": container, "node_type": node_type, "display_name": display_name,
                    "applied_version": None, "applied_checksum": "", "status": "failed",
                    "message": str(exc)[:500], "details": {},
                })
        self.report(nodes)
        self.save_state(snapshot, nodes)
        return 1 if any(node["status"] == "failed" for node in nodes) else 0


def main():
    try:
        return HostSyncAgent().run()
    except HTTPError as exc:
        print(f"同步 API 返回 HTTP {exc.code}", flush=True)
        return 2
    except (URLError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"同步失败: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
