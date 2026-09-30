"""V2分层测评配置与逐裁判证据。"""
import uuid

from django.conf import settings
from django.db import models


class EvaluationRubric(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="evaluation_rubrics"
    )
    task_type = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=64)
    dimensions = models.JSONField(default=list, blank=True)
    required_items = models.JSONField(default=list, blank=True)
    forbidden_items = models.JSONField(default=list, blank=True)
    jury_config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_evaluation_rubrics",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "task_type", "name", "version"],
                name="uniq_evaluation_rubric_version",
            )
        ]
        indexes = [models.Index(fields=["project", "task_type", "is_active"])]


class JudgeResult(models.Model):
    STATUS_CHOICES = [
        ("completed", "完成"), ("failed", "失败"), ("needs_review", "待人工复核")
    ]
    LEVEL_CHOICES = [("l0", "L0"), ("l1", "L1"), ("l2", "L2"), ("l3", "L3")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation_result = models.ForeignKey(
        "knowledge_evolution.EvaluationResult", on_delete=models.CASCADE,
        related_name="judge_results",
    )
    rubric = models.ForeignKey(
        EvaluationRubric, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="judge_results",
    )
    level = models.CharField(max_length=4, choices=LEVEL_CHOICES, db_index=True)
    evaluator_type = models.CharField(max_length=64, db_index=True)
    evaluator_version = models.CharField(max_length=64)
    judge_name = models.CharField(max_length=128)
    model_version = models.CharField(max_length=200, blank=True)
    score = models.FloatField(null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    confidence = models.FloatField(default=1.0)
    dimensions = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    rationale = models.TextField(blank=True)
    raw_output = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed", db_index=True)
    latency_ms = models.PositiveIntegerField(default=0)
    token_usage = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["evaluation_result", "level", "judge_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation_result", "level", "evaluator_type", "judge_name"],
                name="uniq_judge_result_per_evaluator",
            )
        ]
        indexes = [models.Index(fields=["level", "status", "created_at"])]
