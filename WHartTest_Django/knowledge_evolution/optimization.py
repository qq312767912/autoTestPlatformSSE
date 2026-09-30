"""基于已确认失败归因生成安全、可评测但不会自动发布的优化候选。"""
from __future__ import annotations

import hashlib

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
        for item in attributions:
            groups.setdefault(TYPE_BY_CATEGORY[item.category], []).append(item)

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
