"""Skill 自进化 V2：可执行补丁、隔离 A/B 变体和人工选优。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .llm_judges import _json_object
from .optimization_models import EvolutionExperiment, EvolutionVariant
from .skill_evolution import (
    SkillCandidateDeriver, SkillEvolutionService, assert_patch_within_whitelist,
    patch_fingerprint,
)


def _stable_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _verified_attributions(items) -> list:
    from .evolution_taxonomy import validate_diagnosis

    values = list(items)
    if not values:
        raise ValidationError("至少选择一条人工确认的归因")
    invalid = [item for item in values if item.state != "confirmed" or item.evidence_status != "verified"]
    if invalid:
        raise ValidationError("只有人工确认且证据已验证的归因才能进入执行进化")
    for item in values:
        validate_diagnosis(item.primary_cause, item.fine_cause, item.repair_target)
    return values


class ExecutablePatchBuilder:
    """把修复目标路由成 Skill 包内可执行、可静态校验的最小补丁。"""

    @classmethod
    def build(cls, *, skill_version, attributions) -> dict:
        items = _verified_attributions(attributions)
        base = SkillCandidateDeriver.build_patch(skill_version=skill_version, attributions=items)
        edits = list(base["edits"])
        grouped: dict[str, list] = {}
        for item in items:
            grouped.setdefault(item.repair_target, []).append(item)

        if "few_shot" in grouped:
            edits.append({
                "path": "references/evolution-few-shot.md",
                "content": cls._examples(grouped["few_shot"]),
            })
        if "tool_schema" in grouped:
            edits.append({
                "path": "schemas/evolution-tool-constraints.json",
                "content": json.dumps(cls._tool_schema(grouped["tool_schema"]), ensure_ascii=False, indent=2),
            })
        if grouped.keys() & {"workflow", "code"}:
            routed = grouped.get("workflow", []) + grouped.get("code", [])
            edits.append({
                "path": "scripts/validate_evolution.py",
                "content": cls._validator(routed),
            })
        if grouped.keys() & {"knowledge", "rag"}:
            routed = grouped.get("knowledge", []) + grouped.get("rag", [])
            edits.append({
                "path": "references/evolution-knowledge.md",
                "content": cls._knowledge(routed),
            })
        patch = {
            **base,
            "schema": "skill_patch/v2",
            "strategy": "minimal_targeted_mutation",
            "edits": edits,
            "repair_targets": sorted(grouped),
            "impact": cls.impact(edits=edits, skill_version=skill_version),
        }
        patch["whitelist_paths"] = assert_patch_within_whitelist(patch)
        return patch

    @staticmethod
    def impact(*, edits: list, skill_version) -> dict:
        root = Path(skill_version.get_full_path() or "")
        old_chars = 0
        new_chars = 0
        for edit in edits:
            path = root / edit["path"]
            if path.is_file():
                try:
                    old_chars += len(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    pass
            new_chars += len(str(edit.get("content") or ""))
        delta = new_chars - old_chars
        return {
            "files": sorted({edit["path"] for edit in edits}),
            "estimated_token_delta": round(delta / 4),
            "touches_shared_capability": False,
            "risk_level": "medium" if len(edits) > 2 or abs(delta) > 8000 else "low",
        }

    @staticmethod
    def _examples(items) -> str:
        lines = ["# 自进化正负样本", "", "仅使用已确认报告中的证据生成；运行前仍需人工复核。", ""]
        for index, item in enumerate(items, 1):
            lines += [f"## 样本 {index}", f"- 失败类型：{item.fine_cause}", f"- 正确约束：{item.hypothesis}", ""]
        return "\n".join(lines)

    @staticmethod
    def _tool_schema(items) -> dict:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "EvolutionToolConstraints",
            "type": "object",
            "required": ["validated"],
            "properties": {
                "validated": {"const": True},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
            "x-confirmed-constraints": [item.hypothesis for item in items],
        }

    @staticmethod
    def _validator(items) -> str:
        constraints = json.dumps([item.hypothesis for item in items], ensure_ascii=False)
        return (
            '"""由已确认归因生成的确定性前置校验。"""\n'
            f"CONSTRAINTS = {constraints}\n\n"
            "def validate(payload):\n"
            "    if not isinstance(payload, dict):\n"
            "        raise ValueError('输入必须是对象')\n"
            "    return {'validated': True, 'constraints': CONSTRAINTS}\n"
        )

    @staticmethod
    def _knowledge(items) -> str:
        lines = ["# 已确认知识与检索修正", "", "以下条目只对当前实验分支生效。", ""]
        for item in items:
            lines.append(f"- [{item.fine_cause}] {item.hypothesis}")
        return "\n".join(lines) + "\n"


class SkillCreatorAdapter:
    """读取预置 skill-creator 指令并调用当前激活 LLM 生成结构变体。"""

    ALLOWED = (
        "SKILL.md", "references/", "schemas/", "templates/",
        "scripts/validate", "scripts/check",
    )

    def __init__(self, llm_factory=None):
        self.llm_factory = llm_factory or self._default_factory

    @staticmethod
    def _default_factory(config):
        from langgraph_integration.views import create_llm_instance
        return create_llm_instance(config, temperature=0)

    @staticmethod
    def instruction() -> str:
        path = Path(settings.BASE_DIR) / "bundled_skills" / "skill-creator" / "SKILL.md"
        if not path.is_file():
            raise ValidationError("预置 skill-creator 不存在，无法生成结构优化变体")
        return path.read_text(encoding="utf-8")

    def build(self, *, skill_version, attributions) -> dict:
        from langgraph_integration.models import LLMConfig
        from .report_optimization import SkillPackageReader

        items = _verified_attributions(attributions)
        config = LLMConfig.objects.filter(is_active=True).first()
        if not config:
            raise ValidationError("未配置激活的 LLM，skill-creator 结构变体无法生成")
        package = SkillPackageReader.read(skill_version)
        payload = [{
            "primary_cause": item.primary_cause,
            "fine_cause": item.fine_cause,
            "repair_target": item.repair_target,
            "hypothesis": item.hypothesis,
            "evidence": [entry.get("summary", "") for entry in (item.evidence or [])[:3]],
        } for item in items]
        prompt = (
            self.instruction() + "\n\n"
            "请生成 Candidate B 结构化重构补丁。必须补齐适用的前置检查、分步拆解、分支 SOP、失败兜底。\n"
            f"当前包：{json.dumps(package, ensure_ascii=False)}\n"
            f"已确认诊断：{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            result = _json_object(self.llm_factory(config).invoke(prompt))
        except Exception as exc:
            raise ValidationError(f"skill-creator 调用失败：{exc}") from exc
        patch = {
            "schema": "skill_patch/v2",
            "strategy": "skill_creator_structural",
            "edits": list(result.get("edits") or []),
            "attribution_ids": sorted(str(item.id) for item in items),
            "categories": sorted({item.category for item in items}),
            "repair_targets": sorted({item.repair_target for item in items}),
            "rationale": str(result.get("rationale") or ""),
        }
        if not patch["edits"]:
            raise ValidationError("skill-creator 未返回可执行编辑")
        patch["whitelist_paths"] = assert_patch_within_whitelist(patch)
        patch["impact"] = ExecutablePatchBuilder.impact(edits=patch["edits"], skill_version=skill_version)
        return patch


class EvolutionExecutionService:
    """从同一精确基线派生隔离 A/B，并管理本阶段同输入对照。"""

    @classmethod
    @transaction.atomic
    def create_experiment(
        cls, *, output, skill_version, attributions, actor, skill_creator=None,
    ) -> EvolutionExperiment:
        items = _verified_attributions(attributions)
        if output.skill_version_id != skill_version.id:
            raise ValidationError("实验基线必须是当次审查实际运行的精确 Skill 版本")
        baseline_sha = skill_version.package_sha256
        experiment = EvolutionExperiment.objects.create(
            project=output.project, output=output, baseline_skill_version=skill_version,
            baseline_sha256=baseline_sha, created_by=actor,
        )
        experiment.attributions.set(items)
        cls._baseline(experiment, skill_version)
        patch_a = ExecutablePatchBuilder.build(skill_version=skill_version, attributions=items)
        patch_b = (skill_creator or SkillCreatorAdapter()).build(
            skill_version=skill_version, attributions=items,
        )
        cls._candidate(experiment, "A", "minimal_targeted_mutation", patch_a, items, actor)
        cls._candidate(experiment, "B", "skill_creator_structural", patch_b, items, actor)
        skill_version.refresh_from_db()
        if skill_version.package_sha256 != baseline_sha:
            raise ValidationError("实验派生触碰了基线包，事务已中止")
        experiment.state = "ready"
        experiment.save(update_fields=["state", "updated_at"])
        return experiment

    @staticmethod
    def _baseline(experiment, skill_version):
        payload = {"experiment": str(experiment.id), "label": "baseline", "sha": skill_version.package_sha256}
        EvolutionVariant.objects.create(
            experiment=experiment, label="baseline", mutation_strategy="unchanged",
            skill_version=skill_version, sandbox_key=f"evo:{experiment.id}:baseline",
            rollback_target=str(skill_version.id), fingerprint=_stable_hash(payload),
            impact_scope={"files": [], "risk_level": "none"},
        )

    @staticmethod
    def _candidate(experiment, label, strategy, patch, items, actor):
        derived = SkillEvolutionService.derive_candidate(
            skill_version=experiment.baseline_skill_version, attributions=items, actor=actor,
            patch=patch, variant_label=label,
            allow_v2_routes=True,
            change_reason=f"实验 {experiment.id} Candidate {label}：{strategy}",
            expected_benefit="修复已确认归因，同时保留 Baseline 作为对照",
            impact_scope=json.dumps(patch.get("impact") or {}, ensure_ascii=False),
        )
        EvolutionVariant.objects.create(
            experiment=experiment, label=label, mutation_strategy=strategy,
            skill_version=derived["candidate"], sandbox_key=f"evo:{experiment.id}:{label}",
            patch=patch, asset_snapshots={"skill_package_sha256": derived["candidate"].package_sha256},
            impact_scope=patch.get("impact") or {}, rollback_target=str(experiment.baseline_skill_version_id),
            fingerprint=patch_fingerprint(patch),
        )

    @classmethod
    @transaction.atomic
    def record_run(cls, *, experiment, label: str, result: dict) -> EvolutionVariant:
        variant = experiment.variants.select_for_update().get(label=label)
        variant.run_result = result
        variant.status = "failed" if result.get("error") else "completed"
        variant.save(update_fields=["run_result", "status", "updated_at"])
        variants = list(experiment.variants.all())
        if all(item.status in {"completed", "failed"} for item in variants):
            experiment.state = "awaiting_choice"
            experiment.save(update_fields=["state", "updated_at"])
        return variant

    @classmethod
    def rerun_same_input(cls, *, experiment) -> EvolutionExperiment:
        """在三个独立上下文中以同一源文件重跑本阶段，保留原始对比数据。"""
        from langgraph_integration.views import create_llm_instance
        from testcases.models import TestCaseReview
        from testcases.review_service import (
            TESTCASE_REVIEW_CHUNK_SIZE, _get_testcase_review_llm_config,
            _read_rows, _review_chunk,
        )

        review = TestCaseReview.objects.filter(
            project=experiment.project,
            summary__output_id=str(experiment.output_id),
        ).first()
        if review is None:
            raise ValidationError("找不到原审查任务，无法以相同输入重跑")
        rows = _read_rows(review.source_file.path)
        if not rows:
            raise ValidationError("原审查输入为空，无法重跑")
        config = _get_testcase_review_llm_config()
        chunks = [rows[index:index + TESTCASE_REVIEW_CHUNK_SIZE] for index in range(0, len(rows), TESTCASE_REVIEW_CHUNK_SIZE)]
        experiment.state = "running"
        experiment.save(update_fields=["state", "updated_at"])
        for variant in experiment.variants.select_related("skill_version").order_by("label"):
            started = time.perf_counter()
            variant.status = "running"
            variant.save(update_fields=["status", "updated_at"])
            try:
                skill_path = Path(variant.skill_version.get_full_path() or "") / "SKILL.md"
                skill_prompt = skill_path.read_text(encoding="utf-8")
                results = []
                for chunk in chunks:
                    llm = create_llm_instance(config, temperature=0.1, max_retries=0)
                    results.append(_review_chunk(llm, skill_prompt, chunk, review.business_context))
                issues = [item for result in results for item in (result.get("issues") or [])]
                pending = [item for result in results for item in (result.get("pending_confirmations") or [])]
                output_payload = {"issues": issues, "pending_confirmations": pending}
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                cls.record_run(experiment=experiment, label=variant.label, result={
                    "input_hash": _stable_hash(rows),
                    "output": output_payload,
                    "output_hash": _stable_hash(output_payload),
                    "issue_count": len(issues),
                    "pending_count": len(pending),
                    "latency_ms": elapsed_ms,
                    "estimated_input_tokens": round((len(skill_prompt) + len(json.dumps(rows, ensure_ascii=False))) / 4),
                    "estimated_output_tokens": round(len(json.dumps(output_payload, ensure_ascii=False)) / 4),
                    "fixed_hits": cls._fixed_hits(experiment, issues),
                })
            except Exception as exc:
                cls.record_run(experiment=experiment, label=variant.label, result={
                    "input_hash": _stable_hash(rows), "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                })
        experiment.refresh_from_db()
        return experiment

    @staticmethod
    def _fixed_hits(experiment, issues: list) -> dict:
        issue_types = {str(item.get("issue_type") or "") for item in issues}
        expected = set(experiment.attributions.values_list("issue_type", flat=True))
        remaining = sorted(expected & issue_types)
        return {
            "expected_issue_types": sorted(expected),
            "remaining_issue_types": remaining,
            "resolved_issue_types": sorted(expected - issue_types),
        }

    @classmethod
    @transaction.atomic
    def choose_winner(cls, *, experiment, label: str, reason: str) -> EvolutionExperiment:
        if experiment.state != "awaiting_choice":
            raise ValidationError("Baseline/A/B 尚未全部重跑完成，不能选优")
        if label not in {"A", "B"}:
            raise ValidationError("winner 只能选择 Candidate A 或 Candidate B")
        if not reason.strip():
            raise ValidationError("必须填写人工对比理由")
        winner = experiment.variants.get(label=label)
        if winner.status != "completed":
            raise ValidationError("不能选择运行失败的变体")
        experiment.winner_label = label
        experiment.winner_reason = reason.strip()
        experiment.state = "winner_selected"
        experiment.save(update_fields=["winner_label", "winner_reason", "state", "updated_at"])
        return experiment

    @classmethod
    @transaction.atomic
    def submit_winner(cls, *, experiment, actor) -> EvolutionExperiment:
        """仅把人工 winner 送入既有发布审批，绝不自动激活。"""
        from skills.versions import SkillVersionService

        if experiment.state != "winner_selected" or experiment.winner_label not in {"A", "B"}:
            raise ValidationError("必须先完成人工 A/B 选优，才能提交发布审批")
        winner = experiment.variants.select_related("skill_version").get(label=experiment.winner_label)
        SkillVersionService.submit_for_validation(
            winner.skill_version, actor=actor,
            reason=f"实验 {experiment.id} 人工选优：{experiment.winner_reason}",
        )
        experiment.state = "submitted"
        experiment.save(update_fields=["state", "updated_at"])
        return experiment


def experiment_view(experiment) -> dict:
    variants = []
    for item in experiment.variants.select_related("skill_version").order_by("label"):
        variants.append({
            "id": str(item.id), "label": item.label, "strategy": item.mutation_strategy,
            "sandbox_key": item.sandbox_key, "status": item.status,
            "skill_version_id": str(item.skill_version_id or ""),
            "skill_version": getattr(item.skill_version, "version", ""),
            "package_sha256": getattr(item.skill_version, "package_sha256", ""),
            "patch": item.patch, "impact_scope": item.impact_scope,
            "rollback_target": item.rollback_target, "run_result": item.run_result,
        })
    return {
        "experiment_id": str(experiment.id), "state": experiment.state,
        "baseline_version": experiment.baseline_skill_version.version,
        "baseline_sha256": experiment.baseline_sha256,
        "winner_label": experiment.winner_label, "winner_reason": experiment.winner_reason,
        "variants": variants,
    }
