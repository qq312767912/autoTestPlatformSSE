"""项目级四阶段 Skill 质量流水线门禁模型。"""
import uuid

from django.conf import settings
from django.db import models

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

