#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="tencent-embedding-proxy"
INSTALL_DIR="${INSTALL_DIR:-/opt/${SERVICE_NAME}}"
CONFIG_FILE="${1:-./proxy.env}"
SERVICE_USER="${SERVICE_USER:-embedding-proxy}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 运行：sudo $0 [配置文件]" >&2
    exit 1
fi

for command_name in python3 systemctl install; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        echo "缺少命令：${command_name}" >&2
        exit 1
    }
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_SOURCE="${CONFIG_FILE}"
case "${CONFIG_SOURCE}" in
    /*) ;;
    *) CONFIG_SOURCE="$(cd "$(dirname "${CONFIG_SOURCE}")" && pwd)/$(basename "${CONFIG_SOURCE}")" ;;
esac

if [ ! -f "${CONFIG_SOURCE}" ]; then
    echo "配置文件不存在：${CONFIG_SOURCE}" >&2
    echo "请先复制 tencent-embedding-proxy.env.example 为 proxy.env，并填写 ADP_HOST。" >&2
    exit 1
fi

if ! grep -Eq '^(ADP_HOST|ADP_ENDPOINT)=.+' "${CONFIG_SOURCE}"; then
    echo "配置文件必须设置 ADP_HOST 或 ADP_ENDPOINT。" >&2
    exit 1
fi

log "运行离线单元测试"
python3 -m unittest -v "${SCRIPT_DIR}/test_tencent_embedding_proxy.py"

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    log "创建低权限运行用户 ${SERVICE_USER}"
    useradd --system --home-dir "${INSTALL_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

log "安装代理到 ${INSTALL_DIR}"
install -d -m 0750 -o root -g "${SERVICE_USER}" "${INSTALL_DIR}"
install -m 0755 -o root -g "${SERVICE_USER}" "${SCRIPT_DIR}/tencent_embedding_proxy.py" "${INSTALL_DIR}/tencent_embedding_proxy.py"
install -m 0640 -o root -g "${SERVICE_USER}" "${CONFIG_SOURCE}" "${INSTALL_DIR}/proxy.env"

log "写入 systemd 服务"
{
    echo "[Unit]"
    echo "Description=Tencent LKEAP ADP Embedding OpenAI-compatible Proxy"
    echo "After=network-online.target"
    echo "Wants=network-online.target"
    echo
    echo "[Service]"
    echo "Type=simple"
    echo "User=${SERVICE_USER}"
    echo "Group=${SERVICE_USER}"
    echo "EnvironmentFile=${INSTALL_DIR}/proxy.env"
    echo "ExecStart=/usr/bin/python3 ${INSTALL_DIR}/tencent_embedding_proxy.py"
    echo "Restart=on-failure"
    echo "RestartSec=3"
    echo "NoNewPrivileges=true"
    echo "PrivateTmp=true"
    echo "ProtectSystem=strict"
    echo "ProtectHome=true"
    echo
    echo "[Install]"
    echo "WantedBy=multi-user.target"
} > "${UNIT_FILE}"
chmod 0644 "${UNIT_FILE}"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

PROXY_PORT_VALUE="$(sed -n 's/^PROXY_PORT=//p' "${CONFIG_SOURCE}" | tail -n 1)"
PROXY_PORT_VALUE="${PROXY_PORT_VALUE:-8920}"
log "等待健康检查"
for attempt in $(seq 1 15); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PROXY_PORT_VALUE}/health', timeout=2).read()" >/dev/null 2>&1; then
        log "部署成功：http://127.0.0.1:${PROXY_PORT_VALUE}/v1/embeddings"
        systemctl --no-pager --full status "${SERVICE_NAME}" || true
        exit 0
    fi
    sleep 1
done

echo "服务健康检查失败，最近日志如下：" >&2
journalctl -u "${SERVICE_NAME}" -n 80 --no-pager >&2 || true
exit 1
