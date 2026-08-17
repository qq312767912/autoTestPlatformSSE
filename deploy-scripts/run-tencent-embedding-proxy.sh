#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${1:-${SCRIPT_DIR}/proxy.env}"

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "配置文件不存在：${CONFIG_FILE}" >&2
    echo "请复制 tencent-embedding-proxy.env.example 为 proxy.env 后填写。" >&2
    exit 1
fi

set -a
. "${CONFIG_FILE}"
set +a
exec python3 "${SCRIPT_DIR}/tencent_embedding_proxy.py"
