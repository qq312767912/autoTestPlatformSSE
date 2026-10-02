"""T10：业务能力注册表——把"能力"的分类口径收敛成唯一真值。

需求里出现的分类维度有三套，容易互相打架，这里一次性说清：

1. **可进化能力的形态**（需求 §2.1 / §2.2）
   - Skill 型：`case_review`、`test_plan_generation`、`testcase_generation`、
     `test_execution`、`report_generation`、`risk_identification`、`issue_tracking`
     （7 个，走 Skill 包 + 版本化运行时）
   - 复合型：`code_review`（走 Prompt/机器规则/CRG/反证/工具配置的子单元聚合发布）

2. **四阶段主链路**（唯一真值，与 ``operations.DEFAULT_WORKFLOW_STAGE_ORDER`` 一致）
   ``test_plan_generation -> testcase_generation -> test_execution -> report_generation``
   即 **方案生成 → 用例生成 → 测试执行 → 报告产出**。

   短期出现过一条中间模板
   ``risk_identification -> testcase_generation -> test_execution -> issue_tracking``，
   已退回为**历史模板**（``LEGACY_WORKFLOW_STAGES``）：它只用于解释那几天内发起过的
   流程，新流程不再使用。``risk_identification`` / ``issue_tracking`` 两个能力保留，
   但**不在主链路上**——它们是单次能力（MODE_SINGLE + 三分区），
   与主链路四阶段（MODE_WORKFLOW + 五分区）区分开。

3. **平台工具**：`knowledge_query`——知识问答是平台基础能力，不作为业务能力对外呈现。

需求正文写的是"按八类业务能力"组织金标，而平台 ``EvaluationSuite.TASK_TYPE_CHOICES``
一共 9 项。两者的差在哪里，本模块给出**显式**答案而不是留个悬念：

    八类业务能力 = 7 个 Skill 型 + code_review（复合型）
    平台工具       = knowledge_query

加总仍是八类、九项任务类型，口径对得上，且不丢掉任何一类任务的归属。金标组织、
门禁分区策略、前端目录分组都从 ``CAPABILITY_REGISTRY`` 取，避免同一份分类在三个
地方各写一遍。
"""
from __future__ import annotations

#: 五个评测分区，顺序即"分区完整性"检查的展示顺序。
#: 定义在此处（不依赖任何模型）是因为门禁服务、金标目录、前端目录都要用它；
#: 放在依赖模型的文件里会形成"注册表 → 模型 → 注册表"的回环。
PARTITION_ORDER = ("gold", "regression", "fresh", "challenge", "hidden")

#: 别名，供只关心"分区顺序"的调用方使用，语义更明确。
PARTITION_ORDER_SAFE = PARTITION_ORDER

#: 每个分区的中文名，用于给用户返回可读的缺失条件。
PARTITION_LABELS = {
    "gold": "金标集",
    "regression": "回归集",
    "fresh": "新鲜集",
    "challenge": "挑战集",
    "hidden": "隐藏集",
}

#: 影子分区不是 case 分区，而是"候选与基线可比对"的关系（详见 gate_models 文档）。
SHADOW_PARTITION = "shadow"


#: 四阶段主链路（唯一真值，与 ``operations.DEFAULT_WORKFLOW_STAGE_ORDER`` 一致）。
#: 方案生成 → 用例生成 → 测试执行 → 报告产出。
WORKFLOW_STAGES = (
    "test_plan_generation",
    "testcase_generation",
    "test_execution",
    "report_generation",
)

#: 历史主链路：2026-10-02 前后短暂启用过的四阶段模板，期间发起过真实流程。
#: 保留它只为一件事：让那批存量流程仍能被正确解释、继续推进，而不是变成读不出来的砖。
#: 新流程**不再**使用这个序列，也不要把新代码往它上面挂。
LEGACY_WORKFLOW_STAGES = (
    "risk_identification",
    "testcase_generation",
    "test_execution",
    "issue_tracking",
)

#: 出现过的全部链路阶段（新 + 历史）。判定"某个 stage 是不是链路阶段"必须用它，
#: 只认 ``WORKFLOW_STAGES`` 会让存量流程的产出被当成旁路产出而丢掉门禁。
ALL_WORKFLOW_STAGES = tuple(
    dict.fromkeys(WORKFLOW_STAGES + LEGACY_WORKFLOW_STAGES)
)

