"""节点级执行轨迹与失败归因模型。"""
import uuid

from django.conf import settings
from django.db import models


class ExecutionSpan(models.Model):
    STEP_CHOICES = [
        ("intent", "意图理解"), ("planning", "任务规划"),
        ("retrieval", "知识检索"), ("graph_retrieval", "图谱检索"),
        ("prompt", "Prompt组装"), ("model", "模型推理"),
        ("tool", "工具调用"), ("validation", "结果校验"),
        ("handoff", "Agent交接"), ("human_edit", "人工修改"),
        ("downstream", "下游执行"),
    ]
    STATUS_CHOICES = [
        ("running", "运行中"), ("completed", "完成"),
        ("failed", "失败"), ("skipped", "跳过"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        "knowledge_evolution.RetrievalTrace", on_delete=models.CASCADE, related_name="spans"
    )
    parent_span = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    workflow_id = models.CharField(max_length=128, blank=True, db_index=True)
    stage = models.CharField(max_length=64, db_index=True)
    step_type = models.CharField(max_length=32, choices=STEP_CHOICES, db_index=True)
    sequence = models.PositiveIntegerField(default=0)
    agent_name = models.CharField(max_length=128, blank=True)
    agent_version = models.CharField(max_length=128, blank=True)
    prompt_version = models.CharField(max_length=128, blank=True)
    model_version = models.CharField(max_length=200, blank=True)
    knowledge_version_ids = models.JSONField(default=list, blank=True)
    tool_name = models.CharField(max_length=128, blank=True)
    tool_version = models.CharField(max_length=128, blank=True)
    input_hash = models.CharField(max_length=64, blank=True)
    output_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="running", db_index=True)
    error_type = models.CharField(max_length=100, blank=True, db_index=True)
    error_message = models.TextField(blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    token_usage = models.PositiveIntegerField(default=0)
    evidence = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["trace", "sequence", "created_at"]
        indexes = [
            models.Index(fields=["trace", "stage", "sequence"]),
            models.Index(fields=["workflow_id", "status"]),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.parent_span_id and self.parent_span.trace_id != self.trace_id:
            raise ValidationError("父子执行节点必须属于同一检索轨迹")


class FailureAttribution(models.Model):
    CATEGORY_CHOICES = [
        ("intent_error", "理解失败"), ("planning_error", "规划失败"),
        ("knowledge_missing", "知识缺失"), ("knowledge_stale", "知识过期"),
        ("retrieval_error", "检索失败"), ("prompt_error", "Prompt失败"),
        ("tool_error", "工具失败"), ("generation_error", "生成失败"),
        ("downstream_execution_error", "下游执行失败"),
    ]
    SOURCE_CHOICES = [("rule", "确定性规则"), ("llm", "LLM辅助"), ("human", "人工")]
    STATE_CHOICES = [("proposed", "待确认"), ("confirmed", "已确认"), ("rejected", "已驳回")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="failure_attributions"
    )
    output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", null=True, blank=True,
        on_delete=models.CASCADE, related_name="failure_attributions",
    )
    span = models.ForeignKey(
        ExecutionSpan, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="failure_attributions",
    )
    workflow_id = models.CharField(max_length=128, blank=True, db_index=True)
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES, db_index=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="rule")
    confidence = models.FloatField(default=1.0)
    hypothesis = models.TextField()
    evidence = models.JSONField(default=list, blank=True)
    counterevidence = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="proposed", db_index=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="confirmed_failure_attributions",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "category", "state"]),
            models.Index(fields=["workflow_id", "state"]),
        ]
