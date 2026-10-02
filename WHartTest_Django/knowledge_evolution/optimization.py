"""基于已确认失败归因生成安全、可评测但不会自动发布的优化候选。"""
from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction

from .optimization_models import OptimizationProposal


TYPE_BY_CATEGORY = {
    "intent_error": "prompt",
    "planning_error": "prompt",
    "prompt_error": "prompt",
    "generation_error": "prompt",
    "knowledge_missing": "knowledge",
    "knowledge_stale": "knowledge",
    "retrieval_error": "retrieval_policy",
    "tool_error": "skill_tool",
    "downstream_execution_error": "skill_tool",
}

#: 明确**不可**通过改候选包修复的归因类别（T11/T12）。
#:
#: ``environment_error`` 指依赖缺失、超时、配额、网络这类执行环境问题。
#: 它没有对应的优化类型——不是"暂时没实现"，而是**改 Prompt/Skill/知识都不解决它**。
#: 如果把它硬塞进某个类型，会产出"看起来在优化、实际上无效"的候选，
#: 并且污染评测基线（真实回归率被环境噪声掩盖）。所以这里显式拒绝并说明原因。
NON_OPTIMIZABLE_CATEGORIES = {
    "environment_error": "执行环境问题需修复运行环境本身，不能通过修改能力候选解决",
}


TEMPLATES = {
    "prompt": {
        "operation": "add_guardrail_and_reasoning_check",
        "instructions": ["补充失败维度检查", "输出前执行证据一致性和反证校验"],
    },
    "knowledge": {
        "operation": "create_knowledge_candidate",
        "instructions": ["从已确认Badcase提取规则", "保留来源证据并进入人工审核"],
    },
    "retrieval_policy": {
        "operation": "adjust_retrieval_policy",
        "instructions": ["扩大有效召回通道", "增加噪声过滤和证据重排"],
    },
    "skill_tool": {
        "operation": "adjust_skill_tool_config",
        "instructions": ["增加调用前参数校验", "增加失败重试、降级和结果校验"],
    },
}


class OptimizationProposalService:
    @staticmethod
    @transaction.atomic
    def generate(*, attributions, actor, capability=None, baseline_release=None):
        attributions = list(attributions)
        if not attributions:
            raise ValidationError("至少需要一条失败归因")
        if any(item.state != "confirmed" for item in attributions):
            raise ValidationError("只有人工确认的归因才能生成优化候选")
        project_ids = {item.project_id for item in attributions}
        if len(project_ids) != 1:
            raise ValidationError("优化候选不能跨项目生成")
        if capability and capability.project_id not in project_ids:
            raise ValidationError("能力定义与归因不属于同一项目")

        groups = {}
        blocked = []
        for item in attributions:
            if item.category in NON_OPTIMIZABLE_CATEGORIES:
                blocked.append(item.category)
                continue
            proposal_type = TYPE_BY_CATEGORY.get(item.category)
            if proposal_type is None:
                blocked.append(item.category)
                continue
            groups.setdefault(proposal_type, []).append(item)

        if blocked:
            reasons = sorted({NON_OPTIMIZABLE_CATEGORIES.get(
                category, f"归因类别 {category} 没有对应的可优化类型"
            ) for category in blocked})
            raise ValidationError("；".join(reasons))
        if not groups:
            raise ValidationError("所提供的归因没有可生成优化候选的类型")

        proposals = []
        for proposal_type, items in groups.items():
            attribution_ids = sorted(str(item.id) for item in items)
            identity = f"{next(iter(project_ids))}:{proposal_type}:{','.join(attribution_ids)}:{capability.id if capability else ''}"
            fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            proposal, created = OptimizationProposal.objects.get_or_create(
                fingerprint=fingerprint,
                defaults={
                    "project_id": next(iter(project_ids)),
                    "capability": capability,
                    "baseline_release": baseline_release,
                    "proposal_type": proposal_type,
                    "title": f"{proposal_type} 优化候选 · {len(items)} 条已确认归因",
                    "summary": "；".join(sorted({item.hypothesis for item in items})),
                    "change_patch": TEMPLATES[proposal_type],
                    "expected_benefit": {
                        "target_categories": sorted({item.category for item in items}),
                        "target_badcase_count": len(items),
                    },
                    "impact_scope": {"capability_id": str(capability.id) if capability else None},
                    "risk_notes": ["候选尚未通过金标、挑战、隐藏和影子验证"],
                    "rollback_plan": {"strategy": "restore_previous_capability_release"},
                    "created_by": actor,
                },
            )
            if created:
                proposal.attributions.set(items)
            proposals.append(proposal)
        return proposals


class PromptOptimizer:
    """用平台激活 LLM 生成可审计 Prompt 候选，不直接发布。"""

    def __init__(self, llm_factory=None):
        self.llm_factory = llm_factory or self._default_factory

    @staticmethod
    def _default_factory(config):
        from langgraph_integration.views import create_llm_instance
        return create_llm_instance(config, temperature=0.1)

    def generate(self, proposal):
        from langgraph_integration.models import LLMConfig
        from .llm_judges import _json_object

        config = LLMConfig.objects.filter(is_active=True).first()
        if not config:
            raise ValidationError("未配置激活LLM，无法生成Prompt候选")
        baseline = (proposal.baseline_release.config if proposal.baseline_release_id else {}) or {}
        original = str(baseline.get("prompt") or baseline.get("system_prompt") or "")
        prompt = (
            "你是受控Prompt优化器。只能在原Prompt上补充已确认失败维度的检查，"
            "不得删除安全、权限、全覆盖和反证要求。"
            "仅返回JSON：{\"candidate_prompt\":\"...\",\"changes\":[],\"risks\":[]}\n"
            f"原Prompt：{original}\n"
            f"失败归因：{json.dumps(list(proposal.attributions.values('category','hypothesis')), ensure_ascii=False)}"
        )
        payload = _json_object(self.llm_factory(config).invoke(prompt))
        candidate = str(payload.get("candidate_prompt") or "").strip()
        if not candidate:
            raise ValidationError("LLM未返回有效的candidate_prompt")
        if original and original not in candidate:
            raise ValidationError("候选Prompt必须保留原Prompt全文，只允许增量优化")
        return {
            "prompt": candidate,
            "diff": {"mode": "append_only", "changes": payload.get("changes") or []},
            "risks": payload.get("risks") or [],
            "generator_model": config.name,
        }


