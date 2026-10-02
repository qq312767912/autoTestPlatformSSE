import uuid

from django.conf import settings
from django.db import models


class CapabilityRelease(models.Model):
    KIND_CHOICES = [
        ("knowledge", "知识"), ("retrieval_policy", "检索策略"),
        ("prompt", "Prompt"), ("skill", "Skill"), ("agent", "Agent"),
        # 复合能力：代码审查等由多个可进化子单元（Prompt/规则/检索/反证/工具配置）
        # 组合发布的单元，不产出 Skill 包。
        ("composite", "复合能力"),
    ]
    STATE_CHOICES = [
        ("draft", "草稿"),
        # 校验中：候选已创建，静态校验/安全扫描尚未通过，不允许进入影子评测。
        ("validating", "校验中"),
        ("shadow", "影子验证"),
        ("awaiting_approval", "待审批"), ("active", "生产生效"),
        ("retired", "已退役"), ("rolled_back", "已回滚"),
        ("rejected", "未通过"),
        # 隔离：安全或一致性风险，任何运行时都不得加载，需人工处置后才能解除。
        ("quarantined", "已隔离"),
    ]

    #: 允许作为激活前置的状态集合，供服务层与测试共用，避免各处硬编码。
    ACTIVATABLE_STATES = frozenset({"awaiting_approval"})
    #: 终态：不允许再回到其它状态。
    TERMINAL_STATES = frozenset({"retired", "rolled_back"})
    #: 禁止被运行时加载的状态。
    BLOCKED_STATES = frozenset({"quarantined", "rejected"})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="capability_releases")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=100)
    config = models.JSONField(default=dict, blank=True)
    artifact_hash = models.CharField(max_length=64, db_index=True)
    state = models.CharField(max_length=24, choices=STATE_CHOICES, default="draft", db_index=True)
    candidate = models.ForeignKey("knowledge_evolution.KnowledgeCandidate", null=True, blank=True, on_delete=models.SET_NULL, related_name="capability_releases")
    previous_release = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="successors")
    baseline_run = models.ForeignKey("knowledge_evolution.EvaluationRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="baseline_releases")
    candidate_run = models.ForeignKey("knowledge_evolution.EvaluationRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="candidate_releases")
    gate_report = models.JSONField(default=dict, blank=True)
    # ---- T13：审批引用的评测证据必须能被"事后对账" ----
    # 提交审批时记下当时那份门禁快照的内容哈希；真正激活前重新比对一次。
    # 为什么非要这一步：候选提交审批后仍可能被重新评测（补样本、修评测集、
    # 甚至悄悄换掉评测集让指标变好看）。若激活时不再校验，负责人签字批准的是
    # 快照 A，上线的却是依据快照 B 的版本——审批就成了形式。
    approval_snapshot_hash = models.CharField(max_length=64, blank=True, db_index=True)
    #: 该版本当前（最近一次）被观察到的生产指标窗口键与状态，便于前端直接展示。
    observation_state = models.CharField(max_length=16, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_capability_releases")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_capability_releases")
    approved_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # 版本唯一性的粒度必须带上 name：不同 Skill/能力各自发 1.0.0 是合法的，
            # 只按 (project, kind, version) 会误伤第二个同名版本号的能力。
            models.UniqueConstraint(
                fields=["project", "kind", "name", "version"],
                name="uniq_capability_release_version",
            ),
            # 同一发布目标（项目 + 类型 + 名称）最多一个 active 版本。
            # 这是并发激活的数据库级兜底：两个事务同时激活时，后者会在提交阶段
            # 违反该约束而整体回滚，不会留下"双活"状态。
            models.UniqueConstraint(
                fields=["project", "kind", "name"],
                condition=models.Q(state="active"),
                name="uniq_active_release_per_target",
            ),
        ]
        indexes = [models.Index(fields=["project", "kind", "state"])]

    def __str__(self):
        return f"{self.kind}:{self.name}@{self.version} ({self.state})"


class CapabilityDefinition(models.Model):
    """平台级能力定义：把飞轮从记录器升级为可声明、可复用的公共能力。

    - evaluation_mode=single：对单一阶段（case_review/code_review/knowledge_query）做测评与晋级。
    - evaluation_mode=workflow：对全链路测试（plan→case→execution→report）做端到端自进化。
    - stages：有序阶段列表；single 模式允许 1 个或多个阶段聚合测评，workflow 模式必须 >=2 且按执行顺序。
    """

    EVALUATION_MODE_CHOICES = [
        ("single", "单次测评"),
        ("workflow", "链路自进化"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="capability_definitions"
    )
    kind = models.CharField(max_length=32, choices=CapabilityRelease.KIND_CHOICES, db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    description = models.TextField(blank=True)
    evaluation_mode = models.CharField(
        max_length=20, choices=EVALUATION_MODE_CHOICES, default="single", db_index=True
    )
    stages = models.JSONField(
        default=list, blank=True,
        help_text="有序阶段列表，如 ['risk_identification', 'test_plan_generation', ...]",
    )
    default_suite = models.ForeignKey(
        "knowledge_evolution.EvaluationSuite", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="capability_definitions",
    )
    gate_rules = models.JSONField(
        default=dict, blank=True,
        help_text="晋级门禁规则：min_mean_diff、max_latency_regression、max_token_regression 等",
    )
    active_release = models.ForeignKey(
        CapabilityRelease, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="active_for_definitions",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_capability_definitions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], name="uniq_capability_definition_project_name"
            )
        ]
        indexes = [
            models.Index(fields=["project", "kind", "evaluation_mode"]),
            models.Index(fields=["project", "is_active"]),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        valid_stages = {c[0] for c in self.default_suite.TASK_TYPE_CHOICES} if self.default_suite else set()
        valid_stages = valid_stages or {
            "case_review", "code_review", "knowledge_query", "risk_identification",
            "test_plan_generation", "testcase_generation", "test_execution", "issue_tracking",
            "report_generation",
        }
        if not isinstance(self.stages, list):
            raise ValidationError({"stages": "必须是列表"})
        if not self.stages:
            raise ValidationError({"stages": "不能为空"})
        unknown = set(self.stages) - valid_stages
        if unknown:
            raise ValidationError({"stages": f"未知阶段: {unknown}"})
        if self.evaluation_mode == "workflow" and len(self.stages) < 2:
            raise ValidationError({"stages": "链路自进化模式至少需要 2 个阶段"})


class PromotionDecision(models.Model):
    DECISION_CHOICES = [
        ("approved", "通过"),
        ("rejected", "驳回"),
        ("rollback", "回滚"),
        # 隔离属于安全事件，与普通驳回分开留痕，便于审计时单独统计。
        ("quarantine", "隔离"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey(CapabilityRelease, on_delete=models.CASCADE, related_name="decisions")
    decision = models.CharField(max_length=16, choices=DECISION_CHOICES)
    gate_snapshot = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="capability_decisions")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class ReleaseObservation(models.Model):
    """灰度与生产观察窗口，用于门禁后持续监控及自动回滚。"""

    STATUS_CHOICES = [("healthy", "健康"), ("breached", "超阈值")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey(
        CapabilityRelease, on_delete=models.CASCADE, related_name="observations"
    )
    window_key = models.CharField(max_length=128)
    metrics = models.JSONField(default=dict)
    thresholds = models.JSONField(default=dict)
    breaches = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="capability_release_observations",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["release", "window_key"], name="uniq_release_observation_window"
            )
        ]
        indexes = [models.Index(fields=["release", "status", "created_at"])]
