#!/usr/bin/env bash
# 上游模型「能力矩阵」探测：一次跑清当前激活模型支持哪些特性，以及不支持时会打断哪条功能。
#
# 背景：排查用例审查失败时，经常被问到「是不是模型不支持 tool 调用」。
# 代码事实是：用例审查（testcases/review_service.py::_review_chunk）只用
#   [SystemMessage(skill_prompt), HumanMessage(prompt)] 做一次纯文本调用，
#   再用 _extract_json 正则提取 JSON —— 全程不传 tools、不传 response_format。
# 全项目扫描结果：bind_tools 0 处、with_structured_output 0 处，
#   tools= 只出现在智能体编排（orchestrator_integration 的 create_agent）。
#
# 也就是说「需要 tool 调用」这个说法，对用例审查不成立；真正受模型能力影响的
# 是另外几项。本脚本把「平台依赖哪项能力」与「网关是否提供」一次性对齐，
# 把讨论从推测变成实测。
#
# 用法：
#   bash 27-probe-llm-capabilities.sh
#   PROBE_SKIP=tool_calling bash 27-probe-llm-capabilities.sh   # 跳过指定项（逗号分隔）
# 可调环境变量：PROBE_TIMEOUT（默认 60 秒/项）、PROBE_SKIP、BACKEND_CONTAINER
set -uo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/llm-capabilities-$(date '+%Y%m%d-%H%M%S').log"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-60}"
PROBE_SKIP="${PROBE_SKIP:-}"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }

echo "模型能力矩阵探测日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"
echo "单项超时：${PROBE_TIMEOUT}s   跳过项：${PROBE_SKIP:-无}"

section "Backend 状态与镜像"
docker inspect "$BACKEND_CONTAINER" --format \
  'image={{.Config.Image}} revision={{index .Config.Labels "org.opencontainers.image.revision"}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' || true

section "当前激活模型配置"
docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c '
import json
from langgraph_integration.models import LLMConfig

config = LLMConfig.objects.filter(is_active=True).first()
print(json.dumps({
    "id": getattr(config, "id", None),
    "provider": getattr(config, "provider", None),
    "model": getattr(config, "name", None),
    "api_url": getattr(config, "api_url", None),
    "api_key_configured": bool(getattr(config, "api_key", None)),
    "request_timeout": getattr(config, "request_timeout", None),
    "max_retries": getattr(config, "max_retries", None),
    "config_candidates": list(LLMConfig.objects.values_list("id", "provider", "name", "is_active")),
}, ensure_ascii=False, indent=2))
' || true

section "能力逐项探测（basic_chat / json_text / json_mode / tool_calling / streaming）"
docker exec -e PROBE_TIMEOUT="$PROBE_TIMEOUT" -e PROBE_SKIP="$PROBE_SKIP" "$BACKEND_CONTAINER" \
  /opt/venv/bin/python /app/manage.py shell -c '
import json
import os
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph_integration.models import LLMConfig
from requirements.services import create_llm_instance

timeout = float(os.environ.get("PROBE_TIMEOUT") or 60)
skip = [x.strip() for x in (os.environ.get("PROBE_SKIP") or "").split(",") if x.strip()]
config = LLMConfig.objects.filter(is_active=True).first()

results = {}


def rec(name, ok, detail, **extra):
    results[name] = dict(ok=bool(ok), detail=detail, **extra)
    print(json.dumps({"probe": name, "ok": bool(ok), "detail": detail, **extra}, ensure_ascii=False))


def build(temperature=0.1):
    # 与用例审查同一条构造路径：requirements.services.create_llm_instance。
    return create_llm_instance(config, temperature=temperature, timeout=timeout, max_retries=0)


if not config:
    print(json.dumps({"error": "没有已激活的模型配置，无法探测"}, ensure_ascii=False))
