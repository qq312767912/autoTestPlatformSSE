#!/usr/bin/env bash
set -euo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[应用] 大 Excel 用例审查稳定性热修复"
bash "$UPDATE_DIR/19-apply-testcase-review-hotfix.sh"

echo "[验证] 表头识别、分片并发、重试退避与断点续审"
docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path
service = Path("/app/testcases/review_service.py").read_text(encoding="utf-8")
assert "TESTCASE_REVIEW_CHUNK_SIZE = 20" in service
assert "TESTCASE_REVIEW_MAX_WORKERS = 2" in service
assert "def _is_case_header" in service
assert "def _chunk_attempt_limit" in service
assert "def _retry_delay" in service
assert "ThreadPoolExecutor" in service
assert "\"_checkpoint\"" in service
assert "串行小分片" not in service, "仍挂载着旧版（10 行串行）review_service.py"
print("large testcase review hotfix OK")
'

echo "[完成] 请在平台对失败的用例审查记录点击“重试”。"