#: Skill 型可进化能力：主链路四阶段 + 单次能力。
#: 链路阶段必须在这里——它们靠 Skill 包驱动、需要版本锁定与分流回滚。
#: 顺序即 Skill Hub 目录与金标分组的展示顺序：**主链路在前，单次能力在后**。
SKILL_CAPABILITY_STAGES = (
    "test_plan_generation",
    "testcase_generation",
    "test_execution",
    "report_generation",
    "case_review",
    "risk_identification",
    "issue_tracking",
)

#: 复合型可进化能力（不得包装成 Skill）。
COMPOSITE_CAPABILITY_STAGES = ("code_review",)

#: 已无"只作数据源、不可进化"的业务阶段。
#: `risk_identification` / `issue_tracking` 已升为 Skill 型链路阶段；此处保留空元组
#: 是为了让 ``BUSINESS_CAPABILITY_STAGES`` 的构成式子仍然显式可读——不加这一项，
#: "八类"是怎么加出来的就又要靠猜。
FEEDBACK_SOURCE_STAGES = ()

#: 平台工具阶段：有产出、能反馈，但不作为业务能力对外呈现。
PLATFORM_UTILITY_STAGES = ("knowledge_query",)

#: 八类业务能力（口径见模块文档）。
BUSINESS_CAPABILITY_STAGES = (
    SKILL_CAPABILITY_STAGES + COMPOSITE_CAPABILITY_STAGES + FEEDBACK_SOURCE_STAGES
)

#: 平台支持的全部任务类型。
ALL_TASK_TYPES = BUSINESS_CAPABILITY_STAGES + PLATFORM_UTILITY_STAGES

STAGE_LABELS = {
    "case_review": "用例审查",
    "code_review": "代码审查",
    "test_plan_generation": "测试方案生成",
    "testcase_generation": "测试用例生成",
    "test_execution": "测试执行",
    "report_generation": "报告生成",
    "risk_identification": "风险识别",
    "issue_tracking": "问题跟踪",
    "knowledge_query": "知识问答",
}

#: 能力形态。
KIND_SKILL = "skill"
KIND_COMPOSITE = "composite"
KIND_FEEDBACK_SOURCE = "feedback_source"
KIND_PLATFORM_UTILITY = "platform_utility"

#: 评测模式（与 ``CapabilityDefinition.EVALUATION_MODE_CHOICES`` 对齐）。
MODE_SINGLE = "single"
MODE_WORKFLOW = "workflow"

