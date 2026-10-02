"""T09：统一能力评测分区与硬门禁。

设计要点（为什么这样做，而不只是"多写几个 if"）：

1. **分区是硬约束，不是标签。** 需求 R7 要求候选必须跑完「金标、回归、新鲜、挑战、隐藏」
   五类分区，而实验服务里的 ``run_full_race`` 也强制五分区齐全。这里把同一份分区口径
   抽成唯一真值 ``PARTITION_ORDER``，让"缺哪个分区"能被明确报出来，而不是让门禁默默通过。
2. **漏报 / 误报必须来自客观信号，不能由评测者自述。** 分母取
   ``TP = defect_confirmed + accepted + test_passed``（确认有效的发现），
   于是 ``误报率 = FP / (FP + TP)``、``漏报率 = missed / (missed + TP)``，
   即"报了的问题里有多少是假的"和"真问题里漏了多少"。用评测集的自评分算不出这两个数。
3. **基线不可比 = 门禁不通过**，而不是"跳过比较"。候选若悄悄换掉了评测集，指标一定好看，
   所以先校验两次评测跑在同一个套件、且候选**没有缩小**样本集合。
4. **门禁结论落成不可变快照**（``EvaluationGateSnapshot``），审批引用快照哈希。
   这样"审批时看到的指标"和"事后能复查的指标"是同一份字节。

本模块只做判定与落证据，不做状态流转；状态流转统一在 ``capabilities.py``。
"""
from __future__ import annotations

import hashlib
import json
import statistics

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count

from .capability_registry import (
    PARTITION_LABELS,
    PARTITION_ORDER,
    SHADOW_PARTITION,
    required_partitions,
)
from .evaluation_models import EvaluationResult, EvaluationRun
from .gate_models import EvaluationGateSnapshot
from .models import FeedbackEvent, GenerationOutput

#: 客观信号分组。分母口径见模块文档第 2 条。
POSITIVE_SIGNALS = ("accepted", "merged", "test_passed", "defect_confirmed")
#: 误报：平台报了问题，实际不是问题。
FALSE_POSITIVE_SIGNALS = ("false_positive",)
#: 漏报：实际有问题，平台没报出来。
MISSED_SIGNALS = ("missed",)

DEFAULT_THRESHOLDS = {
    # 候选自身的质量下限。
    "min_quality": 0.70,
    # 相对基线的质量下降上限（绝对值，不是百分比）。
    "max_quality_regression": 0.05,
    "max_false_negative_rate": 0.20,
    "max_false_positive_rate": 0.20,
    # 相对基线的耗时/Token 涨幅上限（比例）。
    "max_latency_regression": 0.20,
    "max_token_regression": 0.20,
    "min_stability": 0.60,
    "min_sample_count": 1,
    "min_partition_cases": 1,
    # 是否要求五分区齐全。T14 单能力闭环只需 gold+regression，可用 False 放宽。
    "require_all_partitions": True,
}

#: 硬门禁编码 → 中文说明。前端"缺失条件"直接读这份映射，避免中英文在两处漂移。
GATE_LABELS = {
    "partitions_complete": "评测分区不完整",
    "sample_count": "样本数不足",
    "baseline_comparable": "基线与候选不可比",
    "baseline_present": "缺少基线评测",
    "candidate_run_present": "缺少候选评测",
    "quality_floor": "候选质量未达下限",
    "quality_regression": "质量相对基线倒退",
    "false_negative_rate": "漏报率超限",
    "false_positive_rate": "误报率超限",
    "latency_regression": "耗时回归超限",
    "token_regression": "Token 回归超限",
    "stability": "评测稳定性不足",
}


