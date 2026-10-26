"""内置公开集报告：在现有代码审查报告之上增加基准分组、分母说明与原生口径占位。

四条硬约束（对应 R5，均在代码里强制，不依赖撰写人记得）：
1. 质量指标分母只含「成功且已核验」样本；失败、取消、排除、未核验单列；
2. 无干净样本时 `clean_pr_false_positive_rate` 与 `f1` 返回 None，并给出原因；
3. 三个基准不合并总分，本模块只产出单基准报告；
4. 官方原生指标单独命名空间，未实现即 `implemented=false`，不填 0。
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .benchmark_catalog import CATALOG

TERMINAL = {"completed", "degraded", "partial", "failed", "cancelled"}
SUCCESS = {"completed", "degraded"}


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    """小样本比例区间用 Wilson，不用正态近似。"""
    if total <= 0:
        return None
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)]


def _bucket() -> dict[str, Any]:
    return {
        "n_selected": 0, "n_success": 0, "n_verified": 0, "n_failed": 0, "n_unverified": 0,
        "gold_issues": 0, "reported_findings": 0, "raw_findings": 0, "verified_hits": 0,
        "verified_valid_findings": 0, "verified_false_findings": 0,
        "located_gold": 0, "located_hits": 0,
        # 质量指标专用分母：只累计「成功且已核验」样本的金标，避免未核验样本把分母放大。
        "verified_gold": 0, "verified_located_gold": 0,
        # clean_cases 统计本轮跑过并成功的干净样本；clean_verified_cases 只统计其中已核验的。
        "clean_cases": 0, "clean_cases_with_findings": 0,
        "clean_verified_cases": 0, "clean_verified_with_findings": 0,
        "tokens": 0, "duration_ms": 0, "duration_samples": 0,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _finalize(bucket: dict[str, Any], *, clean_available: bool) -> dict[str, Any]:
    verified = bucket["n_verified"]
    verified_gold = bucket["verified_gold"]
    bucket["recall"] = _rate(bucket["verified_hits"], verified_gold) if verified and verified_gold else None
    bucket["recall_ci95"] = (
        wilson_interval(bucket["verified_hits"], verified_gold) if verified and verified_gold else None
    )
    if verified and not verified_gold:
        bucket["recall_note"] = "已核验样本均无金标问题，无法计算召回率"
    bucket["line_precision"] = (
        _rate(bucket["located_hits"], bucket["verified_located_gold"])
        if verified and bucket["verified_located_gold"] else None
    )
    reported = bucket["verified_valid_findings"] + bucket["verified_false_findings"]
    if clean_available and verified and reported:
        bucket["precision"] = _rate(bucket["verified_valid_findings"], reported)
    else:
        bucket["precision"] = None
        if not clean_available:
            bucket["precision_note"] = "该基准无干净样本，未计算准确率与 F1"
    bucket["noise_rate"] = _rate(bucket["verified_false_findings"], reported) if verified and reported else None
    if clean_available and bucket["clean_verified_cases"]:
        bucket["clean_pr_false_positive_rate"] = _rate(
            bucket["clean_verified_with_findings"], bucket["clean_verified_cases"]
        )
        bucket["clean_pr_false_positive_ci95"] = wilson_interval(
            bucket["clean_verified_with_findings"], bucket["clean_verified_cases"]
        )
    else:
        bucket["clean_pr_false_positive_rate"] = None
        bucket["clean_pr_false_positive_ci95"] = None
        if not clean_available:
            bucket.setdefault("note", "该基准无干净样本，未计算误报率")
        elif bucket["clean_cases"]:
            bucket.setdefault("note", "干净样本尚未逐样本核验，暂不计算误报率")
        else:
            bucket.setdefault("note", "本轮无成功的干净样本，未计算误报率")
    if bucket["recall"] is not None and bucket["precision"] is not None and clean_available:
        precision, recall = bucket["precision"], bucket["recall"]
        bucket["f1"] = round(2 * precision * recall / (precision + recall), 4) if precision + recall else None
    else:
        bucket["f1"] = None
    bucket["avg_tokens"] = round(bucket["tokens"] / bucket["n_success"]) if bucket["n_success"] else None
    bucket["avg_duration_ms"] = round(bucket["duration_ms"] / bucket["duration_samples"]) if bucket["duration_samples"] else None
    bucket["reliable"] = bucket["n_verified"] >= 10
    return bucket


def build_benchmark_report(run) -> dict[str, Any]:
    from .code_review_benchmark import build_report

    base = build_report(run)
    binding = getattr(run, "benchmark_binding", None)
    if binding is None:
        return base
    entry = CATALOG.get(binding.benchmark_id, {})
    cases_meta = {
        str(item.case_number): (item.metadata or {})
        for item in run.suite.cases.all()
    }
    totals = _bucket()
    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(_bucket))
    # 数据集级判断：该基准是否本来就包含干净样本。没有干净样本时，准确率与 F1 没有意义。
    clean_available = (binding.version.clean_total or 0) > 0
    rows = []
    for case in base["cases"]:
        meta = cases_meta.get(str(case["case_number"]), {})
        if meta.get("source") != "benchmark":
            continue
        is_clean = meta.get("is_clean")
        adjudication = case.get("adjudication") or {}
        issues = case.get("issues") or []
        located = [issue for issue in issues if issue.get("line")]
        matches = adjudication.get("matches") or []
        located_hits = sum(1 for pair in matches if pair.get("gold_index") is not None
                           and pair["gold_index"] < len(issues) and issues[pair["gold_index"]].get("line"))
        findings = case.get("findings") or []
        status = case.get("status")
        verified = bool(adjudication.get("completed")) and status in SUCCESS
        dimensions = {
            "language": meta.get("language") or "unknown",
            "is_clean": "clean" if is_clean is True else ("defect" if is_clean is False else "unknown"),
            "repo": meta.get("repo") or "unknown",
        }
        for bucket in [totals, *(groups[dim][val] for dim, val in dimensions.items())]:
            bucket["n_selected"] += 1
            if status in SUCCESS:
                bucket["n_success"] += 1
                bucket["gold_issues"] += len(issues)
                bucket["reported_findings"] += len(findings)
                bucket["raw_findings"] += int(case.get("raw_findings") or 0)
                bucket["located_gold"] += len(located)
                bucket["tokens"] += case.get("tokens") or 0
                if case.get("duration_ms") is not None:
                    bucket["duration_ms"] += case["duration_ms"]
                    bucket["duration_samples"] += 1
                if is_clean is True:
                    bucket["clean_cases"] += 1
                    bucket["clean_cases_with_findings"] += int(bool(findings))
                if verified:
                    bucket["n_verified"] += 1
                    bucket["verified_hits"] += len(matches)
                    bucket["located_hits"] += located_hits
                    bucket["verified_gold"] += len(issues)
                    bucket["verified_located_gold"] += len(located)
                    bucket["verified_valid_findings"] += len(matches) + len(adjudication.get("valid_findings") or [])
                    bucket["verified_false_findings"] += len(adjudication.get("false_findings") or [])
                    if is_clean is True:
                        bucket["clean_verified_cases"] += 1
                        bucket["clean_verified_with_findings"] += int(bool(findings))
                else:
                    bucket["n_unverified"] += 1
            else:
                bucket["n_failed"] += 1
        rows.append({
            "case_key": meta.get("case_key"),
            "upstream_instance_id": (meta.get("case_key") or "").rsplit(":", 1)[-1],
            "repo": meta.get("repo"),
            "language": meta.get("language"),
            "is_clean": is_clean,
            "gold_count": len(issues),
            "located_gold": len(located),
            "status": status,
            "verified": verified,
            "findings": len(findings),
            "raw_findings": int(case.get("raw_findings") or 0),
            "matches": len(matches),
            "tokens": case.get("tokens"),
            "duration_ms": case.get("duration_ms"),
            "source_url": meta.get("source_url"),
            "error": case.get("error"),
        })
    excluded = list(binding.excluded_cases or [])
    coverage = {
        "selected": len(binding.selected_case_keys or []),
        "excluded": len(excluded),
        "failed": totals["n_failed"],
        "unverified": totals["n_unverified"],
        "verified": totals["n_verified"],
        "verified_rate": _rate(totals["n_verified"], totals["n_success"]),
    }
    return {
        **base,
        "benchmark": {
            "benchmark_id": binding.benchmark_id,
            "display_name": entry.get("display_name", binding.benchmark_id),
            "version": binding.version.version,
            "manifest_digest": binding.version.manifest_digest,
            "upstream_revision": binding.version.upstream_revision,
            "adapter_version": binding.version.adapter_version,
            "mode": binding.mode,
            "license": entry.get("license", {}),
            "homepage": entry.get("homepage", ""),
            "languages": binding.version.language_counts,
            "code_version": binding.code_version,
            "model_snapshot": binding.model_snapshot,
            "coverage": coverage,
            "excluded_cases": excluded[:200],
            "summary": _finalize(totals, clean_available=clean_available),
            "groups": {
                dimension: {key: _finalize(value, clean_available=clean_available) for key, value in sorted(values.items())}
                for dimension, values in groups.items()
            },
            "native_metrics": {
                "benchmark_id": binding.benchmark_id,
                "official_metric": {
                    "swe_care": "SEM-F1 / task-specific F1（官方 harness）",
                    "swr_bench": "PR-Review 架构下的缺陷检出率（官方仓库脚本）",
                    "aacr_bench": "Precision / Recall / Line Precision / Noise Rate（official evaluator_runner）",
                }.get(binding.benchmark_id, "未登记"),
                "implemented": False,
                "value": None,
                "note": "官方口径未在本平台实现，避免用平台口径冒充官方分数；需要时另行接入官方 evaluator。",
            },
            "cases": rows,
            "constraints": [
                "质量指标分母仅含成功且已核验样本；失败、排除、未核验单列。",
                "无干净样本时不输出误报率与 F1。",
                "三个基准不合并总分；跨基准数字不可比。",
            ],
        },
    }