else:
    # ---------- 1. 基础对话：全平台底线 ----------
    if "basic_chat" in skip:
        print(json.dumps({"probe": "basic_chat", "skipped": True}, ensure_ascii=False))
    else:
        started = time.monotonic()
        try:
            response = build().invoke([HumanMessage(content="只回复 OK")])
            content = getattr(response, "content", "") or ""
            elapsed = round(time.monotonic() - started, 2)
            rec("basic_chat", bool(content.strip()),
                "耗时 %ss，content 长度 %s，内容=%s" % (elapsed, len(content), content.strip()[:30]))
        except Exception as exc:
            rec("basic_chat", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                elapsed_seconds=round(time.monotonic() - started, 2))

    # ---------- 2. 纯文本 JSON 输出：用例审查的真实要求 ----------
    # 用例审查不要求 tool，但要求模型「听话地只吐 JSON」。这一项挂了，
    # 审查会以「模型未返回可解析的 JSON」失败。
    if "json_text" in skip:
        print(json.dumps({"probe": "json_text", "skipped": True}, ensure_ascii=False))
    else:
        started = time.monotonic()
        try:
            from testcases import review_service as rs
            response = rs.safe_llm_invoke(
                build(),
                [
                    SystemMessage(content="你是测试用例审查助手，负责识别用例中的模糊表述。"),
                    HumanMessage(content=(
                        "只输出一个 JSON 对象，不要 Markdown，不要解释。结构："
                        "{\"issues\": [], \"pending_confirmations\": [], \"governance_suggestions\": []}"
                    )),
                ],
                max_retries=1,
                retry_delay=1,
            )
            parsed = rs._extract_json(getattr(response, "content", "") or "")
            ok = isinstance(parsed, dict) and "issues" in parsed
            rec("json_text", ok,
                "耗时 %ss，解析结果 keys=%s" % (round(time.monotonic() - started, 2),
                                              sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__))
        except Exception as exc:
            rec("json_text", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                elapsed_seconds=round(time.monotonic() - started, 2))

    # ---------- 3. JSON mode（response_format=json_object）：代码审查迭代修复依赖 ----------
    if "json_mode" in skip:
        print(json.dumps({"probe": "json_mode", "skipped": True}, ensure_ascii=False))
    else:
        started = time.monotonic()
        try:
            response = build(temperature=0.1).invoke(
                "只输出一个 JSON 对象，内容为 {\"ok\": true}",
                response_format={"type": "json_object"},
            )
            content = getattr(response, "content", "") or ""
            try:
                payload = json.loads(content)
                ok = isinstance(payload, dict)
            except Exception:
                ok = False
            rec("json_mode", ok,
                "耗时 %ss，content 长度 %s，前 80 字符=%s" % (round(time.monotonic() - started, 2),
                                                          len(content), content.strip()[:80]))
        except Exception as exc:
            rec("json_mode", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                elapsed_seconds=round(time.monotonic() - started, 2))

    # ---------- 4. function calling：只有智能体编排依赖 ----------
    if "tool_calling" in skip:
        print(json.dumps({"probe": "tool_calling", "skipped": True}, ensure_ascii=False))
    else:
        started = time.monotonic()
        tool_spec = [{
            "type": "function",
            "function": {
                "name": "get_case_count",
                "description": "查询指定模块下的测试用例数量",
                "parameters": {
                    "type": "object",
                    "properties": {"module": {"type": "string", "description": "模块名称"}},
                    "required": ["module"],
                },
            },
        }]
        try:
            bound = build(temperature=0).bind_tools(tool_spec)
            response = bound.invoke([
                HumanMessage(content="请调用工具查询模块 login 的用例数量，不要自己编造数字。")
            ])
            calls = getattr(response, "tool_calls", None) or []
            detail = "耗时 %ss，tool_calls 数=%s" % (round(time.monotonic() - started, 2), len(calls))
            if calls:
                detail += "，首个=%s" % (calls[0].get("name") if isinstance(calls[0], dict) else getattr(calls[0], "name", "?"))
            rec("tool_calling", bool(calls), detail)
        except Exception as exc:
            rec("tool_calling", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                elapsed_seconds=round(time.monotonic() - started, 2))

    # ---------- 5. 流式：502 flatMap 报文相关 ----------
    if "streaming" in skip:
        print(json.dumps({"probe": "streaming", "skipped": True}, ensure_ascii=False))
    else:
        started = time.monotonic()
        first_chunk_at = None
        chunks = 0
        try:
            for _ in build().stream([HumanMessage(content="用一句话说明测试用例审查的意义。")]):
                chunks += 1
                if first_chunk_at is None:
                    first_chunk_at = round(time.monotonic() - started, 2)
            rec("streaming", chunks > 0,
                "首个 chunk %ss，chunk 总数 %s" % (first_chunk_at, chunks))
        except Exception as exc:
            rec("streaming", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                chunks_before_failure=chunks,
                elapsed_seconds=round(time.monotonic() - started, 2))

impact = {
    "basic_chat": "全平台 AI 功能（底线，挂了什么都做不了）",
    "json_text": "用例审查 testcases/review_service.py::_review_chunk",
    "json_mode": "代码审查迭代修复 code_analysis/services.py:1055",
    "tool_calling": "智能体编排 orchestrator_integration create_agent（用例审查不依赖）",
    "streaming": "网关流式转发路径（502 flatMap 无输出的相关项）",
}
failed = [k for k, v in results.items() if not v.get("ok")]
print()
print(json.dumps({
    "failed": failed,
    "impact_of_failed": {k: impact.get(k) for k in failed},
    "conclusion": (
        "全部通过：当前模型满足平台所有已用到的能力。"
        if not failed else
        "以下能力不可用，对应功能会失败：" + "；".join("%s -> %s" % (k, impact.get(k)) for k in failed)
    ),
}, ensure_ascii=False, indent=2))
' || true

section "模型调用相关近期日志"
docker exec "$BACKEND_CONTAINER" sh -c \
  'grep -Eai "testcase.review|用例审查|Initialized.*LLM|timeout|timed out|upstream|502|503|504|response_format|tool|circuit|熔断" /app/data/logs/worker_err.log /app/data/logs/worker_out.log 2>/dev/null | tail -n 400' || true

section "探测结束"
echo "请将该日志文件返回：$LOG_FILE"
echo "判读：tool_calling 失败只影响智能体，不影响用例审查；json_text 失败才是用例审查的直接病因。"
