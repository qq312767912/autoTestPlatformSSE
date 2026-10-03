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

#: Skill 可声明的「平台基础能力」档。
#:
#: 有些 Skill 是**跨阶段的公共手段**，不属于任何单一测试阶段：平台自带的测试管理工具
#: （`whart-test` / `api-automation` / `ui-automation`）、浏览器自动化（`playwright-skill` /
#: `playwright-cli` / `browser-use` / `agent-browser-skill`）、视觉识别（`vision-analysis`）、
#: 知识库检索（`weknora-kb`）、URL 解析与画图等。硬塞进八类里任何一类都是错的。
#:
#: 它**不是业务能力**：不进 ``BUSINESS_CAPABILITY_STAGES``（八类的构成式子与金标分组口径
#: 一个字都不动），也不进 ``ALL_TASK_TYPES``（它不是任务类型，没有产出、没有门禁分区）。
#: 只用于 Skill Hub 的「所属阶段」分类与分组展示。
#:
#: 副作用是它永远匹配不上 ``Q(declared_stage=stage)`` 的阶段检索——这正是想要的语义：
#: 平台基础能力不该被某个阶段独占绑定。
PLATFORM_BASE_STAGE = "platform_base"

#: ``Skill.declared_stage`` 的取值真值（也是 Skill Hub 阶段下拉的顺序）。
#: 八类业务能力在前，平台基础能力垫底——它是"不绑单一阶段"的兜底归属，不该抢在
#: 真实阶段前面。
SKILL_STAGE_OPTIONS = BUSINESS_CAPABILITY_STAGES + (PLATFORM_BASE_STAGE,)

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
    # 非任务类型，仅作 Skill 的归属标签（见 ``PLATFORM_BASE_STAGE``）。
    PLATFORM_BASE_STAGE: "平台基础能力",
}

#: 中文标签 -> 规范标识符的反查表（供 :func:`normalize_stage_input` 归一化用户输入）。
#:
#: ⚠️ 只收 ``SKILL_STAGE_OPTIONS`` 里**可声明**的阶段。``STAGE_LABELS`` 还登记了
#: ``knowledge_query``（平台工具），但它是 ``PLATFORM_UTILITY_STAGES``、不是可绑定的
#: 归属档——不把它排除掉，"知识问答"就会被当成规范阶段放行，`knowledge_query`
#: 会顺着 ``declared_stage`` 混进阶段匹配。这个坑有存量测试兜着
#: （``DeclaredStageTests.test_platform_utility_stage_is_rejected``）。
_LABEL_TO_STAGE = {
    label: stage for stage, label in STAGE_LABELS.items()
    if stage in SKILL_STAGE_OPTIONS
}

#: 登记在 ``STAGE_LABELS``、但**刻意不放进** ``SKILL_STAGE_OPTIONS`` 的阶段
#: （当前只有平台工具 ``knowledge_query``）。既不能当规范阶段放行，也不该被当作
#: 用户自定义档收进 ``custom:`` 命名空间——那会凭空造出一个与平台工具同名的假自定义档，
#: 比直接报错更难排查。标识符与中文名两种写法都要拦住。
_NON_BINDABLE_NAMES = frozenset(
    name
    for stage in STAGE_LABELS
    if stage not in SKILL_STAGE_OPTIONS
    for name in (stage, STAGE_LABELS[stage])
)


# ---------------------------------------------------------------------------
# 用户自定义阶段（上传 / 导入 / 补填时用户自己敲的归属档）
# ---------------------------------------------------------------------------
#
# 「所属阶段」的**规范取值**只有 :data:`SKILL_STAGE_OPTIONS` 那九项——它们是任务类型
# 的真值，背后各有门禁分区与评测模板。用户导入 Skill 时想自己加一档（例如「性能测试」
# 「安全测试」）是合理诉求，但**不能**往任务类型真值里塞：真值一旦被用户输入污染，
# ``Q(declared_stage=stage)`` 的阶段匹配、金标分组、评测模板都会跟着漂。
#
# 所以自定义阶段走**带前缀的独立命名空间**，一律存成 ``custom:<名称>``：
#
# * 结构上不可能冒充规范阶段——用户把 ``test_execution`` 敲成 ``test_executoin`` 时，
#   它与规范标识符撞不上，页面会如实显示"自定义阶段：test_executoin"，而不是像没填
#   一样悄无声息（那正是无前缀方案最难排查的失败模式）；
# * 与 :data:`PLATFORM_BASE_STAGE` 同语义：永远是"不绑单一阶段"的归属标签，
#   不会被任何阶段的 ``Q(declared_stage=stage)`` 检索命中；
# * 不新增数据表、不新增迁移：在用集合由 ``Skill.declared_stage`` 前缀反查得出，
#   依旧是单一真值来源。

