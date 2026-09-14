#!/usr/bin/env bash
set -euo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[应用] 大 Excel 用例审查稳定性热修复"
bash "$UPDATE_DIR/19-apply-testcase-review-hotfix.sh"

echo "[验证] 表头识别、串行小分片和断点续审"
docker exec wharttest-backend /opt/venv/bin/python -c '
from pathlib import Path
service = Path("/app/testcases/review_service.py").read_text(encoding="utf-8")
assert "TESTCASE_REVIEW_CHUNK_SIZE = 10" in service
assert "TESTCASE_REVIEW_CHUNK_ATTEMPTS = 2" in service
assert "def _is_case_header" in service
assert "串行小分片" in service
assert "\"_checkpoint\"" in service
print("large testcase review hotfix OK")
'

echo "[完成] 请在平台对失败的用例审查记录点击“重试”。"
