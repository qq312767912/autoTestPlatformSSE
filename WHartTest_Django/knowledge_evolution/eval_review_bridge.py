from typing import Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from .evaluation_models import EvaluationResult, EvaluationRun
from .knowledge_models import KnowledgeCandidate, KnowledgeVersion
from .models import GenerationOutput, RetrievalTrace


class EvaluationReviewBridge:
    """从评测失败样本生成待复核候选（任务 12）。"""

    DEFAULT_THRESHOLDS = {"l0": 0.5, "l1": 0.5, "l2": 0.5, "l3": 0.5}

    def __init__(
        self,
        run: EvaluationRun,
        thresholds: Optional[Dict[str, float]] = None,
        min_failure_count: int = 1,
    ):
        self.run = run
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS
        self.min_failure_count = min_failure_count

    def find_failures(
        self,
        filters: Optional[Dict[str, str]] = None,
    ) -> List[EvaluationResult]:
        """按阈值筛选失败的 EvaluationResult。"""
        qs = self.run.results.filter(status="completed")
        if filters:
            if "split" in filters:
                qs = qs.filter(case__split=filters["split"])
            if "task_type" in filters:
                qs = qs.filter(case__task_type=filters["task_type"])

        failures = []
        for result in qs.select_related("case"):
            for level, threshold in self.thresholds.items():
                score = getattr(result, f"{level}_score")
                if score is not None and score < threshold:
                    failures.append(result)
                    break
        return failures

    @transaction.atomic
    def generate_candidates(
        self,
        actor=None,
        reason_template: str = "评测 run {run_id} 中 {level} 得分 {score:.2f}，低于阈值 {threshold:.2f}",
    ) -> List[KnowledgeCandidate]:
        """为每个失败样本生成 KnowledgeCandidate（origin=evaluation_failure）。"""
        failures = self.find_failures()
        if len(failures) < self.min_failure_count:
            return []

        created = []
        project = self.run.suite.project

        for result in failures:
            case = result.case
            level, score, threshold = self._primary_failure_level(result)

            # 尝试溯源到版本（优先取 golden_labels 中的 version_id）
            version_id = (case.golden_labels or {}).get("version_id")
            version = None
            if version_id:
                version = KnowledgeVersion.objects.filter(pk=version_id).first()

            trace = case.source_trace
            output = case.source_output

            # 证据：把 trace/output/result 作为候选证据
            evidence = []
            if trace:
                evidence.append({
                    "type": "retrieval_trace",
                    "trace_id": str(trace.id),
                    "query": trace.query,
                })
            if output:
                evidence.append({
                    "type": "generation_output",
                    "output_id": str(output.id),
                    "output_hash": output.output_hash,
                })
            evidence.append({
                "type": "evaluation_result",
                "result_id": str(result.id),
                "level": level,
                "score": score,
                "threshold": threshold,
            })

            kind = self._infer_kind(case.task_type)
            payload = {
                "input": case.input_payload,
                "expected": case.expected_payload,
                "predicted": result.predicted_payload,
                "failure_level": level,
                "score": score,
                "threshold": threshold,
            }

            normalized = self._normalize_for_dedup(payload)
            dedup_key = KnowledgeCandidate.build_dedup_key(
                project.id, kind, normalized
            )

            candidate, is_new = KnowledgeCandidate.objects.get_or_create(
                dedup_key=dedup_key,
                defaults={
                    "project": project,
                    "kind": kind,
                    "origin": "evaluation_failure",
                    "payload": payload,
                    "level": "L2",
                    "confidence": score,
                    "source_snapshot": version.source_snapshot if version else None,
                    "evidence": evidence,
                    "state": "pending",
                    "review_reason": reason_template.format(
                        run_id=str(self.run.id),
                        level=level,
                        score=score,
                        threshold=threshold,
                    ),
                    "created_by": actor,
                    "extracted_by": "EvaluationReviewBridge",
                },
            )
            if not is_new:
                # 已存在则追加最新证据并刷新原因
                candidate.evidence = evidence
                candidate.review_reason = reason_template.format(
                    run_id=str(self.run.id),
                    level=level,
                    score=score,
                    threshold=threshold,
                )
                candidate.save(update_fields=["evidence", "review_reason", "updated_at"])
            created.append(candidate)

        return created

    def _primary_failure_level(
        self, result: EvaluationResult
    ) -> tuple:
        """返回最显著的失败层级 (level, score, threshold)。"""
        worst = None
        for level in ["l0", "l1", "l2", "l3"]:
            if level not in self.thresholds:
                continue
            score = getattr(result, f"{level}_score")
            threshold = self.thresholds[level]
            if score is not None and score < threshold:
                if worst is None or (threshold - score) > (worst[2] - worst[1]):
                    worst = (level, score, threshold)
        if worst:
            return worst
        # 没有任何层级越界时，返回第一个可用阈值作为兜底
        first_level = next(iter(self.thresholds))
        return first_level, 0.0, self.thresholds[first_level]

    @staticmethod
    def _infer_kind(task_type: str) -> str:
        mapping = {
            "knowledge_query": "concept",
            "code_review": "rule",
            "test_execution": "rule",
            "testcase_generation": "rule",
        }
        return mapping.get(task_type, "knowledge_atom")

    @staticmethod
    def _normalize_for_dedup(payload: dict) -> str:
        import hashlib, json

        normalized = {
            "input": payload.get("input"),
            "failure_level": payload.get("failure_level"),
        }
        text = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def build_review_report(self) -> dict:
        """生成失败样本归因报告，用于测评界面展示。"""
        failures = self.find_failures()
        by_level = {"l0": [], "l1": [], "l2": [], "l3": []}
        for result in failures:
            level, score, threshold = self._primary_failure_level(result)
            by_level[level].append({
                "case_number": result.case.case_number,
                "split": result.case.split,
                "task_type": result.case.task_type,
                "level": level,
                "score": score,
                "threshold": threshold,
                "trace_id": str(result.case.source_trace_id) if result.case.source_trace_id else None,
                "output_id": str(result.case.source_output_id) if result.case.source_output_id else None,
            })

        return {
            "run_id": str(self.run.id),
            "suite_id": str(self.run.suite_id),
            "thresholds": self.thresholds,
            "total_failures": len(failures),
            "by_level": {level: items for level, items in by_level.items() if items},
            "sample_links": [
                {
                    "result_id": str(r.id),
                    "case_number": r.case.case_number,
                    "split": r.case.split,
                    "predicted": r.predicted_payload,
                }
                for r in failures[:10]
            ],
        }
