"""能力自进化引擎：把 CapabilityDefinition + 历史产出 + 反馈 连成闭环。

- single 模式：对单一阶段的历史产出做测评，按反馈信号打分。
- workflow 模式：按 workflow_id 聚合链路多阶段产出，按阶段权重加权打分。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from .capability_models import CapabilityDefinition, CapabilityRelease
from .evaluation_models import EvaluationResult, EvaluationRun
from .models import EvaluationCase, EvaluationSuite, FeedbackEvent, GenerationOutput, RetrievalTrace


# workflow 阶段默认权重：越靠近最终业务结果权重越高
DEFAULT_STAGE_WEIGHTS = {
    "risk_identification": 0.15,
    "test_plan_generation": 0.20,
    "testcase_generation": 0.25,
    "test_execution": 0.25,
    "issue_tracking": 0.15,
}

# 反馈信号 → L1 质量分映射
SIGNAL_SCORES = {
    "accepted": 1.0,
    "merged": 1.0,
    "test_passed": 1.0,
    "defect_confirmed": 1.0,
    "edited": 0.7,
    "rejected": 0.0,
    "test_failed": 0.0,
    "false_positive": 0.0,
    "missed": 0.0,
    "reverted": 0.0,
}

POSITIVE_SIGNALS = {"accepted", "merged", "test_passed", "defect_confirmed", "edited"}
NEGATIVE_SIGNALS = {"rejected", "test_failed", "false_positive", "missed", "reverted"}


@dataclass(frozen=True)
class EvolutionReport:
    suite: EvaluationSuite
    run: EvaluationRun
    case_count: int
    created_release: CapabilityRelease | None
    gate_passed: bool | None


class CapabilityEvolutionService:
    """按能力定义自动从历史产出构建评测集、打分、生成发布单元。"""

    def __init__(self, definition: CapabilityDefinition):
        self.definition = definition
        self.project = definition.project

    # ------------------------------------------------------------------ 入口

    def run_evolution(
        self,
        *,
        name: str = "",
        triggered_by=None,
        baseline_release: CapabilityRelease | None = None,
        candidate_config: dict[str, Any] | None = None,
        version: str = "",
    ) -> EvolutionReport:
        """端到端执行一次自进化：建/取 suite → 创建 run → 打分 → 生成 candidate release。"""
        suite = self._ensure_suite()
        run = self._create_and_score_run(suite, name=name, triggered_by=triggered_by)

        created_release = None
        gate_passed = None
        if candidate_config is not None:
            from .capabilities import CapabilityReleaseService
            created_release = CapabilityReleaseService.create(
                project=self.project,
                kind=self.definition.kind,
                name=self.definition.name,
                version=version or f"auto-{timezone.now().strftime('%Y%m%d-%H%M%S')}",
                config=candidate_config,
                actor=triggered_by,
            )
            if baseline_release and baseline_release.candidate_run:
                gate_report = CapabilityReleaseService.evaluate_shadow(
                    created_release,
                    baseline_run=baseline_release.candidate_run,
                    candidate_run=run,
                    **self._gate_kwargs(),
                )
                gate_passed = gate_report.get("passed", False)
        return EvolutionReport(
            suite=suite, run=run, case_count=run.results.count(),
            created_release=created_release, gate_passed=gate_passed,
        )

    # ------------------------------------------------------------------ suite / case

    def _ensure_suite(self) -> EvaluationSuite:
        if self.definition.default_suite_id:
            return self.definition.default_suite
        suite, _ = EvaluationSuite.objects.get_or_create(
            project=self.project,
            name=f"{self.definition.name}-auto",
            suite_type="regression",
            task_type=self.definition.stages[0],
            defaults={
                "description": f"能力 [{self.definition.name}] 自动生成的回归评测集",
                "split_ratio": {"gold": 0.0, "regression": 1.0, "fresh": 0.0, "challenge": 0.0},
            },
        )
        self.definition.default_suite = suite
        self.definition.save(update_fields=["default_suite", "updated_at"])
        return suite

    def _create_and_score_run(
        self, suite: EvaluationSuite, *, name: str = "", triggered_by=None
    ) -> EvaluationRun:
        run = EvaluationRun.objects.create(
            suite=suite,
            name=name or f"evolution-{timezone.now().strftime('%Y%m%d-%H%M%S')}",
            config={"capability_id": str(self.definition.id), "stages": self.definition.stages},
            status="running",
            triggered_by=triggered_by,
            started_at=timezone.now(),
        )
        if self.definition.evaluation_mode == "workflow":
            self._score_workflow_outputs(run)
        else:
            self._score_single_outputs(run)
        self._summarize(run)
        return run

    def _score_single_outputs(self, run: EvaluationRun):
        """single 模式：每个符合能力阶段的产出生成一条 case。"""
        outputs = self._fetch_outputs()
        for index, output in enumerate(outputs, start=1):
            case, _ = EvaluationCase.objects.get_or_create(
                suite=run.suite,
                case_number=index,
                defaults={
                    "task_type": output.task_type,
                    "input_payload": {"query": output.trace.query, "task_id": output.task_id},
                    "expected_payload": {},
                    "source_trace": output.trace,
                    "source_output": output,
                    "split": "regression",
                },
            )
            self._write_result(run, case, output)

    def _score_workflow_outputs(self, run: EvaluationRun):
        """workflow 模式：按 workflow_id 聚合各阶段产出为一条 case。"""
        raw_ids = (
            GenerationOutput.objects.filter(
                project=self.project, task_type__in=self.definition.stages,
                capability_id=self.definition.id,
            )
            .exclude(metadata__protocol__workflow_id="")
            .values_list("metadata__protocol__workflow_id", flat=True)
        )
        workflow_ids = sorted({str(wid) for wid in raw_ids if wid})
        for index, workflow_id in enumerate(workflow_ids, start=1):
            outputs = GenerationOutput.objects.filter(
                project=self.project,
                task_type__in=self.definition.stages,
                capability_id=self.definition.id,
                metadata__protocol__workflow_id=workflow_id,
            ).select_related("trace").order_by("created_at")
            if not outputs.exists():
                continue
            case, _ = EvaluationCase.objects.get_or_create(
                suite=run.suite,
                case_number=index,
                defaults={
                    "task_type": self.definition.stages[0],
                    "input_payload": {"workflow_id": workflow_id, "stages": self.definition.stages},
                    "expected_payload": {},
                    "split": "regression",
                },
            )
            self._write_workflow_result(run, case, workflow_id, outputs)

    # ------------------------------------------------------------------ 打分

    def _write_result(self, run: EvaluationRun, case: EvaluationCase, output: GenerationOutput):
        score, detail = self._output_score(output)
        result, _ = EvaluationResult.objects.get_or_create(run=run, case=case)
        result.status = "completed"
        result.predicted_payload = {"output_id": str(output.id), **detail}
        result.l0_score = score
        result.l1_score = score
        result.l2_score = score
        result.l3_score = score
        result.latency_ms = (output.trace.timings or {}).get("total_ms", 0)
        result.token_usage = output.trace.token_usage or 0
        result.raw_scores = detail
        result.save()

    def _write_workflow_result(
        self, run: EvaluationRun, case: EvaluationCase, workflow_id: str, outputs
    ):
        total_weight = 0.0
        weighted_score = 0.0
        stage_scores = {}
        for output in outputs:
            stage = output.task_type
            weight = self._stage_weight(stage)
            score, detail = self._output_score(output)
            stage_scores[stage] = {"score": score, "output_id": str(output.id), **detail}
            total_weight += weight
            weighted_score += weight * score
        final_score = weighted_score / total_weight if total_weight else 0.5
        result, _ = EvaluationResult.objects.get_or_create(run=run, case=case)
        result.status = "completed"
        result.predicted_payload = {"workflow_id": workflow_id, "stage_scores": stage_scores}
        result.l0_score = final_score
        result.l1_score = final_score
        result.l2_score = final_score
        result.l3_score = final_score
        result.raw_scores = stage_scores
        result.save()

    def _output_score(self, output: GenerationOutput) -> tuple[float, dict[str, Any]]:
        signals = list(FeedbackEvent.objects.filter(output=output).values_list("signal", flat=True))
        if not signals:
            return 0.5, {"signal_count": 0, "signals": [], "reason": "no_feedback"}
        positive = sum(1 for s in signals if s in POSITIVE_SIGNALS)
        negative = sum(1 for s in signals if s in NEGATIVE_SIGNALS)
        if positive and not negative:
            score = 1.0
        elif negative and not positive:
            score = 0.0
        elif positive and negative:
            score = positive / (positive + negative)
        else:
            score = 0.5
        return score, {"signal_count": len(signals), "signals": signals, "positive": positive, "negative": negative}

    # ------------------------------------------------------------------ 工具

    def _fetch_outputs(self):
        qs = GenerationOutput.objects.filter(
            project=self.project, task_type__in=self.definition.stages
        ).select_related("trace")
        if self.definition.evaluation_mode == "single":
            qs = qs.filter(capability_id=self.definition.id)
        return qs.order_by("-created_at")[:200]

    def _stage_weight(self, stage: str) -> float:
        rules = self.definition.gate_rules or {}
        weights = rules.get("stage_weights") or DEFAULT_STAGE_WEIGHTS
        return float(weights.get(stage, 1.0 / len(self.definition.stages)))

    def _gate_kwargs(self) -> dict[str, Any]:
        rules = self.definition.gate_rules or {}
        return {
            "min_mean_diff": float(rules.get("min_mean_diff", 0.0)),
            "max_latency_regression": float(rules.get("max_latency_regression", 0.2)),
            "max_token_regression": float(rules.get("max_token_regression", 0.2)),
        }

    @staticmethod
    def _summarize(run: EvaluationRun):
        results = list(run.results.filter(status="completed"))
        if not results:
            run.status = "completed"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return
        scores = [r.l1_score for r in results if r.l1_score is not None]
        run.metrics_summary = {
            "l0_score": _mean(scores),
            "l1_score": _mean(scores),
            "l2_score": _mean(scores),
            "l3_score": _mean(scores),
            "sample_count": len(results),
        }
        run.cost_summary = {
            "total_tokens": sum(r.token_usage for r in results),
            "total_latency_ms": sum(r.latency_ms for r in results),
        }
        run.status = "completed"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "metrics_summary", "cost_summary", "finished_at", "updated_at"])


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