#: 能力注册表：task_type -> 分类信息。
#: ``partitions`` 是该能力门禁**必需**的分区集合：全链路测试必须五分区齐全，
#: 单能力闭环只需金标+回归+新鲜，避免为了凑"隐藏集"而制造无意义样本。
CAPABILITY_REGISTRY = {
    "case_review": {
        "label": STAGE_LABELS["case_review"], "kind": KIND_SKILL, "mode": MODE_SINGLE,
        "partitions": ("gold", "regression", "fresh"),
        "findings_based": True,
    },
    "code_review": {
        "label": STAGE_LABELS["code_review"], "kind": KIND_COMPOSITE, "mode": MODE_SINGLE,
        "partitions": ("gold", "regression", "fresh", "challenge"),
        "findings_based": True,
    },
    # 主链路四阶段：方案生成 → 用例生成 → 测试执行 → 报告产出。
    # ``mode`` 为 ``workflow`` 表示它们靠 Skill 包驱动、参与版本锁定与分流回滚，
    # 且门禁**必须五分区齐全**。
    "test_plan_generation": {
        "label": STAGE_LABELS["test_plan_generation"], "kind": KIND_SKILL, "mode": MODE_WORKFLOW,
        "partitions": ("gold", "regression", "fresh", "challenge", "hidden"),
        "findings_based": False,
    },
    "testcase_generation": {
        "label": STAGE_LABELS["testcase_generation"], "kind": KIND_SKILL, "mode": MODE_WORKFLOW,
        "partitions": ("gold", "regression", "fresh", "challenge", "hidden"),
        "findings_based": False,
    },
    "test_execution": {
        "label": STAGE_LABELS["test_execution"], "kind": KIND_SKILL, "mode": MODE_WORKFLOW,
        "partitions": ("gold", "regression", "fresh", "challenge", "hidden"),
        "findings_based": False,
    },
    "report_generation": {
        "label": STAGE_LABELS["report_generation"], "kind": KIND_SKILL, "mode": MODE_WORKFLOW,
        "partitions": ("gold", "regression", "fresh", "challenge", "hidden"),
        "findings_based": False,
    },
    # 以下两项**不在主链路**，是单次能力：能评测、能反馈、能自进化，但不占链路阶段位。
    # 分区要求随之从五分区降到三分区——单能力闭环不需要凑"隐藏集"，
    # 否则为了满足一个已无链路意义的门禁去造无意义样本。
    "risk_identification": {
        "label": STAGE_LABELS["risk_identification"], "kind": KIND_SKILL, "mode": MODE_SINGLE,
        "partitions": ("gold", "regression", "fresh"),
        # 产出的是"识别出的高风险点"，误报/漏报对它同样有意义。
        "findings_based": True,
    },
    "issue_tracking": {
        "label": STAGE_LABELS["issue_tracking"], "kind": KIND_SKILL, "mode": MODE_SINGLE,
        "partitions": ("gold", "regression", "fresh"),
        # 产出的是"问题清单与闭环状态"，本质也是 findings。
        "findings_based": True,
    },
    "knowledge_query": {
        "label": STAGE_LABELS["knowledge_query"], "kind": KIND_PLATFORM_UTILITY,
        "mode": MODE_SINGLE, "partitions": ("gold", "regression"), "findings_based": False,
    },
}


def capability_info(task_type: str) -> dict:
    """取能力的分类信息；未知任务类型退化成"数据源"而不是抛错。

    退化而不是抛错，是因为 ``GenerationOutput.task_type`` 是自由 CharField，
    平台上确实可能存在尚未登记的类型。让它可被记录、但**不被当作可进化能力**，
    比让产出写入失败更安全。
    """
    return CAPABILITY_REGISTRY.get(task_type) or {
        "label": task_type or "未分类", "kind": KIND_FEEDBACK_SOURCE,
        "mode": MODE_SINGLE, "partitions": ("gold", "regression"), "findings_based": False,
    }


def is_evolvable(task_type: str) -> bool:
    """该能力是否允许派生候选版本（Skill 包或复合能力）。"""
    return capability_info(task_type)["kind"] in {KIND_SKILL, KIND_COMPOSITE}


def is_findings_based(task_type: str) -> bool:
    """该能力产出的是"发现的问题"——只有这类能力的误报/漏报才有意义。"""
    return bool(capability_info(task_type)["findings_based"])


def required_partitions(task_type: str) -> tuple:
    return tuple(capability_info(task_type)["partitions"])


def package_sha256_of(output) -> str:
    """取一份产出所依赖的 Skill 包哈希，带外键回退。

    ``GenerationOutput.skill_package_sha256`` 是服务端在发布产出时写入的**冗余列**
    （见 ``services.record_task_output``）。但直接经 ORM 造出来的产出、以及历史回填
    的产出可能只有 ``skill_version`` 外键而没有这一列，此时返空会让溯源链断掉。
    所以这里以列为先，列为空时回退到版本自身的哈希——两者本应一致。
    """
    if output is None:
        return ""
    direct = getattr(output, "skill_package_sha256", "") or ""
    if direct:
        return direct
    version = getattr(output, "skill_version", None)
    return getattr(version, "package_sha256", "") or ""


def grouped_stages() -> dict:
    """按形态分组，供 Skill Hub 左侧目录与金标组织使用。"""
    grouped: dict[str, list[dict]] = {
        KIND_SKILL: [], KIND_COMPOSITE: [], KIND_FEEDBACK_SOURCE: [], KIND_PLATFORM_UTILITY: [],
    }
    for task_type in ALL_TASK_TYPES:
        info = capability_info(task_type)
        grouped[info["kind"]].append({
            "task_type": task_type,
            "label": info["label"],
            "mode": info["mode"],
            "partitions": list(info["partitions"]),
        })
    return grouped
