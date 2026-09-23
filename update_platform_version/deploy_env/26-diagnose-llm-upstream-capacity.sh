#!/usr/bin/env bash
# 上游模型网关容量与可用性诊断：区分「网关整体故障」与「单请求体量超出上游承受」。
#
# 背景：用例审查失败最常见的形态是
#   InternalServerError: Error code: 502 - {"error": {"message": "Did not observe any item
#   or terminal signal within ...ms in 'flatMap' (and no fallback has been configured)",
#   "type": "upstream_error", "code": "upstream_unavailable"}}
# 这条报错由上游网关（不是平台）产生，含义是「网关在自己的等待窗口内，没有从模型拿到任何输出」。
# 它既可能是上游整体故障（此时小请求也挂），也可能是单请求体量太大导致模型迟迟不吐 token。
#
# 18-diagnose-small-testcase-review.sh 里的真实调用只有「只回复 OK」这种极小请求，
# 测不出只有大 prompt 才会复现的故障；本脚本按体量阶梯加压，定位上游从哪一档开始不吐 token。
#
# 用法：
#   bash 26-diagnose-llm-upstream-capacity.sh                    # 台阶探测 + 体量测量
#   REVIEW_ID=123 REAL_PROBE=1 bash 26-diagnose-llm-upstream-capacity.sh
#                                                                # 追加：用该次审查的真实
#                                                                # Skill + 前 20 行复现一次
# 可调环境变量：LADDER_SIZES（默认 2000,8000,20000,40000）、PROBE_TIMEOUT（默认 180 秒）、
#              REAL_PROBE_ROWS（默认 20）、BACKEND_CONTAINER（默认 wharttest-backend）
set -uo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/llm-upstream-capacity-$(date '+%Y%m%d-%H%M%S').log"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
REVIEW_ID="${REVIEW_ID:-}"
LADDER_SIZES="${LADDER_SIZES:-2000,8000,20000,40000}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-180}"
REAL_PROBE="${REAL_PROBE:-0}"
REAL_PROBE_ROWS="${REAL_PROBE_ROWS:-20}"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }

echo "上游网关容量诊断日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"
echo "台阶（prompt 字符数）：$LADDER_SIZES"
echo "单次探测超时：${PROBE_TIMEOUT}s   真实复现：${REAL_PROBE}   指定审查：${REVIEW_ID:-最新一条}"

section "Backend 状态与镜像"
docker inspect "$BACKEND_CONTAINER" --format \
  'image={{.Config.Image}} revision={{index .Config.Labels "org.opencontainers.image.revision"}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' || true

section "平台侧参数与本次请求体量"
docker exec -e REVIEW_ID="$REVIEW_ID" "$BACKEND_CONTAINER" \
  /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
from pathlib import Path
from langgraph_integration.models import LLMConfig
from testcases import review_service as rs
from testcases.models import TestCaseReview

config = LLMConfig.objects.filter(is_active=True).first()
print(json.dumps({
    "active_config": {
        "id": config.id if config else None,
        "provider": config.provider if config else None,
        "model": config.name if config else None,
        "api_url": config.api_url if config else None,
        "api_key_configured": bool(config and config.api_key),
        "request_timeout": config.request_timeout if config else None,
        "max_retries": config.max_retries if config else None,
    },
    "review_params": {
        "chunk_size": getattr(rs, "TESTCASE_REVIEW_CHUNK_SIZE", None),
        "max_workers": getattr(rs, "TESTCASE_REVIEW_MAX_WORKERS", None),
        "attempts_per_batch": rs._chunk_attempt_limit(config) if (config and hasattr(rs, "_chunk_attempt_limit")) else None,
        "circuit_breaker": getattr(rs, "TESTCASE_REVIEW_CIRCUIT_BREAKER", None),
        "budget_seconds": getattr(rs, "TESTCASE_REVIEW_TOTAL_BUDGET_SECONDS", None),
        "per_batch_timeout_seconds": max(30, min(int((config.request_timeout if config else None) or 120), 600)),
        "has_circuit_breaker": hasattr(rs, "TESTCASE_REVIEW_CIRCUIT_BREAKER"),
    },
}, ensure_ascii=False, indent=2))