class OptimizationMaterializationService:
    """把四类优化建议落为隔离的候选发布单元，从不修改当前生产对象。"""

    @staticmethod
    def _ensure_allowed(proposal):
        if proposal.state != "draft":
            raise ValidationError("只有草稿优化建议可以生成候选")
        if not proposal.attributions.exists() or proposal.attributions.exclude(state="confirmed").exists():
            raise ValidationError("候选必须仅来自已确认归因")
        prohibited = proposal.attributions.filter(
            output__gold_cases__allow_optimization=False,
        ).exists()
        if prohibited:
            raise ValidationError("候选包含禁止用于优化的金标样本")

    @staticmethod
    def _version(proposal):
        return f"candidate-{str(proposal.id)[:12]}"

    @classmethod
    @transaction.atomic
    def materialize(cls, *, proposal, actor=None, prompt_optimizer=None):
        from .capabilities import CapabilityReleaseService
        from .capability_models import CapabilityRelease
        from .knowledge_models import KnowledgeCandidate
        from .retrieval_models import RetrievalPolicy

        cls._ensure_allowed(proposal)
        expected_kind = "skill" if proposal.proposal_type == "skill_tool" else proposal.proposal_type
        existing = CapabilityRelease.objects.filter(
            project=proposal.project, kind=expected_kind, version=cls._version(proposal),
        ).first()
        if existing:
            return existing
        baseline = (proposal.baseline_release.config if proposal.baseline_release_id else {}) or {}
        release_kind = proposal.proposal_type
        candidate = None
        if proposal.proposal_type == "prompt":
            config = (prompt_optimizer or PromptOptimizer()).generate(proposal)
        elif proposal.proposal_type == "knowledge":
            hypotheses = sorted({item.hypothesis for item in proposal.attributions.all()})
            payload = {
                "title": proposal.title, "content": "\n".join(hypotheses),
                "source_attribution_ids": [str(value) for value in proposal.attributions.values_list("id", flat=True)],
            }
            dedup = hashlib.sha256(
                f"{proposal.project_id}:knowledge:{json.dumps(payload, ensure_ascii=False, sort_keys=True)}".encode()
            ).hexdigest()
            candidate, _ = KnowledgeCandidate.objects.get_or_create(
                dedup_key=dedup,
                defaults={
                    "project": proposal.project, "kind": "knowledge_atom",
                    "origin": "evaluation_failure", "payload": payload,
                    "confidence": min(item.confidence for item in proposal.attributions.all()),
                    "evidence": [{"attribution_id": value} for value in payload["source_attribution_ids"]],
                    "extracted_by": "controlled-knowledge-optimizer-v1", "created_by": actor,
                },
            )
            config = {"candidate_id": str(candidate.id), "payload_hash": dedup}
        elif proposal.proposal_type == "retrieval_policy":
            base_config = dict(baseline.get("config") or baseline)
            sources = dict(base_config.get("sources") or {})
            graph = dict(sources.get("graph") or {})
            graph.update({"enabled": True, "k": min(200, max(1, int(graph.get("k") or 80)))})
            sources["graph"] = graph
            config = {**base_config, "sources": sources, "counterevidence_filter": True}
            last_version = RetrievalPolicy.objects.filter(
                project=proposal.project, name=proposal.title,
            ).order_by("-version").values_list("version", flat=True).first() or 0
            policy = RetrievalPolicy.objects.create(
                project=proposal.project, name=proposal.title, version=last_version + 1,
                is_default=False, is_active=False, config=config, created_by=actor,
            )
            config = {"policy_id": str(policy.id), "policy_config": config}
        elif proposal.proposal_type == "skill_tool":
            allowed = {"timeout_seconds", "max_retries", "validate_input", "validate_output", "fallback"}
            requested = (proposal.change_patch or {}).get("config") or {}
            unknown = set(requested) - allowed
            if unknown:
                raise ValidationError(f"Skill/工具候选包含未允许配置: {sorted(unknown)}")
            config = {
                **{key: value for key, value in baseline.items() if key in allowed},
                "timeout_seconds": min(300, max(1, int(requested.get("timeout_seconds", 60)))),
                "max_retries": min(5, max(0, int(requested.get("max_retries", 2)))),
                "validate_input": bool(requested.get("validate_input", True)),
                "validate_output": bool(requested.get("validate_output", True)),
                "fallback": requested.get("fallback") or "fail_closed",
            }
            release_kind = "skill"
        else:
            raise ValidationError(f"不支持的优化类型: {proposal.proposal_type}")

        release = CapabilityReleaseService.create(
            project=proposal.project, kind=release_kind, name=proposal.title,
            version=cls._version(proposal), config=config, actor=actor, candidate=candidate,
        )
        proposal.change_patch = {**(proposal.change_patch or {}), "materialized_release_id": str(release.id)}
        proposal.save(update_fields=["change_patch", "updated_at"])
        return release
