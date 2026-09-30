"""受控优化候选和验证实验。"""
import uuid

from django.conf import settings
from django.db import models


class OptimizationProposal(models.Model):
    TYPE_CHOICES = [
        ("prompt", "Prompt"), ("knowledge", "知识"),
        ("retrieval_policy", "检索策略"), ("skill_tool", "Skill/工具配置"),
    ]
    STATE_CHOICES = [
        ("draft", "草稿"), ("evaluating", "评测中"),
        ("awaiting_approval", "待审批"), ("approved", "已批准"),
        ("rejected", "已驳回"), ("superseded", "已取代"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="optimization_proposals"
    )
    capability = models.ForeignKey(
        "knowledge_evolution.CapabilityDefinition", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="optimization_proposals",
    )
    baseline_release = models.ForeignKey(
        "knowledge_evolution.CapabilityRelease", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="optimization_proposals",
    )
    attributions = models.ManyToManyField(
        "knowledge_evolution.FailureAttribution", related_name="optimization_proposals"
    )
    proposal_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    summary = models.TextField()
    change_patch = models.JSONField(default=dict)
    expected_benefit = models.JSONField(default=dict, blank=True)
    impact_scope = models.JSONField(default=dict, blank=True)
    risk_notes = models.JSONField(default=list, blank=True)
    rollback_plan = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=24, choices=STATE_CHOICES, default="draft", db_index=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    generated_by = models.CharField(max_length=128, default="controlled-optimizer-v1")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_optimization_proposals",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "proposal_type", "state"])]


class OptimizationExperiment(models.Model):
    STATUS_CHOICES = [
        ("pending", "待运行"), ("running", "运行中"),
        ("passed", "通过"), ("failed", "失败"), ("cancelled", "取消"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(
        OptimizationProposal, on_delete=models.CASCADE, related_name="experiments"
    )
    gold_dataset_version = models.ForeignKey(
        "knowledge_evolution.GoldDatasetVersion", on_delete=models.PROTECT,
        related_name="optimization_experiments",
    )
    baseline_run = models.ForeignKey(
        "knowledge_evolution.EvaluationRun", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="optimization_baseline_experiments",
    )
    candidate_run = models.ForeignKey(
        "knowledge_evolution.EvaluationRun", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="optimization_candidate_experiments",
    )
    candidate_release = models.ForeignKey(
        "knowledge_evolution.CapabilityRelease", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="optimization_experiments",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    gate_report = models.JSONField(default=dict, blank=True)
    shadow_metrics = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_optimization_experiments",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["proposal", "status"])]
