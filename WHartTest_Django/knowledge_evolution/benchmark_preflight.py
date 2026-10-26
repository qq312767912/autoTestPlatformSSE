"""运行前预检：把「能不能跑」在提交前算清楚。

预检不下载仓库、不调用模型，只做确定性判断：
数据版本是否可运行、样本是否齐备、Commit 是否合法、快照是否已缓存、
模型是否已配置，并按样本给出可用/不可用与原因。
"""

from __future__ import annotations

from typing import Any, Iterable

from .benchmark_catalog import SUPPORTED_LANGUAGES
from .benchmark_models import BenchmarkCase, BenchmarkDatasetVersion, BenchmarkRepositorySnapshot

DEFAULT_PILOT_CASES = 10
TOKENS_PER_CASE = 18000
SECONDS_PER_CASE = 90


def _snapshot_index(cases: Iterable[BenchmarkCase]) -> dict[tuple[str, str, str], BenchmarkRepositorySnapshot]:
    keys = {(case.repo, case.base_sha, case.head_sha) for case in cases}
    if not keys:
        return {}
    snapshots = BenchmarkRepositorySnapshot.objects.filter(
        repo__in={key[0] for key in keys},
    )
    return {
        (item.repo, item.base_sha, item.head_sha): item
        for item in snapshots
        if (item.repo, item.base_sha, item.head_sha) in keys
    }


def select_cases(
    version: BenchmarkDatasetVersion,
    *,
    case_keys: list[str] | None = None,
    languages: list[str] | None = None,
    include_clean: bool | None = None,
    limit: int | None = None,
) -> list[BenchmarkCase]:
    queryset = version.cases.filter(conversion_status="ok").order_by("upstream_instance_id")
    if case_keys:
        wanted = {str(key).rsplit(":", 1)[-1] for key in case_keys}
        queryset = queryset.filter(upstream_instance_id__in=wanted)
    if languages:
        queryset = queryset.filter(language__in=[item for item in languages if item in SUPPORTED_LANGUAGES])
    if include_clean is True:
        queryset = queryset.filter(is_clean=True)
    elif include_clean is False:
        queryset = queryset.filter(is_clean=False)
    if limit:
        queryset = queryset[: int(limit)]
    return list(queryset)


def preflight(
    version: BenchmarkDatasetVersion,
    *,
    case_keys: list[str] | None = None,
    languages: list[str] | None = None,
    include_clean: bool | None = None,
    limit: int | None = None,
    model_ready: bool = True,
    model_label: str = "",
) -> dict[str, Any]:
    cases = select_cases(
        version, case_keys=case_keys, languages=languages, include_clean=include_clean, limit=limit,
    )
    snapshots = _snapshot_index(cases)
    rows, available, excluded = [], 0, []
    language_counts: dict[str, int] = {}
    clean_counts = {"clean": 0, "defect": 0, "unknown": 0}
    for case in cases:
        reasons = []
        if case.language not in SUPPORTED_LANGUAGES:
            reasons.append("language_unsupported")
        if not case.base_sha or not case.head_sha:
            reasons.append("missing_commit")
        if case.gold_count == 0 and case.is_clean is not True:
            reasons.append("no_scorable_gold")
        snapshot = snapshots.get((case.repo, case.base_sha, case.head_sha))
        snapshot_state = "cached" if snapshot and snapshot.status == "ready" else "will_prepare"
        if snapshot and snapshot.status == "failed":
            snapshot_state = "failed"
            reasons.append("snapshot_failed")
        if not model_ready:
            reasons.append("model_not_configured")
        usable = not reasons
        available += int(usable)
        language_counts[case.language] = language_counts.get(case.language, 0) + 1
        if case.is_clean is True:
            clean_counts["clean"] += 1
        elif case.is_clean is False:
            clean_counts["defect"] += 1
        else:
            clean_counts["unknown"] += 1
        row = {
            "case_key": case.case_key,
            "upstream_instance_id": case.upstream_instance_id,
            "repo": case.repo,
            "language": case.language,
            "is_clean": case.is_clean,
            "gold_count": case.gold_count,
            "available": usable,
            "reasons": reasons,
            "snapshot": snapshot_state,
            "source_url": case.source_url,
        }
        rows.append(row)
        if not usable:
            excluded.append({"case_key": case.case_key, "reasons": reasons})
    if version.status != "ready":
        blocked_reason = f"数据版本状态为 {version.status}"
    elif not cases:
        blocked_reason = "所选范围内没有样本"
    elif not model_ready:
        blocked_reason = "未配置可用的代码审查模型"
    else:
        blocked_reason = ""
    return {
        "benchmark_id": version.benchmark_id,
        "version": version.version,
        "status": version.status,
        "model": {"ready": model_ready, "label": model_label},
        "total": len(cases),
        "available": available,
        "unavailable": len(cases) - available,
        "excluded": excluded,
        "language_counts": language_counts,
        "clean_counts": clean_counts,
        "snapshot_cached": sum(1 for row in rows if row["snapshot"] == "cached"),
        "snapshot_pending": sum(1 for row in rows if row["snapshot"] == "will_prepare"),
        "blocked_reason": blocked_reason,
        "submittable": bool(available) and not blocked_reason,
        "estimate": {
            "basis": f"每样本约 {TOKENS_PER_CASE} tokens、{SECONDS_PER_CASE}s；按历史均值估算，仅供容量判断",
            "tokens": available * TOKENS_PER_CASE,
            "seconds": available * SECONDS_PER_CASE,
        },
        "cases": rows,
    }