requested = (os.environ.get("REVIEW_ID") or "").strip()
query = TestCaseReview.objects.order_by("-created_at")
review = query.filter(pk=int(requested)).first() if requested.isdigit() else query.first()
if not review:
    print(json.dumps({"error": "没有找到用例审查记录"}, ensure_ascii=False))
else:
    payload = {
        "id": review.id,
        "file": review.source_name,
        "status": review.status,
        "step": review.current_step,
        "progress": review.progress,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "error": (review.error_message or "")[:2000],
        "uncovered_chunks": (review.summary or {}).get("uncovered_chunks") if isinstance(review.summary, dict) else None,
        "total_chunks": (review.summary or {}).get("total_chunks") if isinstance(review.summary, dict) else None,
    }
    try:
        path = review.source_file.path if review.source_file else None
        rows = rs._read_rows(path) if (path and Path(path).exists()) else []
        skill_prompt = review.skill_snapshot or ""
        if not skill_prompt:
            try:
                skill_prompt = rs.build_skill_snapshot(review.selected_skill)
            except Exception as exc:
                skill_prompt = ""
                payload["skill_snapshot_fallback_error"] = f"{type(exc).__name__}: {exc}"
        chunk_size = getattr(rs, "TESTCASE_REVIEW_CHUNK_SIZE", 20)
        first_chunk = rows[:chunk_size]
        # 每批实际送进模型的 prompt 体量 = Skill 快照 + 待审查行 JSON + 指令模板。
        # 与下面台阶探测的「prompt 字符数」直接可比，用来判断上游是不是被体量压垮。
        payload["prompt_budget"] = {
            "recognized_rows": len(rows),
            "chunk_size": chunk_size,
            "batches": ((len(rows) + chunk_size - 1) // chunk_size) if (rows and chunk_size) else 0,
            "skill_prompt_chars": len(skill_prompt),
            "chunk_payload_chars": len(json.dumps(first_chunk, ensure_ascii=False)) if first_chunk else 0,
            "estimated_per_batch_chars": len(skill_prompt) + (len(json.dumps(first_chunk, ensure_ascii=False)) if first_chunk else 0),
            "skill_is_full_version": ("## 9. 严重程度参考" in skill_prompt) or ("## 完成条件" in skill_prompt),
        }
    except Exception as exc:
        payload["prompt_budget_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
' || true

section "上游体量阶梯探测（每档一次调用，零重试，要求只回复两个字）"
docker exec -e LADDER_SIZES="$LADDER_SIZES" -e PROBE_TIMEOUT="$PROBE_TIMEOUT" "$BACKEND_CONTAINER" \
  /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
import time
from openai import OpenAI
from langgraph_integration.models import LLMConfig

config = LLMConfig.objects.filter(is_active=True).first()
sizes = [int(x) for x in (os.environ.get("LADDER_SIZES") or "2000,8000,20000,40000").split(",") if x.strip()]
timeout = float(os.environ.get("PROBE_TIMEOUT") or 180)
unit = "用例：登录页面输入正确的用户名和密码，点击登录按钮，预期跳转到首页并显示欢迎信息。"

rows = []
for size in sizes:
    entry = {"prompt_chars": size}
    started = time.monotonic()
    if not config:
        entry.update({"ok": False, "error": "没有已激活的模型配置"})
        entry["elapsed_seconds"] = round(time.monotonic() - started, 2)
        rows.append(entry)
        print(json.dumps(entry, ensure_ascii=False))
        break
    try:
        client = OpenAI(api_key=config.api_key or "EMPTY", base_url=config.api_url, timeout=timeout, max_retries=0)
        response = client.chat.completions.create(
            model=config.name,
            messages=[{"role": "user", "content": "下面是测试用例文本，请只回复两个字：收到\n" + (unit * (size // len(unit) + 1))[:size]}],
            temperature=0,
            max_tokens=512,
        )
        content = response.choices[0].message.content if response.choices else ""
        entry.update({"ok": bool(content), "content": (content or "")[:40], "finish_reason": response.choices[0].finish_reason if response.choices else None})
    except Exception as exc:
        entry.update({"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:600]})
    entry["elapsed_seconds"] = round(time.monotonic() - started, 2)
    rows.append(entry)
    print(json.dumps(entry, ensure_ascii=False))

ok_sizes = [r["prompt_chars"] for r in rows if r.get("ok")]
bad_sizes = [r["prompt_chars"] for r in rows if not r.get("ok")]
if not rows or not ok_sizes and not bad_sizes:
    verdict = "未能完成探测"
elif not bad_sizes:
    verdict = "全部台阶通过：上游当前可用。用例审查失败更可能是瞬时故障或并发压力（多批同时在跑）"
elif not ok_sizes:
    verdict = "所有台阶均失败：上游网关当前整体不可用，与平台代码和 Skill 内容无关"
else:
    verdict = "从 " + str(min(bad_sizes)) + " 字符档开始失败：上游对单请求体量敏感，需要减小分片（chunk_size）或降低并发"
print(json.dumps({"ok_sizes": ok_sizes, "failed_sizes": bad_sizes, "verdict": verdict}, ensure_ascii=False, indent=2))
' || true

if [ "$REAL_PROBE" = "1" ]; then
  section "真实复现：本次审查的 Skill 快照 + 前 ${REAL_PROBE_ROWS} 行，走平台同一调用路径"
  docker exec -e REVIEW_ID="$REVIEW_ID" -e REAL_PROBE_ROWS="$REAL_PROBE_ROWS" "$BACKEND_CONTAINER" \
    /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
import time
from langgraph_integration.models import LLMConfig
from requirements.services import create_llm_instance
from testcases import review_service as rs
from testcases.models import TestCaseReview

config = LLMConfig.objects.filter(is_active=True).first()
requested = (os.environ.get("REVIEW_ID") or "").strip()
rows_n = int(os.environ.get("REAL_PROBE_ROWS") or 20)
query = TestCaseReview.objects.order_by("-created_at")
review = query.filter(pk=int(requested)).first() if requested.isdigit() else query.first()
timeout = max(30, min(int((config.request_timeout if config else None) or 120), 600))
result = {"review_id": getattr(review, "id", None), "rows": rows_n, "per_batch_timeout_seconds": timeout}
started = time.monotonic()
if not (config and review):
    result.update({"ok": False, "error": "缺少激活模型配置或审查记录"})
else:
    try:
        data = rs._read_rows(review.source_file.path)[:rows_n]
        skill_prompt = review.skill_snapshot or rs.build_skill_snapshot(review.selected_skill)
        result["skill_prompt_chars"] = len(skill_prompt)
        result["payload_chars"] = len(json.dumps(data, ensure_ascii=False))
        llm = create_llm_instance(config, temperature=0.1, timeout=timeout, max_retries=0)
        payload = rs._review_chunk(llm, skill_prompt, data, review.business_context)
        result.update({"ok": True, "issues": len(payload.get("issues", [])) if isinstance(payload, dict) else None})
    except Exception as exc:
        result.update({"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:1000]})
result["elapsed_seconds"] = round(time.monotonic() - started, 2)
print(json.dumps(result, ensure_ascii=False, indent=2))
' || true
else
  section "真实复现未启用"
  echo "如需用本次审查的真实 Skill 与大分段请求复现，请加 REAL_PROBE=1 重跑（会真实消耗一次模型调用）。"
fi

section "Celery 活跃与等待任务"
docker exec "$BACKEND_CONTAINER" timeout 30 celery -A wharttest_django inspect active --timeout=15 || true
docker exec "$BACKEND_CONTAINER" timeout 30 celery -A wharttest_django inspect reserved --timeout=15 || true

section "近期模型调用与用例审查日志"
docker exec "$BACKEND_CONTAINER" sh -c \
  'grep -Eai "testcase.review|用例审查|Initialized.*LLM|timeout|timed out|upstream|502|503|504|circuit|熔断|错误|error" /app/data/logs/worker_err.log /app/data/logs/worker_out.log 2>/dev/null | tail -n 600' || true

section "诊断结束"
echo "请将该日志文件返回：$LOG_FILE"
echo "下一步判据：全部台阶都失败 -> 上游问题；小台阶过、大台阶挂 -> 减小 chunk_size 或并发。"
