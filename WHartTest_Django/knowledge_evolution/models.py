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
    trace = models.ForeignKey(
        RetrievalTrace, on_delete=models.PROTECT, related_name="generation_outputs"
    )
    capability = models.ForeignKey(
        "knowledge_evolution.CapabilityDefinition", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="generation_outputs",
    )
    task_type = models.CharField(max_length=64, db_index=True)
    task_id = models.CharField(max_length=128, blank=True, db_index=True)
    model_version = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=128, default="knowledge-template-v1")
    content = models.TextField()
    output_hash = models.CharField(max_length=64, db_index=True)
    #: 产出所依赖的 Skill 版本（T08 / R4）。任务启动时由 ``SkillRuntimeResolver``
    #: 固化的那一个版本，而不是"产出时刻的当前活跃版本"——新版本激活后回头再看
    #: 这条产出，仍然能回答"它当时跑的是哪份包"。
    skill_version = models.ForeignKey(
        "skills.SkillVersion", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="generation_outputs",
        verbose_name="Skill 版本",
        help_text="产出生成时锁定的 Skill 版本，用于归因与复现",
    )
    #: 冗余保存包哈希：即使版本记录被清理，产出仍能证明自己基于哪份内容。
    skill_package_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
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
    knowledge_versions = models.ManyToManyField(
        "knowledge_evolution.KnowledgeVersion",
        blank=True,
        related_name="feedback_events",
    )
    # ---- T10：把反馈精确绑定到"生成它的版本" ----
    # 这三个外键不是 output 的冗余：output 在产出被清理后会被 SET_NULL，
    # 而"这条反馈针对哪个能力/哪次发布/哪个 Skill 包"必须比产出本身活得更久，
    # 否则金标、归因、候选生成在事后都无法确定责任版本。
    capability = models.ForeignKey(
        "knowledge_evolution.CapabilityDefinition", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedback_events",
    )
    release = models.ForeignKey(
        "knowledge_evolution.CapabilityRelease", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedback_events",
    )
    skill_version = models.ForeignKey(
        "skills.SkillVersion", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedback_events",
    )
    #: 证据链：来源链接、截图、日志引用、复现步骤等。空列表表示"只有结论没有证据"。
    evidence = models.JSONField(default=list, blank=True)
    #: 能力形态快照（skill / composite / feedback_source / platform_utility），
    #: 存快照是因为注册表口径可能演进，历史反馈的分类不应被后续调整改写。
    capability_kind = models.CharField(max_length=32, blank=True, db_index=True)
    signal = models.CharField(max_length=32, choices=SIGNAL_CHOICES, db_index=True)
    value = models.FloatField(default=1.0)
    reason_code = models.CharField(max_length=100, blank=True)
    comment = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)
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


# ------------------------------------------------------------------- 4. 评测集
# 任务 3：种子评测集放在 models.py 而非 knowledge_models.py，避免循环引用
# RetrievalTrace / GenerationOutput。


