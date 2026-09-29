import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class RetrievalTrace(models.Model):
    STATUS_CHOICES = [
        ("completed", "已完成"),
        ("failed", "失败"),
        ("untraceable", "不可进入飞轮"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="retrieval_traces"
    )
    knowledge_base = models.ForeignKey(
        "knowledge.KnowledgeBase", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="retrieval_traces",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="retrieval_traces",
    )
    task_type = models.CharField(max_length=64, db_index=True)
    task_id = models.CharField(max_length=128, blank=True, db_index=True)
    query = models.TextField()
    rewritten_query = models.TextField(blank=True)
    policy_version = models.CharField(max_length=128, default="knowledge-legacy-v1")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")
    channels = models.JSONField(default=dict, blank=True)
    candidates = models.JSONField(default=list, blank=True)
    citations = models.JSONField(default=list, blank=True)
    timings = models.JSONField(default=dict, blank=True)
    token_usage = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "task_type", "created_at"]),
            models.Index(fields=["knowledge_base", "created_at"]),
        ]
        permissions = [
            ("export_retrievaltrace", "Can export retrieval traces"),
        ]


class GenerationOutput(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="generation_outputs"
    )
    trace = models.OneToOneField(
        RetrievalTrace, on_delete=models.PROTECT, related_name="generation_output"
    )
    task_type = models.CharField(max_length=64, db_index=True)
    task_id = models.CharField(max_length=128, blank=True, db_index=True)
    model_version = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=128, default="knowledge-template-v1")
    content = models.TextField()
    output_hash = models.CharField(max_length=64, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "task_type", "created_at"])]


class FeedbackEvent(models.Model):
    SIGNAL_CHOICES = [
        ("accepted", "采纳"),
        ("rejected", "驳回"),
        ("edited", "编辑"),
        ("test_passed", "测试通过"),
        ("test_failed", "测试失败"),
        ("defect_confirmed", "缺陷确认"),
        ("false_positive", "误报"),
        ("missed", "漏报"),
        ("merged", "已合并"),
        ("reverted", "已回退"),
    ]
    ACTOR_TYPE_CHOICES = [
        ("user", "用户"),
        ("system", "系统"),
        ("integration", "集成"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_feedback_events"
    )
    output = models.ForeignKey(
        GenerationOutput, on_delete=models.PROTECT, null=True, blank=True,
        related_name="feedback_events",
    )
    trace = models.ForeignKey(
        RetrievalTrace, on_delete=models.PROTECT, null=True, blank=True,
        related_name="feedback_events",
    )
    knowledge_version_ids = models.JSONField(default=list, blank=True)
    signal = models.CharField(max_length=32, choices=SIGNAL_CHOICES, db_index=True)
    value = models.FloatField(default=1.0)
    reason_code = models.CharField(max_length=100, blank=True)
    comment = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="knowledge_feedback_events",
    )
    actor_type = models.CharField(
        max_length=20, choices=ACTOR_TYPE_CHOICES, default="user"
    )
    idempotency_key = models.CharField(max_length=160, unique=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["project", "signal", "occurred_at"]),
            models.Index(fields=["trace", "signal"]),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if not self.output_id and not self.trace_id:
            raise ValidationError("反馈必须关联生成输出或检索轨迹")
        related_project_id = (
            self.output.project_id if self.output_id else self.trace.project_id
        )
        if related_project_id != self.project_id:
            raise ValidationError("反馈与关联输出必须属于同一项目")
