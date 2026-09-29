import uuid

from django.conf import settings
from django.db import models


class EvaluationRun(models.Model):
    """一次评测运行（任务 11：回放引擎）。"""

    STATUS_CHOICES = [
        ("pending", "待开始"),
        ("running", "运行中"),
        ("completed", "已完成"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    suite = models.ForeignKey(
        "knowledge_evolution.EvaluationSuite", on_delete=models.CASCADE,
        related_name="runs",
    )
    name = models.CharField(max_length=255, blank=True)
    config = models.JSONField(
        default=dict, blank=True,
        help_text="运行配置：executor_id / policy_id / model_version / top_k 等",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True,
    )
    metrics_summary = models.JSONField(default=dict, blank=True)
    cost_summary = models.JSONField(default=dict, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="evaluation_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["suite", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.suite} run {self.created_at}"


class EvaluationResult(models.Model):
    """某个 case 在某次 run 中的结果。"""

    STATUS_CHOICES = [
        ("pending", "待运行"),
        ("running", "运行中"),
        ("completed", "已完成"),
        ("failed", "失败"),
        ("skipped", "已跳过"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        EvaluationRun, on_delete=models.CASCADE, related_name="results",
    )
    case = models.ForeignKey(
        "knowledge_evolution.EvaluationCase", on_delete=models.CASCADE,
        related_name="results",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True,
    )
    predicted_payload = models.JSONField(default=dict, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    token_usage = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.FloatField(default=0.0)
    l0_score = models.FloatField(null=True, blank=True)
    l1_score = models.FloatField(null=True, blank=True)
    l2_score = models.FloatField(null=True, blank=True)
    l3_score = models.FloatField(null=True, blank=True)
    raw_scores = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["run", "case__case_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "case"], name="uniq_evalresult_run_case"
            ),
        ]
        indexes = [
            models.Index(fields=["run", "status"]),
            models.Index(fields=["case", "status"]),
        ]
