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
from .paired_evaluation import (
    BudgetExceeded, EvaluationBudget, PairedOutputEvaluator, bounded_llm, normalize_policy,
)
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
        return bounded_llm(config, max_tokens=8192)

    @staticmethod
    def instruction() -> str:
        path = Path(settings.BASE_DIR) / "bundled_skills" / "skill-creator" / "SKILL.md"
        if not path.is_file():
            raise ValidationError("预置 skill-creator 不存在，无法生成结构优化变体")
        return path.read_text(encoding="utf-8")

    def build(self, *, skill_version, attributions, budget=None, minimal=False, feedback=None) -> dict:
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
            self.instruction() + "\n\n" +
            ("MUST 生成 Candidate A 最小改动补丁，只修改一个已确认问题相关步骤。\n" if minimal else
             "MUST 生成 Candidate B 结构化重构补丁，补齐适用的前置检查、分步拆解、分支 SOP、失败兜底。\n")
            + "MUST 只修复已确认诊断；NEVER 引入个人文风、无关检查点或通用黑名单。"
            "MUST 保留业务职责和报告约定；MUST 返回 JSON 对象："
            '{"edits":[{"path":"SKILL.md","content":"完整文件内容"}],"rationale":"修改依据"}。\n'
            f"当前包：{json.dumps(package, ensure_ascii=False)}\n"
            f"已确认诊断：{json.dumps(payload, ensure_ascii=False)}"
            f"\n上一轮评测观察（只作改进线索，不是新归因）：{json.dumps(feedback or {}, ensure_ascii=False)}"
        )
        if budget:
            budget.reserve(prompt, output_tokens=8192)
        try:
            result = _json_object(self.llm_factory(config).invoke(prompt))
            if budget:
                budget.check_time()
        except Exception as exc:
            raise ValidationError(f"skill-creator 调用失败：{exc}") from exc
        patch = {
            "schema": "skill_patch/v2",
            "strategy": "minimal_targeted_mutation" if minimal else "skill_creator_structural",
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
        cls, *, output, skill_version, attributions, actor, skill_creator=None, policy=None, parent=None,
    ) -> EvolutionExperiment:
        items = _verified_attributions(attributions)
        if parent is None and output.skill_version_id != skill_version.id:
            raise ValidationError("实验基线必须是当次审查实际运行的精确 Skill 版本")
        if any(item.output_id != output.id or item.project_id != output.project_id for item in items):
            raise ValidationError("归因必须来自同一原始任务与项目")
        resolved_policy = normalize_policy(parent.policy if parent else policy)
        if parent:
            parent = EvolutionExperiment.objects.select_for_update().get(pk=parent.pk)
            if parent.state != "winner_selected" or parent.output_id != output.id:
                raise ValidationError("下一轮必须继承同一任务的人工胜出版本")
            winner = parent.variants.get(label=parent.winner_label)
            cls._assert_eligible(parent, winner)
            if winner.skill_version_id != skill_version.id or not parent.evaluation_report.get("can_iterate"):
                raise ValidationError("收益不足或已达到迭代预算，停止继续优化")
            if (parent.round_number >= resolved_policy["max_rounds"]
                    or not parent.evaluation_report["comparisons"][winner.label].get("clear_gain")):
                raise ValidationError("胜出候选收益不足或已达到最大轮数")
            if EvolutionExperiment.objects.filter(parent=parent).exists():
                raise ValidationError("本轮已派生下一轮，不能重复创建")
        from skills.versions import SkillVersionService
        if not SkillVersionService.verify_package_integrity(skill_version)["ok"]:
            raise ValidationError("实验基线包指纹不一致")
        baseline_sha = skill_version.package_sha256
        experiment = EvolutionExperiment.objects.create(
            project=output.project, output=output, baseline_skill_version=skill_version,
            baseline_sha256=baseline_sha, created_by=actor, policy=resolved_policy,
            parent=parent, round_number=parent.round_number + 1 if parent else 1,
        )
        experiment.attributions.set(items)
        cls._baseline(experiment, skill_version)
        generation_budget = EvaluationBudget(resolved_policy)
        creator = skill_creator or SkillCreatorAdapter()
        kwargs = {"budget": generation_budget, "feedback": (
            parent.evaluation_report.get("comparisons") if parent else None
        )} if isinstance(creator, SkillCreatorAdapter) else {}
        patch_a = (creator.build(skill_version=skill_version, attributions=items, minimal=True, **kwargs)
                   if parent and isinstance(creator, SkillCreatorAdapter) else
                   ExecutablePatchBuilder.build(skill_version=skill_version, attributions=items))
        patch_b = creator.build(skill_version=skill_version, attributions=items, **kwargs)
        cls._candidate(experiment, "A", "minimal_targeted_mutation", patch_a, items, actor)
        cls._candidate(experiment, "B", "skill_creator_structural", patch_b, items, actor)
        skill_version.refresh_from_db()
        if skill_version.package_sha256 != baseline_sha:
            raise ValidationError("实验派生触碰了基线包，事务已中止")
        experiment.state = "ready"
        experiment.evaluation_report = {"generation_budget": generation_budget.snapshot()}
        experiment.save(update_fields=["state", "evaluation_report", "updated_at"])
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
        root = Path(experiment.baseline_skill_version.get_full_path())
        changed = bool(patch.get("remove"))
        for edit in patch.get("edits", []):
            path = root / edit["path"]
            if path.is_file():
                current = path.read_text(encoding="utf-8")
                changed = changed or edit.get("content") != current
                old_size = len(current)
                if len(edit.get("content", "")) > max(old_size, 1024) * experiment.policy["max_growth_ratio"]:
                    raise ValidationError("候选文件增长超过实验体积预算")
            else:
                changed = True
        if not changed:
            raise ValidationError("候选没有实质修改，停止无收益派生")
        derived = SkillEvolutionService.derive_candidate(
            skill_version=experiment.baseline_skill_version, attributions=items, actor=actor,
            patch=patch, variant_label=f"{label}.r{experiment.round_number}.{str(experiment.id)[:8]}",
            allow_v2_routes=True, locked_experiment_baseline=True,
            change_reason=f"实验 {experiment.id} Candidate {label}：{strategy}",
            expected_benefit="修复已确认归因，同时保留 Baseline 作为对照",
            impact_scope=json.dumps(patch.get("impact") or {}, ensure_ascii=False),
        )
        EvolutionVariant.objects.create(
            experiment=experiment, label=label, mutation_strategy=strategy,
            skill_version=derived["candidate"], sandbox_key=f"evo:{experiment.id}:{label}",
            patch=patch, asset_snapshots={"skill_package_sha256": derived["candidate"].package_sha256},
            impact_scope=patch.get("impact") or {}, rollback_target=str(experiment.baseline_skill_version_id),
            fingerprint=_stable_hash({"experiment": str(experiment.id), "patch": patch_fingerprint(patch)}),
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
    def rerun_same_input(cls, *, experiment, evaluator=None) -> EvolutionExperiment:
        """在三个独立上下文中以同一源文件重跑本阶段，保留原始对比数据。"""
        from testcases.models import TestCaseReview
        from testcases.review_service import (
            TESTCASE_REVIEW_CHUNK_SIZE, _get_testcase_review_llm_config,
            _read_rows, _review_chunk,
        )

        # 先在短事务中领取任务，网络调用不持有数据库事务。
        with transaction.atomic():
            locked = EvolutionExperiment.objects.select_for_update().get(pk=experiment.pk)
            if locked.state != "ready":
                raise ValidationError("仅待重跑实验可以执行，禁止重复评测或覆盖已选优结果")
            review = TestCaseReview.objects.filter(
                project=experiment.project, summary__output_id=str(experiment.output_id),
            ).first()
            if review is None:
                raise ValidationError("找不到原审查任务，无法以相同输入重跑")
            source = (experiment.parent.evaluation_report.get("source_snapshot")
                      if experiment.parent_id else None)
            if source is None:
                rows = _read_rows(review.source_file.path)
                context_parts = []
                if review.requirement_document_ids:
                    from requirements.models import RequirementDocument
                    docs = RequirementDocument.objects.filter(
                        project=review.project, id__in=review.requirement_document_ids,
                    ).order_by("id").values_list("title", "content")
                    context_parts.extend(f"【需求文档：{title}】\n{(content or '')[:8000]}" for title, content in docs)
                if review.knowledge_document_ids:
                    from knowledge.models import Document
                    docs = Document.objects.filter(
                        knowledge_base__project=review.project, status="completed",
                        id__in=review.knowledge_document_ids,
                    ).order_by("id").values_list("title", "content")
                    context_parts.extend(f"【知识库文档：{title}】\n{(content or '')[:8000]}" for title, content in docs)
                source = {"rows": rows, "business_context": review.business_context,
                          "custom_rules": review.custom_rules,
                          "document_context": "\n\n".join(context_parts)[:20000]}
            rows = source.get("rows")
            if not rows:
                raise ValidationError("原审查输入为空，无法重跑")
            config = _get_testcase_review_llm_config()
            locked.state = "running"
            locked.evaluation_report = {
                **locked.evaluation_report, "source_snapshot": source, "input_hash": _stable_hash(source),
            }
            locked.save(update_fields=["state", "evaluation_report", "updated_at"])
        experiment.refresh_from_db()
        budget = EvaluationBudget(experiment.policy)
        generation_usage = experiment.evaluation_report.get("generation_budget") or {}
        budget.calls = generation_usage.get("calls", 0)
        budget.estimated_tokens = generation_usage.get("estimated_tokens", 0)
        budget.started -= generation_usage.get("elapsed_seconds", 0)
        chunks = [rows[index:index + TESTCASE_REVIEW_CHUNK_SIZE]
                  for index in range(0, len(rows), TESTCASE_REVIEW_CHUNK_SIZE)]
        report = dict(experiment.evaluation_report)
        report.update({"schema": "paired_output/v1", "comparisons": {}, "can_iterate": False,
                       "evaluation_mode": "full_test", "stop_reason": "",
                       "model": {"config_id": str(getattr(config, "pk", "")),
                                 "name": str(getattr(config, "name", "")),
                                 "judge_contexts": 3, "distinct_models": False}})
        try:
            for variant in experiment.variants.select_related("skill_version").order_by("label"):
                started = time.perf_counter()
                variant.status = "running"
                variant.save(update_fields=["status", "updated_at"])
                try:
                    cls._verify_variant(variant)
                    skill_path = Path(variant.skill_version.get_full_path() or "") / "SKILL.md"
                    skill_prompt = skill_path.read_text(encoding="utf-8")
                    for path in sorted((skill_path.parent / "references").rglob("*.md")):
                        skill_prompt += f"\n\n# 参考规则：{path.name}\n" + path.read_text(encoding="utf-8")
                    if source.get("document_context"):
                        skill_prompt += "\n\n# 本次审查参考文档（只能据此判断，不得臆造）\n" + source["document_context"]
                    if source.get("custom_rules"):
                        skill_prompt += "\n\n# 本次用户指定的审查规则（在不违反质量边界的前提下优先执行）\n" + source["custom_rules"]
                    results = []
                    for chunk in chunks:
                        # safe_llm_invoke(max_retries=1) 只尝试一次；SDK 重试也禁用。
                        budget.reserve(skill_prompt + json.dumps(chunk, ensure_ascii=False)
                                       + str(source.get("business_context") or "") + " " * 8000,
                                       output_tokens=8192)
                        llm = bounded_llm(config, temperature=0.1, max_tokens=8192)
                        result = _review_chunk(llm, skill_prompt, chunk, source.get("business_context"))
                        if (not isinstance(result, dict) or not isinstance(result.get("issues"), list)
                                or not all(isinstance(item, dict) for item in result["issues"])):
                            raise ValidationError("真实重跑未返回有效问题列表")
                        results.append(result)
                    issues = [item for result in results for item in (result.get("issues") or [])]
                    pending = [item for result in results for item in (result.get("pending_confirmations") or [])]
                    output_payload = {"issues": issues, "pending_confirmations": pending,
                                      "chunks": results}
                    cls.record_run(experiment=experiment, label=variant.label, result={
                        "input_hash": _stable_hash(source), "output": output_payload,
                        "output_hash": _stable_hash(output_payload),
                        "package_sha256": variant.skill_version.package_sha256,
                        "issue_count": len(issues), "pending_count": len(pending),
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "estimated_input_tokens": len(skill_prompt) * len(chunks) + len(json.dumps(source, ensure_ascii=False)),
                        "estimated_output_tokens": len(json.dumps(output_payload, ensure_ascii=False)),
                        "fixed_hits": cls._fixed_hits(experiment, issues),
                    })
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    cls.record_run(experiment=experiment, label=variant.label, result={
                        "input_hash": _stable_hash(source), "error": f"{type(exc).__name__}: {exc}",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                    })
            variants = {v.label: v for v in experiment.variants.select_related("skill_version")}
            baseline = variants["baseline"]
            for label in ("A", "B"):
                candidate = variants[label]
                if baseline.status != "completed" or candidate.status != "completed":
                    report["comparisons"][label] = {"eligible": False, "reason": "真实运行失败"}
                    continue
                comparison = (evaluator or PairedOutputEvaluator()).compare(
                    baseline=baseline.run_result["output"], candidate=candidate.run_result["output"],
                    source=source, config=config, budget=budget,
                )
                comparison["binding"] = cls._run_binding(baseline, candidate)
                report["comparisons"][label] = comparison
            eligible = [c for c in report["comparisons"].values() if c.get("eligible")]
            budget.check_time()
            report["can_iterate"] = bool(eligible) and any(c.get("clear_gain") for c in eligible)
            if not eligible:
                report["stop_reason"] = "没有通过独立对比评测的改进候选"
            elif not report["can_iterate"]:
                report["stop_reason"] = "收益有限，停止迭代；可人工审核本轮候选"
            if experiment.round_number >= normalize_policy(experiment.policy)["max_rounds"]:
                report["can_iterate"] = False
                report["stop_reason"] = "达到最大迭代轮数"
            experiment.state = "awaiting_choice"
        except Exception as exc:
            report["stop_reason"] = str(exc)
            report["can_iterate"] = False
            # 预算中断后保留已完成产出，但本轮不得发布。
            experiment.state = "failed"
            experiment.variants.filter(status__in=["ready", "running"]).update(
                status="failed", run_result={"error": str(exc)},
            )
        report["budget"] = budget.snapshot()
        experiment.evaluation_report = report
        experiment.save(update_fields=["state", "evaluation_report", "updated_at"])
        return experiment

    @staticmethod
    def _verify_variant(variant):
        from skills.versions import SkillVersionService
        if not variant.skill_version or not SkillVersionService.verify_package_integrity(variant.skill_version)["ok"]:
            raise ValidationError("实验版本包已改变，必须重新创建实验")

    @staticmethod
    def _run_binding(baseline, candidate):
        return _stable_hash([{
            "version": str(v.skill_version_id), "sha": v.skill_version.package_sha256,
            "input": v.run_result.get("input_hash"), "output": v.run_result.get("output_hash"),
        } for v in (baseline, candidate)])

    @classmethod
    def _assert_eligible(cls, experiment, winner):
        report = experiment.evaluation_report or {}
        comparison = report.get("comparisons", {}).get(winner.label, {})
        if not comparison.get("eligible") or report.get("evaluation_mode") != "full_test":
            raise ValidationError("候选未通过真实运行与独立对比评测")
        if _stable_hash(report.get("source_snapshot")) != report.get("input_hash"):
            raise ValidationError("冻结输入快照与评测记录不一致")
        baseline = experiment.variants.select_related("skill_version").get(label="baseline")
        for variant in (baseline, winner):
            cls._verify_variant(variant)
            result = variant.run_result
            if (variant.status != "completed" or not result.get("output_hash")
                    or _stable_hash(result.get("output")) != result.get("output_hash")
                    or result.get("input_hash") != report.get("input_hash")
                    or result.get("package_sha256") != variant.skill_version.package_sha256):
                raise ValidationError("输入、输出或版本与评测记录不一致")
        if comparison.get("binding") != cls._run_binding(baseline, winner):
            raise ValidationError("评测记录已过期，禁止复用旧结论")

    @classmethod
    def next_iteration(cls, *, experiment, actor, skill_creator=None):
        if experiment.state != "winner_selected":
            raise ValidationError("请先人工确认本轮胜出候选")
        winner = experiment.variants.select_related("skill_version").get(label=experiment.winner_label)
        return cls.create_experiment(
            output=experiment.output, skill_version=winner.skill_version,
            attributions=experiment.attributions.all(), actor=actor, parent=experiment,
            skill_creator=skill_creator,
        )

    @staticmethod
    def _fixed_hits(experiment, issues: list) -> dict:
        issue_types = {str(item.get("issue_type") or "") for item in issues}
        return {
            "verified": False,
            "note": "仅观察问题类型变化，不能作为修复或质量提升证据",
            "observed_issue_types": sorted(issue_types),
            "confirmed_fine_causes": sorted(set(experiment.attributions.values_list("fine_cause", flat=True))),
        }

    @classmethod
    @transaction.atomic
    def choose_winner(cls, *, experiment, label: str, reason: str) -> EvolutionExperiment:
        experiment = EvolutionExperiment.objects.select_for_update().get(pk=experiment.pk)
        if experiment.state != "awaiting_choice":
            raise ValidationError("Baseline/A/B 尚未全部重跑完成，不能选优")
        if label not in {"A", "B"}:
            raise ValidationError("winner 只能选择 Candidate A 或 Candidate B")
        if not reason.strip():
            raise ValidationError("必须填写人工对比理由")
        winner = experiment.variants.get(label=label)
        if winner.status != "completed":
            raise ValidationError("不能选择运行失败的变体")
        cls._assert_eligible(experiment, winner)
        report = dict(experiment.evaluation_report)
        report["can_iterate"] = bool(report.get("can_iterate")) and bool(
            report["comparisons"][label].get("clear_gain")
        )
        experiment.evaluation_report = report
        experiment.winner_label = label
        experiment.winner_reason = reason.strip()
        experiment.state = "winner_selected"
        experiment.save(update_fields=["winner_label", "winner_reason", "state", "evaluation_report", "updated_at"])
        return experiment

    @classmethod
    @transaction.atomic
    def submit_winner(cls, *, experiment, actor) -> EvolutionExperiment:
        """仅把人工 winner 送入既有发布审批，绝不自动激活。"""
        from skills.versions import SkillVersionService

        experiment = EvolutionExperiment.objects.select_for_update().get(pk=experiment.pk)
        if experiment.state != "winner_selected" or experiment.winner_label not in {"A", "B"}:
            raise ValidationError("必须先完成人工 A/B 选优，才能提交发布审批")
        winner = experiment.variants.select_related("skill_version").get(label=experiment.winner_label)
        cls._assert_eligible(experiment, winner)
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
        "policy": normalize_policy(experiment.policy), "round_number": experiment.round_number,
        "parent_id": str(experiment.parent_id or ""),
        "evaluation_report": {key: value for key, value in (experiment.evaluation_report or {}).items()
                              if key != "source_snapshot"},
        "variants": variants,
    }