class EvaluationSuite(models.Model):
    """种子/回归/新鲜/挑战评测集（tasks.md 任务 3、任务 11）。"""

    SUITE_TYPE_CHOICES = [
        ("seed", "种子集"),
        ("gold", "金标集"),
        ("regression", "回归集"),
        ("fresh", "新鲜集"),
        ("challenge", "挑战集"),
    ]
    TASK_TYPE_CHOICES = [
        ("knowledge_query", "知识库问答"),
        ("case_review", "用例审查"),
        ("code_review", "代码审查"),
        ("risk_identification", "风险识别"),
        ("test_plan_generation", "测试方案生成"),
        ("test_execution", "测试执行"),
        ("testcase_generation", "用例生成"),
        ("issue_tracking", "问题跟踪"),
        ("report_generation", "报告生成"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="evaluation_suites"
    )
    name = models.CharField(max_length=255)
    suite_type = models.CharField(max_length=20, choices=SUITE_TYPE_CHOICES, db_index=True)
    task_type = models.CharField(max_length=32, choices=TASK_TYPE_CHOICES, db_index=True)
    description = models.TextField(blank=True)
    split_ratio = models.JSONField(
        default=dict, blank=True,
        help_text="split 比例：{'gold': 0.2, 'regression': 0.5, 'fresh': 0.2, 'challenge': 0.1}",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_evaluation_suites",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "suite_type"], name="uniq_suite_project_name_type"
            )
        ]
        indexes = [
            models.Index(fields=["project", "suite_type", "task_type"]),
            models.Index(fields=["project", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"[{self.suite_type}] {self.name}"


class EvaluationCase(models.Model):
    """评测集里的一条样本。去重键由 suite + case_number 保证。"""

    SPLIT_CHOICES = [
        ("gold", "Gold"),
        ("regression", "Regression"),
        ("fresh", "Fresh"),
        ("challenge", "Challenge"),
        ("hidden", "Hidden"),
    ]
    TASK_TYPE_CHOICES = EvaluationSuite.TASK_TYPE_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    suite = models.ForeignKey(
        EvaluationSuite, on_delete=models.CASCADE, related_name="cases"
    )
    case_number = models.PositiveIntegerField()
    task_type = models.CharField(max_length=32, choices=TASK_TYPE_CHOICES)
    input_payload = models.JSONField(
        default=dict, help_text="输入：query/diff/requirement/测试计划"
    )
    expected_payload = models.JSONField(
        default=dict, blank=True, help_text="期望输出/标准答案/评分要点"
    )
    source_trace = models.ForeignKey(
        RetrievalTrace, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="evaluation_cases",
    )
    source_output = models.ForeignKey(
        GenerationOutput, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="evaluation_cases",
    )
    golden_labels = models.JSONField(
        default=dict, blank=True, help_text="人工标注：正确/错误/应召回证据/应拒绝项"
    )
    split = models.CharField(max_length=20, choices=SPLIT_CHOICES, default="regression", db_index=True)
    annotator = models.CharField(max_length=255, blank=True, help_text="标注人/脚本名")
    annotated_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["suite", "case_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["suite", "case_number"], name="uniq_case_suite_number"
            )
        ]
        indexes = [
            models.Index(fields=["suite", "split"]),
            models.Index(fields=["task_type", "split"]),
        ]

    def __str__(self) -> str:
        return f"{self.suite} #{self.case_number}"


# 通过 models 模块导出，确保 Django 在应用加载时注册正式金标模型。
from .gold_models import (  # noqa: E402,F401
    AnnotationConflict,
    GoldAnnotation,
    GoldCase,
    GoldDataset,
    GoldDatasetVersion,
)


# 任务 4：知识资产化核心模型单独成文件，这里显式导入以便 Django 注册到本 app。
from .knowledge_models import (  # noqa: E402,F401
    ACLMixin,
    ACLQuerySet,
    IndexProjection,
    KnowledgeAsset,
    KnowledgeAuditLog,
    KnowledgeCandidate,
    KnowledgeConflict,
    KnowledgeEvidence,
    KnowledgeVersion,
    IndexProjectionOutbox,
    SourceSnapshot,
)

# 任务 8：文档图谱节点/边模型
from .graph_models import GraphEdge, GraphNode  # noqa: E402,F401

# 任务 9：可版本化检索编排器策略模型
from .retrieval_models import RetrievalPolicy  # noqa: E402,F401

# 任务 11：评测运行与结果模型
from .evaluation_models import EvaluationResult, EvaluationRun  # noqa: E402,F401

# V2分层测评量表和逐裁判结果
from .evaluation_v2_models import EvaluationRubric, JudgeResult  # noqa: E402,F401

# 节点级执行轨迹与失败归因
from .trace_models import ExecutionSpan, FailureAttribution  # noqa: E402,F401

# 项目级四阶段 Skill 质量门禁 + 任务级 Skill 版本锁（T08/T15）。
# 两个都显式导入：模型注册靠"应用加载时被 import 到"，若只靠别处顺手 import，
# 一旦那个模块被重构掉，模型就会从 Django 的应用注册表里消失（表现为迁移生成不出、表查不到）。
from .workflow_models import WorkflowSkillLock, WorkflowStageGate  # noqa: E402,F401

# T09：能力评测门禁的不可变快照。
from .gate_models import EvaluationGateSnapshot  # noqa: E402,F401

# 受控优化候选与实验
from .optimization_models import OptimizationExperiment, OptimizationProposal  # noqa: E402,F401

# 任务 14–16：能力候选、影子门禁、发布与回滚
from .capability_models import (  # noqa: E402,F401
    CapabilityDefinition, CapabilityRelease, PromotionDecision, ReleaseObservation,
)
