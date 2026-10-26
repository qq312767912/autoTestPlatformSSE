"""公开集运行编排：锁定样本 → 准备快照 → 建标准审查任务 → 汇总报告。

与内部集的区别（对应 R4/R6）：
- 数据版本、样本清单、快照 Commit、模型与代码版本全部写进 `BenchmarkRunBinding`，报告可复现；
- 不借用用户仓库与 GitLab 凭据，审查任务走 `benchmark_case` 来源；
- 试跑与全量是两个独立运行，重复提交同一运行不会产生重复审查。
"""

from __future__ import annotations

import os
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .benchmark_catalog import CATALOG
from .benchmark_models import (
    BenchmarkCase,
    BenchmarkDatasetVersion,
    BenchmarkRunBinding,
)
from .benchmark_preflight import preflight, select_cases
from .benchmark_snapshots import SnapshotError, ensure_snapshot, snapshot_lease
from .evaluation_models import EvaluationRun, EvaluationResult
from .models import EvaluationCase, EvaluationSuite

PILOT_LIMIT = 10


def _code_version() -> str:
    return (os.environ.get("GIT_COMMIT") or os.environ.get("CODE_VERSION") or "")[:64]


def mirror_suite(project, version: BenchmarkDatasetVersion) -> EvaluationSuite:
    """每个项目 + 基准 + 版本一个评测集，承载项目内的样本与运行。"""
    entry = CATALOG[version.benchmark_id]
    name = f"内置公开集 · {entry['short_name']} · {version.version}"[:255]
    suite, _ = EvaluationSuite.objects.get_or_create(
        project=project,
        name=name,
        suite_type="gold",
        defaults={
            "task_type": "code_review",
            "description": (
                f"{entry['display_name']}（{entry['homepage']}），数据版本 {version.version}，"
                f"许可 {entry['license']['spdx']}。样本由平台内置目录准备，不可在此导入。"
            )[:2000],
        },
    )
    return suite


def _materialize_cases(suite: EvaluationSuite, cases: list[BenchmarkCase]) -> dict[str, EvaluationCase]:
    """把公开样本物化为项目内评测样本；按 benchmark case 去重，重复运行复用。"""
    existing = {
        str((item.metadata or {}).get("benchmark_case_id")): item
        for item in EvaluationCase.objects.filter(suite=suite)
        if (item.metadata or {}).get("benchmark_case_id")
    }
    missing = [case for case in cases if str(case.pk) not in existing]
    if missing:
        start = (EvaluationCase.objects.filter(suite=suite).order_by("-case_number")
                 .values_list("case_number", flat=True).first() or 0)
        created = EvaluationCase.objects.bulk_create([
            EvaluationCase(
                suite=suite,
                case_number=start + index,
                task_type="code_review",
                input_payload={
                    "benchmark_case_id": str(case.pk),
                    "repo": case.repo,
                    "base_sha": case.base_sha,
                    "head_sha": case.head_sha,
                },
                expected_payload={"issues": case.gold_issues},
                golden_labels={"issues": case.gold_issues},
                split="gold",
                annotator="benchmark_catalog",
                annotated_at=timezone.now(),
                metadata={
                    "source": "benchmark",
                    "benchmark_id": case.benchmark_id,
                    "version": case.version.version,
                    "benchmark_case_id": str(case.pk),
                    "case_key": case.case_key,
                    "language": case.language,
                    "is_clean": case.is_clean,
                    "repo": case.repo,
                    "source_url": case.source_url,
                    "provenance": "upstream",
                },
            ) for index, case in enumerate(missing, 1)
        ], batch_size=200)
        for item in created:
            existing[str((item.metadata or {}).get("benchmark_case_id"))] = item
    return existing


def create_public_run(
    *,
    project,
    version: BenchmarkDatasetVersion,
    actor,
    case_keys: list[str] | None = None,
    languages: list[str] | None = None,
    include_clean: bool | None = None,
    mode: str = "pilot",
    allow_partial: bool = False,
    name: str = "",
    model_config=None,
) -> EvaluationRun:
    if version.status != "ready":
        raise ValidationError({"version": f"数据版本不可运行：{version.status}"})
    if mode not in {"pilot", "full"}:
        raise ValidationError({"mode": "运行模式须为 pilot 或 full"})
    limit = PILOT_LIMIT if mode == "pilot" else None
    model_ready = model_config is not None
    result = preflight(
        version, case_keys=case_keys, languages=languages, include_clean=include_clean,
        limit=limit, model_ready=model_ready,
        model_label=getattr(model_config, "name", "") if model_config else "",
    )
    if not result["submittable"]:
        raise ValidationError({"detail": result["blocked_reason"] or "没有可用样本"})
    if result["unavailable"] and not allow_partial:
        raise ValidationError({
            "detail": f"{result['unavailable']} 条样本不可用；请选择「仅运行可用样本」以记录排除清单",
            "excluded": result["excluded"][:50],
        })
    cases = select_cases(
        version, case_keys=case_keys, languages=languages, include_clean=include_clean, limit=limit,
    )
    cases = [case for case in cases if case.case_key not in {item["case_key"] for item in result["excluded"]}]
    if not cases:
        raise ValidationError({"detail": "所选范围内没有可用样本"})

    suite = mirror_suite(project, version)
    with transaction.atomic():
        run = EvaluationRun.objects.create(
            suite=suite,
            name=(name or f"{CATALOG[version.benchmark_id]['short_name']} {version.version} "
                            f"{'试跑' if mode == 'pilot' else '全量'}")[:255],
            triggered_by=actor,
            status="pending",
            config={
                "benchmark": "code_review",
                "source": "benchmark_catalog",
                "benchmark_id": version.benchmark_id,
                "version": version.version,
                "mode": mode,
                "judge": "manual",
                "model_config_id": getattr(model_config, "pk", None),
                "model_name": getattr(model_config, "name", ""),
                "allow_partial": allow_partial,
            },
            started_at=timezone.now(),
        )
        BenchmarkRunBinding.objects.create(
            run=run,
            version=version,
            benchmark_id=version.benchmark_id,
            mode=mode,
            status="pending",
            selected_case_keys=[case.case_key for case in cases],
            excluded_cases=result["excluded"],
            preflight={
                "total": result["total"],
                "available": result["available"],
                "unavailable": result["unavailable"],
                "language_counts": result["language_counts"],
                "clean_counts": result["clean_counts"],
                "estimate": result["estimate"],
                "snapshot_cached": result["snapshot_cached"],
            },
            model_snapshot={
                "model_config_id": getattr(model_config, "pk", None),
                "name": getattr(model_config, "name", ""),
                "model_name": getattr(model_config, "model_name", ""),
                "base_url": getattr(model_config, "base_url", ""),
            },
            code_version=_code_version(),
        )
    return run


