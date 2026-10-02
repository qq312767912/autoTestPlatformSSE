"""L0-L3分层评测器与可审计的裁判聚合。"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.db import transaction

from .capability_registry import LEGACY_WORKFLOW_STAGES, WORKFLOW_STAGES
from .evaluation_v2_models import EvaluationRubric, JudgeResult
from .models import FeedbackEvent, GenerationOutput


@dataclass(frozen=True)
class EvaluationEvidence:
    level: str
    evaluator_type: str
    evaluator_version: str
    judge_name: str
    score: float | None
    passed: bool | None
    confidence: float = 1.0
    dimensions: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"
    model_version: str = ""
    latency_ms: int = 0
    token_usage: int = 0


@dataclass
class EvaluationContext:
    output: GenerationOutput
    workflow_outputs: list[GenerationOutput] = field(default_factory=list)
    rubric: EvaluationRubric | None = None
    jury_votes: list[dict[str, Any]] = field(default_factory=list)


class Evaluator(Protocol):
    evaluator_type: str
    version: str
    level: str

    def evaluate(self, context: EvaluationContext) -> EvaluationEvidence: ...


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class SchemaEvaluator:
    evaluator_type = "schema"
    version = "schema-v2"
    level = "l0"

    def evaluate(self, context):
        output = context.output
        protocol = (output.metadata or {}).get("protocol") or {}
        checks = {
            "trace_exists": bool(output.trace_id),
            "task_type_exists": bool(output.task_type),
            "content_exists": bool(output.content),
            "output_hash_valid": len(output.output_hash or "") == 64,
            "protocol_version_exists": bool(protocol.get("schema_version")),
            "stage_matches": not protocol.get("stage") or protocol.get("stage") == output.task_type,
        }
        if protocol.get("evaluation_mode") == "workflow":
            checks["workflow_id_exists"] = bool(protocol.get("workflow_id"))
        score = sum(checks.values()) / len(checks)
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-schema", score=score, passed=all(checks.values()),
            dimensions=checks, rationale="统一产出协议结构检查",
        )


class SensitiveDataEvaluator:
    evaluator_type = "sensitive_data"
    version = "sensitive-v2"
    level = "l0"
    PATTERNS = [
        re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/]{12,}"),
        re.compile(r"(?i)(api[-_ ]?key|password|cookie|authorization)\s*[:=]\s*[^\s,;]{6,}"),
    ]

    def evaluate(self, context):
        protocol_text = _flatten((context.output.metadata or {}).get("protocol") or {})
        findings = [pattern.pattern for pattern in self.PATTERNS if pattern.search(protocol_text)]
        passed = not findings
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-privacy", score=1.0 if passed else 0.0, passed=passed,
            confidence=1.0, dimensions={"sensitive_pattern_count": len(findings)},
            rationale="协议元数据敏感信息检查",
        )


class ReferenceValidityEvaluator:
    evaluator_type = "reference_validity"
    version = "ref-validity-v1"
    level = "l0"

    def evaluate(self, context):
        protocol = (context.output.metadata or {}).get("protocol") or {}
        evidence = protocol.get("evidence") or []
        citations = protocol.get("citations") or []
        checks = []
        for item in evidence:
            checks.append({
                "source_type": bool(item.get("source_type")),
                "source_id": bool(item.get("source_id")),
                "location": bool(item.get("location")),
            })
        for item in citations:
            checks.append({
                "source_type": bool(item.get("source_type") or item.get("finding_key")),
                "source_id": bool(item.get("source_id") or item.get("file")),
                "location": bool(item.get("location") or item.get("line_start")),
            })
        if not checks:
            score, passed = 1.0, True
            dimensions = {"reference_count": 0, "valid_count": 0}
        else:
            valid = sum(all(c.values()) for c in checks)
            score = valid / len(checks)
            passed = score == 1.0
            dimensions = {"reference_count": len(checks), "valid_count": valid}
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-reference", score=score, passed=passed,
            confidence=1.0, dimensions=dimensions,
            rationale="引用来源、标识与定位信息完整性检查",
        )


class ToolSuccessEvaluator:
    evaluator_type = "tool_success"
    version = "tool-success-v1"
    level = "l0"

    def evaluate(self, context):
        trace = context.output.trace
        if not trace:
            return EvaluationEvidence(
                level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
                judge_name="deterministic-tool", score=None, passed=None,
                status="needs_review", rationale="无轨迹，跳过工具调用检查",
            )
        tool_spans = trace.spans.filter(step_type="tool")
        total = tool_spans.count()
        if not total:
            return EvaluationEvidence(
                level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
                judge_name="deterministic-tool", score=1.0, passed=True,
                dimensions={"tool_count": 0, "failed_count": 0},
                rationale="本次产出未调用工具",
            )
        failed = tool_spans.filter(status="failed").count()
        score = (total - failed) / total
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-tool", score=score, passed=failed == 0,
            confidence=1.0, dimensions={"tool_count": total, "failed_count": failed},
            rationale="工具调用 span 成功状态检查",
        )


class TruncationEvaluator:
    evaluator_type = "truncation"
    version = "truncation-v2"
    level = "l0"
    # 显式截断标记：出现即视为正文被裁剪。
    EXPLICIT_MARKERS = ["[truncated]", "(truncated)", "内容已截断", "输出已截断"]
    # 省略号只有出现在**结尾**才指示截断；中文正文里的「……」是正常标点，
    # 若按「出现即扣分」处理，会让所有中文产出被误判为截断。
    ELLIPSIS_MARKERS = ["...", "…"]

    def evaluate(self, context):
        content = context.output.content or ""
        stripped = content.rstrip()
        ends_with_marker = any(
            stripped.endswith(marker)
            for marker in self.EXPLICIT_MARKERS + self.ELLIPSIS_MARKERS
        )
        contains_marker = any(marker in content for marker in self.EXPLICIT_MARKERS)
        # 内容为空或极短也视为异常
        too_short = len(content.strip()) < 3
        passed = not ends_with_marker and not too_short
        score = 0.0 if too_short else (0.5 if contains_marker else 1.0)
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-truncation", score=score, passed=passed,
            confidence=1.0,
            dimensions={"content_length": len(content), "ends_with_marker": ends_with_marker, "contains_marker": contains_marker},
            rationale="产出内容截断标记与空内容检查",
        )


class ParsabilityEvaluator:
    evaluator_type = "parsability"
    version = "parsability-v1"
    level = "l0"

    def evaluate(self, context):
        content = context.output.content or ""
        if not content.strip():
            return EvaluationEvidence(
                level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
                judge_name="deterministic-parse", score=0.0, passed=False,
                dimensions={"empty": True},
                rationale="产出内容为空，无法解析",
            )
        # 如果 protocol 声明输出包含 json 键，则尝试解析
        protocol = (context.output.metadata or {}).get("protocol") or {}
        descriptor_keys = set((protocol.get("output_descriptor") or {}).get("keys") or [])
        looks_like_json = bool(descriptor_keys) or content.strip().startswith(("{", "["))
        if looks_like_json:
            try:
                json.loads(content)
                return EvaluationEvidence(
                    level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
                    judge_name="deterministic-parse", score=1.0, passed=True,
                    dimensions={"json": True},
                    rationale="JSON 内容可解析",
                )
            except (json.JSONDecodeError, ValueError):
                return EvaluationEvidence(
                    level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
                    judge_name="deterministic-parse", score=0.0, passed=False,
                    dimensions={"json": True, "parse_error": True},
                    rationale="内容看起来像 JSON 但解析失败",
                )
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-parse", score=1.0, passed=True,
            dimensions={"json": False},
            rationale="非 JSON 文本内容，可视为可解析",
        )


class RuleEvaluator:
    evaluator_type = "rubric_rule"
    version = "rubric-rule-v2"
    level = "l1"

    def evaluate(self, context):
        rubric = context.rubric
        required = list(rubric.required_items if rubric else [])
        forbidden = list(rubric.forbidden_items if rubric else [])
        text = context.output.content.lower()
        required_hits = [item for item in required if str(item).lower() in text]
        forbidden_hits = [item for item in forbidden if str(item).lower() in text]
        total = len(required) + len(forbidden)
        score = (
            (len(required_hits) + len(forbidden) - len(forbidden_hits)) / total
            if total else 0.5
        )
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="deterministic-rubric", score=max(0.0, min(1.0, score)),
            passed=not forbidden_hits and len(required_hits) == len(required),
            dimensions={
                "required_hit": len(required_hits), "required_total": len(required),
                "forbidden_hit": len(forbidden_hits), "forbidden_total": len(forbidden),
            },
            rationale="量表必须项与禁止项检查",
        )


class JuryAggregator:
    evaluator_type = "jury_aggregate"
    version = "jury-v2"
    level = "l1"

    def evaluate(self, context):
        votes = context.jury_votes or []
        if not votes:
            return EvaluationEvidence(
                level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
                judge_name="jury-aggregate", score=None, passed=None, confidence=0.0,
                status="needs_review", rationale="未配置LLM裁判，等待人工或裁判结果",
            )
        scores = [max(0.0, min(1.0, float(v["score"]))) for v in votes]
        mean = statistics.mean(scores)
        spread = max(scores) - min(scores)
        threshold = float((context.rubric.jury_config if context.rubric else {}).get("max_spread", 0.25))
        status = "needs_review" if spread > threshold else "completed"
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="jury-aggregate", score=mean, passed=mean >= 0.7 and status == "completed",
            confidence=max(0.0, 1.0 - spread),
            dimensions={"judge_count": len(scores), "spread": spread, "threshold": threshold},
            raw_output={"votes": votes}, status=status,
            rationale="多裁判聚合；分歧超过阈值时转人工复核",
        )


class FeedbackOutcomeEvaluator:
    evaluator_type = "feedback_outcome"
    version = "feedback-v2"
    level = "l2"
    POSITIVE = {"accepted", "merged", "test_passed", "defect_confirmed"}
    NEGATIVE = {"rejected", "test_failed", "false_positive", "missed", "reverted"}

    def evaluate(self, context):
        signals = list(
            FeedbackEvent.objects.filter(output=context.output).values_list("signal", flat=True)
        )
        positive = sum(signal in self.POSITIVE for signal in signals)
        negative = sum(signal in self.NEGATIVE for signal in signals)
        denominator = positive + negative
        score = positive / denominator if denominator else None
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="business-feedback", score=score,
            passed=None if score is None else score >= 0.7,
            confidence=min(1.0, denominator / 3) if denominator else 0.0,
            dimensions={"positive": positive, "negative": negative, "signal_count": len(signals)},
            raw_output={"signals": signals},
            status="needs_review" if score is None else "completed",
            rationale="真实采纳、缺陷、测试、误报和漏报信号",
        )


class WorkflowOutcomeEvaluator:
    evaluator_type = "workflow_outcome"
    #: 版本号随判据一起升：主链路口径从"方案/用例/执行/报告"切成
    #: "风险识别/用例/执行/问题跟踪"之后，旧结论与新结论不是同一种判据，
    #: 共用一个版本号会让两次评测看起来可比，实际不可比。
    version = "workflow-v3"
    level = "l3"
    #: 能识别的链路模板（新链路在前）。按上游实际产出的阶段挑一套最贴合的，
    #: 而不是只看新序列——否则存量流程永远被算成"缺两步"，覆盖率恒为 0.5。
    STAGE_TEMPLATES = (
        tuple(WORKFLOW_STAGES),
        tuple(LEGACY_WORKFLOW_STAGES),
    )

    @classmethod
    def template_for(cls, stages: set[str]) -> tuple:
        """挑与这批产出重合度最高的模板；打平时用新序列（下标小者优先）。"""
        return max(
            (len(stages & set(template)), -index, template)
            for index, template in enumerate(cls.STAGE_TEMPLATES)
        )[2]

    def evaluate(self, context):
        outputs = context.workflow_outputs or [context.output]
        stages = {output.task_type for output in outputs}
        template = self.template_for(stages)
        coverage = len(stages & set(template)) / len(template)
        # 收口阶段从模板里取：报告生成与问题跟踪都算"链路最后一段有实质内容"。
        closing_stage = template[-1]
        closing_outputs = [output for output in outputs if output.task_type == closing_stage]
        closing_ready = 1.0 if any((output.content or "").strip() for output in closing_outputs) else 0.0
        score = 0.8 * coverage + 0.2 * closing_ready
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="workflow-business-outcome", score=score,
            passed=coverage == 1.0 and closing_ready == 1.0,
            confidence=1.0 if len(outputs) >= len(template) else coverage,
            dimensions={
                "stage_coverage": coverage, "covered_stages": sorted(stages),
                "stage_template": list(template), "closing_stage": closing_stage,
                "closing_count": len(closing_outputs), "closing_ready": bool(closing_ready),
            },
            rationale="全链路测试四阶段覆盖与收口产出可用性",
        )


class EvaluatorRegistry:
    def __init__(self):
        self._evaluators: list[Evaluator] = []

    def register(self, evaluator: Evaluator):
        self._evaluators.append(evaluator)
        return evaluator

    def all(self):
        return tuple(self._evaluators)


DEFAULT_EVALUATORS = EvaluatorRegistry()
for _evaluator in (
    SchemaEvaluator(), SensitiveDataEvaluator(), ReferenceValidityEvaluator(),
    ToolSuccessEvaluator(), TruncationEvaluator(), ParsabilityEvaluator(),
    RuleEvaluator(), JuryAggregator(),
    FeedbackOutcomeEvaluator(), WorkflowOutcomeEvaluator(),
):
    DEFAULT_EVALUATORS.register(_evaluator)


class LayeredEvaluationService:
    """执行不同数据源的分层评测，并将逐裁判证据持久化。"""

    @staticmethod
    @transaction.atomic
    def evaluate(*, evaluation_result, output, workflow_outputs=None, rubric=None, jury_votes=None,
                 jury_service=None):
        llm_evidences = []
        if jury_votes is None:
            from .llm_judges import LLMJuryService
            service = jury_service or LLMJuryService()
            expected_payload = getattr(getattr(evaluation_result, "case", None), "expected_payload", {})
            llm_votes = service.run(
                output=output, rubric=rubric, expected_payload=expected_payload,
            )
            jury_votes = [vote.aggregate_vote() for vote in llm_votes if vote.aggregate_vote()]
            llm_evidences = [
                EvaluationEvidence(
                    level="l1", evaluator_type="llm_judge", evaluator_version="llm-judge-v1",
                    judge_name=vote.judge_name, model_version=vote.model_version,
                    score=vote.score, passed=vote.passed, confidence=vote.confidence,
                    dimensions=vote.dimensions, evidence=vote.evidence, rationale=vote.rationale,
                    raw_output=vote.raw_output, status=vote.status, latency_ms=vote.latency_ms,
                    token_usage=vote.token_usage,
                ) for vote in llm_votes
            ]
        context = EvaluationContext(
            output=output, workflow_outputs=list(workflow_outputs or []),
            rubric=rubric, jury_votes=list(jury_votes or []),
        )
        evidences = [evaluator.evaluate(context) for evaluator in DEFAULT_EVALUATORS.all()]
        evidences[3:3] = llm_evidences
        JudgeResult.objects.filter(evaluation_result=evaluation_result).delete()
        for item in evidences:
            JudgeResult.objects.create(
                evaluation_result=evaluation_result, rubric=rubric, level=item.level,
                evaluator_type=item.evaluator_type, evaluator_version=item.evaluator_version,
                judge_name=item.judge_name, model_version=item.model_version,
                score=item.score, passed=item.passed, confidence=item.confidence,
                dimensions=item.dimensions, evidence=item.evidence, rationale=item.rationale,
                raw_output=item.raw_output, status=item.status,
                latency_ms=item.latency_ms, token_usage=item.token_usage,
            )

        by_level = {level: [item for item in evidences if item.level == level] for level in ("l0", "l1", "l2", "l3")}
        l0_items = by_level["l0"]
        l0 = min(item.score for item in l0_items if item.score is not None)
        rule = next(item for item in by_level["l1"] if item.evaluator_type == "rubric_rule")
        jury = next(item for item in by_level["l1"] if item.evaluator_type == "jury_aggregate")
        l1 = jury.score if jury.score is not None else rule.score
        l2 = by_level["l2"][0].score
        l3 = by_level["l3"][0].score
        evaluation_result.l0_score = l0
        evaluation_result.l1_score = l1
        evaluation_result.l2_score = l2
        evaluation_result.l3_score = l3
        evaluation_result.raw_scores = {
            "evaluator_version": "layered-v2",
            "levels": {
                level: [
                    {"type": item.evaluator_type, "score": item.score, "passed": item.passed,
                     "status": item.status, "confidence": item.confidence}
                    for item in items
                ]
                for level, items in by_level.items()
            },
        }
        evaluation_result.save(update_fields=[
            "l0_score", "l1_score", "l2_score", "l3_score", "raw_scores", "updated_at",
        ])
        return evaluation_result
