"""项目级四阶段 Skill 质量流水线门禁模型。"""
import uuid

from django.conf import settings
from django.db import models


class WorkflowStageGate(models.Model):
    STAGE_CHOICES = [
        ("test_plan_generation", "测试方案生成"),
        ("testcase_generation", "测试用例生成"),
        ("test_execution", "测试执行"),
        ("report_generation", "报告生成"),
    ]
    STATUS_CHOICES = [
        ("pending", "待测评"),
        ("passed", "测评通过"),
        ("failed", "测评失败"),
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
