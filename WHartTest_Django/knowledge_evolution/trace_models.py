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
        # T11：需求 R6 要求归因覆盖"执行环境"这一层。原先 9 个类别里没有一类
        # 对应环境问题（依赖缺失、超时、配额、网络），环境类失败只能被勉强塞进
        # tool_error 或 downstream，责任层判断失真，候选也就派错了对象。
        ("environment_error", "执行环境失败"),
        ("downstream_execution_error", "下游执行失败"),
    ]
    SOURCE_CHOICES = [("rule", "确定性规则"), ("llm", "LLM辅助"), ("human", "人工")]
    STATE_CHOICES = [("proposed", "待确认"), ("confirmed", "已确认"), ("rejected", "已驳回")]

    #: 八层归因（需求 R6「意图、规划、Prompt、知识、检索、Skill/工具、执行环境和下游结果」）。
    #: 层是**责任归属**的口径，类别是**具体毛病**的口径；一层可含多类。
    #: 与 ``ATTRIBUTION_LAYERS`` 保持一致，由 ``layer_of_category`` 派生写入。
    LAYER_CHOICES = [
        ("intent", "意图层"),
        ("planning", "规划层"),
        ("prompt", "Prompt层"),
        ("knowledge", "知识层"),
        ("retrieval", "检索层"),
        ("skill_tool", "Skill/工具层"),
        ("environment", "执行环境层"),
        ("downstream", "下游结果层"),
    ]

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
    #: 责任层。由 category 派生（见 ``CATEGORY_LAYER_MAP``）。
    #:
    #: 默认值刻意留空而不是 ``skill_tool``：字段若带非空默认值，Django 会在构造实例时
    #: 就把它填上，``save()`` 里的"为空则补"判断永远为假，派生逻辑形同虚设。
    #: 留空后由 ``save()`` 统一补，既保证必填，又不给调用方猜的机会。
    layer = models.CharField(max_length=24, choices=LAYER_CHOICES, default="", blank=True, db_index=True)
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
            models.Index(fields=["project", "layer", "state"]),
        ]

    def save(self, *args, **kwargs):
        # 类别 → 层是纯函数映射，落库时补齐，避免调用方各自猜一层。
        if not self.layer:
            self.layer = CATEGORY_LAYER_MAP.get(self.category, "skill_tool")
        super().save(*args, **kwargs)


#: 类别 → 责任层。八层的唯一真值，`FailureAttribution.layer` 与归因服务都从它取。
#:
#: 把 ``generation_error`` 归到 ``skill_tool`` 是有意的：生成质量由 Skill 指令、
#: 模板与工具配置共同决定，而这三者恰好是 T12 允许派生候选的可改对象；
#: 若把它单独成层，就会出现"有一层永远无法通过改 Skill 修复"的死角。
CATEGORY_LAYER_MAP = {
    "intent_error": "intent",
    "planning_error": "planning",
    "prompt_error": "prompt",
    "knowledge_missing": "knowledge",
    "knowledge_stale": "knowledge",
    "retrieval_error": "retrieval",
    "tool_error": "skill_tool",
    "generation_error": "skill_tool",
    "environment_error": "environment",
    "downstream_execution_error": "downstream",
}

#: 八层的展示顺序（需求 R6 的列举顺序）。
LAYER_ORDER = (
    "intent", "planning", "prompt", "knowledge",
    "retrieval", "skill_tool", "environment", "downstream",
)

LAYER_LABELS = {
    "intent": "意图",
    "planning": "规划",
    "prompt": "Prompt",
    "knowledge": "知识",
    "retrieval": "检索",
    "skill_tool": "Skill/工具",
    "environment": "执行环境",
    "downstream": "下游结果",
}
