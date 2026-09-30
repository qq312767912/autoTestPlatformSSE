import uuid

from django.conf import settings
from django.db import models


class CapabilityRelease(models.Model):
    KIND_CHOICES = [
        ("knowledge", "知识"), ("retrieval_policy", "检索策略"),
        ("prompt", "Prompt"), ("skill", "Skill"), ("agent", "Agent"),
    ]
    STATE_CHOICES = [
        ("draft", "草稿"), ("shadow", "影子验证"),
        ("awaiting_approval", "待审批"), ("active", "生产生效"),
        ("retired", "已退役"), ("rolled_back", "已回滚"),
        ("rejected", "未通过"),
    ]
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
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_capability_releases")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_capability_releases")
    approved_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["project", "kind", "version"], name="uniq_capability_release_version")]
        indexes = [models.Index(fields=["project", "kind", "state"])]


class CapabilityDefinition(models.Model):
    """平台级能力定义：把飞轮从记录器升级为可声明、可复用的公共能力。

    - evaluation_mode=single：对单一阶段（case_review/code_review/knowledge_query）做测评与晋级。
    - evaluation_mode=workflow：对链路（risk→plan→case→execution→issue）做端到端自进化。
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
    DECISION_CHOICES = [("approved", "通过"), ("rejected", "驳回"), ("rollback", "回滚")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey(CapabilityRelease, on_delete=models.CASCADE, related_name="decisions")
    decision = models.CharField(max_length=16, choices=DECISION_CHOICES)
    gate_snapshot = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="capability_decisions")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
