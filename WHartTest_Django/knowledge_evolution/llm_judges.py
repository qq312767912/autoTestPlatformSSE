"""基于平台现有 LLM 配置的双裁判执行器。

裁判只读取本次评测的量表和金标答案，返回结构化证据；这些内容不会写入
优化候选的输入，避免隐藏集答案泄漏给自进化链路。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from langgraph_integration.models import LLMConfig


def _json_object(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else getattr(value, "content", "")
    if isinstance(text, list):
        text = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in text)
    text = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("裁判未返回JSON对象")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("裁判结果必须是JSON对象")
    return payload


def _usage(response: Any) -> int:
    metadata = getattr(response, "usage_metadata", None) or {}
    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or {}
    return int(
        metadata.get("total_tokens")
        or token_usage.get("total_tokens")
        or 0
    )


@dataclass(frozen=True)
class LLMJudgeVote:
    judge_name: str
    model_version: str
    score: float | None
    passed: bool | None
    confidence: float
    dimensions: dict[str, Any]
    evidence: list[dict[str, Any]]
    rationale: str
    raw_output: dict[str, Any]
    status: str
    latency_ms: int
    token_usage: int

    def aggregate_vote(self) -> dict[str, Any] | None:
        if self.status != "completed" or self.score is None:
            return None
        return {
            "judge_name": self.judge_name,
            "model_version": self.model_version,
            "score": self.score,
            "passed": self.passed,
            "confidence": self.confidence,
        }


class LLMJuryService:
    """运行两个独立裁判。

    ``jury_config.llm_config_ids`` 可指定两个配置；未指定时使用激活配置和
    最近的备选配置。只有一个配置时用两份隔离提示词执行，仍保留独立调用和证据。
    """

    def __init__(self, llm_factory: Callable | None = None):
        self.llm_factory = llm_factory or self._default_factory

    @staticmethod
    def _default_factory(config):
        from langgraph_integration.views import create_llm_instance
        return create_llm_instance(config, temperature=0)

    def _configs(self, rubric) -> list[LLMConfig]:
        jury_config = rubric.jury_config if rubric else {}
        config_ids = jury_config.get("llm_config_ids") or []
        if config_ids:
            configs = list(LLMConfig.objects.filter(id__in=config_ids))
            order = {str(value): index for index, value in enumerate(config_ids)}
            configs.sort(key=lambda item: order.get(str(item.id), 999))
        else:
            configs = list(LLMConfig.objects.order_by("-is_active", "-updated_at")[:2])
        if len(configs) == 1:
            configs.append(configs[0])
        return configs[:2]

    @staticmethod
    def _prompt(*, output, rubric, expected_payload, persona):
        criteria = rubric.dimensions if rubric else []
        return (
            "你是质量评测陪审团的独立裁判。"
            f"本次角色侧重：{persona}。\n"
            "严格依据量表、参考答案和实际输出评分，不要推测未提供事实。\n"
            "仅返回JSON：{\"score\":0到1,\"passed\":true/false,"
            "\"confidence\":0到1,\"dimensions\":{},\"evidence\":[],\"rationale\":\"\"}\n"
            f"量表：{json.dumps(criteria, ensure_ascii=False, default=str)}\n"
            f"必须项：{json.dumps(rubric.required_items if rubric else [], ensure_ascii=False)}\n"
            f"禁止项：{json.dumps(rubric.forbidden_items if rubric else [], ensure_ascii=False)}\n"
            f"参考答案：{json.dumps(expected_payload or {}, ensure_ascii=False, default=str)}\n"
            f"实际输出：{output.content}"
        )

    def run(self, *, output, rubric=None, expected_payload=None) -> list[LLMJudgeVote]:
        configs = self._configs(rubric)
        if not configs:
            return []
        personas = ("业务正确性与风险", "可执行性、边界与反证")
        votes = []
        for index, (config, persona) in enumerate(zip(configs, personas), start=1):
            started = time.perf_counter()
            judge_name = f"llm-judge-{index}:{config.config_name}"
            try:
                response = self.llm_factory(config).invoke(self._prompt(
                    output=output, rubric=rubric, expected_payload=expected_payload, persona=persona,
                ))
                payload = _json_object(response)
                score = max(0.0, min(1.0, float(payload["score"])))
                confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.7))))
                votes.append(LLMJudgeVote(
                    judge_name=judge_name, model_version=config.name, score=score,
                    passed=bool(payload.get("passed", score >= 0.7)), confidence=confidence,
                    dimensions=payload.get("dimensions") or {}, evidence=payload.get("evidence") or [],
                    rationale=str(payload.get("rationale") or ""), raw_output=payload,
                    status="completed", latency_ms=int((time.perf_counter() - started) * 1000),
                    token_usage=_usage(response),
                ))
            except Exception as exc:  # 裁判失败不得阻断业务评测
                votes.append(LLMJudgeVote(
                    judge_name=judge_name, model_version=config.name, score=None, passed=None,
                    confidence=0.0, dimensions={}, evidence=[], rationale=str(exc)[:1000],
                    raw_output={"error_type": type(exc).__name__}, status="failed",
                    latency_ms=int((time.perf_counter() - started) * 1000), token_usage=0,
                ))
        return votes
