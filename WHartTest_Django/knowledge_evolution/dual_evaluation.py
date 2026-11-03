"""将两类测评接到既有实验状态机，保留各自证据和失败状态。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .optimization_models import EvolutionExperiment, SkillEvaluationProfile
from .paired_evaluation import normalize_policy
from .skill_assessment import (
    ASSESSMENT_SCHEMA, GENERAL_RUBRIC, BusinessMetricEvaluator, GeneralSkillEvaluator,
    compare_business, digest, normalize_metrics, number, sheet_catalog, workbook_sheets,
)


def frozen_config(*, project, skill_version):
    profile = SkillEvaluationProfile.objects.filter(project=project, skill=skill_version.skill).first()
    return {"schema": ASSESSMENT_SCHEMA, "general_rubric": deepcopy(GENERAL_RUBRIC),
            "business_metrics": normalize_metrics(profile.metrics if profile else []),
            "skill_id": str(skill_version.skill_id),
            "profile_updated_at": profile.updated_at.isoformat() if profile else None}


def profile_view(*, project, review, skill_version):
    profile = SkillEvaluationProfile.objects.filter(project=project, skill=skill_version.skill).first()
    catalog, warnings = [], []
    if review.report_file:
        try:
            with review.report_file.open("rb") as file:
                data = file.read(25 * 1024 * 1024 + 1)
            if len(data) > 25 * 1024 * 1024:
                raise ValidationError("产出物超过 25MB，请明确填写 Sheet 来源")
            catalog = sheet_catalog(workbook_sheets(data))
        except Exception as exc:
            warnings.append(f"产出物目录读取失败：{exc}")
    return {"skill_id": str(skill_version.skill_id), "skill_version_id": str(skill_version.pk),
            "general_rubric": GENERAL_RUBRIC, "metrics": profile.metrics if profile else [],
            "sheet_catalog": catalog, "warnings": warnings}


def full_package(version):
    from skills.packaging import iter_package_files, redact_text
    root = Path(version.get_full_path())
    files, chars = [], 0
    for relative in iter_package_files(root):
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            files.append({"path": relative, "binary": True})
            continue
        content, _ = redact_text(content)
        chars += len(content)
        if chars > 400000:
            raise ValidationError("Skill 包超过通用测评文本范围，未截断评分")
        files.append({"path": relative, "content": content})
    return {"package_sha256": version.package_sha256, "files": files}


def evaluation_binding(experiment, report, variants):
    return digest({"config": experiment.assessment_config,
                   "runs": {label: {"input": item.run_result.get("input_hash"),
                                    "output": item.run_result.get("output_hash"),
                                    "version": str(item.skill_version_id)} for label, item in variants.items()},
                   "manual_values": report.get("manual_values", {})})


def evaluate_dual(*, experiment, report, variants, source, config, budget,
                  general_evaluator=None, business_evaluator=None):
    settings = experiment.assessment_config
    metrics = normalize_metrics(settings.get("business_metrics", []))
    report["assessment_config_hash"] = digest(settings)
    report["general"] = {"rubric": settings["general_rubric"], "results": {}}
    report["business"] = {"metrics": metrics, "results": {}}
    for label, variant in variants.items():
        if variant.status != "completed":
            report["general"]["results"][label] = {"status": "failed", "reason": "真实运行失败"}
            report["business"]["results"][label] = {}
            continue
        try:
            general = (general_evaluator or GeneralSkillEvaluator()).evaluate(
                package=full_package(variant.skill_version), output=variant.run_result["output"],
                source=source, config=config, budget=budget,
                rubric=settings["general_rubric"],
            )
        except ValidationError as exc:
            # BudgetExceeded 是 ValidationError 子类，必须保留预算中断语义。
            from .paired_evaluation import BudgetExceeded
            if isinstance(exc, BudgetExceeded):
                raise
            general = {"status": "failed", "reason": str(exc)}
        report["general"]["results"][label] = general
        report["business"]["results"][label] = (business_evaluator or BusinessMetricEvaluator()).evaluate(
            metrics=metrics, sheets=variant.run_result["output"].get("artifact_sheets", []),
            output=variant.run_result["output"], source=source, config=config, budget=budget,
            human_values=report.get("manual_values", {}).get(label),
        )
    report["assessment_binding"] = evaluation_binding(experiment, report, variants)
    refresh_dual_gate(experiment, report)


def refresh_dual_gate(experiment, report):
    metrics = normalize_metrics(experiment.assessment_config.get("business_metrics", []))
    general = report.get("general", {}).get("results", {})
    business = report.get("business", {}).get("results", {})
    for label in ("A", "B"):
        previous = report["comparisons"].get(label, {})
        paired = previous.get("paired", previous)
        comparison = compare_business(metrics, business.get("baseline", {}), business.get(label, {}))
        general_complete = all(general.get(key, {}).get("status") == "completed" for key in ("baseline", label))
        eligible = bool(paired.get("eligible")) and general_complete and comparison["eligible"]
        reasons = []
        if not general_complete:
            reasons.append("通用九维测评未完成")
        if not comparison["eligible"]:
            reasons.append(comparison["reason"])
        if not paired.get("eligible"):
            reasons.append(paired.get("reason", "实际产出盲评未通过"))
        report["comparisons"][label] = {**paired, "paired": paired, "business": comparison,
            "general_complete": general_complete, "eligible": eligible,
            "clear_gain": eligible and bool(paired.get("clear_gain")),
            "reason": "两类测评完整、业务指标未退化且实际产出盲评通过" if eligible else "；".join(reasons)}
    eligible = [item for item in report["comparisons"].values() if item.get("eligible")]
    can_iterate = any(item.get("clear_gain") for item in eligible)
    reason = "" if can_iterate else ("收益有限，停止迭代；可人工审核本轮候选" if eligible else "通用或业务测评未通过，请查看两类结果与待补充指标")
    if experiment.round_number >= normalize_policy(experiment.policy)["max_rounds"]:
        can_iterate, reason = False, "达到最大迭代轮数"
    report.update({"can_iterate": can_iterate, "stop_reason": reason})


def assert_dual_current(experiment, report, variants, label):
    if experiment.assessment_config.get("schema") != ASSESSMENT_SCHEMA:
        return
    if (report.get("assessment_config_hash") != digest(experiment.assessment_config)
            or report.get("assessment_binding") != evaluation_binding(experiment, report, variants)):
        raise ValidationError("两类测评配置、人工评分或实际产出已变化，评测结论不可复用")
    refreshed = deepcopy(report)
    refresh_dual_gate(experiment, refreshed)
    if not refreshed["comparisons"][label].get("eligible"):
        raise ValidationError("通用或业务测评尚未通过，不能选优或发布")


@transaction.atomic
def record_human_metrics(*, experiment, values, actor):
    experiment = EvolutionExperiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.state != "awaiting_choice" or experiment.assessment_config.get("schema") != ASSESSMENT_SCHEMA:
        raise ValidationError("仅本轮两类测评完成且待选优时可补录人工业务评分")
    report = deepcopy(experiment.evaluation_report)
    variants = {item.label: item for item in experiment.variants.select_related("skill_version")}
    if report.get("assessment_binding") != evaluation_binding(experiment, report, variants):
        raise ValidationError("人工评分绑定的产出已变化")
    if not isinstance(values, dict) or not values or set(values) - {"baseline", "A", "B"}:
        raise ValidationError("人工评分需要指定 Baseline/A/B")
    human_ids = {item["id"] for item in normalize_metrics(experiment.assessment_config["business_metrics"]) if item["source"] == "human"}
    for label, entries in values.items():
        if not isinstance(entries, dict) or not entries or set(entries) - human_ids:
            raise ValidationError("只能填写本轮冻结配置中的人工指标")
        if variants[label].status != "completed":
            raise ValidationError("不能为运行失败的变体填人工评分")
        for key, entry in entries.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("evidence"), str) or not entry["evidence"].strip():
                raise ValidationError("人工评分必须填写产出证据")
            normalized = {"value": number(entry.get("value")), "evidence": entry["evidence"].strip()[:4000],
                          "actor_id": str(actor.pk), "recorded_at": timezone.now().isoformat()}
            report.setdefault("manual_values", {}).setdefault(label, {})[key] = normalized
            report["business"]["results"].setdefault(label, {})[key] = {
                "status": "completed", "value": normalized["value"], "evidence": [normalized["evidence"]],
                "provenance": "human_confirmed", "actor_id": normalized["actor_id"], "recorded_at": normalized["recorded_at"],
            }
    report["assessment_binding"] = evaluation_binding(experiment, report, variants)
    refresh_dual_gate(experiment, report)
    experiment.evaluation_report = report
    experiment.save(update_fields=["evaluation_report", "updated_at"])
    return experiment
