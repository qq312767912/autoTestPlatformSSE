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


class AssetCandidateEvent(models.Model):
    """候选沉淀的幂等事件与失败补偿记录（T04 / P5、R10）。

    要解决的问题：候选发现过去挂在"调用方记得调一次服务"上——正式阶段产出、
    确认稿上传、缺陷确认、漏测、误报、失败、回滚、编辑分散在五六个入口，
    谁漏调一次就没有候选，而且**没有任何地方能看出漏了**。

    这里把"要不要沉淀"与"沉淀成什么"拆开：

    * 业务入口只负责把信号写成一条**幂等**事件（``idempotency_key`` 唯一，
      同一信号重放不会重复建候选），写事件与业务写库在同一事务里，
      失败不会回滚业务结果；
    * 统一处理器做预检、去重、推荐归属，并且**只**产出
      ``GoldCase(state=candidate)``——自动化永远不产生"已确认"。

    失败不是"记条日志就完事"（R9）：``attempts`` 累加、``last_error`` 留痕，
    超过 ``max_attempts`` 落到 ``dead_letter`` 并由控制台告警，可人工重试。

    状态语义（刻意不含"已确认"这一档）：

    * ``pending``：已入队待处理；
    * ``processing``：正在处理；
    * ``needs_review``：已处理且**预检无阻断项**，已进入人工审核队列；
    * ``completed``：已处理但缺件（如缺陷类信号缺证据），候选保留、
      缺件清单在 ``preflight.missing``，**不进**审核队列直到补齐；
    * ``failed``：本次处理失败，未达死信阈值，可重试；
    * ``dead_letter``：连续失败超阈值，需人工介入。
    """

    SOURCE_TYPE_CHOICES = [
        ("stage_output", "正式阶段产出"),
        ("feedback", "人工反馈"),
        ("history_import", "历史资料导入"),
    ]
    STATUS_CHOICES = [
        ("pending", "待处理"),
        ("processing", "处理中"),
        ("needs_review", "待人工审核"),
        ("completed", "已处理"),
        ("failed", "处理失败"),
        ("dead_letter", "死信"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="asset_candidate_events",
    )
    source_type = models.CharField(max_length=24, choices=SOURCE_TYPE_CHOICES, db_index=True)
    #: 触发来源的业务对象标识（GenerationOutput / FeedbackEvent 的主键）。存字符串而不是
    #: 外键：来源可能是尚未入库的历史导入包，硬外键会让"先入队再落库"的路径写不进去。
    source_id = models.CharField(max_length=64, db_index=True)
    #: 触发信号：阶段产出记 ``stage_output``，其余为 ``FeedbackEvent.signal``。
    signal = models.CharField(max_length=32, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    last_error = models.TextField(blank=True)
    #: 处理输入快照：只放标识与哈希，不放业务正文，避免把敏感内容复制进队列表。
    payload = models.JSONField(default=dict, blank=True)
    #: 预检结果：完整性清单、去重/冲突结论、推荐归属与优先级，供页面下钻。
    preflight = models.JSONField(default=dict, blank=True)
    candidate = models.ForeignKey(
        "knowledge_evolution.GoldCase", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="candidate_events",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status", "created_at"]),
            models.Index(fields=["project", "source_type", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.source_id} -> {self.status}"


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
    TestAssetTaxonomy,
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
from .workflow_models import (  # noqa: E402,F401
    FlywheelRun, StageExecutionContext, StageExecutionAttempt,
    WorkflowSkillLock, WorkflowStageGate,
)

from .history_models import (  # noqa: E402,F401
    HistoryImportBatch, HistoryImportItem, HistoryReplay,
    HistoryReplayDifference, ProjectFlywheelSetting,
)

# T09：能力评测门禁的不可变快照。
from .gate_models import EvaluationGateSnapshot  # noqa: E402,F401

# 受控优化候选与实验
from .optimization_models import OptimizationExperiment, OptimizationProposal  # noqa: E402,F401

# 任务 14–16：能力候选、影子门禁、发布与回滚
from .capability_models import (  # noqa: E402,F401
    CapabilityDefinition, CapabilityRelease, PromotionDecision, ReleaseObservation,
)