def _rate(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None（"没有信号"≠"率是 0"）。

    区分这两者是必要的：没有信号时记 0 会让"零样本候选"看起来完美，
    从而通过门禁。返回 None 后由调用方决定按 ``needs_review`` 处理。
    """
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def required_for_run(run: EvaluationRun) -> tuple:
    """按能力注册表推出该次评测应具备的分区。"""
    if run is None:
        return PARTITION_ORDER
    task_type = getattr(run.suite, "task_type", "") or ""
    return required_partitions(task_type)


class RunMetricsService:
    """把一次 ``EvaluationRun`` 折算成统一的六维指标。"""

    #: 输出为"发现问题"型能力（误报/漏报才有意义）的能力集合。
    FINDING_TASK_TYPES = frozenset({
        "case_review", "code_review", "risk_identification", "issue_tracking",
    })

    @classmethod
    def summarize(cls, run: EvaluationRun) -> dict:
        results = list(
            EvaluationResult.objects.filter(run=run, status="completed")
            .select_related("case")
        )
        scores = [float(item.l1_score) for item in results if item.l1_score is not None]
        latencies = [int(item.latency_ms or 0) for item in results]
        tokens = [int(item.token_usage or 0) for item in results]

        metrics = {
            "sample_count": len(results),
            "quality": round(sum(scores) / len(scores), 6) if scores else None,
            "quality_min": min(scores) if scores else None,
            "quality_stdev": round(statistics.pstdev(scores), 6) if len(scores) > 1 else (0.0 if scores else None),
            # 稳定性 = 1 - l1 分数的总体标准差（满分 1.0 归一）。分数越集中越稳定。
            "stability": None,
            "latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "token_usage": round(sum(tokens) / len(tokens), 2) if tokens else None,
            "token_total": sum(tokens),
            "latency_total_ms": sum(latencies),
        }
        if metrics["quality_stdev"] is not None:
            metrics["stability"] = round(max(0.0, min(1.0, 1.0 - metrics["quality_stdev"])), 6)

        # 逐层均分：门禁与前端指标对比都要看，但只有 l1 参与质量门禁。
        for level in range(4):
            aggregate = EvaluationResult.objects.filter(run=run, status="completed").aggregate(
                value=Avg(f"l{level}_score")
            )["value"]
            metrics[f"average_l{level}"] = round(float(aggregate), 6) if aggregate is not None else None

        metrics.update(cls.signal_metrics(run))
        return metrics

    @classmethod
    def signal_metrics(cls, run: EvaluationRun) -> dict:
        """从产出反馈里折算漏报率 / 误报率 / 采纳率。"""
        output_ids = list(
            EvaluationResult.objects.filter(run=run)
            .exclude(case__source_output=None)
            .values_list("case__source_output_id", flat=True)
        )
        counts = {signal: 0 for signal, _label in FeedbackEvent.SIGNAL_CHOICES}
        if output_ids:
            rows = (
                FeedbackEvent.objects.filter(output_id__in=output_ids)
                .values("signal").annotate(count=Count("id"))
            )
            for row in rows:
                counts[row["signal"]] = row["count"]

        true_positive = sum(counts.get(signal, 0) for signal in POSITIVE_SIGNALS)
        false_positive = sum(counts.get(signal, 0) for signal in FALSE_POSITIVE_SIGNALS)
        missed = sum(counts.get(signal, 0) for signal in MISSED_SIGNALS)
        rejected = counts.get("rejected", 0)

        decisions = true_positive + false_positive + rejected
        return {
            "signal_counts": counts,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "missed": missed,
            "false_positive_rate": _rate(false_positive, false_positive + true_positive),
            "false_negative_rate": _rate(missed, missed + true_positive),
            "adoption_rate": _rate(true_positive, decisions),
        }


class EvaluationPartitionService:
    """分区完整性与基线可比性。"""

    @staticmethod
    def partition_detail(run: EvaluationRun) -> dict:
        """按分区统计样本数与完成数。

        以 ``case.split`` 为准（不是 suite.suite_type）：一个套件里可以混合分区，
        真正的样本归属在 case 上。
        """
        rows = (
            EvaluationResult.objects.filter(run=run)
            .values("case__split")
            .annotate(cases=Count("case_id", distinct=True), results=Count("id"))
        )
        detail = {}
        for row in rows:
            split = row["case__split"] or "unassigned"
            detail[split] = {"cases": row["cases"], "results": row["results"]}
        return detail

    @classmethod
    def missing_partitions(
        cls, run: EvaluationRun, *, thresholds: dict | None = None,
        required: tuple | None = None,
    ) -> list:
        """列出缺失的分区。

        取哪一套分区，优先级从高到低：

        1. 显式传入的 ``required``；
        2. ``thresholds["require_all_partitions"]`` 为真 → 五分区全要（R7 的四阶段口径）；
        3. 否则按能力注册表口径（见 ``capability_registry``）——单能力闭环只需
           金标+回归+新鲜，不逼着它去造隐藏集，那属于为流程造数据。

        ``DEFAULT_THRESHOLDS`` 里 ``require_all_partitions`` 默认为真，因此**默认是严格的**；
        单能力闭环必须在调用时显式放宽，放开是有意为之、可审计，而不是悄悄漏过。
        """
        limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        if required is None:
            required = (
                PARTITION_ORDER if limits.get("require_all_partitions")
                else required_for_run(run)
            )
        detail = cls.partition_detail(run)
        minimum = int(limits.get("min_partition_cases", 1))
        missing = []
        for partition in required:
            stats = detail.get(partition) or {}
            if int(stats.get("results", 0)) < minimum:
                missing.append(partition)
        return missing

    @staticmethod
    def baseline_comparability(baseline_run: EvaluationRun | None, candidate_run: EvaluationRun) -> dict:
        """基线可比性：同套件、候选取样不缩小、样本有交集。"""
        if baseline_run is None:
            return {"comparable": False, "reason": "baseline_missing", "detail": "缺少基线评测"}

        if str(baseline_run.suite_id) != str(candidate_run.suite_id):
            return {
                "comparable": False, "reason": "suite_mismatch",
                "detail": "基线与候选不在同一个评测套件，指标不可比",
            }

        baseline_cases = set(
            EvaluationResult.objects.filter(run=baseline_run).values_list("case_id", flat=True)
        )
        candidate_cases = set(
            EvaluationResult.objects.filter(run=candidate_run).values_list("case_id", flat=True)
        )
        if not baseline_cases:
            return {"comparable": False, "reason": "baseline_empty", "detail": "基线评测没有样本"}
        if not candidate_cases:
            return {"comparable": False, "reason": "candidate_empty", "detail": "候选评测没有样本"}

        dropped = sorted(str(case_id) for case_id in baseline_cases - candidate_cases)
        if dropped:
            return {
                "comparable": False, "reason": "sample_shrunk",
                # 缩小样本集合是"用减少覆盖换通过率"的典型手法，必须硬拦。
                "detail": f"候选评测丢掉了 {len(dropped)} 个基线样本，不允许以缩小覆盖换取通过率",
                "dropped": dropped[:20],
            }

        return {
            "comparable": True, "reason": "",
            "baseline_cases": len(baseline_cases),
            "candidate_cases": len(candidate_cases),
            "overlap": len(baseline_cases & candidate_cases),
        }


def _regression(baseline: float | None, candidate: float | None) -> float | None:
    """候选相对基线的涨幅（比例）。基线为 0 或缺失时无法比较，返回 None。"""
    if baseline is None or candidate is None:
        return None
    if baseline == 0:
        return None
    return round((candidate - baseline) / baseline, 6)


class EvaluationGateService:
    """硬门禁判定与不可变快照落库。"""

    @classmethod
    @transaction.atomic
    def run_gate(
        cls, *, candidate_run: EvaluationRun, baseline_run: EvaluationRun | None = None,
        release=None, skill_version=None, thresholds: dict | None = None,
        actor=None, kind: str = "full", required_partitions: tuple | None = None,
    ) -> EvaluationGateSnapshot:
        """对一次候选评测执行完整门禁，并落一份不可变快照。"""
        if candidate_run is None:
            raise ValidationError("缺少候选评测，无法执行门禁")
        limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

        candidate_metrics = RunMetricsService.summarize(candidate_run)
        baseline_metrics = RunMetricsService.summarize(baseline_run) if baseline_run else {}

        comparability = EvaluationPartitionService.baseline_comparability(baseline_run, candidate_run)
        missing_partitions = EvaluationPartitionService.missing_partitions(
            candidate_run, thresholds=limits, required=required_partitions,
        )
        required_set = tuple(required_partitions) if required_partitions else (
            PARTITION_ORDER if limits.get("require_all_partitions") else required_for_run(candidate_run)
        )
        partitions = EvaluationPartitionService.partition_detail(candidate_run)
        partitions[SHADOW_PARTITION] = {
            "baseline_comparable": comparability["comparable"],
            "reason": comparability.get("reason", ""),
        }
        if baseline_run is not None:
            partitions["baseline"] = EvaluationPartitionService.partition_detail(baseline_run)

        checks: list[dict] = []

        def record(code: str, ok: bool, detail: str = "", value=None, limit=None) -> None:
            checks.append({
                "code": code, "label": GATE_LABELS.get(code, code), "ok": bool(ok),
                "detail": detail, "value": value, "limit": limit,
            })

        # ---- 前置：评测是否具备判定条件
        record(
            "candidate_run_present", candidate_run is not None,
            "候选评测已就绪" if candidate_run else "缺少候选评测",
        )
        record(
            "baseline_present", baseline_run is not None,
            "基线评测已就绪" if baseline_run else "缺少基线评测（无法判定回归）",
        )
        record(
            "baseline_comparable", comparability["comparable"],
            comparability.get("detail") or "基线与候选可比",
        )
        record(
            "partitions_complete", not missing_partitions,
            "评测分区齐全" if not missing_partitions
            else "缺少分区：" + "、".join(PARTITION_LABELS.get(item, item) for item in missing_partitions),
            value=sorted(partitions.keys()),
            limit=list(required_set),
        )
        sample_count = int(candidate_metrics["sample_count"] or 0)
        min_samples = int(limits["min_sample_count"])
        record(
            "sample_count", sample_count >= min_samples,
            f"候选样本 {sample_count} 条",
            value=sample_count, limit=min_samples,
        )

        # ---- 质量
        quality = candidate_metrics["quality"]
        record(
            "quality_floor", quality is not None and quality >= limits["min_quality"],
            "候选质量均分未达标" if quality is None else f"候选质量均分 {quality}",
            value=quality, limit=limits["min_quality"],
        )
        quality_regression = (
            None if quality is None or baseline_metrics.get("quality") is None
            else round(float(baseline_metrics["quality"]) - float(quality), 6)
        )
        record(
            "quality_regression",
            quality_regression is not None and quality_regression <= limits["max_quality_regression"],
            "缺少可比基线或候选质量" if quality_regression is None
            else f"质量变化 {quality_regression:+}",
            value=quality_regression, limit=limits["max_quality_regression"],
        )

        # ---- 漏报 / 误报
        fn_rate = candidate_metrics["false_negative_rate"]
        record(
            "false_negative_rate", fn_rate is not None and fn_rate <= limits["max_false_negative_rate"],
            # 没有信号时不判"通过"：缺证据视为未满足硬门禁，让流程停下来补证据。
            "候选无任何反馈信号，漏报率无法判定" if fn_rate is None else f"漏报率 {fn_rate}",
            value=fn_rate, limit=limits["max_false_negative_rate"],
        )
        fp_rate = candidate_metrics["false_positive_rate"]
        record(
            "false_positive_rate", fp_rate is not None and fp_rate <= limits["max_false_positive_rate"],
            "候选无任何反馈信号，误报率无法判定" if fp_rate is None else f"误报率 {fp_rate}",
            value=fp_rate, limit=limits["max_false_positive_rate"],
        )

        # ---- 耗时 / Token 回归
        latency_regression = _regression(baseline_metrics.get("latency_ms"), candidate_metrics.get("latency_ms"))
        record(
            "latency_regression",
            latency_regression is None or latency_regression <= limits["max_latency_regression"],
            "缺少可比基线耗时" if latency_regression is None else f"耗时变化 {latency_regression:+.2%}",
            value=latency_regression, limit=limits["max_latency_regression"],
        )
        token_regression = _regression(baseline_metrics.get("token_usage"), candidate_metrics.get("token_usage"))
        record(
            "token_regression",
            token_regression is None or token_regression <= limits["max_token_regression"],
            "缺少可比基线 Token" if token_regression is None else f"Token 变化 {token_regression:+.2%}",
            value=token_regression, limit=limits["max_token_regression"],
        )

        # ---- 稳定性
        stability = candidate_metrics["stability"]
        record(
            "stability", stability is not None and stability >= limits["min_stability"],
            "候选样本不足，稳定性无法判定" if stability is None else f"稳定性 {stability}",
            value=stability, limit=limits["min_stability"],
        )

        # ``latency_regression`` / ``token_regression`` 在缺基线时按"无法判定"处理：
        # 单能力闭环（T14）可以不带基线跑门禁，此时不能用这两项否决，但也不能记通过。
        undecidable = {"latency_regression", "token_regression"}
        passed = all(
            item["ok"] for item in checks if item["code"] not in undecidable or item["value"] is not None
        )

        metrics = {
            "candidate": candidate_metrics,
            "baseline": baseline_metrics,
            "quality_regression": quality_regression,
            "latency_regression": latency_regression,
            "token_regression": token_regression,
        }
        content_hash = cls._content_hash(
            kind=kind,
            partitions=partitions,
            metrics=metrics,
            checks=checks,
        )

        snapshot, _created = EvaluationGateSnapshot.objects.get_or_create(
            candidate_run=candidate_run, kind=kind, content_hash=content_hash,
            defaults={
                "project": candidate_run.suite.project,
                "release": release,
                "skill_version": skill_version,
                "suite": candidate_run.suite,
                "gold_dataset_version": candidate_run.gold_dataset_version,
                "baseline_run": baseline_run,
                "partitions": partitions,
                "metrics": metrics,
                "checks": checks,
                "thresholds": limits,
                "passed": passed,
                "created_by": actor,
            },
        )
        return snapshot

    @staticmethod
    def _content_hash(*, kind: str, partitions: dict, metrics: dict, checks: list) -> str:
        """快照内容哈希：同输入同结论必然同哈希，保证幂等与"快照是否变化"可判定。"""
        payload = json.dumps(
            {"kind": kind, "partitions": partitions, "metrics": metrics, "checks": checks},
            ensure_ascii=False, sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ 查询

    @staticmethod
    def latest_snapshot(release, *, kind: str = "full") -> EvaluationGateSnapshot | None:
        if release is None or not getattr(release, "pk", None):
            return None
        return EvaluationGateSnapshot.objects.filter(release=release, kind=kind).first()

    @staticmethod
    def latest_snapshot_for_candidate(candidate_run, *, kind: str = "full") -> EvaluationGateSnapshot | None:
        if candidate_run is None:
            return None
        return EvaluationGateSnapshot.objects.filter(candidate_run=candidate_run, kind=kind).first()

    # ------------------------------------------------------------ 审批前置条件

    @classmethod
    def missing_conditions(cls, release, *, kind: str = "full") -> list[dict]:
        """列出"当前还缺什么"，供前端禁用按钮时展示具体原因（R11）。

        返回的是**条件清单**而不是一句"不允许提交"——用户需要知道差什么。
        """
        conditions: list[dict] = []
        if release is None:
            return [{"code": "release_missing", "label": "候选发布", "detail": "发布单元不存在"}]

        snapshot = cls.latest_snapshot(release, kind=kind)
        if snapshot is None:
            conditions.append({
                "code": "gate_not_run", "label": "评测门禁",
                "detail": "候选尚未执行质量门禁",
            })
            return conditions

        failed = [item for item in (snapshot.checks or []) if not item.get("ok")]
        for item in failed:
            conditions.append({
                "code": item["code"], "label": item.get("label", item["code"]),
                "detail": item.get("detail") or item.get("label", ""),
            })
        if not failed and not snapshot.passed:
            conditions.append({
                "code": "gate_failed", "label": "评测门禁", "detail": "门禁结论为未通过",
            })
        return conditions

    @classmethod
    def assert_can_submit_approval(cls, release, *, kind: str = "full") -> EvaluationGateSnapshot:
        """提交审批前的硬校验：没通过门禁就不许进入 ``awaiting_approval``。"""
        conditions = cls.missing_conditions(release, kind=kind)
        if conditions:
            detail = "；".join(item["detail"] for item in conditions[:5])
            raise ValidationError(f"候选未满足提交审批的前置条件：{detail}")
        return cls.latest_snapshot(release, kind=kind)

    @classmethod
    def snapshot_summary(cls, release, *, kind: str = "full") -> dict | None:
        """给前端用的门禁摘要（含缺失条件），审批面板直接渲染。"""
        snapshot = cls.latest_snapshot(release, kind=kind)
        if snapshot is None:
            return {
                "snapshot_id": None, "passed": False, "checks": [], "metrics": {},
                "partitions": {}, "content_hash": "",
                "missing_conditions": cls.missing_conditions(release, kind=kind),
            }
        return {
            "snapshot_id": str(snapshot.id),
            "passed": snapshot.passed,
            "content_hash": snapshot.content_hash,
            "created_at": snapshot.created_at.isoformat(),
            "checks": snapshot.checks,
            "metrics": snapshot.metrics,
            "partitions": snapshot.partitions,
            "thresholds": snapshot.thresholds,
            "missing_conditions": cls.missing_conditions(release, kind=kind),
        }


def attach_skill_version_to_run(run: EvaluationRun, skill_version) -> EvaluationRun:
    """把一次评测绑定到 Skill 版本上（配置里留溯源，不新增列）。

    ``EvaluationRun`` 已有 ``capability_release`` 外键指向 Release；Skill 版本的强关联
    由 ``SkillVersion.release`` 一对一给出，因此这里只需补一个便于查询的冗余键，
    不额外加列，避免为了一个展示字段动表结构。
    """
    if skill_version is None:
        return run
    config = dict(run.config or {})
    config["skill_version_id"] = str(getattr(skill_version, "pk", skill_version))
    config["skill_id"] = str(getattr(skill_version, "skill_id", "") or "")
    config["package_sha256"] = getattr(skill_version, "package_sha256", "") or ""
    run.config = config
    run.save(update_fields=["config", "updated_at"])
    return run


def collect_case_outputs(run: EvaluationRun) -> list[GenerationOutput]:
    """取一次评测涉及的所有产出（归因与金标回流都要用）。"""
    output_ids = (
        EvaluationResult.objects.filter(run=run)
        .exclude(case__source_output=None)
        .values_list("case__source_output_id", flat=True)
    )
    return list(GenerationOutput.objects.filter(id__in=set(output_ids)))
