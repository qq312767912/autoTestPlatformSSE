"""Skill 自进化 V2 的结构化归因词表。

``FailureAttribution.category`` 仍表示运行结果层的原始失败信号；
本模块回答“根因是什么、应改哪一层”。词表是 Critic Prompt、
服务端验证和前端级联选择的单一真值。
"""
from __future__ import annotations

from django.core.exceptions import ValidationError


PRIMARY_CAUSES = {
    "prompt_rules": {
        "label": "规则/指令漏洞",
        "fine_causes": {
            "format_protocol_violation": ("格式协议违背", ("prompt", "skill_sop")),
            "required_field_missing": ("核心字段缺失", ("prompt", "skill_sop")),
            "boundary_uncovered": ("边界约束未覆盖", ("prompt", "skill_sop", "few_shot")),
            "instruction_priority_ambiguity": ("指令优先级歧义", ("prompt", "skill_sop")),
        },
    },
    "context_rag": {
        "label": "上下文与记忆",
        "fine_causes": {
            "history_truncated": ("历史上下文截断", ("workflow", "rag")),
            "retrieval_noise": ("检索片段噪声大", ("rag",)),
            "critical_context_missed": ("关键信息未召回", ("rag", "knowledge")),
        },
    },
    "tools_logic": {
        "label": "工具与逻辑",
        "fine_causes": {
            "invalid_tool_argument": ("参数格式/枚举非法", ("tool_schema", "code")),
            "tool_api_failure": ("API 逻辑异常", ("code", "workflow")),
            "tool_chain_step_skipped": ("多工具调用链跳步", ("workflow", "skill_sop")),
        },
    },
    "domain_knowledge": {
        "label": "知识与语义",
        "fine_causes": {
            "terminology_mismatch": ("行业术语/专有名词偏差", ("knowledge", "few_shot")),
            "private_semantic_drift": ("领域私有语义理解漂移", ("knowledge", "few_shot", "rag")),
        },
    },
    "skill_schema": {
        "label": "Skill 结构缺失",
        "fine_causes": {
            "precheck_missing": ("缺少前置检查步骤", ("skill_sop",)),
            "branch_sop_missing": ("缺乏特定分支 SOP", ("skill_sop",)),
            "guided_instruction_missing": ("引导式提示缺失", ("skill_sop", "prompt")),
            "fallback_missing": ("兜底流程缺失", ("skill_sop", "workflow")),
        },
    },
}

REPAIR_TARGET_LABELS = {
    "skill_sop": "Skill SOP", "prompt": "Prompt", "few_shot": "Few-Shot",
    "tool_schema": "Tool Schema", "workflow": "Workflow", "code": "Code",
    "knowledge": "Knowledge", "rag": "RAG",
}

PRIMARY_CAUSE_CHOICES = tuple((key, value["label"]) for key, value in PRIMARY_CAUSES.items())
FINE_CAUSE_CHOICES = tuple(
    (fine, config[0])
    for primary in PRIMARY_CAUSES.values()
    for fine, config in primary["fine_causes"].items()
)
REPAIR_TARGET_CHOICES = tuple(REPAIR_TARGET_LABELS.items())
EVIDENCE_STATUS_CHOICES = (
    ("unverified", "未验证"), ("verified", "已验证"),
    ("unresolvable", "无法定位"),
)


def allowed_repair_targets(primary_cause: str, fine_cause: str) -> tuple[str, ...]:
    primary = PRIMARY_CAUSES.get(str(primary_cause or ""))
    if not primary:
        return ()
    fine = primary["fine_causes"].get(str(fine_cause or ""))
    return tuple(fine[1]) if fine else ()


def validate_diagnosis(primary_cause: str, fine_cause: str, repair_target: str) -> None:
    """校验“一级归因 + 二级原因 + 修复目标”的路由。"""
    if primary_cause not in PRIMARY_CAUSES:
        raise ValidationError(f"未知的一级归因：{primary_cause or '（空）'}")
    if fine_cause not in PRIMARY_CAUSES[primary_cause]["fine_causes"]:
        raise ValidationError(f"二级归因 {fine_cause or '（空）'} 不属于 {primary_cause}")
    allowed = allowed_repair_targets(primary_cause, fine_cause)
    if repair_target not in allowed:
        raise ValidationError(
            f"修复目标 {repair_target or '（空）'} 不适用于 {fine_cause}；"
            f"允许值：{'、'.join(allowed)}"
        )


def taxonomy_view() -> dict:
    return {
        "schema_version": "skill_attribution/v2",
        "primary_causes": [
            {
                "value": primary_key,
                "label": primary["label"],
                "fine_causes": [
                    {
                        "value": fine_key,
                        "label": fine[0],
                        "repair_targets": [
                            {"value": target, "label": REPAIR_TARGET_LABELS[target]}
                            for target in fine[1]
                        ],
                    }
                    for fine_key, fine in primary["fine_causes"].items()
                ],
            }
            for primary_key, primary in PRIMARY_CAUSES.items()
        ],
    }
