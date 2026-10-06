"""项目级四阶段 Skill 质量流水线门禁模型。"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .capability_registry import ALL_WORKFLOW_STAGES, STAGE_LABELS

#: 允许出现门禁记录的阶段 = 新主链路 ∪ 历史主链路（顺序为新链路在前）。
#:
#: 为什么必须是并集：``choices`` 在 Django 里不是"随便写写"，它会被
#: ``full_clean()`` / 表单 / 后台下拉当成硬约束。只列新四阶段会让存量流程的门禁
#: 在管理端打不开；只列历史四阶段则会让新链路的 ``risk_identification`` /
#: ``issue_tracking`` 变成"非法取值"——而它们在库里明明有记录。
#: 真值源在 ``capability_registry.ALL_WORKFLOW_STAGES``，这里引用它而不是再抄一份。
WORKFLOW_STAGE_CHOICES = [(stage, STAGE_LABELS[stage]) for stage in ALL_WORKFLOW_STAGES]

# ---------------------------------------------------------------- 门禁状态语义真值
#
# 这几个集合是**模块级**（不是 ``WorkflowStageGate`` 的类属性）的，原因很实际：
# 别的模块要写 ``from .workflow_models import GATE_PASSING_STATES``，
# 而类属性根本导不出来——写成类属性时那句 import 会在运行时抛 ImportError，
# 而且因为 ``operations`` 是从各 view 里惰性 import 的，
# ``manage.py check`` 根本碰不到它，问题要等到跑测试/真调接口才炸。
#
# 任何"能不能进下一步""能不能打分"的判断都必须读这里，不允许各模块自己写一遍
# ``{"passed", "overridden"}``——早先这个集合散落在 4 处、2 个模块，加一个
# ``confirmed`` 就会出现"页面显示可以继续、接口却 400"这种谁都不认账的状态不一致。

#: 门禁**放行**状态集合：上一阶段落在这几个状态里，下一阶段才能进入。
#:
#: 三个成员的语义边界（对应 ``WorkflowStageGate.STATUS_CHOICES``）：
#: - ``passed``：评测算出来的通过，证据是 ``scores``；
#: - ``confirmed``：人工确认放行——**没有评分也能走**，证据是 ``decided_by``；
#: - ``overridden``：负责人**在评测失败之后**强制放行，留痕含义比 ``confirmed`` 更重，
#:   因此保留独立状态，两者不合并。
GATE_PASSING_STATES = frozenset({"passed", "confirmed", "overridden"})

#: 允许人工确认（→ ``confirmed``）的**起始**状态。
#:
#: ``failed`` 刻意不在其中：评测已给出"不达标"结论时，正确动作是修复后重跑，
#: 或走负责人强制放行（``override``，要填原因、留更重的痕），而不是用一次普通点击
#: 把"不达标"悄悄变成"已通过"。``passed`` 同样不需要再确认。
GATE_CONFIRMABLE_STATES = frozenset({"pending", "unscored"})

#: 允许「人工评分」的**起始**状态。
#:
#: 尚未评过（``pending``）、自动评测无信号（``unscored``）都可以打分；
#: 已有结论（``passed`` / ``failed``）也允许**改判**——人打错了分是常事，
#: 不给人改的路，只会逼人去数据库里改。
#: ``confirmed`` / ``overridden`` 不在其中：那是已经拍板放行的终态，
#: 再打分等于把已生效的放行撤回，属于"改判"而非"评分"。
GATE_SCORABLE_STATES = frozenset({"pending", "unscored", "passed", "failed"})

#: 已由**人**拍板、不可被自动重算推翻的终态。
#:
#: 单独列出来是因为 ``is_human_decided()`` 还要认 ``detail.manual_score``——
#: 人工评分后的状态是 ``passed``/``failed``，光看 status 分不出"机器评的"还是
#: "人评的"，而这两者的可信度与责任归属完全不同。
GATE_HUMAN_FINAL_STATES = frozenset({"confirmed", "overridden"})


# ---------------------------------------------------------------- 执行尝试状态机真值
#
# 与上面的 ``GATE_*_STATES`` 同样的理由，这些集合必须是**模块级**的：
# attempt 的状态机是"谁能改谁"的唯一判据。散进 service / view 各写一遍，
# 必然出现"接口放行、模型不放行"这种两套说法——而 attempt 的错状态会直接把
# 飞轮的"运行中/已产出/失败"显示带偏，比门禁状态更难人工发现。

#: 执行尝试的状态集合。
#:
#: ``output_published`` 与 ``completed`` 刻意分开："Agent 已正式提交产出"和
#: "这一轮执行已收尾"是两件事——产出提交之后还要绑定 ``output``、重算门禁、
#: 登记候选，任一步失败都会让 attempt 停在 ``output_published``。
#: 合并成一个状态，就再也看不出它卡在哪一步。
ATTEMPT_STATUS_CHOICES = [
    ("planned", "已计划"),
    ("dispatched", "已派发"),
    ("running", "运行中"),
    ("output_published", "已发布产出"),
    ("completed", "已完成"),
    ("failed", "失败"),
    ("cancelled", "已取消"),
    ("timed_out", "已超时"),
]

#: 终态：落在这些状态之后不再允许流转。
#:
#: 失败/取消/超时同样是终态，不是"中间态"——它们各自有独立的失败原因留痕，
#: 需要继续跑就**新建一条 attempt**（``retry_of`` 指回来）。允许把 ``failed``
#: 直接改成 ``completed``，等于让一次失败从记录里消失。
ATTEMPT_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "timed_out"})

#: 非终态集合（= 还在链路上）。飞轮据此显示"进行中"，
#: 重复派发判定据此判断"这一轮是不是还活着"。
ATTEMPT_ACTIVE_STATES = frozenset(
    status for status, _ in ATTEMPT_STATUS_CHOICES
) - ATTEMPT_TERMINAL_STATES

#: 合法迁移表：**只能前进，或转向某个失败终态**。
#:
#: 回退（``running`` → ``dispatched``）和跨终态更新（``failed`` → ``completed``）
#: 在这里被挡掉，而不是靠每个调用方自己记得判断。终态的出边为空集，
#: 这正是"禁止跨终态更新"的实现方式。
ATTEMPT_TRANSITIONS = {
    "planned": frozenset({"dispatched", "cancelled", "failed", "timed_out"}),
    "dispatched": frozenset({"running", "cancelled", "failed", "timed_out"}),
    "running": frozenset({
        "output_published", "completed", "failed", "cancelled", "timed_out",
    }),
    # 刚发布产出就收尾是正常路径；发布之后才发现绑定/校验失败也算 failed。
    "output_published": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "timed_out": frozenset(),
}


def attempt_transition_allowed(current: str, target: str) -> bool:
    """状态迁移的唯一判定入口。未知状态一律不放行。"""
    return target in ATTEMPT_TRANSITIONS.get(current, frozenset())


class FlywheelRun(models.Model):
    """跨需求、Agent、测试管理和飞轮入口共享的项目级流程上下文。"""

    ENTRY_CHOICES = [
        ("requirement", "需求管理"),
        ("chat", "Agent 对话"),
        ("test_management", "测试管理"),
        ("flywheel", "质量飞轮"),
        ("history_replay", "历史回放"),
    ]
    INTENT_CHOICES = [
        ("production", "生产流程"),
        ("history_replay", "历史回放"),
        ("shadow_evaluation", "影子评测"),
    ]
    STATUS_CHOICES = [
        ("draft", "草稿"),
        ("running", "运行中"),
        ("completed", "已完成"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="flywheel_runs",
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    entry_type = models.CharField(max_length=24, choices=ENTRY_CHOICES)
    intent = models.CharField(max_length=24, choices=INTENT_CHOICES, default="production")
    requirement_document_ids = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft", db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="flywheel_runs",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "workflow_id"], name="uniq_flywheel_run_project_workflow",
            )
        ]
        indexes = [
            models.Index(fields=["project", "status", "updated_at"], name="ke_run_proj_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}/{self.workflow_id}"


class WorkflowStageGate(models.Model):
    STAGE_CHOICES = WORKFLOW_STAGE_CHOICES
    #: 状态机真值。新增状态时**必须**同时看本文件顶部的 GATE_*_STATES——
    #: 那些集合决定新状态能否放行、能否打分，漏改就会出现页面与接口两套说法。
    STATUS_CHOICES = [
        ("pending", "待测评"),
        ("unscored", "无评分"),
        ("passed", "测评通过"),
        ("failed", "测评失败"),
        ("confirmed", "人工确认"),
        ("overridden", "负责人放行"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="workflow_stage_gates"
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, db_index=True)
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="workflow_gates",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    scores = models.JSONField(default=dict, blank=True)
    detail = models.JSONField(
        default=dict, blank=True,
        help_text=(
            "门禁的可核验明细：报告契约校验结果（report_contract）与"
            "阶段/端到端评测结论（evaluation）。状态机只由 status 决定，"
            "这里只存证据，不参与流转判定。"
        ),
    )
    threshold = models.FloatField(default=0.7)
    reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="workflow_gate_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["workflow_id", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "workflow_id", "stage"],
                name="uniq_workflow_stage_gate",
            )
        ]
        indexes = [
            models.Index(fields=["project", "workflow_id", "status"], name="ke_gate_proj_wf_status_idx"),
        ]


class WorkflowSkillLock(models.Model):
    """任务启动时固化的 Skill 版本锁（T08 / R4、R8）。

    解决的问题：**新版本激活不能改变运行中任务的行为**。如果运行时每次都去查
    "当前活跃版本"，那么一条跑了三小时的四阶段流水线会在中途换了包，前半段和
    后半段的产出没法归因到同一个版本，回滚也无法界定影响范围。

    因此任务创建时就在这里固化一次解析结果，之后产出、反馈、评测、归因一律
    引用这把锁。锁本身不复制发布状态（状态仍然只由 ``CapabilityRelease`` 决定），
    它只回答"这个任务用的是哪一份包"。

    ``lock_key`` 是锁的定位键：四阶段流水线用阶段名（一个阶段一把锁），
    单能力任务用能力标识。之所以不用 ``stage`` 字段直接做唯一键，是因为单能力
    场景下 ``stage`` 为空，而 PostgreSQL 唯一约束不约束 NULL（会放进任意多条）。
    """

    SCOPE_CHOICES = [
        ("single", "单能力"),
        ("workflow", "四阶段流水线"),
        ("composite", "复合能力"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="workflow_skill_locks",
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    lock_key = models.CharField(
        max_length=128, db_index=True,
        help_text="锁的定位键：四阶段用阶段名，单能力用能力标识或 default",
    )
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default="single")
    stage = models.CharField(max_length=32, blank=True, db_index=True)
    capability = models.ForeignKey(
        "knowledge_evolution.CapabilityDefinition", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="workflow_skill_locks",
    )
    skill = models.ForeignKey(
        "skills.Skill", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="workflow_locks",
    )
    skill_version = models.ForeignKey(
        "skills.SkillVersion", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="workflow_locks",
    )
    release = models.ForeignKey(
        "knowledge_evolution.CapabilityRelease", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="workflow_skill_locks",
    )
    package_sha256 = models.CharField(
        max_length=64, blank=True,
        help_text="锁定时刻的包哈希，用于事后核对锁指向的确实是同一份内容",
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="workflow_skill_locks",
    )
    detail = models.JSONField(default=dict, blank=True)
    locked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["workflow_id", "locked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "workflow_id", "lock_key"],
                name="uniq_workflow_skill_lock",
            )
        ]
        indexes = [
            models.Index(fields=["project", "workflow_id"], name="ke_skill_lock_proj_wf_idx"),
        ]

    def __str__(self) -> str:
        version = self.skill_version.version if self.skill_version_id else "-"
        return f"{self.workflow_id}/{self.lock_key} -> {version}"


class StageExecutionAttempt(models.Model):
    """一次阶段执行的尝试记录（T01 / R4）。

    存在的理由：``GenerationOutput`` 只描述"正式产出"，而 Agent 会在产出之前
    失败、超时、被取消——那时没有 output，但用户必须能看到"这一轮跑过、卡在哪"。
    把两者合成一张表，等于要求"失败也必须伪造一个产出"。

    因此职责切分是：
    - **attempt 表示一次运行**：只要派发过就存在一条，失败也保留；
    - **``output`` 只在正式发布之后才有值**：它指向 ``GenerationOutput``，
      是"这一轮确实产出了东西"的证据，而不是"这一轮存在过"的证据。

    ``retry_of`` 让重试链可追溯：重试**新建**一条 attempt 并指回原记录，
    而不是把失败记录改写成成功——否则"重试过几次、每次为什么失败"就丢了。
    """

    STAGE_CHOICES = WORKFLOW_STAGE_CHOICES
    STATUS_CHOICES = ATTEMPT_STATUS_CHOICES

    #: 入口类型的真值取 ``FlywheelRun.ENTRY_CHOICES``，不另抄一份：
    #: 飞轮流程和它的执行尝试必须能用同一套词描述"这一轮从哪来"，
    #: 抄成两份早晚会出现"流程说来自飞轮、尝试说来自聊天"。
    ENTRY_CHOICES = FlywheelRun.ENTRY_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="stage_attempts",
    )
    flywheel_run = models.ForeignKey(
        FlywheelRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stage_attempts",
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, db_index=True)
    #: 本轮**实际**使用的 Skill 版本。它不一定来自版本锁（旁路模式没有锁），
    #: 所以单独存一份，而不是每次回查 ``WorkflowSkillLock``。
    skill_version = models.ForeignKey(
        "skills.SkillVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stage_attempts",
    )
    skill_package_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    #: 上游阶段的产出 ID 列表。存列表而不是单个外键：报告阶段可能同时依赖
    #: 用例产出与执行结果，后续多阶段图谱合并也要用到。
    parent_output_ids = models.JSONField(default=list, blank=True)
    session_id = models.CharField(max_length=128, blank=True, db_index=True)
    entry_type = models.CharField(max_length=24, choices=ENTRY_CHOICES, default="flywheel")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="planned", db_index=True,
    )
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="stage_attempts",
    )
    retry_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="retries",
    )
    #: 幂等键：重复点击"执行"只应得到同一条 attempt。
    #: 空串表示"调用方不要幂等保证"（旁路入口），因此唯一约束只约束非空值。
    idempotency_key = models.CharField(max_length=128, blank=True, db_index=True)
    error_code = models.CharField(max_length=100, blank=True)
    #: 受限错误摘要：只存可展示的结论，不落完整堆栈与敏感参数。
    error_summary = models.TextField(blank=True)
    detail = models.JSONField(
        default=dict, blank=True,
        help_text="执行参数的快照与扩展明细；状态机只由 status 决定，这里不参与判定。",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stage_attempts",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    output_published_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # 幂等只对"同一项目 + 同一幂等键"生效；空串（旁路入口）不参与，
            # 否则第二条旁路 attempt 会撞上唯一约束。
            models.UniqueConstraint(
                fields=["project", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_stage_attempt_idempotency",
            )
        ]
        indexes = [
            models.Index(fields=["project", "workflow_id", "stage"], name="ke_attempt_proj_wf_stage_idx"),
            models.Index(fields=["project", "status", "created_at"], name="ke_attempt_proj_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}/{self.stage} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in ATTEMPT_TERMINAL_STATES

    def can_transition_to(self, target: str) -> bool:
        return attempt_transition_allowed(self.status, target)


# ---------------------------------------------------------------- 执行上下文有效期

#: 执行上下文的存活时长（分钟）。
#:
#: 上下文是一次「从飞轮点击跳到业务页面、再把页面上的 Agent 跑起来」的**临时凭据**，
#: 不是长期资源。给得太长，等于把一把"能改流程产出"的钥匙长期留在浏览器 URL 里；
#: 给得太短，人在业务页面上挑知识库、选模块的时间都不够，会逼出"把 workflow_id 抄下来"
#: 这种设计明确要消灭的动作。
#:
#: 30 分钟是按"点进页面 → 配好参数 → 点生成"的正常操作节奏留的余量；超时不是失败，
#: 页面会提示回到飞轮重新派发（重新派发只新建一条上下文，不影响已有 attempt）。
EXECUTION_CONTEXT_TTL_MINUTES = 30


class StageExecutionContext(models.Model):
    """阶段执行的短期可信上下文（T02 / R3）。

    要解决的问题：飞轮派发之后，业务页面必须知道"这一轮属于哪条流程、哪个阶段、
    锁的是哪一份 Skill、上一阶段的产出是哪些"。最省事的做法是让前端把这些值
    拼在 URL 里带过去——于是 `workflow_id`、`module_key`、`skill_version_id`
    就都成了**用户可改**的字符串。改一个 `skill_version_id` 就能让受控运行
    用上另一份没被锁定的包，而流程记录上仍显示"用的是锁定版本"。

    因此这里把参数**存在服务端**，前端只拿一个不透明的 `id`。页面用它换取
    可信上下文，取值一律以本表为准；URL 被篡改也只能篡改这个 id 本身，
    而 id 是随机的 UUID，且解析时还要过项目、用户、过期三道校验。

    与 attempt 的关系是**一对多**而非一对一：上下文会过期，过期后需要重新派发，
    而"重新派发"不应该在流程里凭空多出一条 attempt——同一个 attempt 可以先后
    拥有多条上下文（旧的已失效、新的生效）。反过来，`attempt` 为空是不允许的：
    没有 attempt 的上下文无法回答"这一轮到底跑没跑"，正是本设计要消灭的模糊状态。
    """

    STAGE_CHOICES = WORKFLOW_STAGE_CHOICES
    ENTRY_CHOICES = FlywheelRun.ENTRY_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="stage_execution_contexts",
    )
    flywheel_run = models.ForeignKey(
        FlywheelRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="execution_contexts",
    )
    attempt = models.ForeignKey(
        StageExecutionAttempt, on_delete=models.CASCADE, related_name="execution_contexts",
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, db_index=True)
    entry_type = models.CharField(max_length=24, choices=ENTRY_CHOICES, default="flywheel")
    #: 执行通道快照（``WorkflowGateService.STAGE_EXECUTION_CHANNELS`` 的取值）。
    #: 存快照而不是每次回查通道表：通道表将来补上某个阶段的执行器时，
    #: 已经发出去的上下文不应该凭空变成另一种执行方式。
    channel = models.CharField(max_length=16, blank=True)
    module_key = models.CharField(max_length=64, blank=True)
    skill_version = models.ForeignKey(
        "skills.SkillVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="execution_contexts",
    )
    skill_package_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    parent_output_ids = models.JSONField(default=list, blank=True)
    #: 可信参数快照。业务页面把用户配的参数先报给服务端（``prepare``），
    #: 由服务端在这里落一份；Agent 请求只带 id，服务端以这份快照为准。
    #: 这样"页面上配的参数"和"实际执行的参数"不会因为一次前端改版就悄悄分叉。
    payload = models.JSONField(default=dict, blank=True)
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stage_execution_contexts",
    )
    expires_at = models.DateTimeField(db_index=True)
    #: 最近一次解析时间与次数，纯粹是审计用：不参与有效性判定
    #: （重复解析必须幂等，因此不存在"解析过就作废"这种一次性语义）。
    last_resolved_at = models.DateTimeField(null=True, blank=True)
    resolve_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "workflow_id", "stage"], name="ke_ctx_proj_wf_stage_idx"),
            models.Index(fields=["expires_at"], name="ke_ctx_expires_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}/{self.stage} ctx[{self.attempt_id}]"

    def is_expired(self, *, at=None) -> bool:
        moment = at or timezone.now()
        return bool(self.expires_at and self.expires_at <= moment)

    @property
    def is_active(self) -> bool:
        return not self.is_expired()


# ---------------------------------------------------------------- 平台派生产物
#
# 为什么派生产物要**单独一张表**，而不是继续塞进 ``GenerationOutput.metadata``：
#
# 1. **归属不同**。``GenerationOutput`` 是 Skill 的产出，是不可变的业务事实；
#    证据图谱/质量摘要/确认报告是**平台**对这份产出的解读。解读会随平台判定逻辑
#    升级而变，业务产出不会。混在一张表里，一次平台升级会让"产出变了"和
#    "解读变了"再也分不开。
# 2. **要能回答"当时是按哪版逻辑判的"**。所以每份派生物都带 ``generator_version``
#    与源产出的哈希；源产出换了（人重新生成了一次方案），派生物必须作废重算，
#    而不是继续挂着一份对不上号的解读。
# 3. **反查方向**。人工确认报告里的每一行都要能回到具体的方案项 ID 与证据 ID，
#    这些 id 只在派生物里；塞进 metadata 会让按 id 反查变成全表扫 JSON。
#
# ``(output, kind)`` 唯一：同一份产出的同一种派生只有一份"当前"版本，重复生成
# 覆盖它（内容一致时哈希不变）。历史留在 ``metadata['history']`` 里，用于回答
# "换了判定逻辑之后结论为什么变了"。

#: 派生物种类真值。加种类时**必须**同时在 ``derived_artifacts.KIND_FILENAMES`` /
#: ``KIND_GENERATOR_VERSIONS`` 里登记，否则生成器会找不到落盘名而静默跳过。
DERIVED_ARTIFACT_KINDS = (
    ("evidence_graph", "执行证据图谱"),
    ("quality_summary", "阶段质量摘要"),
    ("review_report", "阶段确认报告"),
)


class StageDerivedArtifact(models.Model):
    """平台从阶段产出派生出来的文件（T07 / R9、R10）。

    ``path`` 存**相对 ``MEDIA_ROOT``** 的路径：绝对路径一换部署环境就失效，
    而相对路径可以随 ``MEDIA_ROOT`` 一起迁移（容器里挂载点不同也不影响）。
    """

    KIND_CHOICES = DERIVED_ARTIFACT_KINDS
    STAGE_CHOICES = WORKFLOW_STAGE_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="stage_derived_artifacts",
    )
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.CASCADE,
        related_name="derived_artifacts",
    )
    attempt = models.ForeignKey(
        StageExecutionAttempt, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="derived_artifacts",
    )
    workflow_id = models.CharField(max_length=128, blank=True, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, db_index=True)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    filename = models.CharField(max_length=200)
    path = models.CharField(max_length=500, help_text="相对 MEDIA_ROOT 的路径")
    content_hash = models.CharField(max_length=64, db_index=True)
    byte_size = models.PositiveIntegerField(default=0)
    #: 生成器版本：回答"这份解读是按哪版平台逻辑算出来的"。
    generator_version = models.CharField(max_length=64, blank=True, db_index=True)
    #: 源产出哈希：Skill 侧 ``stage_result.json`` 的 sha256。源变了派生物就必须重算，
    #: 否则会出现"报告里列着已经不存在的方案项"这种看起来正常的错误数据。
    source_output_hash = models.CharField(max_length=64, blank=True, db_index=True)
    source_path = models.CharField(max_length=500, blank=True)
    #: 产出等级（L0–L3）快照：等级判定逻辑升级后，历史记录仍显示当时的结论。
    level = models.CharField(max_length=8, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stage_derived_artifacts",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["output", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["output", "kind"], name="uniq_stage_derived_artifact_output_kind",
            )
        ]
        indexes = [
            models.Index(fields=["project", "stage", "kind"], name="ke_derived_proj_stage_kind_idx"),
            models.Index(fields=["workflow_id", "kind"], name="ke_derived_wf_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.filename}"


class StageFeedbackAttachment(models.Model):
    """阶段人工补充文件的绑定记录（T10 / §7.4）。

    物理文件走既有的 ``file_management.FileAsset``（它已经解决项目隔离、哈希、
    软删除、下载/预览），这张表只回答**"这份文件属于哪个流程的哪个阶段、
    以什么用途交上来的"**。不把二进制塞进飞轮自己的表，是因为那样就得把
    "文件存储"再实现一遍，而且两份实现必然在权限口径上分叉。

    ⚠️ 上传**只产生反馈证据**：不派生 Skill、不改 ``active`` 版本、不进金标集。
    这条边界靠"本模型没有任何指向 SkillVersion / EvaluationSuite 的写路径"
    保证——把这件事写成注释而不是代码，早晚有人顺手加一句"顺便激活一下"。

    ``sha256`` 从 ``FileAsset`` 复制一份而不是纯 join，是为了让幂等判定的唯一约束
    能落在本表上：文件资产可以被多份绑定复用（同一份补充材料同时作为
    "人工补充产物"和"问题证据"），幂等要按**绑定**判，不能按文件判。
    """

    purpose = models.CharField(max_length=32, db_index=True)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="stage_feedback_attachments",
    )
    workflow_id = models.CharField(max_length=128, blank=True, db_index=True)
    stage = models.CharField(max_length=64, db_index=True)
    #: 原始产出。允许为空：旁路运行的产出尚未纳管时，人已经可以先交补充材料。
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedback_attachments",
    )
    #: 提交时的受控尝试。同样可空，理由同上。
    attempt = models.ForeignKey(
        StageExecutionAttempt, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedback_attachments",
    )
    file = models.ForeignKey(
        "file_management.FileAsset", on_delete=models.PROTECT,
        related_name="stage_feedback_attachments",
    )
    sha256 = models.CharField(max_length=64, db_index=True)
    original_name = models.CharField(max_length=255, blank=True)
    byte_size = models.BigIntegerField(default=0)
    mime_type = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=500, blank=True)
    #: 退役（软删除）。为什么不用 ``is_deleted`` 布尔量：退役是**有原因**的动作，
    #: 只留一个布尔量会让"谁在什么时候为什么退掉它"永久丢失，而这恰好是
    #: 后面判断"这份证据还能不能用"时唯一要看的东西。
    retired_at = models.DateTimeField(null=True, blank=True, db_index=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="retired_stage_attachments",
    )
    retire_reason = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="uploaded_stage_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            # 幂等：同一流程同一阶段同一用途下，同一份内容只留一条绑定。
            # 空 ``workflow_id`` 也要参与判定（旁路提交的补充材料同样不该重复），
            # 所以这里不做"空值排除"——与 attempt 幂等键那里的处理**故意不同**：
            # 那里空键代表"没传"，这里空代表"不属于任何流程"，后者是确定语义。
            models.UniqueConstraint(
                fields=["project", "workflow_id", "stage", "purpose", "sha256"],
                name="uniq_stage_feedback_attachment_file",
            )
        ]
        indexes = [
            models.Index(fields=["project", "stage", "purpose"], name="ke_attach_proj_stage_idx"),
            models.Index(fields=["output", "purpose"], name="ke_attach_output_purpose_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.stage}/{self.purpose}:{self.original_name}"

    @property
    def is_active(self) -> bool:
        return self.retired_at is None

    def history(self) -> list[dict]:
        """审计轨迹（上传 / 退役 / 复用的时间线，最新在前）。"""
        entries = list((self.metadata or {}).get("audit") or [])
        return list(reversed(entries))


# ------------------------------------------------------------ 旁路产出纳管语义真值
#
# 与门禁状态、attempt 状态同样的理由：取值集合放在**模块级**，
# 模型 choices、服务判定与前端目录接口都从同一处取。写成类属性会让
# ``from .workflow_models import SUBMISSION_TARGETS`` 直接 ImportError。

#: 纳管去处。``new_workflow`` = 为它开一条新流程；``existing_workflow`` = 挂进已有流程。
SUBMISSION_TARGET_NEW = "new_workflow"
SUBMISSION_TARGET_EXISTING = "existing_workflow"
SUBMISSION_TARGETS: tuple[str, ...] = (SUBMISSION_TARGET_NEW, SUBMISSION_TARGET_EXISTING)
SUBMISSION_TARGET_LABELS: dict[str, str] = {
    SUBMISSION_TARGET_NEW: "纳入新流程",
    SUBMISSION_TARGET_EXISTING: "纳入已有流程",
}
SUBMISSION_TARGET_CHOICES = [
    (value, SUBMISSION_TARGET_LABELS[value]) for value in SUBMISSION_TARGETS
]

#: 纳管绑定的状态。``superseded`` 表示它指向的产出已被同阶段的新产出替换。
SUBMISSION_STATE_ADMITTED = "admitted"
SUBMISSION_STATE_SUPERSEDED = "superseded"
SUBMISSION_STATES: tuple[str, ...] = (SUBMISSION_STATE_ADMITTED, SUBMISSION_STATE_SUPERSEDED)
SUBMISSION_STATE_LABELS: dict[str, str] = {
    SUBMISSION_STATE_ADMITTED: "已纳管",
    SUBMISSION_STATE_SUPERSEDED: "已被替换",
}
SUBMISSION_STATE_CHOICES = [
    (value, SUBMISSION_STATE_LABELS[value]) for value in SUBMISSION_STATES
]


class WorkflowStageSubmission(models.Model):
    """旁路产出的纳管绑定（T12 / §9）。

    为什么不直接往 ``GenerationOutput.metadata`` 里补写 ``workflow_id``：
    metadata 是**产出当时**的协议快照，事后改写它，一份"从业务页面直接生成"
    的产出就会看起来从来都在受控流程里 —— 历史从此不可信，而"可信"正是这一层
    存在的全部理由。纳管是**新增一条绑定**，不是改写产出：产出、它当时的协议、
    旧门禁与旧反馈全部原地保留。

    ``supersedes_output`` 记录"这次纳管替换掉了谁"，与产出自身
    ``protocol.supersedes_output_id`` 是两件事：后者是受控执行在新产出发布那一刻
    就写死的声明，前者是旁路产出**纳管时**才确定的替换对象。旁路产出发布时
    无从知道要替换谁，硬塞进 metadata 只能靠事后改写——那正是本模型要避免的。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="workflow_stage_submissions",
    )
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.CASCADE,
        related_name="workflow_submissions",
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    stage = models.CharField(max_length=32, choices=WORKFLOW_STAGE_CHOICES, db_index=True)
    target = models.CharField(
        max_length=24, choices=SUBMISSION_TARGET_CHOICES, default=SUBMISSION_TARGET_EXISTING,
    )
    #: 产出**当时**携带的 Skill 版本快照。产出本身也存 skill_version，
    #: 这里再记一份是为了让"纳管时它绑的是哪一版"在产出被后续改动影响时仍可回答。
    skill_version = models.ForeignKey(
        "skills.SkillVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="workflow_stage_submissions",
    )
    package_sha256 = models.CharField(max_length=64, blank=True)
    #: 被本次纳管替换掉的旧产出。为空 = 这次是首纳管（该阶段原本没有产出）。
    supersedes_output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="superseded_by_submissions",
    )
    state = models.CharField(
        max_length=16, choices=SUBMISSION_STATE_CHOICES,
        default=SUBMISSION_STATE_ADMITTED, db_index=True,
    )
    note = models.CharField(max_length=500, blank=True)
    #: 纳管时的上下文与留档（旧门禁结论快照、替换确认人等）。
    detail = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="workflow_stage_submissions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # 一份产出只能被纳管进一条链路的同一个阶段：同一个产出出现在两条
            # 流程里，会让"这份产出属于哪条链路"没有唯一答案，门禁与评测就失去基准。
            models.UniqueConstraint(
                fields=["project", "output", "stage"],
                name="uniq_stage_submission_output_stage",
            )
        ]
        indexes = [
            models.Index(fields=["workflow_id", "stage"], name="ke_submit_wf_stage_idx"),
            models.Index(fields=["project", "stage", "state"], name="ke_submit_proj_state_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}/{self.stage}:{self.output_id}"

    @property
    def is_admitted(self) -> bool:
        return self.state == SUBMISSION_STATE_ADMITTED

    def gate_snapshot(self) -> dict:
        """被替换掉的旧门禁结论（无替换时为空）。"""
        return dict((self.detail or {}).get("superseded_gate") or {})


# --------------------------------------------------- 登记失败补偿的状态语义真值
#
# 模块级（不是模型类属性）：``registration.py`` 与 ``rollout.py`` 都要
# ``from .workflow_models import REGISTRATION_OPEN_STATES``。写成类属性导不出来，
# 而且因为调用方多是惰性 import，``manage.py check`` 照样通过、要跑起来才炸。

#: 补偿记录**有待处理**的状态：控制台据此计数。
REGISTRATION_PENDING = "pending"
#: 尝试过、失败但还没到死信阈值 —— 可自动/手动重试。
REGISTRATION_FAILED = "failed"
#: 尝试次数用尽，必须人工介入。**告警看这个，不看 failed。**
REGISTRATION_DEAD_LETTER = "dead_letter"
#: 补登成功或确认无需登记，记录留档不再重试。
REGISTRATION_RESOLVED = "resolved"

REGISTRATION_STATUS_CHOICES = [
    (REGISTRATION_PENDING, "待补偿"),
    (REGISTRATION_FAILED, "补偿失败"),
    (REGISTRATION_DEAD_LETTER, "死信"),
    (REGISTRATION_RESOLVED, "已解决"),
]

#: 还应该被调度/重试的状态。
REGISTRATION_OPEN_STATES = frozenset({REGISTRATION_PENDING, REGISTRATION_FAILED})
#: 需要人工介入的告警状态。``failed`` 刻意不在其中：它还会被自动重试消化，
#: 把它计入告警会让控制台在正常重试窗口里持续闪红，真正该看的人就不看了。
REGISTRATION_ALERT_STATES = frozenset({REGISTRATION_DEAD_LETTER})

#: 默认重试上限。与候选事件队列（``AssetCandidateEvent``）保持同一量级，
#: 避免"同一个死信，两个队列给出不同阈值"。
DEFAULT_REGISTRATION_MAX_ATTEMPTS = 3


class FlywheelRegistrationFailure(models.Model):
    """飞轮登记失败的补偿队列（T14 / 设计 §13）。

    设计原文：*"业务生成成功和飞轮登记成功分别展示。飞轮写入失败不得让业务
    产物丢失，也不得只记日志；通过幂等事件、重试和死信队列补偿。"*

    这三句话各自对应一个**不能省的**决定：

    1. **"不得让业务产物丢失"** —— 登记失败必须发生在 ``record_task_output``
       之后，且失败**不得**回滚业务写入。业务产出在这条链路之外本来就已经可用，
       把它连带回滚等于用"飞轮的账没记上"去销毁"业务已经产出的东西"。
    2. **"不得只记日志"** —— 失败要落成这条可查、可重试、可告警的记录。
       只写 ``logger.error`` 的失败，运维只能在出事之后翻日志，而"有没有漏登记"
       这个问题在日志里是**搜不出来**的（没有反例可搜）。
    3. **"幂等事件"** —— 同一产出同一阶段的重复登记失败**只累加 attempts**，
       不新增记录。否则一次网络抖动重试三次就会长出三条死信，
       控制台看到的是"3 个产出登记失败"，实际只有一个——告警数字失真之后，
       就再没人相信它了。

    真值边界：``resolved`` 之后同一产出**再失败**会复用同一条记录并把状态打回
    ``failed``（见 ``FlywheelRegistrationService.record_failure``）。一条记录
    代表"这个产出这一阶段的登记问题"，不是"某一次尝试"。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE,
        related_name="flywheel_registration_failures",
    )
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", on_delete=models.CASCADE,
        related_name="registration_failures",
        help_text="业务侧已经落库的正式产出：它的存在与可访问性不因登记失败而改变",
    )
    workflow_id = models.CharField(max_length=128, db_index=True)
    stage = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16, choices=REGISTRATION_STATUS_CHOICES,
        default=REGISTRATION_PENDING, db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(
        default=DEFAULT_REGISTRATION_MAX_ATTEMPTS,
    )
    last_error = models.TextField(blank=True)
    #: 逐次尝试的错误摘要（时间 + 阶段 + 错误）。留痕但不复制产出正文。
    history = models.JSONField(default=list, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="resolved_registration_failures",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # 幂等键：一份产出在一条链路的同一个阶段上只有一条补偿记录。
            models.UniqueConstraint(
                fields=["output", "workflow_id", "stage"],
                name="uniq_registration_failure_output_stage",
            )
        ]
        indexes = [
            models.Index(fields=["project", "status"], name="ke_regfail_proj_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}/{self.stage}:{self.output_id}:{self.status}"

    @property
    def is_open(self) -> bool:
        return self.status in REGISTRATION_OPEN_STATES

    @property
    def needs_human(self) -> bool:
        return self.status in REGISTRATION_ALERT_STATES