#: 自定义阶段在 ``Skill.declared_stage`` 里的存储前缀。
CUSTOM_STAGE_PREFIX = "custom:"

#: 自定义阶段名称的长度上限。``Skill.declared_stage`` 是 64 字符、前缀占 7，
#: 留足余量后定 32——再长在卡片标签上也放不下。
CUSTOM_STAGE_LABEL_MAX_LENGTH = 32


def is_custom_stage(value) -> bool:
    """判断 ``declared_stage`` 里的值是否是自定义阶段（而不是规范任务类型）。"""
    return str(value or '').startswith(CUSTOM_STAGE_PREFIX)


def make_custom_stage(label, max_length=CUSTOM_STAGE_LABEL_MAX_LENGTH) -> str:
    """把用户敲的名称包成 ``custom:<名称>``；散落的空白先收拢，免得"性能测试"与
    "性能 测试"在库里裂成两档。调用方需自行保证它不是规范阶段名。"""
    text = ' '.join(str(label or '').split())
    if not text:
        raise ValueError('自定义阶段名称不能为空')
    if len(text) > max_length:
        raise ValueError(f'自定义阶段名称不能超过 {max_length} 个字符')
    return CUSTOM_STAGE_PREFIX + text


def stage_display_label(value) -> str:
    """阶段的展示名。自定义阶段剥掉前缀只显示名称，规范阶段查中文标签，其余原样。"""
    text = str(value or '')
    if text.startswith(CUSTOM_STAGE_PREFIX):
        return text[len(CUSTOM_STAGE_PREFIX):] or text
    return STAGE_LABELS.get(text, text)


def normalize_stage_input(value, allow_custom=True) -> str:
    """把用户输入归一化成 ``Skill.declared_stage`` 的存储值。

    接受的三种写法（统一收敛成一个存储值）：

    1. 规范标识符（``test_execution``）——原样返回；
    2. 规范阶段的中文名（``测试执行``）——反查回标识符，避免与标识符并存两份；
    3. 其它自由文本——包成 ``custom:<名称>``（``allow_custom=False`` 时直接报错）。

    空串一律报错：本函数服务于"有值"的入口（导入分类、补填阶段），撤销声明由调用方
    在进来之前就拦掉（见 ``SkillStageBindingSerializer.validate_stage``）。

    失败抛 :class:`ValueError`，消息可直接展示给用户——具体框架的异常类型由调用方转换。
    """
    text = str(value or '').strip()
    if not text:
        raise ValueError('所属阶段不能为空')
    if text in SKILL_STAGE_OPTIONS:
        return text
    canonical = _LABEL_TO_STAGE.get(text)
    if canonical:
        return canonical
    if text in _NON_BINDABLE_NAMES:
        # 平台工具阶段（如 knowledge_query）不参与业务能力口径，也不是可声明的归属档。
        raise ValueError(
            f'「{STAGE_LABELS.get(text, text)}」是平台工具阶段，不能作为 Skill 的所属阶段'
        )
    if text.startswith(CUSTOM_STAGE_PREFIX):
        # 幂等：前端回显的就是库里的值，二次提交不该再套一层前缀。
        return make_custom_stage(text[len(CUSTOM_STAGE_PREFIX):])
    if not allow_custom:
        raise ValueError(f'未知的能力阶段：{text}；可选值见 SKILL_STAGE_OPTIONS')
    return make_custom_stage(text)

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