def prepare_run(run: EvaluationRun, *, defer_review: bool = True) -> dict[str, Any]:
    """准备快照并创建标准审查任务；重复调用不会重复创建任务。"""
    binding = run.benchmark_binding
    cases = list(BenchmarkCase.objects.filter(
        version=binding.version, upstream_instance_id__in=[
            key.rsplit(":", 1)[-1] for key in binding.selected_case_keys
        ], conversion_status="ok",
    ))
    binding.status = "running"
    binding.save(update_fields=["status"])
    snapshots, snapshot_manifest = [], []
    for case in cases:
        try:
            snapshot = ensure_snapshot(case.repo, case.base_sha, case.head_sha)
        except SnapshotError as exc:
            snapshot_manifest.append({"case_key": case.case_key, "error": str(exc)[:300]})
            continue
        snapshots.append(snapshot)
        snapshot_manifest.append({
            "case_key": case.case_key, "repo": case.repo, "path": snapshot.path,
            "size_bytes": snapshot.size_bytes, "files": snapshot.file_count,
        })
    binding.snapshot_manifest = snapshot_manifest
    failed_snapshots = [item for item in snapshot_manifest if item.get("error")]
    if failed_snapshots:
        # 快照失败必须进排除清单，否则报告里会变成「样本凭空消失」。
        excluded = list(binding.excluded_cases or [])
        known = {item.get("case_key") for item in excluded}
        for item in failed_snapshots:
            if item["case_key"] in known:
                continue
            excluded.append({
                "case_key": item["case_key"],
                "reasons": ["snapshot_failed"],
                "detail": str(item["error"])[:300],
            })
        binding.excluded_cases = excluded
        binding.save(update_fields=["excluded_cases"])
    binding.save(update_fields=["snapshot_manifest"])

    created, skipped = 0, 0
    with snapshot_lease(snapshots):
        suite = run.suite
        case_map = _materialize_cases(suite, [case for case in cases if case.case_key in {
            item.get("case_key") for item in snapshot_manifest if not item.get("error")
        }])
        from code_analysis.models import AnalysisTask
        from code_analysis.tasks import run_code_analysis

        for case in cases:
            evaluation_case = case_map.get(str(case.pk))
            if evaluation_case is None:
                skipped += 1
                continue
            if EvaluationResult.objects.filter(run=run, case=evaluation_case).exists():
                skipped += 1
                continue
            task = AnalysisTask.objects.create(
                project=run.suite.project,
                repository=None,
                benchmark_case=case,
                creator=run.triggered_by,
                executor=run.triggered_by,
                source_type="commits",
                base_sha=case.base_sha,
                head_sha=case.head_sha,
                title=f"公开测评 {case.benchmark_id} {case.upstream_instance_id}"[:500],
                mode="standard",
                status="queued",
            )
            EvaluationResult.objects.create(
                run=run, case=evaluation_case, status="pending",
                predicted_payload={"analysis_task_id": str(task.id)},
            )
            created += 1
            if defer_review:
                run_code_analysis.delay(str(task.id), force_refresh=True)
    if created == 0:
        # 一条任务都没建成时必须显式失败，不能让运行停在「运行中」假装还在跑。
        reason = (
            "全部样本快照获取失败，未创建审查任务" if failed_snapshots
            else "没有可提交的样本，未创建审查任务"
        )
        run.status = "failed"
        run.finished_at = timezone.now()
        run.config = {**(run.config or {}), "blocked_reason": reason}
        run.save(update_fields=["status", "finished_at", "config", "updated_at"])
        binding.status = "failed"
        binding.save(update_fields=["status"])
        return {
            "created": 0, "skipped": skipped, "snapshots": len(snapshots),
            "blocked": reason, "excluded": failed_snapshots[:10],
        }
    run.status = "running"
    run.save(update_fields=["status", "updated_at"])
    binding.status = "running"
    binding.save(update_fields=["status"])
    if defer_review:
        from .code_review_benchmark import finalize_code_review_benchmark

        finalize_code_review_benchmark.apply_async(args=[str(run.id)], countdown=30)
    return {"created": created, "skipped": skipped, "snapshots": len(snapshots)}


@shared_task(bind=True, max_retries=2)
def execute_benchmark_run(self, run_id: str) -> dict[str, Any]:
    run = EvaluationRun.objects.select_related("suite", "benchmark_binding").get(pk=run_id)
    try:
        return prepare_run(run)
    except Exception as exc:
        binding = getattr(run, "benchmark_binding", None)
        if binding:
            binding.status = "failed"
            binding.preflight = {**(binding.preflight or {}), "error": f"{type(exc).__name__}: {exc}"}
            binding.save(update_fields=["status", "preflight"])
        raise
