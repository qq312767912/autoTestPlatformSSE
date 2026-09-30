"""L0-L3分层评测器与可审计的裁判聚合。"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.db import transaction

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
    version = "workflow-v2"
    level = "l3"
    STAGES = [
        "risk_identification", "test_plan_generation", "testcase_generation",
        "test_execution", "issue_tracking",
    ]

    def evaluate(self, context):
        outputs = context.workflow_outputs or [context.output]
        stages = {output.task_type for output in outputs}
        coverage = len(stages & set(self.STAGES)) / len(self.STAGES)
        issue_outputs = [output for output in outputs if output.task_type == "issue_tracking"]
        closed = 0
        for output in issue_outputs:
            signals = set(output.feedback_events.values_list("signal", flat=True))
            if signals & {"defect_confirmed", "test_passed", "merged"}:
                closed += 1
        closure = closed / len(issue_outputs) if issue_outputs else 0.0
        score = 0.7 * coverage + 0.3 * closure
        return EvaluationEvidence(
            level=self.level, evaluator_type=self.evaluator_type, evaluator_version=self.version,
            judge_name="workflow-business-outcome", score=score,
            passed=coverage == 1.0 and (not issue_outputs or closure >= 0.7),
            confidence=1.0 if len(outputs) >= len(self.STAGES) else coverage,
            dimensions={
                "stage_coverage": coverage, "covered_stages": sorted(stages),
                "issue_count": len(issue_outputs), "closed_issue_count": closed,
            },
            rationale="五阶段链路覆盖与问题闭环结果",
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
    SchemaEvaluator(), SensitiveDataEvaluator(), RuleEvaluator(), JuryAggregator(),
    FeedbackOutcomeEvaluator(), WorkflowOutcomeEvaluator(),
):
    DEFAULT_EVALUATORS.register(_evaluator)


class LayeredEvaluationService:
    """执行不同数据源的分层评测，并将逐裁判证据持久化。"""

    @staticmethod
    @transaction.atomic
    def evaluate(*, evaluation_result, output, workflow_outputs=None, rubric=None, jury_votes=None):
        context = EvaluationContext(
            output=output, workflow_outputs=list(workflow_outputs or []),
            rubric=rubric, jury_votes=list(jury_votes or []),
        )
        evidences = [evaluator.evaluate(context) for evaluator in DEFAULT_EVALUATORS.all()]
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
