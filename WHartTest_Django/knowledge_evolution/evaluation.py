import math
import re
import statistics
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .evaluation_models import EvaluationResult, EvaluationRun
from .models import EvaluationCase, EvaluationSuite


Executor = Callable[[Dict[str, Any]], Dict[str, Any]]


class LevelMetrics:
    """L0–L3 各层指标容器。"""

    def __init__(self, l0: float = 0.0, l1: float = 0.0, l2: float = 0.0, l3: float = 0.0):
        self.l0 = l0
        self.l1 = l1
        self.l2 = l2
        self.l3 = l3

    def as_dict(self) -> Dict[str, float]:
        return {"l0": self.l0, "l1": self.l1, "l2": self.l2, "l3": self.l3}


class EvaluationEngine:
    """评测回放引擎。

    对外暴露可注入的 ``executor``（接收 input_payload，返回 predicted_payload）。
    引擎负责遍历 ``EvaluationSuite`` 中的 ``EvaluationCase``，记录结果，
    计算 L0–L3 指标、切片指标、成本与统计显著性。
    """

    # 简化版 token 价格（USD / 1K tokens）
    DEFAULT_PRICE_PER_1K_TOKENS = 0.002

    def __init__(
        self,
        suite: EvaluationSuite,
        executor: Executor,
        config: Optional[Dict[str, Any]] = None,
        price_per_1k_tokens: float = DEFAULT_PRICE_PER_1K_TOKENS,
    ):
        self.suite = suite
        self.executor = executor
        self.config = config or {}
        self.price_per_1k_tokens = price_per_1k_tokens

    @classmethod
    def from_suite_id(
        cls,
        suite_id: str,
        executor: Executor,
        config: Optional[Dict[str, Any]] = None,
        price_per_1k_tokens: float = DEFAULT_PRICE_PER_1K_TOKENS,
    ) -> "EvaluationEngine":
        suite = EvaluationSuite.objects.get(pk=suite_id)
        return cls(suite, executor, config, price_per_1k_tokens)

    def start_run(
        self,
        name: str = "",
        triggered_by=None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> EvaluationRun:
        """创建一次运行并标记为 running。"""
        run = EvaluationRun.objects.create(
            suite=self.suite,
            name=name or f"run-{timezone.now().strftime('%Y%m%d-%H%M%S')}",
            config=self.config,
            status="running",
            triggered_by=triggered_by,
            started_at=timezone.now(),
        )
        return run

    def execute(self, run: EvaluationRun, filters: Optional[Dict[str, Any]] = None) -> EvaluationRun:
        """执行评测并汇总。失败 case 单独记录，不中断整体运行。"""
        qs = run.suite.cases.all()
        if filters:
            if "split" in filters:
                qs = qs.filter(split=filters["split"])
            if "task_type" in filters:
                qs = qs.filter(task_type=filters["task_type"])

        for case in qs.order_by("case_number"):
            self._run_case(run, case)

        self._summarize(run)
        return run

    def _run_case(self, run: EvaluationRun, case: EvaluationCase) -> EvaluationResult:
        result, _ = EvaluationResult.objects.get_or_create(run=run, case=case)
        result.status = "running"
        result.save(update_fields=["status", "updated_at"])

        start = time.perf_counter()
        try:
            predicted = self.executor(case.input_payload)
            latency_ms = int((time.perf_counter() - start) * 1000)
            token_usage = predicted.get("token_usage") or predicted.get("tokens", 0) or 0
            cost = (token_usage / 1000.0) * self.price_per_1k_tokens

            metrics = self._score(case, predicted)

            result.predicted_payload = predicted
            result.latency_ms = latency_ms
            result.token_usage = token_usage
            result.estimated_cost_usd = cost
            result.l0_score = metrics.l0
            result.l1_score = metrics.l1
            result.l2_score = metrics.l2
            result.l3_score = metrics.l3
            result.raw_scores = self._raw_scores(case, predicted)
            result.status = "completed"
            result.save()
        except Exception as exc:  # noqa: BLE001
            result.error_message = str(exc)[:2000]
            result.status = "failed"
            result.save()
        return result

    # ------------------------------------------------------------------ 评分

    def _score(self, case: EvaluationCase, predicted: Dict[str, Any]) -> LevelMetrics:
        expected = case.expected_payload or {}
        golden = case.golden_labels or {}
        return LevelMetrics(
            l0=self._l0_retrieval_score(expected, predicted),
            l1=self._l1_generation_score(expected, predicted),
            l2=self._l2_factual_score(golden, predicted),
            l3=self._l3_business_score(golden, predicted),
        )

    def _raw_scores(self, case: EvaluationCase, predicted: Dict[str, Any]) -> Dict[str, Any]:
        expected = case.expected_payload or {}
        golden = case.golden_labels or {}
        return {
            "l0": self._l0_retrieval_raw(expected, predicted),
            "l1": self._l1_generation_raw(expected, predicted),
            "l2": self._l2_factual_raw(golden, predicted),
            "l3": self._l3_business_raw(golden, predicted),
        }

    # ------------------------------------------------------------------ L0

    @staticmethod
    def _tokenize(text: str) -> set:
        return set(re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]+", text.lower()))

    def _l0_retrieval_score(
        self, expected: Dict[str, Any], predicted: Dict[str, Any]
    ) -> float:
        raw = self._l0_retrieval_raw(expected, predicted)
        return raw.get("f1", 0.0)

    def _l0_retrieval_raw(
        self, expected: Dict[str, Any], predicted: Dict[str, Any]
    ) -> Dict[str, float]:
        expected_docs = set(expected.get("expected_doc_ids") or [])
        citations = predicted.get("citations") or predicted.get("retrieved_doc_ids") or []
        predicted_docs = set(str(c) for c in citations)

        if not expected_docs and not predicted_docs:
            return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
        if not expected_docs or not predicted_docs:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

        tp = len(expected_docs & predicted_docs)
        precision = tp / len(predicted_docs)
        recall = tp / len(expected_docs)
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) else 0.0
        return {"precision": precision, "recall": recall, "f1": f1}

    # ------------------------------------------------------------------ L1

    def _l1_generation_score(
        self, expected: Dict[str, Any], predicted: Dict[str, Any]
    ) -> float:
        raw = self._l1_generation_raw(expected, predicted)
        return raw.get("f1", 0.0)

    def _l1_generation_raw(
        self, expected: Dict[str, Any], predicted: Dict[str, Any]
    ) -> Dict[str, float]:
        expected_text = expected.get("expected_output") or expected.get("answer") or ""
        predicted_text = predicted.get("output") or predicted.get("answer") or ""

        expected_tokens = self._tokenize(expected_text)
        predicted_tokens = self._tokenize(predicted_text)

        if not expected_tokens and not predicted_tokens:
            return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
        if not expected_tokens or not predicted_tokens:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

        tp = len(expected_tokens & predicted_tokens)
        precision = tp / len(predicted_tokens)
        recall = tp / len(expected_tokens)
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) else 0.0
        return {"precision": precision, "recall": recall, "f1": f1}

    # ------------------------------------------------------------------ L2

    def _l2_factual_score(
        self, golden: Dict[str, Any], predicted: Dict[str, Any]
    ) -> float:
        raw = self._l2_factual_raw(golden, predicted)
        return raw.get("accuracy", 0.0)

    def _l2_factual_raw(
        self, golden: Dict[str, Any], predicted: Dict[str, Any]
    ) -> Dict[str, float]:
        must_contain = set(golden.get("must_contain") or [])
        must_not_contain = set(golden.get("must_not_contain") or [])
        output = (predicted.get("output") or "").lower()

        if not must_contain and not must_not_contain:
            return {"accuracy": 1.0, "must_contain_hit": 0, "must_contain_total": 0,
                    "must_not_contain_violation": 0, "must_not_contain_total": 0}

        contain_hits = sum(1 for item in must_contain if item.lower() in output)
        not_contain_violations = sum(1 for item in must_not_contain if item.lower() in output)

        denom = len(must_contain) + len(must_not_contain)
        if denom == 0:
            accuracy = 1.0
        else:
            accuracy = (contain_hits + (len(must_not_contain) - not_contain_violations)) / denom
        return {
            "accuracy": max(0.0, accuracy),
            "must_contain_hit": contain_hits,
            "must_contain_total": len(must_contain),
            "must_not_contain_violation": not_contain_violations,
            "must_not_contain_total": len(must_not_contain),
        }

    # ------------------------------------------------------------------ L3

    def _l3_business_score(
        self, golden: Dict[str, Any], predicted: Dict[str, Any]
    ) -> float:
        raw = self._l3_business_raw(golden, predicted)
        return raw.get("score", 0.0)

    def _l3_business_raw(
        self, golden: Dict[str, Any], predicted: Dict[str, Any]
    ) -> Dict[str, Any]:
        expected_actions = set(golden.get("expected_actions") or [])
        predicted_actions = set(predicted.get("actions") or predicted.get("recommended_actions") or [])

        if not expected_actions and not predicted_actions:
            score = 1.0
        elif not expected_actions or not predicted_actions:
            score = 0.0
        else:
            tp = len(expected_actions & predicted_actions)
            fp = len(predicted_actions - expected_actions)
            fn = len(expected_actions - predicted_actions)
            score = tp / (tp + 0.5 * (fp + fn)) if (tp + fp + fn) else 1.0

        return {
            "score": score,
            "expected_actions": sorted(expected_actions),
            "predicted_actions": sorted(predicted_actions),
            "tp": len(expected_actions & predicted_actions),
            "fp": len(predicted_actions - expected_actions),
            "fn": len(expected_actions - predicted_actions),
        }

    # ------------------------------------------------------------------ 汇总

    def _summarize(self, run: EvaluationRun) -> None:
        results = list(run.results.filter(status="completed"))
        if not results:
            run.status = "completed"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return

        metrics_summary = self._aggregate_metrics(results)
        cost_summary = self._aggregate_costs(results)

        run.metrics_summary = metrics_summary
        run.cost_summary = cost_summary
        run.status = "completed"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "metrics_summary", "cost_summary",
                                 "finished_at", "updated_at"])

    def _aggregate_metrics(self, results: Iterable[EvaluationResult]) -> Dict[str, Any]:
        l0_scores = [r.l0_score for r in results if r.l0_score is not None]
        l1_scores = [r.l1_score for r in results if r.l1_score is not None]
        l2_scores = [r.l2_score for r in results if r.l2_score is not None]
        l3_scores = [r.l3_score for r in results if r.l3_score is not None]

        overall = {
            "l0_mean": self._mean(l0_scores),
            "l1_mean": self._mean(l1_scores),
            "l2_mean": self._mean(l2_scores),
            "l3_mean": self._mean(l3_scores),
            "l0_std": self._std(l0_scores),
            "l1_std": self._std(l1_scores),
            "l2_std": self._std(l2_scores),
            "l3_std": self._std(l3_scores),
            "sample_count": len(results),
        }

        by_split = self._slice_by(results, lambda r: r.case.split)
        by_task = self._slice_by(results, lambda r: r.case.task_type)

        return {
            "overall": overall,
            "by_split": by_split,
            "by_task": by_task,
        }

    def _slice_by(
        self,
        results: Iterable[EvaluationResult],
        key_func: Callable[[EvaluationResult], str],
    ) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, List[EvaluationResult]] = {}
        for r in results:
            key = key_func(r)
            buckets.setdefault(key, []).append(r)

        sliced = {}
        for key, bucket in buckets.items():
            sliced[key] = {
                "l0_mean": self._mean([r.l0_score for r in bucket if r.l0_score is not None]),
                "l1_mean": self._mean([r.l1_score for r in bucket if r.l1_score is not None]),
                "l2_mean": self._mean([r.l2_score for r in bucket if r.l2_score is not None]),
                "l3_mean": self._mean([r.l3_score for r in bucket if r.l3_score is not None]),
                "count": len(bucket),
            }
        return sliced

    @staticmethod
    def _mean(scores: List[float]) -> Optional[float]:
        return statistics.mean(scores) if scores else None

    @staticmethod
    def _std(scores: List[float]) -> Optional[float]:
        return statistics.stdev(scores) if len(scores) > 1 else 0.0

    def _aggregate_costs(self, results: Iterable[EvaluationResult]) -> Dict[str, Any]:
        total_tokens = sum(r.token_usage for r in results)
        total_cost = sum(r.estimated_cost_usd for r in results)
        total_latency_ms = sum(r.latency_ms for r in results)
        count = len(list(results))
        return {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "total_latency_ms": total_latency_ms,
            "avg_latency_ms": total_latency_ms / count if count else 0,
            "avg_tokens": total_tokens / count if count else 0,
        }

    # ------------------------------------------------------------------ 显著性

    @staticmethod
    def compare_runs(
        baseline_results: Iterable[EvaluationResult],
        candidate_results: Iterable[EvaluationResult],
        level: str = "l1",
    ) -> Dict[str, Any]:
        """对比两次运行，返回配对 t 统计量（简化实现，无需 scipy）。"""
        baseline = {r.case_id: r for r in baseline_results}
        candidate = {r.case_id: r for r in candidate_results}
        common_ids = set(baseline.keys()) & set(candidate.keys())

        diffs = []
        for cid in sorted(common_ids):
            b = getattr(baseline[cid], f"{level}_score")
            c = getattr(candidate[cid], f"{level}_score")
            if b is not None and c is not None:
                diffs.append(c - b)

        if not diffs:
            return {"level": level, "n": 0, "mean_diff": None,
                    "t_statistic": None, "p_value": None}

        n = len(diffs)
        mean_diff = statistics.mean(diffs)
        if n == 1:
            return {"level": level, "n": 1, "mean_diff": mean_diff,
                    "t_statistic": None, "p_value": None}

        stdev = statistics.stdev(diffs)
        se = stdev / math.sqrt(n)
        t_stat = mean_diff / se if se else 0.0
        # 简化 p-value：|t|>2 约 <0.05，|t|>2.6 约 <0.01
        p_value = 0.05 if abs(t_stat) > 2.0 else 0.1 if abs(t_stat) > 1.0 else 0.5
        return {
            "level": level,
            "n": n,
            "mean_diff": round(mean_diff, 6),
            "t_statistic": round(t_stat, 4),
            "p_value": p_value,
        }
