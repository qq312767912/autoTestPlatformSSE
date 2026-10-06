from django.db.models import Q
from django.shortcuts import get_object_or_404
from projects.models import ProjectMember
from projects.roles import ensure_test_lead
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    AnnotationConflict,
    AssetCandidateEvent,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    GoldAnnotation,
    GoldCase,
    GoldDataset,
    GoldDatasetVersion,
    TestAssetTaxonomy,
    FlywheelRun,
    StageExecutionAttempt,
    HistoryImportBatch,
    HistoryReplay,
    HistoryReplayDifference,
    ProjectFlywheelSetting,
    KnowledgeCandidate,
    RetrievalTrace,
)
from .serializers import (
    AnnotationConflictSerializer,
    AssetCandidateEventSerializer,
    EvaluationResultSerializer,
    EvaluationRunSerializer,
    EvaluationSuiteSerializer,
    FeedbackEventSerializer,
    GenerationOutputSerializer,
    KnowledgeCandidateSerializer,
    KnowledgeAssetSerializer,
    KnowledgeAuditLogSerializer,
    KnowledgeConflictSerializer,
    KnowledgeEvidenceSerializer,
    KnowledgeVersionSerializer,
    RetrievalTraceSerializer,
    CapabilityReleaseSerializer,
    CapabilityDefinitionSerializer,
    AnnotationConflictSerializer,
    GoldAnnotationSerializer,
    GoldCaseSerializer,
    GoldDatasetSerializer,
    GoldDatasetVersionSerializer,
    TestAssetTaxonomySerializer,
    FlywheelRunSerializer,
    StageExecutionContextSerializer,
    StageExecutionAttemptSerializer,
    HistoryImportBatchSerializer,
    HistoryReplaySerializer,
    HistoryReplayDifferenceSerializer,
    ProjectFlywheelSettingSerializer,
    EvaluationRubricSerializer,
    JudgeResultSerializer,
    ExecutionSpanSerializer,
    FailureAttributionSerializer,
    OptimizationExperimentSerializer,
    OptimizationProposalSerializer,
)
from .capability_models import CapabilityDefinition, CapabilityRelease
from .evaluation_v2_models import EvaluationRubric, JudgeResult
from .trace_models import ExecutionSpan, FailureAttribution
from .optimization_models import OptimizationExperiment, OptimizationProposal
from .knowledge_models import (
    KnowledgeAsset,
    KnowledgeAuditLog,
    KnowledgeConflict,
    KnowledgeEvidence,
    KnowledgeVersion,
)
from .retrieval import RetrievalOrchestrator, RetrievalRequest


def _project_ids(user):
    return ProjectMember.objects.filter(user=user).values_list("project_id", flat=True)


def _ensure_project_member(user, project_id):
    if user.is_superuser:
        return
    if not ProjectMember.objects.filter(user=user, project_id=project_id).exists():
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("无权访问该项目")


def _ensure_test_lead(user, project_id):
    """现有 admin/owner 映射为测试负责人，member 映射为测试执行人员。

    角色判定真值统一在 ``projects.roles``：Skill Hub 与质量飞轮共用一套映射，
    避免"同一语义、两处实现"导致授权口径分叉。
    """
    ensure_test_lead(user, project_id)


def _ensure_linkage_enabled(project_id):
    """项目级灰度开关：飞轮**控制面入口**的统一闸门（T14 / R15）。

    只用 403 不用 400："这个项目还没灰度到这个能力"是权限语义；用 400 会把用户
    引向去检查 payload，而 payload 一点问题都没有。文案取 ``rollout`` 里的唯一真值。

    ⚠️ 调用顺序：**先成员、后开关**。反过来的话，非成员会因为"项目没开开关"
    拿到 403，而成员拿到的是同一状态码——两种身份得到同样的反馈，
    等于顺手把"这个项目是否已灰度"泄露给了不该知道的人。

    ⚠️ 只闸入口（发起 / 启动 / 纳管），**不闸执行面**（产出发布、登记、反馈、归因）：
    否则运维事后关掉开关，会把一条正在跑的受控链路从半路掐断。
    """
    from rest_framework.exceptions import PermissionDenied

    from .rollout import LINKAGE_DISABLED_MESSAGE, linkage_enabled

    if not linkage_enabled(project_id):
        raise PermissionDenied(LINKAGE_DISABLED_MESSAGE)


class ProjectScopedReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def scoped(self, queryset):
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))


class ProjectFlywheelSettingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectFlywheelSettingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ProjectFlywheelSetting.objects.select_related("project", "updated_by")
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    @action(detail=False, methods=["get"], url_path="state")
    def state(self, request):
        """当前项目是否已灰度开启飞轮联动（T14）。

        前端必须在**渲染入口按钮之前**拿到这个值：让用户点了按钮才收到 403，
        他看到的是"功能坏了"；提前拿到 ``enabled=false``，他看到的是
        "这个项目还没灰度到"，两种体验的差别就在这一次查询。

        成员即可读（不是测试负责人）：普通执行人员也要知道按钮为什么是灰的。
        """
        from rest_framework.exceptions import ValidationError

        from projects.models import Project
        from .rollout import linkage_state

        raw = request.query_params.get("project")
        if not raw:
            raise ValidationError({"project": "必须指定项目"})
        project_id = int(raw)
        _ensure_project_member(request.user, project_id)
        return Response(linkage_state(get_object_or_404(Project, pk=project_id)))

    @action(detail=False, methods=["post"], url_path="set")
    def set_flag(self, request):
        project = get_object_or_404(__import__("projects.models", fromlist=["Project"]).Project,
                                    pk=request.data.get("project"))
        _ensure_test_lead(request.user, project.pk)
        setting, _ = ProjectFlywheelSetting.objects.update_or_create(
            project=project, defaults={"enabled": bool(request.data.get("enabled")),
                                       "rollout_note": str(request.data.get("rollout_note") or ""),
                                       "updated_by": request.user},
        )
        return Response(self.get_serializer(setting).data)


class HistoryImportViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = HistoryImportBatchSerializer
    filterset_fields = ["project", "status"]

    def get_queryset(self):
        queryset = HistoryImportBatch.objects.select_related(
            "project", "created_by", "confirmed_by",
        ).prefetch_related("items__file")
        return self.scoped(queryset)

    @action(detail=False, methods=["post"])
    def preflight(self, request):
        from projects.models import Project
        from .history_ingestion import HistoryIngestionService
        project = get_object_or_404(Project, pk=request.data.get("project"))
        _ensure_project_member(request.user, project.pk)
        _ensure_linkage_enabled(project.pk)
        return Response(HistoryIngestionService.preflight(
            project=project, manifest=request.data.get("manifest") or {},
        ))

    @action(detail=False, methods=["post"])
    def confirm(self, request):
        from projects.models import Project
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .history_ingestion import HistoryIngestionService
        project = get_object_or_404(Project, pk=request.data.get("project"))
        _ensure_test_lead(request.user, project.pk)
        _ensure_linkage_enabled(project.pk)
        try:
            batch, created = HistoryIngestionService.confirm(
                project=project, token=str(request.data.get("confirmation_token") or ""), actor=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(self.get_serializer(batch).data, status=201 if created else 200)


class HistoryReplayViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = HistoryReplaySerializer
    filterset_fields = ["project", "batch", "status"]

    def get_queryset(self):
        queryset = HistoryReplay.objects.select_related(
            "project", "batch", "flywheel_run", "gold_version", "created_by",
        ).prefetch_related("differences")
        return self.scoped(queryset)

    @action(detail=False, methods=["post"])
    def start(self, request):
        from projects.models import Project
        from .history_ingestion import HistoryReplayService
        project = get_object_or_404(Project, pk=request.data.get("project"))
        _ensure_test_lead(request.user, project.pk)
        _ensure_linkage_enabled(project.pk)
        batch = get_object_or_404(HistoryImportBatch, pk=request.data.get("batch"), project=project)
        gold = None
        if request.data.get("gold_version"):
            gold = get_object_or_404(GoldDatasetVersion, pk=request.data["gold_version"])
        replay = HistoryReplayService.create(
            project=project, batch=batch, actor=request.user,
            workflow_id=str(request.data.get("workflow_id") or f"history-{batch.pk}"),
            gold_version=gold, config=request.data.get("config") or {},
        )
        return Response(self.get_serializer(replay).data, status=201)

    @action(detail=True, methods=["post"], url_path="record-results")
    def record_results(self, request, pk=None):
        from .history_ingestion import HistoryReplayService
        replay = self.get_object()
        _ensure_test_lead(request.user, replay.project_id)
        replay = HistoryReplayService.record(replay=replay, rows=request.data.get("rows") or [])
        return Response(self.get_serializer(replay).data)

    @action(detail=True, methods=["post"], url_path="decide-difference")
    def decide_difference(self, request, pk=None):
        from .history_ingestion import HistoryReplayService
        replay = self.get_object()
        _ensure_test_lead(request.user, replay.project_id)
        difference = get_object_or_404(HistoryReplayDifference, pk=request.data.get("difference"), replay=replay)
        difference = HistoryReplayService.decide(
            difference=difference, actor=request.user,
            decision=str(request.data.get("decision") or ""), note=str(request.data.get("note") or ""),
        )
        return Response(HistoryReplayDifferenceSerializer(difference).data)


class FlywheelRunViewSet(viewsets.ModelViewSet):
    serializer_class = FlywheelRunSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "entry_type", "intent", "status", "workflow_id"]
    search_fields = ["workflow_id"]

    def get_queryset(self):
        queryset = FlywheelRun.objects.select_related("project", "created_by")
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        from .flywheel_context import FlywheelContextService

        project = serializer.validated_data["project"]
        _ensure_project_member(self.request.user, project.pk)
        _ensure_linkage_enabled(project.pk)
        run = FlywheelContextService.create(
            project=project,
            workflow_id=serializer.validated_data["workflow_id"],
            entry_type=serializer.validated_data["entry_type"],
            actor=self.request.user,
            intent=serializer.validated_data.get("intent", "production"),
            requirement_document_ids=serializer.validated_data.get("requirement_document_ids", []),
            metadata=serializer.validated_data.get("metadata", {}),
        )
        serializer.instance = run

    def perform_update(self, serializer):
        _ensure_project_member(self.request.user, serializer.instance.project_id)
        if "project" in serializer.validated_data or "workflow_id" in serializer.validated_data:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("运行创建后不能修改 project 或 workflow_id")
        serializer.save()

    @action(detail=False, methods=["post"], url_path="open")
    def open_run(self, request):
        """四类入口统一的「创建或选择流程上下文」（T06 / R1、R2）。

        为什么单独一个动作而不是让各入口自己 POST /flywheel-runs/：入口关心的不是
        "建一条记录"，而是"在这个项目、这条链上接着干"。派生规则（entry_type+source_id
        → workflow_id）必须唯一，否则同一份需求从需求页和 Agent 页各发起一次就会开出
        两条链，四阶段产出再也拼不回一条可追溯链。
        """
        from .flywheel_context import FlywheelContextService

        project_id = request.data.get("project")
        if not project_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"project": "必须指定项目"})
        _ensure_project_member(request.user, int(project_id))
        _ensure_linkage_enabled(int(project_id))
        from django.core.exceptions import ValidationError as DjangoValidationError
        from projects.models import Project
        project = get_object_or_404(Project, pk=int(project_id))
        try:
            result = FlywheelContextService.open(
                project=project,
                entry_type=request.data.get("entry_type", ""),
                source_id=request.data.get("source_id", ""),
                workflow_id=request.data.get("workflow_id", ""),
                actor=request.user,
                intent=request.data.get("intent", "production"),
                requirement_document_ids=request.data.get("requirement_document_ids") or [],
                metadata=request.data.get("metadata") or {},
            )
        except DjangoValidationError as exc:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(exc.messages)
        return Response(result)

    @action(detail=True, methods=["post"], url_path="resolve-stage")
    def resolve_stage(self, request, pk=None):
        from .flywheel_context import FlywheelContextService

        run = self.get_object()
        _ensure_project_member(request.user, run.project_id)
        try:
            result = FlywheelContextService.resolve_stage(
                run=run,
                stage=request.data.get("stage"),
                parent_output_ids=request.data.get("parent_output_ids") or [],
            )
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError
            from rest_framework.exceptions import ValidationError
            if isinstance(exc, DjangoValidationError):
                raise ValidationError(exc.messages)
            raise
        return Response(result)


class StageExecutionAttemptViewSet(ProjectScopedReadOnlyViewSet):
    """阶段执行尝试的查询与重试（T01 / R4）。

    之所以是**独立资源**而不是挂在 ``GenerationOutput`` 下：attempt 在正式产出
    之前就存在，甚至可能永远没有产出。挂在产出下意味着"跑失败的那一轮"没有
    地方可查，而它恰恰是最需要看的一轮。
    """

    serializer_class = StageExecutionAttemptSerializer
    filterset_fields = ["project", "workflow_id", "stage", "status", "entry_type"]

    def get_queryset(self):
        queryset = StageExecutionAttempt.objects.select_related(
            "project", "flywheel_run", "skill_version__skill", "output", "requested_by",
        )
        return self.scoped(queryset)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """重试一条已结束的 attempt：新建 attempt 并关联原记录。

        需要测试负责人权限：重试会重新消耗模型额度并可能覆盖已评过的产出，
        属于执行决策而不是"谁都能点的刷新"。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .operations import StageExecutionAttemptService

        attempt = self.get_object()
        _ensure_test_lead(request.user, attempt.project_id)
        try:
            new_attempt, created = StageExecutionAttemptService.retry(
                attempt,
                actor=request.user,
                idempotency_key=str(request.data.get("idempotency_key") or ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(
            self.get_serializer(new_attempt).data,
            status=201 if created else 200,
        )

    # ---------------------------------------------------------- 执行链路（T08）

    @action(detail=True, methods=["get"], url_path="trace")
    def trace(self, request, pk=None):
        """一次拿全：业务摘要 + 事件流 + Span 明细。

        合成一个接口而不是三个，是因为飞轮阶段卡在 Agent 运行期间会**反复轮询**：
        三个接口意味着三次往返、三种"前半段新、后半段旧"的拼装结果，
        页面上的进度会来回跳。
        """
        from .attribution import AttemptTraceService

        attempt = self.get_object()
        return Response({
            "summary": AttemptTraceService.summary(attempt, user=request.user),
            "events": AttemptTraceService.events(attempt, user=request.user),
            "spans": AttemptTraceService.spans(attempt, user=request.user),
        })

    @action(detail=True, methods=["get"], url_path="trace-spans")
    def trace_spans(self, request, pk=None):
        """只取 Span 明细（下钻面板展开时刷新用，避免整包重取）。"""
        from .attribution import AttemptTraceService

        attempt = self.get_object()
        return Response({
            "spans": AttemptTraceService.spans(attempt, user=request.user),
            "summary": AttemptTraceService.summary(attempt, user=request.user),
        })

    @action(detail=True, methods=["post"], url_path="trace-span")
    def record_trace_span(self, request, pk=None):
        """增量写入一个 Span（执行过程中即可调用，不等待最终产出）。

        权限用**执行人员**而不是负责人：写 Span 是执行链路的一部分，
        与"重试/覆盖产出"这种决策动作不同。
        """
        from rest_framework.exceptions import ValidationError

        from .attribution import AttemptTraceService

        attempt = self.get_object()
        _ensure_project_member(request.user, attempt.project_id)
        step_type = str(request.data.get("step_type") or "")
        if not step_type:
            raise ValidationError({"step_type": "必须提供 step_type"})
        try:
            span = AttemptTraceService.record_span(
                attempt=attempt,
                step_type=step_type,
                status=str(request.data.get("status") or "running"),
                tool_name=str(request.data.get("tool_name") or ""),
                tool_version=str(request.data.get("tool_version") or ""),
                agent_name=str(request.data.get("agent_name") or ""),
                latency_ms=int(request.data.get("latency_ms") or 0),
                error_type=str(request.data.get("error_type") or ""),
                error_message=str(request.data.get("error_message") or ""),
                input_hash=str(request.data.get("input_hash") or ""),
                output_hash=str(request.data.get("output_hash") or ""),
                sequence=request.data.get("sequence"),
                evidence=request.data.get("evidence") or [],
                metadata=request.data.get("metadata") or {},
            )
        except ValueError as exc:
            raise ValidationError({"sequence": str(exc)})
        return Response(
            {"span_id": str(span.id), "sequence": span.sequence, "status": span.status},
            status=201,
        )


class StageExecutionContextViewSet(viewsets.ReadOnlyModelViewSet):
    """执行上下文的解析接口（T02 / R3）。

    刻意**不继承** ``ProjectScopedReadOnlyViewSet``：那个基类靠 query 里的 project
    过滤 queryset，并把"取不到"表达成 404。而这里要做的是一次四道校验的解析，
    且「不存在」与「存在但跨项目」必须返回**同一个**结论——用基类的 404 语义
    恰好会把两者区分开，那正是我们不想泄漏的信息。
    """

    serializer_class = StageExecutionContextSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return StageExecutionContext.objects.select_related(
            "project", "attempt", "skill_version__skill", "flywheel_run",
        )

    def list(self, request):
        """按项目列出上下文（可选再按 workflow_id / stage 收窄）。

        列表存在的原因是排障：上下文过期后页面会提示"回飞轮重新派发"，
        而人要能查清"上一份是什么时候发的、解析过几次"。
        """
        from rest_framework.exceptions import ValidationError

        from .operations import StageExecutionContextService

        project_id = request.query_params.get("project")
        if not project_id:
            raise ValidationError("必须提供 project")
        project_id = int(project_id)
        StageExecutionContextService._assert_member(request.user, project_id)

        queryset = self.get_queryset().filter(project_id=project_id)
        for name in ("workflow_id", "stage"):
            value = request.query_params.get(name)
            if value:
                queryset = queryset.filter(**{name: str(value)})
        queryset = queryset.order_by("-created_at")[:100]
        return Response(self.get_serializer(queryset, many=True).data)

    def retrieve(self, request, pk=None):
        """解析一份上下文，返回业务页面需要的可信参数。

        返回的是 ``StageExecutionContextService.view`` 的扁平结构而不是
        serializer 的字段：业务页面需要「流程 + 阶段 + 锁定 Skill + 上游产出」
        一次拿全，且字段名要与飞轮派发回执一致，避免同一个概念在前后端
        出现两种拼法。
        """
        from rest_framework.exceptions import ValidationError

        from .operations import StageExecutionContextService

        project_id = request.query_params.get("project")
        if not project_id:
            raise ValidationError("必须提供 project")
        context = StageExecutionContextService.resolve(
            context_id=pk, project_id=int(project_id), user=request.user,
        )
        return Response(StageExecutionContextService.view(context))


class TestAssetTaxonomyViewSet(viewsets.ModelViewSet):
    serializer_class = TestAssetTaxonomySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "scope_key", "state"]

    def get_queryset(self):
        queryset = TestAssetTaxonomy.objects.select_related(
            "project", "maintained_by", "approved_by",
        )
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        _ensure_test_lead(self.request.user, project.pk)
        serializer.save(maintained_by=self.request.user)

    def perform_update(self, serializer):
        taxonomy = serializer.instance
        _ensure_test_lead(self.request.user, taxonomy.project_id)
        if taxonomy.state != "draft":
            from rest_framework.exceptions import ValidationError
            raise ValidationError("只有草稿分类版本可以编辑")
        serializer.save()

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        taxonomy = self.get_object()
        _ensure_test_lead(request.user, taxonomy.project_id)
        if taxonomy.state != "draft":
            from rest_framework.exceptions import ValidationError
            raise ValidationError("只有草稿可以送审")
        taxonomy.state = "review"
        taxonomy.save(update_fields=["state", "updated_at"])
        return Response(self.get_serializer(taxonomy).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        import hashlib
        import json

        from django.utils import timezone
        from rest_framework.exceptions import ValidationError

        taxonomy = self.get_object()
        _ensure_test_lead(request.user, taxonomy.project_id)
        if taxonomy.state != "review":
            raise ValidationError("只有待审批分类版本可以发布")
        if not taxonomy.categories or not taxonomy.critical_scenarios:
            raise ValidationError("发布前必须填写业务分类和关键场景清单")
        payload = {
            "scope_key": taxonomy.scope_key,
            "version": taxonomy.version,
            "categories": taxonomy.categories,
            "critical_scenarios": taxonomy.critical_scenarios,
        }
        taxonomy.content_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
        ).hexdigest()
        taxonomy.state = "published"
        taxonomy.approved_by = request.user
        taxonomy.approved_at = timezone.now()
        taxonomy.save(update_fields=[
            "state", "content_hash", "approved_by", "approved_at", "updated_at",
        ])
        return Response(self.get_serializer(taxonomy).data)

    @action(detail=True, methods=["post"])
    def retire(self, request, pk=None):
        from rest_framework.exceptions import ValidationError

        taxonomy = self.get_object()
        _ensure_test_lead(request.user, taxonomy.project_id)
        if taxonomy.state != "published":
            raise ValidationError("只有已发布分类版本可以退役")
        taxonomy.state = "retired"
        taxonomy.save(update_fields=["state", "updated_at"])
        return Response(self.get_serializer(taxonomy).data)


class GoldDatasetViewSet(viewsets.ModelViewSet):
    serializer_class = GoldDatasetSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "task_type", "status"]
    search_fields = ["name", "description"]

    def get_queryset(self):
        queryset = GoldDataset.objects.select_related("project", "owner", "created_by")
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        _ensure_test_lead(self.request.user, project.id)
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        _ensure_test_lead(self.request.user, serializer.instance.project_id)
        serializer.save()

    def perform_destroy(self, instance):
        _ensure_test_lead(self.request.user, instance.project_id)
        if instance.versions.filter(state="frozen").exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError("包含冻结版本的数据集不能删除，请改为归档")
        instance.delete()


class GoldDatasetVersionViewSet(viewsets.ModelViewSet):
    serializer_class = GoldDatasetVersionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["dataset", "state"]

    def get_queryset(self):
        queryset = GoldDatasetVersion.objects.select_related(
            "dataset__project", "created_by", "frozen_by", "parent_version"
        )
        return queryset if self.request.user.is_superuser else queryset.filter(
            dataset__project_id__in=_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        dataset = serializer.validated_data["dataset"]
        _ensure_test_lead(self.request.user, dataset.project_id)
        parent = serializer.validated_data.get("parent_version")
        if parent and parent.dataset_id != dataset.id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"parent_version": "后继版本必须属于同一数据集"})
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        _ensure_test_lead(self.request.user, serializer.instance.dataset.project_id)
        serializer.save()

    @action(detail=True, methods=["post"])
    def freeze(self, request, pk=None):
        version = self.get_object()
        _ensure_test_lead(request.user, version.dataset.project_id)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .gold import GoldVersionService
        try:
            version = GoldVersionService.freeze(version=version, actor=request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(self.get_serializer(version).data)

    @action(detail=True, methods=["post"], url_path="materialize-evaluation-suite")
    def materialize_evaluation_suite(self, request, pk=None):
        version = self.get_object()
        _ensure_test_lead(request.user, version.dataset.project_id)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .gold_evaluation import GoldEvaluationBridge
        try:
            suite = GoldEvaluationBridge.materialize(version=version, actor=request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(EvaluationSuiteSerializer(suite).data)


class GoldCaseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = GoldCaseSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["version", "task_type", "split", "state", "privacy_level"]
    search_fields = ["title", "source_hash"]

    def get_queryset(self):
        queryset = GoldCase.objects.select_related(
            "version__dataset__project", "source_output", "source_feedback", "created_by"
        ).prefetch_related("annotations__annotator")
        return queryset if self.request.user.is_superuser else queryset.filter(
            version__dataset__project_id__in=_project_ids(self.request.user)
        )

    def perform_update(self, serializer):
        case = serializer.instance
        _ensure_test_lead(self.request.user, case.version.dataset.project_id)
        serializer.save()

    @action(detail=False, methods=["post"], url_path="from-feedback")
    def from_feedback(self, request):
        version = get_object_or_404(GoldDatasetVersion, pk=request.data.get("version"))
        feedback = get_object_or_404(FeedbackEvent, pk=request.data.get("feedback"))
        _ensure_project_member(request.user, version.dataset.project_id)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .gold import GoldCandidateService
        try:
            case = GoldCandidateService.from_feedback(
                version=version, feedback=feedback, actor=request.user,
                split=request.data.get("split", "fresh"),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(self.get_serializer(case).data, status=201)

    @action(detail=True, methods=["post"])
    def annotate(self, request, pk=None):
        case = self.get_object()
        project_id = case.version.dataset.project_id
        _ensure_project_member(request.user, project_id)
        round_name = request.data.get("round", "primary")
        if round_name == "review":
            _ensure_test_lead(request.user, project_id)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .gold import GoldAnnotationService
        try:
            annotation = GoldAnnotationService.submit(
                case=case, round_name=round_name, actor=request.user,
                answer=request.data.get("answer", {}),
                rubric_scores=request.data.get("rubric_scores", {}),
                evidence=request.data.get("evidence", []),
                conclusion=request.data.get("conclusion", "accepted"),
                comment=request.data.get("comment", ""),
                # 治理结论（分区/分类/标签）随审核一起提交：T05 要求"审核动作写入
                # 最终标签、分区、分类、理由与证据快照"。它们不是事后另一次编辑——
                # 否则"这个分区是谁定的"就查不到了。
                tags=request.data.get("tags"),
                split=request.data.get("split", ""),
                category=request.data.get("category", ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(GoldAnnotationSerializer(annotation).data, status=201)


class GoldAnnotationViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = GoldAnnotationSerializer
    filterset_fields = ["case", "round", "conclusion", "annotator"]

    def get_queryset(self):
        queryset = GoldAnnotation.objects.select_related(
            "case__version__dataset__project", "annotator"
        )
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(case__version__dataset__project_id__in=_project_ids(self.request.user))


class AnnotationConflictViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = AnnotationConflictSerializer
    filterset_fields = ["case", "state"]

    def get_queryset(self):
        queryset = AnnotationConflict.objects.select_related(
            "case__version__dataset__project", "primary_annotation__annotator",
            "review_annotation__annotator", "resolved_by",
        )
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(case__version__dataset__project_id__in=_project_ids(self.request.user))

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        conflict = self.get_object()
        _ensure_test_lead(request.user, conflict.case.version.dataset.project_id)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .gold import GoldAnnotationService
        try:
            annotation = GoldAnnotationService.resolve(
                conflict=conflict, actor=request.user,
                answer=request.data.get("answer", {}),
                rubric_scores=request.data.get("rubric_scores", {}),
                evidence=request.data.get("evidence", []),
                conclusion=request.data.get("conclusion", "accepted"),
                comment=request.data.get("comment", ""),
                tags=request.data.get("tags"),
                split=request.data.get("split", ""),
                category=request.data.get("category", ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(GoldAnnotationSerializer(annotation).data)


class AssetCandidateViewSet(ProjectScopedReadOnlyViewSet):
    """候选沉淀事件队列与失败补偿入口（T04）。

    刻意**只读 + 显式重试**：事件状态由 ``AssetCandidateService`` 依据预检结果与
    失败阈值决定。给客户端一个"直接改 status"的入口，等于把死信阈值与去重预检
    变成可绕过的装饰——真正需要人工介入时，正确动作是重试或补齐缺件。
    """

    serializer_class = AssetCandidateEventSerializer
    filterset_fields = ["project", "source_type", "status", "signal"]

    def get_queryset(self):
        queryset = AssetCandidateEvent.objects.select_related("project", "candidate")
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """队列概览与死信告警计数（飞轮控制台轮询）。"""
        from rest_framework.exceptions import ValidationError

        from .gold import AssetCandidateService

        raw = request.query_params.get("project")
        if not raw:
            raise ValidationError({"project": "必须指定项目"})
        _ensure_project_member(request.user, int(raw))
        return Response(AssetCandidateService.status_summary(int(raw)))

    @action(detail=False, methods=["post"], url_path="retry")
    def retry(self, request):
        """批量重试本项目失败/死信事件（R9 的补偿入口）。"""
        from rest_framework.exceptions import ValidationError

        from .gold import AssetCandidateService

        raw = request.data.get("project")
        if not raw:
            raise ValidationError({"project": "必须指定项目"})
        project_id = int(raw)
        _ensure_project_member(request.user, project_id)
        statuses = request.data.get("statuses") or ["failed", "dead_letter"]
        results = AssetCandidateService.retry_failed(
            project_id=project_id, statuses=tuple(str(item) for item in statuses),
            actor=request.user,
        )
        return Response({
            "project_id": project_id, "retried": len(results), "results": results,
        })

    @action(detail=True, methods=["post"], url_path="retry")
    def retry_one(self, request, pk=None):
        from .gold import AssetCandidateService

        event = self.get_object()
        _ensure_project_member(request.user, event.project_id)
        event = AssetCandidateService.retry(event, actor=request.user)
        return Response(self.get_serializer(event).data)


class EvaluationRubricViewSet(viewsets.ModelViewSet):
    serializer_class = EvaluationRubricSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "task_type", "is_active"]
    search_fields = ["name", "version"]

    def get_queryset(self):
        queryset = EvaluationRubric.objects.select_related("project", "created_by")
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        _ensure_test_lead(self.request.user, project.id)
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        _ensure_test_lead(self.request.user, serializer.instance.project_id)
        serializer.save()


class JudgeResultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = JudgeResultSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["evaluation_result", "level", "evaluator_type", "status"]

    def get_queryset(self):
        queryset = JudgeResult.objects.select_related(
            "evaluation_result__run__suite__project", "rubric"
        )
        return queryset if self.request.user.is_superuser else queryset.filter(
            evaluation_result__run__suite__project_id__in=_project_ids(self.request.user)
        )


class ExecutionSpanViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ExecutionSpanSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["trace", "workflow_id", "stage", "step_type", "status"]

    def get_queryset(self):
        queryset = ExecutionSpan.objects.select_related("trace__project", "parent_span")
        return queryset if self.request.user.is_superuser else queryset.filter(
            trace__project_id__in=_project_ids(self.request.user)
        )

    @action(detail=False, methods=["post"], url_path="record-human-edit")
    def record_human_edit(self, request):
        output = get_object_or_404(GenerationOutput, pk=request.data.get("output"))
        _ensure_project_member(request.user, output.project_id)
        if "before" not in request.data or "after" not in request.data:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"before": "必须提供 before 和 after 以计算编辑差异"})
        from .attribution import SpanRecorder
        span = SpanRecorder.record_human_edit(
            output=output, actor=request.user, before=request.data["before"],
            after=request.data["after"], comment=request.data.get("comment", ""),
        )
        return Response(self.get_serializer(span).data, status=201)


class FailureAttributionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FailureAttributionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "output", "workflow_id", "category", "source", "state"]

    def get_queryset(self):
        queryset = FailureAttribution.objects.select_related(
            "project", "output", "span", "confirmed_by"
        )
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    @action(detail=False, methods=["post"], url_path="run")
    def run_attribution(self, request):
        output = get_object_or_404(GenerationOutput, pk=request.data.get("output"))
        _ensure_project_member(request.user, output.project_id)
        from .attribution import AttributionService
        results = AttributionService.run_for_output(output)
        return Response(self.get_serializer(results, many=True).data)

    @action(detail=False, methods=["post"], url_path="run-reverse")
    def run_reverse(self, request):
        output = get_object_or_404(GenerationOutput, pk=request.data.get("output"))
        _ensure_project_member(request.user, output.project_id)
        from .attribution import AttributionService
        results = AttributionService.run_reverse_for_output(output)
        return Response(self.get_serializer(results, many=True).data)

    @action(detail=False, methods=["post"], url_path="run-llm-assisted")
    def run_llm_assisted(self, request):
        output = get_object_or_404(GenerationOutput, pk=request.data.get("output"))
        _ensure_project_member(request.user, output.project_id)
        from .attribution import LLMAssistedAttributionService
        results = LLMAssistedAttributionService().run(output)
        return Response(self.get_serializer(results, many=True).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        attribution = self.get_object()
        _ensure_test_lead(request.user, attribution.project_id)
        from .attribution import AttributionService
        return Response(self.get_serializer(AttributionService.decide(
            attribution=attribution, actor=request.user, accepted=True,
        )).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        attribution = self.get_object()
        _ensure_test_lead(request.user, attribution.project_id)
        from .attribution import AttributionService
        return Response(self.get_serializer(AttributionService.decide(
            attribution=attribution, actor=request.user, accepted=False,
        )).data)


class OptimizationProposalViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OptimizationProposalSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "capability", "proposal_type", "state"]

    def get_queryset(self):
        queryset = OptimizationProposal.objects.select_related(
            "project", "capability", "baseline_release", "created_by"
        ).prefetch_related("attributions")
        return queryset if self.request.user.is_superuser else queryset.filter(
            project_id__in=_project_ids(self.request.user)
        )

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        attribution_ids = request.data.get("attribution_ids") or []
        attributions = list(FailureAttribution.objects.filter(id__in=attribution_ids))
        if len(attributions) != len(set(str(value) for value in attribution_ids)):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"attribution_ids": "包含不存在的归因记录"})
        if attributions:
            _ensure_project_member(request.user, attributions[0].project_id)
        capability = None
        if request.data.get("capability"):
            capability = get_object_or_404(CapabilityDefinition, pk=request.data["capability"])
        baseline = None
        if request.data.get("baseline_release"):
            baseline = get_object_or_404(CapabilityRelease, pk=request.data["baseline_release"])
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .optimization import OptimizationProposalService
        try:
            proposals = OptimizationProposalService.generate(
                attributions=attributions, actor=request.user,
                capability=capability, baseline_release=baseline,
                target_type=request.data.get("target_type"),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(self.get_serializer(proposals, many=True).data, status=201)

    @action(detail=True, methods=["post"], url_path="materialize")
    def materialize(self, request, pk=None):
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .optimization import OptimizationMaterializationService
        try:
            release = OptimizationMaterializationService.materialize(
                proposal=proposal, actor=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(
            CapabilityReleaseSerializer(release, context={"request": request}).data,
            status=201,
        )

    # ---------------------------------------------------------------- T13 工坊
    #
    # 这几个动作构成 Skill 进化工坊的服务端：派生候选 → 冻结集门禁 →
    # 提交审批 → 激活 → 观察 → 回滚。语义不合并（派生不顺带评测、评测不顺带
    # 提交审批），因为每一步的失败处理与授权人都不同；合成一个"一键进化"
    # 会让失败时无法判断停在哪儿、也没法只重跑其中一步。

    @staticmethod
    def _skill_content(proposal):
        from .skill_content import SkillContentGateService
        if proposal.proposal_type != "skill_content":
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"proposal_type": "该动作只适用于 skill_content 候选"})
        return SkillContentGateService

    @action(detail=True, methods=["get"], url_path="skill-content-plan")
    def skill_content_plan(self, request, pk=None):
        """候选计划与门禁摘要（只读）：改了哪些文件、回滚目标、缺哪些条件。"""
        proposal = self.get_object()
        _ensure_project_member(request.user, proposal.project_id)
        return Response(self._skill_content(proposal).summary(proposal))

    @action(detail=True, methods=["post"], url_path="skill-content-materialize")
    def skill_content_materialize(self, request, pk=None):
        """派生候选：产生**新的不可变 SkillVersion**，不改 active 包。"""
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .skill_content import SkillContentGateService, SkillContentOptimizationService
        try:
            result = SkillContentOptimizationService.materialize(
                proposal=proposal, actor=request.user,
                edits=request.data.get("edits") or None,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        payload = SkillContentGateService.summary(proposal)
        payload.update({
            "reused": result["reused"],
            "active_untouched": result["active_untouched"],
            "diff": result.get("diff") or {},
            "patch": result.get("patch") or {},
            "rollback_target": result.get("rollback_target", ""),
            "release": CapabilityReleaseSerializer(
                result["release"], context={"request": request},
            ).data,
        })
        return Response(payload, status=200 if result["reused"] else 201)

    @action(detail=True, methods=["post"], url_path="skill-content-evaluate")
    def skill_content_evaluate(self, request, pk=None):
        """冻结集对照 + 硬门禁。基线/候选必须是同一份冻结金标上的回放。"""
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        service = self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from .models import EvaluationRun, GoldDatasetVersion
        gold = get_object_or_404(
            GoldDatasetVersion, pk=request.data.get("gold_dataset_version"),
            dataset__project_id=proposal.project_id,
        )
        baseline_run = get_object_or_404(
            EvaluationRun, pk=request.data.get("baseline_run"),
            suite__project_id=proposal.project_id,
        )
        candidate_run = get_object_or_404(
            EvaluationRun, pk=request.data.get("candidate_run"),
            suite__project_id=proposal.project_id,
        )
        try:
            experiment = service.run(
                proposal=proposal, gold_version=gold,
                baseline_run=baseline_run, candidate_run=candidate_run,
                actor=request.user, thresholds=request.data.get("thresholds") or None,
                kind=str(request.data.get("kind") or "full"),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response({
            "experiment_id": str(experiment.id),
            "status": experiment.status,
            "gate_report": experiment.gate_report,
            "plan": service.summary(proposal),
        })

    @staticmethod
    def _skill_content_lifecycle(proposal):
        from .skill_content import SkillContentLifecycleService
        return SkillContentLifecycleService

    @action(detail=True, methods=["post"], url_path="skill-content-submit-approval")
    def skill_content_submit_approval(self, request, pk=None):
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        try:
            release = self._skill_content_lifecycle(proposal).submit_for_approval(
                proposal=proposal, actor=request.user,
                reason=str(request.data.get("reason") or ""),
                kind=str(request.data.get("kind") or "full"),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(CapabilityReleaseSerializer(release, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="skill-content-activate")
    def skill_content_activate(self, request, pk=None):
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        try:
            release = self._skill_content_lifecycle(proposal).activate(
                proposal=proposal, actor=request.user,
                reason=str(request.data.get("reason") or ""),
                kind=str(request.data.get("kind") or "full"),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(CapabilityReleaseSerializer(release, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="skill-content-reject")
    def skill_content_reject(self, request, pk=None):
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        try:
            release = self._skill_content_lifecycle(proposal).reject(
                proposal=proposal, actor=request.user,
                reason=str(request.data.get("reason") or ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(CapabilityReleaseSerializer(release, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="skill-content-observe")
    def skill_content_observe(self, request, pk=None):
        """记录一个灰度观察窗口；超阈值时自动回滚（复用发布状态机）。"""
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        try:
            payload = self._skill_content_lifecycle(proposal).observe(
                proposal=proposal,
                window_key=str(request.data.get("window_key") or ""),
                metrics=request.data.get("metrics") or {},
                actor=request.user,
                thresholds=request.data.get("thresholds") or None,
                auto_rollback=bool(request.data.get("auto_rollback", True)),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(payload, status=201)

    @action(detail=True, methods=["post"], url_path="skill-content-rollback")
    def skill_content_rollback(self, request, pk=None):
        proposal = self.get_object()
        _ensure_test_lead(request.user, proposal.project_id)
        self._skill_content(proposal)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        try:
            release = self._skill_content_lifecycle(proposal).rollback(
                proposal=proposal, actor=request.user,
                reason=str(request.data.get("reason") or ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(
            {"release": CapabilityReleaseSerializer(release, context={"request": request}).data}
            if release is not None else {"release": None},
        )

    @action(detail=True, methods=["get"], url_path="skill-content-running-flows")
    def skill_content_running_flows(self, request, pk=None):
        """取证：激活候选后，运行中流程仍钉在各自锁定的版本上。"""
        proposal = self.get_object()
        _ensure_project_member(request.user, proposal.project_id)
        from .skill_content import SkillContentGateService, SkillContentLifecycleService
        version = SkillContentGateService._candidate_version(proposal)
        if version is None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"proposal": "候选尚未派生 Skill 版本"})
        return Response(
            SkillContentLifecycleService.running_flow_evidence(version.skill)
        )


class OptimizationExperimentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OptimizationExperimentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["proposal", "gold_dataset_version", "status"]

    def get_queryset(self):
        queryset = OptimizationExperiment.objects.select_related(
            "proposal__project", "gold_dataset_version", "baseline_run",
            "candidate_run", "candidate_release", "created_by",
        )
        return queryset if self.request.user.is_superuser else queryset.filter(
            proposal__project_id__in=_project_ids(self.request.user)
        )


class RetrievalTraceViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = RetrievalTraceSerializer
    filterset_fields = ["project", "knowledge_base", "task_type", "status"]
    search_fields = ["query", "task_id"]
    ordering_fields = ["created_at", "token_usage"]

    def get_queryset(self):
        return self.scoped(
            RetrievalTrace.objects.select_related("project", "knowledge_base", "user")
        )


class GenerationOutputViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = GenerationOutputSerializer
    filterset_fields = ["project", "task_type", "trace"]
    search_fields = ["task_id", "output_hash"]

    def get_queryset(self):
        return self.scoped(GenerationOutput.objects.select_related("project", "trace"))

    @action(detail=True, methods=["get"], url_path="lineage")
    def lineage(self, request, pk=None):
        """`「这条产出是怎么来的」：闭环七段 + 内容来源（只读）。

        走 ``detail=True`` 复用 ``get_queryset()`` 的项目收口 ——
        越权读取在这个接口上不可能发生，而不是靠"id 猜不到"。

        两个问题一次答完，但**分两段返回**（``stages`` 与 ``sources``）：
        "闭环走到哪一步"与"内容参考了什么"是不同性质的事实。
        合并成一段会让页面把"链路已闭环"读成"内容已被验证"，
        而后者恰恰是这份产出能不能被信任的关键，不该被前者的绿灯盖过去。
        """
        from .lineage import OutputLineageService

        output = self.get_object()
        return Response(OutputLineageService.trace(output))


class FeedbackEventViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = FeedbackEventSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "signal", "trace", "output"]

    def get_queryset(self):
        queryset = FeedbackEvent.objects.select_related("project", "trace", "output", "actor")
        if self.request.user.is_superuser:
            return queryset
        project_ids = _project_ids(self.request.user)
        return queryset.filter(
            Q(project_id__in=project_ids) & Q(trace__project_id__in=project_ids)
        )

    def perform_create(self, serializer):
        trace = serializer.validated_data.get("trace")
        output = serializer.validated_data.get("output")
        trace = trace or (output.trace if output else None)
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=trace.project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权向该项目提交反馈")
        serializer.save()

    @action(detail=False, methods=["post"], url_path="outcomes")
    def outcomes(self, request):
        """外部客观信号回填：测试/缺陷/合并/回退等。"""
        output_id = request.data.get("output_id")
        trace_id = request.data.get("trace_id")
        signal = request.data.get("signal")
        if not signal:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"signal": "必须指定反馈信号"})

        output = None
        trace = None
        if output_id:
            output = get_object_or_404(GenerationOutput, pk=output_id)
            trace = output.trace
        elif trace_id:
            trace = get_object_or_404(RetrievalTrace, pk=trace_id)
        else:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"output_id|trace_id": "必须指定 output_id 或 trace_id"})

        project_id = output.project_id if output else trace.project_id
        if not request.user.is_superuser and not ProjectMember.objects.filter(
            user=request.user, project_id=project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权向该项目提交反馈")

        from .feedback import FeedbackService
        event = FeedbackService.record_for_output(
            output=output,
            signal=signal,
            actor=request.user,
            actor_type=request.data.get("actor_type", "integration"),
            reason_code=request.data.get("reason_code", ""),
            comment=request.data.get("comment", ""),
            detail=request.data.get("detail"),
            knowledge_version_ids=request.data.get("knowledge_version_ids"),
        )
        return Response(FeedbackEventSerializer(event, context={"request": request}).data)


class EvaluationSuiteViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = EvaluationSuiteSerializer
    filterset_fields = ["project", "suite_type", "task_type", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at"]

    def get_queryset(self):
        return self.scoped(
            EvaluationSuite.objects.select_related("project", "created_by")
        )


class EvaluationRunViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = EvaluationRunSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["suite", "status"]
    ordering_fields = ["created_at", "started_at", "finished_at"]

    def get_queryset(self):
        queryset = EvaluationRun.objects.select_related("suite", "triggered_by")
        if self.request.user.is_superuser:
            return queryset
        project_ids = _project_ids(self.request.user)
        return queryset.filter(suite__project_id__in=project_ids)

    def perform_create(self, serializer):
        suite = serializer.validated_data["suite"]
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=suite.project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该评测集下创建运行")
        serializer.save(triggered_by=self.request.user, status="pending")

    @action(detail=True, methods=["post"], url_path="generate-review-candidates")
    def generate_review_candidates(self, request, pk=None):
        run = self.get_object()
        thresholds = request.data.get("thresholds")
        min_failure_count = request.data.get("min_failure_count", 1)
        from .eval_review_bridge import EvaluationReviewBridge
        bridge = EvaluationReviewBridge(
            run,
            thresholds=thresholds,
            min_failure_count=int(min_failure_count),
        )
        candidates = bridge.generate_candidates(actor=request.user)
        return Response({
            "created_count": len(candidates),
            "candidate_ids": [str(c.id) for c in candidates],
        })

    @action(detail=True, methods=["get"], url_path="comparison")
    def comparison(self, request, pk=None):
        """与另一次运行做配对对比（默认 L1 生成层）。"""
        run = self.get_object()
        baseline_id = request.query_params.get("baseline_run") or request.data.get("baseline_run")
        if not baseline_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"baseline_run": "必须指定 baseline_run"})
        baseline = get_object_or_404(EvaluationRun, pk=baseline_id)
        if run.suite_id != baseline.suite_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"baseline_run": "baseline 必须与当前运行属于同一评测集"})

        if not request.user.is_superuser:
            project_ids = _project_ids(request.user)
            if run.suite.project_id not in project_ids or baseline.suite.project_id not in project_ids:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("无权对比该评测运行")

        level = request.query_params.get("level") or request.data.get("level") or "l1"
        from .evaluation import EvaluationEngine
        report = EvaluationEngine.compare_runs(
            baseline.results.select_related("case"),
            run.results.select_related("case"),
            level=str(level),
        )
        return Response(report)


class EvaluationResultViewSet(ProjectScopedReadOnlyViewSet):
    serializer_class = EvaluationResultSerializer
    filterset_fields = ["run", "status", "case__split"]
    ordering_fields = ["created_at", "l0_score", "l1_score", "l2_score", "l3_score"]

    def get_queryset(self):
        return self.scoped(
            EvaluationResult.objects.select_related("run__suite", "case")
        )

    def scoped(self, queryset):
        if self.request.user.is_superuser:
            return queryset
        project_ids = _project_ids(self.request.user)
        return queryset.filter(run__suite__project_id__in=project_ids)

    @action(detail=True, methods=["post"], url_path="layered-evaluate")
    def layered_evaluate(self, request, pk=None):
        """对一条已产出结果执行 L0-L3 分层评测；未显式传入裁判票时调用双 LLM 裁判。"""
        result = self.get_object()
        output = get_object_or_404(GenerationOutput, pk=request.data.get("output"))
        if output.project_id != result.run.suite.project_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"output": "产出与评测运行不属于同一项目"})
        rubric_id = request.data.get("rubric")
        rubric = (
            get_object_or_404(EvaluationRubric, pk=rubric_id)
            if rubric_id else EvaluationRubric.objects.filter(
                project_id=output.project_id, task_type=output.task_type, is_active=True,
            ).first()
        )
        if rubric and rubric.project_id != output.project_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"rubric": "量表与产出不属于同一项目"})
        workflow_id = ((output.metadata or {}).get("protocol") or {}).get("workflow_id")
        workflow_outputs = []
        if workflow_id:
            workflow_outputs = list(GenerationOutput.objects.filter(
                project_id=output.project_id,
                metadata__protocol__workflow_id=workflow_id,
            ))
        from .evaluators import LayeredEvaluationService
        LayeredEvaluationService.evaluate(
            evaluation_result=result, output=output, workflow_outputs=workflow_outputs,
            rubric=rubric,
        )
        return Response({
            "result": self.get_serializer(result).data,
            "judges": JudgeResultSerializer(
                result.judge_results.all(), many=True, context={"request": request},
            ).data,
        })


class KnowledgeCandidateViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = KnowledgeCandidateSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "kind", "origin", "state"]
    search_fields = ["payload", "review_reason"]
    ordering_fields = ["created_at", "confidence", "updated_at"]

    def get_queryset(self):
        queryset = KnowledgeCandidate.objects.select_related(
            "project", "source_snapshot", "promoted_asset", "reviewed_by", "created_by"
        )
        if self.request.user.is_superuser:
            return queryset
        project_ids = _project_ids(self.request.user)
        return queryset.filter(project_id__in=project_ids)

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=project.id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下创建候选")
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["post"], url_path="distill-feedback")
    def distill_feedback(self, request):
        project_id = request.data.get("project")
        task_type = request.data.get("task_type", "knowledge_query")
        if not project_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"project": "必须指定项目"})
        if not request.user.is_superuser and not ProjectMember.objects.filter(
            user=request.user, project_id=project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权蒸馏该项目的反馈")
        from .distillation import ExperienceDistiller
        result = ExperienceDistiller(
            project_id=int(project_id),
            task_type=str(task_type),
            minimum_distinct_tasks=int(request.data.get("minimum_distinct_tasks", 3)),
            minimum_objective_tasks=int(request.data.get("minimum_objective_tasks", 2)),
        ).run()
        return Response({
            "created_count": len(result.created),
            "updated_count": len(result.updated),
            "skipped_group_count": result.skipped_groups,
            "candidate_ids": [str(item.id) for item in (*result.created, *result.updated)],
        })


class CapabilityReleaseViewSet(viewsets.ModelViewSet):
    serializer_class = CapabilityReleaseSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "kind", "state"]

    def get_queryset(self):
        queryset = CapabilityRelease.objects.select_related(
            "project", "candidate", "previous_release", "baseline_run", "candidate_run"
        )
        return queryset if self.request.user.is_superuser else queryset.filter(project_id__in=_project_ids(self.request.user))

    def perform_create(self, serializer):
        from .capabilities import CapabilityReleaseService
        data = serializer.validated_data
        _ensure_test_lead(self.request.user, data["project"].id)
        release = CapabilityReleaseService.create(
            project=data["project"], kind=data["kind"], name=data["name"],
            version=data["version"], config=data.get("config", {}), actor=self.request.user,
            candidate=data.get("candidate"),
        )
        serializer.instance = release

    @action(detail=True, methods=["post"], url_path="evaluate-shadow")
    def evaluate_shadow(self, request, pk=None):
        from .capabilities import CapabilityReleaseService
        from .evaluation_models import EvaluationRun

        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        from rest_framework.exceptions import ValidationError

        try:
            baseline = EvaluationRun.objects.get(pk=request.data["baseline_run"])
            candidate = EvaluationRun.objects.get(pk=request.data["candidate_run"])
        except KeyError as exc:
            raise ValidationError({"runs": f"缺少参数 {exc.args[0]}"})
        except (EvaluationRun.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"runs": "评测运行不存在"})
        if baseline.suite.project_id != release.project_id or candidate.suite.project_id != release.project_id:
            raise ValidationError({"runs": "评测运行必须属于发布单元所在项目"})
        # 只透传已知阈值键：把请求体原样展开会把拼错的参数变成 TypeError（500），
        # 而正确行为是忽略无关字段、按默认阈值判定。
        allowed = (
            "min_mean_diff", "max_latency_regression", "max_token_regression",
            "min_sample_count", "max_p_value", "require_significance",
        )
        incoming = request.data.get("thresholds") or {}
        overrides = {key: incoming[key] for key in allowed if key in incoming}
        report = _run_release_action(
            CapabilityReleaseService.evaluate_shadow, release,
            baseline_run=baseline, candidate_run=candidate, **overrides,
        )
        return Response(report)

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None):
        from .capabilities import CapabilityReleaseService

        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        release = _run_release_action(
            CapabilityReleaseService.promote, release,
            actor=request.user, reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["post"])
    def rollback(self, request, pk=None):
        from .capabilities import CapabilityReleaseService

        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        previous = _run_release_action(
            CapabilityReleaseService.rollback, release,
            actor=request.user, reason=request.data.get("reason", ""),
        )
        return Response({"restored_release_id": str(previous.id) if previous else None})

    @action(detail=True, methods=["post"], url_path="approve-canary")
    def approve_canary(self, request, pk=None):
        from .capabilities import CapabilityReleaseService

        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        release = _run_release_action(
            CapabilityReleaseService.approve_for_canary, release,
            actor=request.user, reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["post"], url_path="record-observation")
    def record_observation(self, request, pk=None):
        release = self.get_object()
        _ensure_project_member(request.user, release.project_id)
        from rest_framework.exceptions import ValidationError
        if not request.data.get("window_key"):
            raise ValidationError({"window_key": "必须指定观察窗口"})
        from .capabilities import CapabilityReleaseService
        from .serializers import ReleaseObservationSerializer

        observation = _run_release_action(
            CapabilityReleaseService.record_observation, release,
            window_key=request.data["window_key"],
            metrics=request.data.get("metrics") or {}, actor=request.user,
            thresholds=request.data.get("thresholds"),
        )
        return Response(ReleaseObservationSerializer(observation).data, status=201)

    @action(detail=True, methods=["post"], url_path="complete-canary")
    def complete_canary(self, request, pk=None):
        from .capabilities import CapabilityReleaseService

        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        release = _run_release_action(
            CapabilityReleaseService.complete_canary, release,
            actor=request.user, reason=request.data.get("reason", ""),
            min_observations=request.data.get("min_observations"),
        )
        return Response(self.get_serializer(release).data)

    # ------------------------------------------------------------------
    # T18：Skill Hub 生产控制台所需的治理读口与动作
    #
    # 控制台的右栏要先回答两个问题才谈得上"可操作"：
    #   1）现在处在状态机的哪一格，还差什么才能提交/激活（``approval-view``）；
    #   2）缺的这些东西为什么缺（``missing_conditions`` 的逐条明细）。
    #
    # 关键设计约束：**缺失条件不在前端重算**。前端自己拼一套"能不能点"的判据，
    # 迟早会与后端校验分叉，表现为"按钮亮着但一点就报错"。这里把
    # ``EvaluationGateService.missing_conditions`` 原样透出，前端只做渲染。
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get"], url_path="approval-view")
    def approval_view(self, request, pk=None):
        """控制台右栏的权威状态：状态机位置、门禁快照、缺失条件、可否提交/激活。

        执行人员也需要读这个口——他要看到"为什么还不能提交审批"，才知道该补什么。
        写操作（提交/驳回/激活/回滚/隔离）的权限在各自 action 里单独判定。
        """
        release = self.get_object()
        _ensure_project_member(request.user, release.project_id)
        from .capabilities import CapabilityReleaseService
        from .evaluation_gates import EvaluationGateService

        payload = CapabilityReleaseService.approval_view(release)
        # 门禁快照摘要另给一份：审批面板要展示逐项 checks 与阈值，而 approval_view
        # 只带结论性的 gate_report，不足以渲染"哪一项没过"。
        payload["gate"] = EvaluationGateService.snapshot_summary(release) or {}
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="submit-approval")
    def submit_approval(self, request, pk=None):
        """把通过硬门禁的候选提交测试负责人审批（R12：留痕）。

        权限刻意只要"项目成员"而非"负责人"：提交审批属于验评环节，
        按设计第 10 节"上传和验评允许测试执行人员"，执行人员发现问题修好后
        应当能自己发起复核，而不是事事等负责人代劳。
        """
        release = self.get_object()
        _ensure_project_member(request.user, release.project_id)
        from .capabilities import CapabilityReleaseService

        release = _run_release_action(
            CapabilityReleaseService.submit_for_approval, release,
            actor=request.user, reason=request.data.get("reason", ""),
            kind=request.data.get("kind", "full"),
        )
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """负责人驳回候选。原因必填——驳回不留原因，执行人员无从知道要改什么。"""
        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        from .capabilities import CapabilityReleaseService

        release = _run_release_action(
            CapabilityReleaseService.reject, release,
            actor=request.user, reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["post"])
    def quarantine(self, request, pk=None):
        """紧急隔离：立即阻断运行时加载（安全事件，原因必填，仅负责人可发起）。"""
        release = self.get_object()
        _ensure_test_lead(request.user, release.project_id)
        from .capabilities import CapabilityReleaseService

        release = _run_release_action(
            CapabilityReleaseService.quarantine, release,
            actor=request.user, reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["get"])
    def bindings(self, request, pk=None):
        """能力绑定（R10-5）：该发布单元被哪些单次能力或四阶段流程使用。

        两种绑定关系分开标注，不能混为一谈：

        - ``active_release``：某个能力定义的**当前活跃发布**就是这一版——强绑定；
        - ``stage``：能力定义的阶段集合包含本发布单元的阶段，但活跃版本是别人
          ——候选绑定。控制台据此提示"激活后会影响这 N 个能力"。
        """
        release = self.get_object()
        _ensure_project_member(request.user, release.project_id)

        from .capability_registry import STAGE_LABELS, WORKFLOW_STAGES

        stage = _release_stage(release)
        definitions = CapabilityDefinition.objects.filter(project_id=release.project_id)

        bindings = []
        for definition in definitions:
            stages = list(definition.stages or [])
            if str(definition.active_release_id or "") == str(release.id):
                binding = "active_release"
            elif stage and stage in stages:
                binding = "stage"
            else:
                continue
            position = stages.index(stage) + 1 if (stage and stage in stages) else None
            bindings.append({
                "id": str(definition.id),
                "name": definition.name,
                "kind": definition.kind,
                "evaluation_mode": definition.evaluation_mode,
                "is_active": definition.is_active,
                "stages": stages,
                "stage_labels": [STAGE_LABELS.get(item, item) for item in stages],
                "position": position,
                "binding": binding,
            })

        # 强绑定排在前面：控制台首屏要看"谁正在用这一版"。
        bindings.sort(key=lambda item: (item["binding"] != "active_release", item["name"]))
        return Response({
            "release_id": str(release.id),
            "kind": release.kind,
            "name": release.name,
            "version": release.version,
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "workflow_stages": list(WORKFLOW_STAGES),
            "definitions": bindings,
        })


def _release_stage(release) -> str:
    """解析发布单元对应的业务阶段。

    Skill 型发布单元的阶段来自版本包 manifest 的 ``stage``；非 Skill 型（复合能力、
    Prompt 等）没有包 manifest，退化为从 ``config`` 里读，读不到就返回空串——
    空串表示"阶段未知"，此时只做 ``active_release`` 强绑定匹配，不做阶段匹配，
    避免把一个阶段未知的发布单元错绑到所有能力定义上。
    """
    if release.kind == "skill":
        from skills.models import SkillVersion

        version = SkillVersion.objects.filter(release=release).first()
        if version is not None:
            stage = version.manifest_stage()
            if stage:
                return stage
    return str((release.config or {}).get("stage", "") or "")


def _run_release_action(service_method, release, **kwargs):
    """调用发布服务并把 ``django.core.exceptions.ValidationError`` 转成 DRF 400。

    服务层刻意抛 Django 版 ValidationError（不依赖 DRF），视图层负责收口；
    这里集中一处转换，免得每个 action 各写一遍 try/except 而漏掉一两个。
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import ValidationError as DRFValidationError

    try:
        return service_method(release, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class CapabilityDefinitionViewSet(viewsets.ModelViewSet):
    serializer_class = CapabilityDefinitionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "kind", "evaluation_mode", "is_active"]
    search_fields = ["name", "description"]

    def get_queryset(self):
        queryset = CapabilityDefinition.objects.select_related(
            "project", "default_suite", "active_release", "created_by"
        )
        return queryset if self.request.user.is_superuser else queryset.filter(project_id__in=_project_ids(self.request.user))

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=project.id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下创建能力定义")
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="activate-release")
    def activate_release(self, request, pk=None):
        definition = self.get_object()
        _ensure_test_lead(request.user, definition.project_id)
        release_id = request.data.get("release_id")
        if not release_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"release_id": "必须指定 release_id"})
        release = get_object_or_404(CapabilityRelease, pk=release_id)
        if release.project_id != definition.project_id or release.kind != definition.kind:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"release_id": "发布单元与能力定义不匹配"})
        definition.active_release = release
        definition.save(update_fields=["active_release", "updated_at"])
        return Response(CapabilityDefinitionSerializer(definition, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="run-evolution")
    def run_evolution(self, request, pk=None):
        """触发一次能力自进化运行：从历史产出+反馈生成评测运行，并可生成候选发布单元。"""
        definition = self.get_object()
        if not request.user.is_superuser and not ProjectMember.objects.filter(
            user=request.user, project_id=definition.project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下执行能力自进化")

        from .evolution import CapabilityEvolutionService
        baseline_release_id = request.data.get("baseline_release_id")
        baseline_release = None
        if baseline_release_id:
            baseline_release = get_object_or_404(CapabilityRelease, pk=baseline_release_id)

        report = CapabilityEvolutionService(definition).run_evolution(
            name=request.data.get("name", ""),
            triggered_by=request.user,
            baseline_release=baseline_release,
            candidate_config=request.data.get("candidate_config"),
            version=request.data.get("version", ""),
        )
        return Response({
            "definition_id": str(definition.id),
            "run_id": str(report.run.id),
            "suite_id": str(report.suite.id),
            "case_count": report.case_count,
            "created_release_id": str(report.created_release.id) if report.created_release else None,
            "gate_passed": report.gate_passed,
        })


class CaseReviewEvolutionViewSet(viewsets.ViewSet):
    """用例审查的自进化入口（T23）。

    三个动作对应向导的三步：列出可进化的审查项目 → 上传已确认报告做预检 →
    发起派生并拿到候选版本。派生内核在 ``case_review_evolution``，这里只做
    权限、参数与错误码的转译。
    """

    permission_classes = [IsAuthenticated]

    def _project(self, request):
        from projects.models import Project

        project_id = request.query_params.get("project") or request.data.get("project")
        if not project_id:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            raise DRFValidationError({"project": "必须指定 project"})
        project = get_object_or_404(Project, pk=project_id)
        _ensure_project_member(request.user, project.id)
        return project

    @staticmethod
    def _threshold(request) -> float:
        raw = request.data.get("threshold")
        if raw in (None, ""):
            from .case_review_evolution import DEFAULT_HUMAN_SCORE_THRESHOLD
            return float(DEFAULT_HUMAN_SCORE_THRESHOLD)
        return float(raw)

    @action(detail=False, methods=["get"], url_path="candidates")
    def candidates(self, request):
        """列出该项目下已跑完、可用于自进化的用例审查项目。"""
        from .case_review_evolution import CaseReviewEvolutionService

        project = self._project(request)
        return Response({
            "threshold": self._threshold(request),
            "items": CaseReviewEvolutionService.list_candidates(project=project),
        })

    @action(detail=False, methods=["post"], url_path="preflight")
    def preflight(self, request):
        """解析上传的报告并给出全部前置判定——**不落库**。

        预检存在的意义是让"不能进化"在点下按钮之前就可见。把判定放到
        ``evolve`` 里一次说一条，用户要来回试三次才知道真正卡在哪。

        ⚠️ 采纳率**不在 blockers 里**：它已降级为版本间对比的评分维度，
        低于参考线只回 ``below_reference`` 供页面标黄。
        真正的硬阻断只有一条 —— 报告里没有任何人工确认的缺陷，
        此时派生无从下手（不是"质量不够"，而是"没有可改的东西"）。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .case_review_evolution import CaseReviewEvolutionService

        project = self._project(request)
        review = self._review(request, project)
        upload = request.FILES.get("file")
        if upload is None:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            raise DRFValidationError({"file": "必须上传已确认的审查报告"})

        threshold = self._threshold(request)

        try:
            scan = CaseReviewEvolutionService.scan(data=upload.read())
        except DjangoValidationError as exc:
            return Response({"detail": _validation_text(exc)}, status=400)
        score = scan.acceptance_score

        described = CaseReviewEvolutionService._describe(review)
        blockers = list(described["blockers"])
        if not scan.defects:
            blockers.append(
                "这份报告里没有人工确认的缺陷（误报，或方向对但被改写的说明），没有可修复的改进点"
            )

        return Response({
            "review": described,
            "threshold": threshold,
            "human_score": score,
            # 参考线只用于展示与标黄：低于它仍然可以继续进化。
            "below_reference": score < threshold,
            "scan": scan.as_dict(),
            "attribution_preview": [
                {"category": item.category, "issue_type": item.issue_type, "count": item.count}
                for item in scan.defects
            ],
            "blockers": blockers,
            "ready": not blockers,
        })

    @action(detail=False, methods=["post"], url_path="evolve")
    def evolve(self, request):
        """从这份已确认报告派生 Skill 候选版本。

        可带 ``attribution_ids``（重复字段或 JSON 数组）：给了就只拿这些**已确认**
        的候选优化点当依据（三步向导的主路径）；不给则降级为"直接采信人工在报告里
        写下的结论"。两条路径都要能用 —— 未配置 LLM 的环境只有后者。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .case_review_evolution import CaseReviewEvolutionService

        project = self._project(request)
        review = self._review(request, project)
        upload = request.FILES.get("file")
        if upload is None:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            raise DRFValidationError({"file": "必须上传已确认的审查报告"})
        try:
            result = CaseReviewEvolutionService.evolve(
                review=review,
                data=upload.read(),
                actor=request.user,
                threshold=self._threshold(request),
                change_reason=request.data.get("change_reason", ""),
                report_name=getattr(upload, "name", "") or "",
                attribution_ids=self._attribution_ids(request),
            )
        except DjangoValidationError as exc:
            # 可预期的业务拒绝（无缺陷、基线不是活跃版本、所选优化点未确认……）
            # 一律 400：回 500 会让前端把它当服务端故障弹"系统错误"，把该看的提示吃掉。
            return Response({"detail": _validation_text(exc)}, status=400)

        return Response(result, status=201)

    @staticmethod
    def _attribution_ids(request) -> list:
        """从请求里取 ``attribution_ids``。multipart / JSON / 重复字段三种形态都认。

        三种都认不是宽容：前端在"确认候选"那一步拿到的是一组 id，
        用 ``getlist`` 追加还是拼一个 JSON 数组取决于它怎么封装 FormData；
        只认一种就会出现"确认了 3 条、派生却说没给依据"。
        """
        import json

        raw = request.data.get("attribution_ids")
        if raw in (None, ""):
            values = request.data.getlist("attribution_ids") if hasattr(request.data, "getlist") else []
        elif isinstance(raw, str):
            text = raw.strip()
            if text.startswith("["):
                try:
                    values = [str(item) for item in json.loads(text)]
                except ValueError:
                    values = [text]
            else:
                values = [text]
        elif isinstance(raw, (list, tuple)):
            values = [str(item) for item in raw]
        else:
            values = [str(raw)]
        seen = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.append(text)
        return seen

    @action(detail=False, methods=["post"], url_path="feedback")
    def feedback(self, request):
        """上传已确认报告，以最后一个 Sheet 的采纳率记录质量反馈。"""
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from .case_review_evolution import CaseReviewEvolutionService

        project = self._project(request)
        review = self._review(request, project)
        upload = request.FILES.get("file")
        if upload is None:
            raise DRFValidationError({"file": "必须上传已确认的审查报告"})
        try:
            result = CaseReviewEvolutionService.record_report_feedback(
                review=review,
                data=upload.read(),
                actor=request.user,
                report_name=getattr(upload, "name", "") or "",
                threshold=self._threshold(request),
            )
        except DjangoValidationError as exc:
            return Response({"detail": _validation_text(exc)}, status=400)
        return Response(result, status=201)

    @action(detail=False, methods=["post"], url_path="attributions")
    def attributions(self, request):
        """第 ③ 步：AI 读四要素，提出**候选**优化点。**只提候选，不派生。**

        为什么与 ``evolve`` 分开：提候选是"读一份报告、给一堆看法"，
        派生是"改包"。合并成一个动作，用户就没法"先看看 AI 想改什么、
        再决定改不改"—— 而这正是这次要做的事。

        **无 LLM 时返回可识别的降级信号（HTTP 200，``degraded=true``）而不是报错**：
        未配置模型不是"服务坏了"，而是一条合法的降级路径 ——
        人工在报告里写下的结论仍然可以直接作为派生依据（走 ``evolve`` 不带
        ``attribution_ids`` 的那条路）。回 500 会让这条路径在页面上变成"系统错误"，
        用户只会反复重试而看不到真正该做的事。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .case_review_evolution import CaseReviewEvolutionService
        from .report_optimization import LLMUnavailable

        project = self._project(request)
        review = self._review(request, project)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "必须上传已确认的审查报告"})

        try:
            result = CaseReviewEvolutionService.propose_optimizations(
                review=review, data=upload.read(), actor=request.user,
            )
        except LLMUnavailable as exc:
            return Response({
                "degraded": True,
                "reason_code": "llm_unavailable",
                "detail": str(exc),
                "candidates": [],
                "review_id": str(review.pk),
            })
        except DjangoValidationError as exc:
            return Response({"degraded": False, "detail": _validation_text(exc)}, status=400)

        return Response({"degraded": False, **result})

    @action(detail=False, methods=["post"], url_path="attributions/confirm")
    def attributions_confirm(self, request):
        """第 ③ 步的后半：人工逐条「采纳 / 改写 / 驳回」。

        入参 ``decisions``：``[{"attribution_id": "...", "action": "accept|edit|reject",
        "hypothesis": "...", "category": "..."}]``。

        为什么用 ``attribution_id`` 而不是候选列表里的下标 ``idx``：
        ``idx`` 只在**某一次** ``attributions`` 响应里有意义。用户中途刷新页面、
        或重新生成一批候选之后，同一个 ``idx`` 指向的是另一条结论 ——
        那会让"我明明驳回了这条"变成驳回另一条，且不留任何痕迹。
        id 是稳定的，下标不是。

        采纳/改写一律落 ``confirmed``，且**不抬高置信度**：AI 假设在确认后写成
        ``confidence=1.0`` 会让人事后分不清"模型猜对了"与"人确认过"。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .case_review_evolution import CaseReviewEvolutionService

        project = self._project(request)
        review = self._review(request, project)
        decisions = request.data.get("decisions")
        if isinstance(decisions, str):  # multipart 里会是字符串
            import json

            try:
                decisions = json.loads(decisions)
            except ValueError as exc:
                raise ValidationError({"decisions": f"decisions 不是合法 JSON：{exc}"})
        if not isinstance(decisions, list) or not decisions:
            raise ValidationError({"decisions": "必须给出至少一条确认决定"})

        results = []
        for index, item in enumerate(decisions):
            if not isinstance(item, dict):
                raise ValidationError({"decisions": f"第 {index + 1} 条不是对象"})
            attribution_id = str(item.get("attribution_id") or "").strip()
            if not attribution_id:
                raise ValidationError({
                    "decisions": f"第 {index + 1} 条缺少 attribution_id；请带上候选优化点的 id"
                })
            try:
                results.append(CaseReviewEvolutionService.decide_optimization(
                    review=review, attribution_id=attribution_id,
                    action=str(item.get("action") or ""), actor=request.user,
                    hypothesis=str(item.get("hypothesis") or ""),
                    category=str(item.get("category") or ""),
                ))
            except DjangoValidationError as exc:
                # 逐条返回而不是整批失败：一次提交里有一条写错（比如 id 已被重新生成
                # 替换），不该把其余 9 条人已经做完的确认全部丢掉。
                results.append({
                    "attribution_id": attribution_id,
                    "error": _validation_text(exc),
                })

        confirmed = [item for item in results if item.get("state") == "confirmed"]
        return Response({
            "review_id": str(review.pk),
            "results": results,
            "confirmed_count": len(confirmed),
            "confirmed_ids": [item["attribution_id"] for item in confirmed],
        })

    def _review(self, request, project):
        from testcases.models import TestCaseReview

        review_id = request.data.get("review_id") or request.query_params.get("review_id")
        if not review_id:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            raise DRFValidationError({"review_id": "必须指定用例审查项目"})
        return get_object_or_404(TestCaseReview, pk=review_id, project=project)


def _validation_text(exc) -> str:
    """把 Django ``ValidationError`` 拍成一句话。

    它的 ``messages`` 可能是嵌套结构（字典字段错误），直接 str() 会带出
    引号与方括号，前端原样显示很难看。
    """
    import json

    messages = getattr(exc, "messages", None)
    if not messages:
        return str(exc)
    parts = []
    for item in messages:
        if isinstance(item, (list, tuple, dict)):
            try:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
            except TypeError:  # pragma: no cover - 兜底
                parts.append(str(item))
        else:
            parts.append(str(item))
    return "；".join(parts)


def _validation_payload(exc) -> dict | str:
    """把字段级校验失败翻成前端能直接定位的结构。

    传 dict 给 ``ValidationError`` 时 Django 会把字段错误收在 ``message_dict`` 里，
    ``messages`` 则会把它展平成带引号的字符串——前端只能整句显示，用户看不出
    是"哪个字段"错了。这里保留字段名，同时给一句可直接展示的话。

    没有字段结构（单句错误）时退回 ``_validation_text``，不为了统一形状而
    编出一个假的 ``fields``：那会让前端以为"有字段级错误"，然后渲染出一片空白。
    """
    message_dict = getattr(exc, "message_dict", None)
    if not message_dict:
        return _validation_text(exc)

    fields = {
        str(field): [str(item) for item in (value if isinstance(value, list) else [value])]
        for field, value in message_dict.items()
    }
    flat = "；".join(
        f"{field}: {message}"
        for field, messages in fields.items()
        for message in messages
    )
    return {
        "message": flat,
        "fields": fields,
        "count": sum(len(messages) for messages in fields.values()),
    }


class FlywheelOperationsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _check(self, request, project_id):
        if not request.user.is_superuser and not ProjectMember.objects.filter(user=request.user, project_id=project_id).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权访问该项目飞轮数据")

    @action(detail=False, methods=["get"], url_path="metrics")
    def metrics(self, request):
        from .operations import FlywheelMetricsService
        project_id = int(request.query_params["project"]); self._check(request, project_id)
        return Response(FlywheelMetricsService().summarize(project_id))

    @action(detail=False, methods=["get"], url_path="health")
    def health(self, request):
        from .operations import KnowledgeHealthService
        project_id = int(request.query_params["project"]); self._check(request, project_id)
        return Response(KnowledgeHealthService().inspect(project_id))

    @action(detail=False, methods=["get"], url_path="candidate-alerts")
    def candidate_alerts(self, request):
        """候选队列与死信告警：控制台据此显示"有事件没处理成功"（T04 / R9）。

        独立成接口而不是塞进 ``metrics``：死信是需要人工介入的运维信号，
        与质量指标的生命周期不同——混在一起会让"指标页很健康"掩盖住积压的死信。
        """
        from .gold import AssetCandidateService
        project_id = int(request.query_params["project"]); self._check(request, project_id)
        return Response(AssetCandidateService.status_summary(project_id))

    @action(detail=False, methods=["get"], url_path="registration-failures")
    def registration_failures(self, request):
        """飞轮登记失败的补偿队列（T14 / §13）。

        与 ``candidate-alerts`` 并列而不是合并：候选事件的失败意味着
        "金标没沉淀"，登记失败意味着"这一版产出没进门禁"。两者的处置人、
        处置动作和严重度都不同，合成一个数字只会让两边都看不清。
        """
        from rest_framework.exceptions import ValidationError

        from .registration import FlywheelRegistrationService

        raw = request.query_params.get("project")
        if not raw:
            raise ValidationError({"project": "必须指定项目"})
        project_id = int(raw)
        self._check(request, project_id)
        summary = FlywheelRegistrationService.status_summary(project_id)
        if str(request.query_params.get("detail") or "") in {"1", "true", "yes"}:
            summary["items"] = [
                {
                    "id": str(record.pk),
                    "output_id": str(record.output_id),
                    "workflow_id": record.workflow_id,
                    "stage": record.stage,
                    "status": record.status,
                    "attempts": record.attempts,
                    "max_attempts": record.max_attempts,
                    "last_error": record.last_error,
                    "created_at": record.created_at,
                }
                for record in FlywheelRegistrationService.open_for_project(project_id)
            ]
        return Response(summary)

    @action(detail=False, methods=["post"], url_path="registration-failures-retry")
    def registration_failures_retry(self, request):
        """批量重试未决的登记失败（补偿入口）。"""
        from rest_framework.exceptions import ValidationError

        from .registration import FlywheelRegistrationService

        raw = request.data.get("project")
        if not raw:
            raise ValidationError({"project": "必须指定项目"})
        project_id = int(raw)
        _ensure_test_lead(request.user, project_id)
        results = FlywheelRegistrationService.retry_failed(
            project_id=project_id, actor=request.user,
        )
        return Response({
            "project_id": project_id, "retried": len(results), "results": results,
            "summary": FlywheelRegistrationService.status_summary(project_id),
        })

    @action(detail=False, methods=["get"], url_path="launch-readiness")
    def launch_readiness(self, request):
        """上线就绪自检（T14）：把检查表里**可自动判定**的条件跑一遍。

        只读且成员可见：上线前要能反复跑，跑之前不该先申请一次权限。
        """
        from rest_framework.exceptions import ValidationError

        from projects.models import Project
        from .rollout import LaunchReadinessService

        raw = request.query_params.get("project")
        if not raw:
            raise ValidationError({"project": "必须指定项目"})
        project_id = int(raw)
        self._check(request, project_id)
        return Response(LaunchReadinessService.check(get_object_or_404(Project, pk=project_id)))

    @action(detail=False, methods=["get"], url_path="cockpit")
    def cockpit(self, request):
        from .operations import ProjectQualityCockpitService
        project_id = int(request.query_params["project"]); self._check(request, project_id)
        return Response(ProjectQualityCockpitService().summarize(project_id))

    @action(detail=False, methods=["get"], url_path="workflow-stage-catalog")
    def workflow_stage_catalog(self, request):
        """发起流程向导第一步的数据源：按阶段列出可选的 Skill 包（只读）。

        放在独立接口而不是让前端拼 ``/skills/``：向导要的是"这个阶段有哪些可选项、
        默认是哪个、选它能不能锁上版本"，这些结论依赖 manifest 声明与发布状态，
        由前端自己算会变成第二套判断——而它一旦和后端不一致，
        用户就会遇到"页面明明能选、发起却 400"。
        """
        from .operations import WorkflowGateService
        from projects.models import Project

        project_id = int(request.query_params["project"]); self._check(request, project_id)
        project = get_object_or_404(Project, pk=project_id)
        return Response(WorkflowGateService.stage_catalog(project=project))

    @action(detail=False, methods=["post"], url_path="start-workflow")
    def start_workflow(self, request):
        """启动四阶段流水线：按阶段锁定 Skill 版本（T15）。

        只允许测试负责人启动：启动动作决定了整条链路用哪几个能力包，
        属于发布决策而非日常执行。

        请求可带 ``pins``：``{"阶段": "SkillVersion 的 UUID"}``——向导里人逐阶段
        选定公开 Skill Hub 中的具体版本。
        不传则沿用"按 manifest 声明解析活跃版本"的旧行为，两种入口都要能用。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .operations import WorkflowGateService
        from projects.models import Project

        project_id = int(request.data["project"]); self._check(request, project_id)
        _ensure_test_lead(request.user, project_id)
        _ensure_linkage_enabled(project_id)
        project = get_object_or_404(Project, pk=project_id)
        raw_pins = request.data.get("pins")
        if raw_pins in (None, ""):
            pins = {}
        elif isinstance(raw_pins, dict):
            pins = {str(stage): str(value) for stage, value in raw_pins.items() if value}
        else:
            raise ValidationError("pins 必须是「阶段 -> Skill ID」的对象")
        try:
            result = WorkflowGateService.start_workflow(
                project=project, workflow_id=str(request.data.get("workflow_id") or ""),
                actor=request.user, pins=pins,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(result, status=201)

    @action(detail=False, methods=["get"], url_path="workflow-status")
    def workflow_status(self, request):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .operations import WorkflowGateService
        from projects.models import Project

        project_id = int(request.query_params["project"]); self._check(request, project_id)
        project = get_object_or_404(Project, pk=project_id)
        try:
            payload = WorkflowGateService.workflow_status(
                project=project,
                workflow_id=str(request.query_params.get("workflow_id") or ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(payload)

    @action(detail=False, methods=["post"], url_path="evaluate-workflow-stage")
    def evaluate_workflow_stage(self, request):
        from .operations import WorkflowGateService
        from .workflow_models import GATE_CONFIRMABLE_STATES, GATE_PASSING_STATES, WorkflowStageGate
        project_id = int(request.data["project"]); self._check(request, project_id)
        gate = get_object_or_404(
            WorkflowStageGate, project_id=project_id,
            workflow_id=str(request.data["workflow_id"]), stage=str(request.data["stage"]),
        )
        gate = WorkflowGateService.evaluate(gate, actor=request.user)
        return Response({
            "id": str(gate.id), "status": gate.status, "scores": gate.scores,
            "reason": gate.reason, "decided_by": request.user.username,
            # 评测后状态可能正好落到 unscored（无信号）——此时页面必须立刻出现
            # 「人工确认」按钮，所以把准入结论一并回给前端，不让它自己猜。
            "confirmable": gate.status in GATE_CONFIRMABLE_STATES,
            "passed": gate.status in GATE_PASSING_STATES,
        })

    @action(detail=False, methods=["post"], url_path="override-workflow-stage")
    def override_workflow_stage(self, request):
        from .operations import WorkflowGateService
        from .workflow_models import WorkflowStageGate
        project_id = int(request.data["project"]); self._check(request, project_id)
        _ensure_test_lead(request.user, project_id)
        gate = get_object_or_404(
            WorkflowStageGate, project_id=project_id,
            workflow_id=str(request.data["workflow_id"]), stage=str(request.data["stage"]),
        )
        gate = WorkflowGateService.override(gate, request.user, str(request.data.get("reason") or ""))
        return Response({
            "id": str(gate.id), "status": gate.status, "scores": gate.scores,
            "reason": gate.reason, "decided_by": request.user.username,
            "confirmable": False, "passed": True,
        })

    @action(detail=False, methods=["post"], url_path="confirm-workflow-stage")
    def confirm_workflow_stage(self, request):
        """人工确认放行：**不要求先有评分**，确认后该阶段即可进入下一阶段。

        权限刻意与 ``evaluate-workflow-stage`` 同一档（项目成员），而**不是**
        ``override-workflow-stage`` 的测试负责人档：

        - ``confirm`` 处理的是"**尚无结论**"（待测评 / 无评分）。链路卡在
          "评测还没接通"上，不该要求执行人员先去找负责人签字——那会把
          "评分挂起"变成"链路停摆"，正是本次要解掉的问题。
        - ``override`` 处理的是"**已有负面结论**"（评测失败）后的人为推翻，
          那是发布决策，仍然只允许测试负责人，且必须填原因。

        留痕不因此打折：``decided_by`` / ``decided_at`` / ``reason`` 照写，
        事后能分清"有人确认过"与"没人管"。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .operations import WorkflowGateService
        from .workflow_models import WorkflowStageGate

        project_id = int(request.data["project"]); self._check(request, project_id)
        gate = get_object_or_404(
            WorkflowStageGate, project_id=project_id,
            workflow_id=str(request.data["workflow_id"]), stage=str(request.data["stage"]),
        )
        try:
            gate = WorkflowGateService.confirm(
                gate, request.user, str(request.data.get("reason") or "")
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response({
            "id": str(gate.id), "status": gate.status, "scores": gate.scores,
            "reason": gate.reason, "decided_by": request.user.username,
            "confirmable": False, "passed": True,
        })

    @action(detail=False, methods=["post"], url_path="score-workflow-stage")
    def score_workflow_stage(self, request):
        """人工评分（百分制）。

        与 ``confirm``（无评分放行）并存而不是二选一：确认回答的是"能不能先过"，
        评分回答的是"这一阶段到底做得怎么样"。前者让链路不被卡住，后者让质量
        有据可查——两者都要，所以两个动作都留。

        权限与 ``evaluate-workflow-stage`` 同一档（项目成员）：评分是对本阶段产出的
        判断，属于执行/评审职责；**推翻负面结论**（``override``）才是发布决策，
        那一档留给测试负责人。
        """
        from .operations import WorkflowGateService
        from .workflow_models import WorkflowStageGate

        project_id = int(request.data["project"]); self._check(request, project_id)
        gate = get_object_or_404(
            WorkflowStageGate, project_id=project_id,
            workflow_id=str(request.data["workflow_id"]), stage=str(request.data["stage"]),
        )
        gate = WorkflowGateService.score(
            gate, request.user, request.data.get("score"),
            str(request.data.get("reason") or ""),
        )
        return Response({
            "id": str(gate.id), "status": gate.status, "scores": gate.scores,
            "threshold": gate.threshold, "reason": gate.reason,
            "decided_by": request.user.username,
            "manual_score": (gate.detail or {}).get("manual_score"),
        })

    @action(detail=False, methods=["post"], url_path="execute-workflow-stage")
    def execute_workflow_stage(self, request):
        """「执行本阶段」：前置门禁校验 + 执行参数下发。

        刻意**不**在服务端把阶段跑起来：平台只有 ``test_execution`` 有真实执行器
        （且必须先由人选好用例套件），方案 / 用例 / 报告三个阶段的产出由 agent 经
        ``/orchestrator/agent-loop/`` 提交。造一个"点了就在后台跑"的假入口，
        会让人以为跑起来了而实际什么都没发生。取舍详见
        ``WorkflowGateService.plan_execution``。
        """
        from .operations import WorkflowGateService
        from projects.models import Project

        project_id = int(request.data["project"]); self._check(request, project_id)
        project = get_object_or_404(Project, pk=project_id)
        plan = WorkflowGateService.plan_execution(
            project=project,
            workflow_id=str(request.data.get("workflow_id") or ""),
            stage=str(request.data.get("stage") or ""),
            actor=request.user,
        )
        return Response(plan, status=201)

    @action(detail=False, methods=["get"], url_path="workflow-stage-output")
    def workflow_stage_output(self, request):
        """「查看结果」：读取某一阶段产出的正文与溯源信息（只读）。

        定位走 ``WorkflowGateService.locate_stage_output``（project + workflow_id
        + stage 三元）。**不按产出 id 取**，否则任何项目成员都能靠猜 id 读到别的
        项目的产出正文，权限口径就变成"取决于 id 是否被猜中"。
        """
        from rest_framework.exceptions import ValidationError

        from .operations import ALL_WORKFLOW_STAGE_SET, WorkflowGateService
        from .workflow_feedback import WorkflowStageFeedbackService

        project_id = int(request.query_params["project"]); self._check(request, project_id)
        workflow_id = str(request.query_params.get("workflow_id") or "")
        stage = str(request.query_params.get("stage") or "")
        # 用**全集**而不是新链路默认序列：存量流程还停在方案/报告阶段，
        # 只认新序列会让它们的产出在页面上永远"查不到"。
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")

        output, gate = WorkflowGateService.locate_stage_output(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        )
        if output is None:
            raise ValidationError(
                f"{WorkflowGateService.STAGE_LABELS.get(stage, stage)}阶段暂无产出可查看；"
                f"请先执行本阶段"
            )

        content = output.content or ""
        # 正文可能很长，接口只回前 4000 字 + 真实长度：页面据此提示"已截断"，
        # 而不是让人以为报告只有这么长。
        excerpt = content[:4000]
        return Response({
            "stage": stage,
            "stage_label": WorkflowGateService.STAGE_LABELS.get(stage, stage),
            "workflow_id": workflow_id,
            "output_id": str(output.pk),
            "task_id": output.task_id,
            "task_type": output.task_type,
            "created_at": output.created_at.isoformat() if output.created_at else "",
            "content": excerpt,
            "content_length": len(content),
            "truncated": len(content) > len(excerpt),
            "skill_name": (
                output.skill_version.skill.name
                if output.skill_version_id and output.skill_version.skill_id else ""
            ),
            "skill_version": output.skill_version.version if output.skill_version_id else "",
            "package_sha256": output.skill_package_sha256 or "",
            # 能不能下载报告由后端说了算：文件是否存在、是登记产物还是回落模板，
            # 前端无从判断。给结论而不是让它自己猜，才不会出现"有按钮点了 404"。
            "artifact": WorkflowGateService.stage_artifact_payload(output, stage=stage),
            # 采纳率按版本横向列出：采纳率是"人对这一版的评价"，只有放到版本序列里
            # 才读得出"skill 是一点点优化出来的"。
            "acceptance_history": (
                WorkflowStageFeedbackService.acceptance_history(output.skill_version.skill)
                if output.skill_version_id and output.skill_version.skill_id else []
            ),
            "gate": ({
                "status": gate.status,
                "scores": gate.scores,
                "threshold": gate.threshold,
                "reason": gate.reason,
                "decided_by": (
                    gate.decided_by.username if gate.decided_by_id else ""
                ),
                "decided_at": gate.decided_at.isoformat() if gate.decided_at else "",
                "manual_score": (gate.detail or {}).get("manual_score"),
                "report_contract": (gate.detail or {}).get("report_contract"),
                "evaluation": (gate.detail or {}).get("evaluation"),
            } if gate is not None else None),
        })

    @action(detail=False, methods=["get"], url_path="workflow-stage-artifact")
    def workflow_stage_artifact(self, request):
        """「下载报告」：下发某一阶段已产出的报告文件。

        与「查看结果」是**两个不同的动作**，不合并：
        查看结果回正文摘要（看内容），下载报告回附件（拿走文件）。
        合并成一个接口要么让"只想看一眼"被迫下载文件，要么让"要文件"拿到
        一段被截断的文本 —— 而截断的报告拿去做人工确认，是最坏的一种错。

        产物优先级：**登记产物优先，模板回落兜底**。
        - 登记产物 = Skill 产出时写进 ``metadata.artifacts`` 的真实文件，
          保留该阶段的专业结构（多 Sheet、图表等）。
        - 无登记产物（或文件已不在盘上）→ 按正文渲染统一 markdown。
          回落是刻意的：没有它，"下不到报告"就变成链路断点；
          有它，最差也能拿到一份可读的文本。
        """
        from urllib.parse import quote

        from django.http import HttpResponse
        from rest_framework.exceptions import ValidationError

        from .operations import ALL_WORKFLOW_STAGE_SET, WorkflowGateService

        project_id = int(request.query_params["project"]); self._check(request, project_id)
        workflow_id = str(request.query_params.get("workflow_id") or "")
        stage = str(request.query_params.get("stage") or "")
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")

        output, _gate = WorkflowGateService.locate_stage_output(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        )
        if output is None:
            # 明确"无产出"，而不是回一个 0 字节附件：空文件会被当成"报告是空的"，
            # 而真相是"这一阶段还没跑"。
            raise ValidationError(
                f"{WorkflowGateService.STAGE_LABELS.get(stage, stage)}阶段暂无产出可下载；"
                f"请先执行本阶段"
            )

        artifact = WorkflowGateService.stage_artifact(output)
        if artifact is not None:
            try:
                with open(artifact["path"], "rb") as handle:
                    body = handle.read()
            except OSError as exc:
                # 登记了但读不到（权限/竞态）→ 回落而不是 500："给不出这个文件"
                # 是这条链路能自己处理的情况，不该让整个下载动作失败。
                artifact = None
            else:
                filename = artifact["name"]
                content_type = "application/octet-stream"

        if artifact is None:
            filename, body = WorkflowGateService.render_stage_report(output=output, stage=stage)
            content_type = "text/markdown; charset=utf-8"

        response = HttpResponse(body, content_type=content_type)
        # 中文文件名必须带 ``filename*=UTF-8''``：只给 ``filename=`` 时
        # 浏览器会按 latin-1 解，中文名变成乱码文件。ascii 兜底名保证老客户端可用。
        response["Content-Disposition"] = (
            f'attachment; filename="stage-report{WorkflowGateService.FALLBACK_SUFFIX}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
        # 让前端能核验"下的就是给的那个文件"：登记的 sha256 有值时才带。
        if artifact is not None and artifact.get("sha256"):
            response["X-Artifact-Sha256"] = artifact["sha256"]
        response["X-Artifact-Source"] = (
            "registered" if artifact is not None else "fallback"
        )
        return response

    @action(detail=False, methods=["post"], url_path="workflow-stage-feedback")
    def workflow_stage_feedback(self, request):
        """「上传反馈」：上传该阶段已人工确认的报告，记录采纳率。**不派生。**

        multipart：``project`` / ``workflow_id`` / ``stage`` / ``file``。

        与派生的关系：反馈只写 ``FeedbackEvent``，一个字节都不动 Skill 包。
        想让这一版变好，走「用 AI 提候选优化点 → 人工确认 → 派生」那条路 ——
        那是一条会被明确点下确认的动作，不该被"记录一次评审"顺带触发。

        采纳率的角色：**只作为版本间对比的评分维度**，不是上传门槛。
        低于参考线照常入库，响应里标 ``below_reference`` 供页面标黄。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .operations import ALL_WORKFLOW_STAGE_SET, WorkflowGateService
        from .workflow_feedback import WorkflowStageFeedbackService

        project_id = int(request.data["project"]); self._check(request, project_id)
        workflow_id = str(request.data.get("workflow_id") or "")
        stage = str(request.data.get("stage") or "")
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")

        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "必须上传已完成人工确认的阶段报告"})

        output, _gate = WorkflowGateService.locate_stage_output(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        )
        if output is None:
            # 反馈要挂在产出上（版本溯源靠它）。没有产出就没有"这一版"，
            # 记下来的分数无处可归 —— 说清楚，而不是落一条无主反馈。
            raise ValidationError(
                f"{WorkflowGateService.STAGE_LABELS.get(stage, stage)}阶段暂无产出，"
                f"无法关联质量反馈；请先执行本阶段"
            )

        reference = request.data.get("reference")
        try:
            result = WorkflowStageFeedbackService.record(
                output=output, stage=stage, data=upload.read(), actor=request.user,
                report_name=getattr(upload, "name", "") or "",
                reference=reference if reference not in (None, "") else None,
            )
        except DjangoValidationError as exc:
            # 格式不合约（缺「采纳率」标签、不是 Excel）一律 400：
            # 回 500 会让前端弹"系统错误"，把该看的那句提示吃掉。
            return Response({"detail": _validation_text(exc)}, status=400)

        return Response({
            "stage": stage,
            "stage_label": WorkflowGateService.STAGE_LABELS.get(stage, stage),
            "workflow_id": workflow_id,
            **result,
        }, status=201)

    # ------------------------------------------------------ 人工确认报告（T09）

    def _resolve_stage_for_review(self, request):
        """三个确认报告动作共用的定位与阶段校验。"""
        from rest_framework.exceptions import ValidationError

        from .operations import ALL_WORKFLOW_STAGE_SET, WorkflowGateService

        project_id = int(request.data.get("project") or request.query_params["project"])
        self._check(request, project_id)
        workflow_id = str(
            request.data.get("workflow_id") or request.query_params.get("workflow_id") or ""
        )
        stage = str(request.data.get("stage") or request.query_params.get("stage") or "")
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")

        output, gate = WorkflowGateService.locate_stage_output(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        )
        if output is None:
            raise ValidationError(
                f"{WorkflowGateService.STAGE_LABELS.get(stage, stage)}阶段暂无产出；"
                f"请先执行本阶段"
            )
        return project_id, workflow_id, stage, output, gate

    @action(detail=False, methods=["get"], url_path="workflow-stage-review-report")
    def workflow_stage_review_report(self, request):
        """「下载确认报告」：生成并登记方案确认报告（T07 + T09）。

        与「下载报告」**不是同一个东西**，刻意分成两个按钮：那份是 Skill 的业务
        主产物（方案本身），这份是**给人工填四列**的表。合成一个文件，人就得在
        业务方案上直接改，改完也没法机器解析。
        """
        from urllib.parse import quote

        from django.http import HttpResponse
        from rest_framework.exceptions import ValidationError

        from . import derived_artifacts as da
        from .operations import WorkflowGateService

        project_id, workflow_id, stage, output, _gate = self._resolve_stage_for_review(request)
        stage_result_payload = self._extract_stage_result_payload(output)
        if stage_result_payload is None:
            raise ValidationError(
                "该产出没有结构化协议（stage_result.json），无法生成逐项确认报告；"
                "请确认本阶段 Skill 已按 stage-result/v1 输出"
            )

        try:
            artifacts = da.register_for_output(
                output, stage_result_payload=stage_result_payload, actor=request.user,
                best_effort=False,
            )
        except ValidationError:
            raise
        except Exception as exc:
            # 结构化协议失败：业务产出仍在，这一点必须在提示里说清，
            # 否则用户会以为方案白跑了。
            detail = getattr(getattr(exc, "validation", None), "detail", "")
            raise ValidationError(
                detail or f"结构化协议校验未通过，无法生成确认报告（业务产出不受影响）：{exc}"
            )

        report = next((item for item in artifacts if item.kind == da.KIND_REVIEW_REPORT), None)
        if report is None:
            raise ValidationError("确认报告未能生成，请稍后重试或联系管理员")

        response = HttpResponse(da.read_artifact(report), content_type=da.KIND_CONTENT_TYPES[report.kind])
        response["Content-Disposition"] = (
            f"attachment; filename=\"stage-review-report.xlsx\"; "
            f"filename*=UTF-8''{quote(report.filename)}"
        )
        # 让前端能核验"下的就是登记的那一份"，并与提交时的哈希对上。
        response["X-Artifact-Sha256"] = report.content_hash
        response["X-Artifact-Id"] = str(report.pk)
        return response

    @action(detail=False, methods=["get"], url_path="workflow-stage-review-status")
    def workflow_stage_review_status(self, request):
        """该阶段人工确认的当前状态（是否已生成报告、上次草稿/提交到哪）。"""
        from . import derived_artifacts as da
        from .operations import WorkflowGateService
        from .workflow_feedback import WorkflowStageReviewService

        project_id, workflow_id, stage, output, _gate = self._resolve_stage_for_review(request)
        report = (
            da.list_for_output(output).filter(kind=da.KIND_REVIEW_REPORT).first()
        )
        latest = WorkflowStageReviewService.latest(output)
        return Response({
            "stage": stage,
            "stage_label": WorkflowGateService.STAGE_LABELS.get(stage, stage),
            "workflow_id": workflow_id,
            "output_id": str(output.pk),
            "structured_input_available": self._extract_stage_result_payload(output) is not None,
            "report": ({
                "artifact_id": str(report.pk),
                "filename": report.filename,
                "content_hash": report.content_hash,
                "generator_version": report.generator_version,
                "level": report.level,
                "created_at": report.created_at.isoformat() if report.created_at else "",
            } if report is not None else None),
            "latest_review": latest,
            "history": (
                WorkflowStageReviewService.history(output.skill_version.skill)
                if output.skill_version_id and output.skill_version.skill_id else []
            ),
        })

    @action(detail=False, methods=["post"], url_path="workflow-stage-review-draft")
    def workflow_stage_review_draft(self, request):
        """保存确认报告草稿：允许有空白，**不进入进化**（T09）。"""
        return self._apply_review_upload(request, submit=False)

    @action(detail=False, methods=["post"], url_path="workflow-stage-review-submit")
    def workflow_stage_review_submit(self, request):
        """正式提交确认报告：校验条件必填，成为可用于进化的版本化反馈。"""
        return self._apply_review_upload(request, submit=True)

    # ------------------------------------------------------------ 人工补充文件（T10）

    @action(detail=False, methods=["get"], url_path="workflow-stage-attachment-catalog")
    def workflow_stage_attachment_catalog(self, request):
        """补充文件上传表单的选项真值（用途 / 阶段）。

        由后端给而不是前端写死：用途与阶段的合法取值属于**协议**，
        前端各抄一份的结果是"页面能选、提交 400"。
        """
        from .feedback_attachments import (
            ATTACHMENT_PURPOSE_LABELS, ATTACHMENT_STAGES, ATTACHMENT_MAX_BYTES,
        )
        from .operations import WorkflowGateService

        return Response({
            "purposes": [
                {"value": value, "label": label}
                for value, label in ATTACHMENT_PURPOSE_LABELS.items()
            ],
            "stages": [
                {
                    "value": stage,
                    "label": WorkflowGateService.STAGE_LABELS.get(stage, stage),
                }
                for stage in ATTACHMENT_STAGES
            ],
            "max_bytes": ATTACHMENT_MAX_BYTES,
        })

    @action(detail=False, methods=["get", "post"], url_path="workflow-stage-attachments")
    def workflow_stage_attachments(self, request):
        """阶段人工补充文件：列表与上传（T10 / §7.4）。

        **只进反馈，不进生产**：这个接口不派生 Skill、不改 ``active`` 版本、
        不进金标集。人工交一份参考附件就改动生产 Skill，是最难被接受的一类副作用。
        """
        from .feedback_attachments import StageAttachmentService

        project_id = int(request.query_params.get("project") or request.data.get("project") or 0)
        if not project_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"project": "必须指定项目"})
        self._check(request, project_id)

        if request.method == "GET":
            rows = StageAttachmentService.list_for(
                project_id=project_id,
                stage=request.query_params.get("stage") or "",
                workflow_id=request.query_params.get("workflow_id") or "",
                purpose=request.query_params.get("purpose") or "",
                include_retired=str(request.query_params.get("include_retired") or "")
                in {"1", "true", "True"},
            )
            return Response({
                "count": len(rows),
                "results": [StageAttachmentService.view(row) for row in rows],
            })

        import uuid

        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.shortcuts import get_object_or_404

        from knowledge_evolution.models import GenerationOutput
        from projects.models import Project

        from .workflow_models import StageExecutionAttempt

        project = get_object_or_404(Project, pk=project_id)
        stage = str(request.data.get("stage") or "")
        purpose = str(request.data.get("purpose") or "")

        output = None
        output_id = str(request.data.get("output_id") or "").strip()
        if output_id:
            # 产出只按 id 取是够的：下面 service 还会校验它属不属于本项目，
            # 越权会得到 400 而不是"读到别的项目的产出"。
            # ⚠️ 产出主键是 UUID，不能按"是不是数字"判合法取出。
            try:
                output_uuid = uuid.UUID(output_id)
            except (ValueError, TypeError, AttributeError):
                output_uuid = None
            output = (
                GenerationOutput.objects.filter(pk=output_uuid).first()
                if output_uuid is not None else None
            )
            if output is None:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"output_id": "产出不存在"})

        attempt = None
        attempt_id = str(request.data.get("attempt_id") or "").strip()
        if attempt_id:
            try:
                attempt_uuid = uuid.UUID(attempt_id)
            except (ValueError, TypeError, AttributeError):
                attempt_uuid = None
            attempt = (
                StageExecutionAttempt.objects.filter(pk=attempt_uuid).first()
                if attempt_uuid is not None else None
            )
            if attempt is None:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"attempt_id": "执行尝试不存在"})

        try:
            attachment, created = StageAttachmentService.upload(
                project=project, stage=stage, purpose=purpose, actor=request.user,
                upload=request.FILES.get("file"),
                file_id=request.data.get("file_id") or None,
                workflow_id=str(request.data.get("workflow_id") or ""),
                output=output, attempt=attempt,
                note=str(request.data.get("note") or ""),
            )
        except DjangoValidationError as exc:
            return Response({"detail": _validation_payload(exc)}, status=400)

        return Response(
            {"created": created, **StageAttachmentService.view(attachment)},
            status=201 if created else 200,
        )

    @action(detail=False, methods=["post"], url_path="workflow-stage-attachment-retire")
    def workflow_stage_attachment_retire(self, request):
        """退役一份补充文件：软删除 + 记原因，物理文件与审计都留下。"""
        from django.shortcuts import get_object_or_404
        from rest_framework.exceptions import ValidationError

        from .feedback_attachments import StageAttachmentService
        from .workflow_models import StageFeedbackAttachment

        attachment_id = request.data.get("attachment_id")
        if not attachment_id:
            raise ValidationError({"attachment_id": "必须指定要退役的文件"})
        attachment = get_object_or_404(StageFeedbackAttachment, pk=attachment_id)
        self._check(request, attachment.project_id)

        StageAttachmentService.retire(
            attachment=attachment, actor=request.user,
            reason=str(request.data.get("reason") or ""),
        )
        return Response(StageAttachmentService.view(attachment))

    def _apply_review_upload(self, request, *, submit: bool):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .workflow_feedback import ReviewSubmissionError, WorkflowStageReviewService

        project_id, workflow_id, stage, output, _gate = self._resolve_stage_for_review(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "必须上传已填写人工列的确认报告"})

        action = (
            WorkflowStageReviewService.submit if submit
            else WorkflowStageReviewService.save_draft
        )
        try:
            result = action(
                output=output, stage=stage, data=upload.read(), actor=request.user,
                report_name=getattr(upload, "name", "") or "",
            )
        except ReviewSubmissionError as exc:
            # 逐行错误原样带出去：这是**行级**校验（"第 3 行缺修改类型"），
            # 拍成一句话会让用户回去一行行找。
            return Response({"detail": {
                "message": exc.detail,
                "errors": exc.errors,
                "count": len(exc.errors),
            }}, status=400)
        except DjangoValidationError as exc:
            # 格式不对、无产出等单句错误：400 并带上那句话，而不是 500
            # 让前端弹"系统错误"把该看的提示吃掉。
            return Response({"detail": _validation_text(exc)}, status=400)

        return Response({"workflow_id": workflow_id, **result}, status=201)

    @staticmethod
    def _extract_stage_result_payload(output):
        """从产出里取出 Skill 的 ``stage_result.json`` 内容。

        三个来源按可信度递减：产出运行时显式回传 → 产出登记的产物文件 →
        产出 metadata 里的内联信封。**不做目录扫描**：扫到哪个算哪个会让
        "这次解读的是哪份结构化产出"取决于磁盘上的偶然残留。
        """
        import json

        from .stage_outputs import STAGE_RESULT_FILENAME

        metadata = output.metadata or {}
        inline = metadata.get("stage_result_payload")
        if isinstance(inline, dict):
            return inline

        artifacts = (metadata.get("artifacts") or [])
        for entry in artifacts:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            path = str(entry.get("path") or "")
            if name != STAGE_RESULT_FILENAME and not path.endswith(STAGE_RESULT_FILENAME):
                continue
            if not path:
                continue
            try:
                with open(path, "rb") as handle:
                    return json.loads(handle.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # 文件在、读不动：当作"取不到"而不是抛错——上层会给出
                # "该产出没有结构化协议"的明确提示，而 500 什么也说明不了。
                return None
        return None

    # -------------------------------------------- 差异、反查与归因工作区（T11）

    @staticmethod
    def _stage_derived_payload(output) -> dict:
        """读取该产出已登记的派生物：证据图谱与质量摘要（T07）。

        读**登记文件**而不是现场重算：检索结果与知识版本在事后都可能变化，
        重算出来的图谱与人工当时看到的那张不是同一张，归因就会指向一个
        当时并不存在的状态。登记文件是把"当时是这么判的"固定下来的唯一凭据。
        """
        import json

        from . import derived_artifacts as da

        payload = {"graph": None, "quality": None, "graph_stale": False, "available": False}
        for artifact in da.list_for_output(output):
            if artifact.kind not in (da.KIND_EVIDENCE_GRAPH, da.KIND_QUALITY_SUMMARY):
                continue
            try:
                content = json.loads(da.read_artifact(artifact).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                content = None
            if artifact.kind == da.KIND_EVIDENCE_GRAPH:
                payload["graph"] = content
                # 产出被替换后（T12），旧解读要显式标为过期而不是继续当有效。
                payload["graph_stale"] = da.output_hash_changed(artifact, output)
            else:
                payload["quality"] = content
        payload["available"] = payload["graph"] is not None or payload["quality"] is not None
        return payload

    @staticmethod
    def _attribution_payload(attribution) -> dict:
        """归因工作区的对外结构。

        ``usable_for_content_patch`` 由 ``assert_usable_for_content_patch`` 派生，
        而不是让前端按 category 自己判：环境层能不能改 Skill 是**规则**，
        抄到前端就会出现"后端拦住了、页面还显示可派生"。
        """
        from .attribution import assert_usable_for_content_patch

        usable = bool(assert_usable_for_content_patch([attribution]))
        return {
            "id": str(attribution.pk),
            "state": attribution.state,
            "state_label": attribution.get_state_display(),
            "category": attribution.category,
            "category_label": attribution.get_category_display(),
            "layer": attribution.layer,
            "layer_label": attribution.get_layer_display() if attribution.layer else "",
            "source": attribution.source,
            "confidence": attribution.confidence,
            "hypothesis": attribution.hypothesis,
            "evidence": attribution.evidence or [],
            "counterevidence": attribution.counterevidence or [],
            "workflow_id": attribution.workflow_id,
            "output_id": str(attribution.output_id or ""),
            "span_id": str(attribution.span_id or ""),
            "confirmed_by": (
                attribution.confirmed_by.username if attribution.confirmed_by_id else ""
            ),
            "confirmed_at": (
                attribution.confirmed_at.isoformat() if attribution.confirmed_at else ""
            ),
            "created_at": attribution.created_at.isoformat(),
            "usable_for_content_patch": usable,
            "excluded_reason": "" if usable else "执行环境问题不得生成 Skill 内容补丁",
        }

    @staticmethod
    def _stage_review_rows(output) -> list[dict]:
        """取最近一次人工确认里"填过的行"，作为差异比对的人工侧。"""
        from .workflow_feedback import WorkflowStageReviewService

        latest = WorkflowStageReviewService.latest(output)
        return list((latest or {}).get("human_rows") or [])

    def _stage_diff_inputs(self, request):
        """差异计算的一次性输入（产出、stage_result、人工行、附件、派生物）。

        三个接口（查看差异、重新归因）必须基于**同一次读取**，否则会出现
        "差异是按新报告算的、归因还是上一版"的错位。
        """
        from rest_framework.exceptions import ValidationError

        from .feedback_attachments import StageAttachmentService
        from .stage_outputs import StageResult, classify_level

        project_id, workflow_id, stage, output, _gate = self._resolve_stage_for_review(request)
        raw = self._extract_stage_result_payload(output)
        if not isinstance(raw, dict):
            raise ValidationError(
                "该产出没有可解析的结构化协议（stage_result.json），无法计算人工差异"
            )
        stage_result = StageResult.from_payload(raw, classify_level(raw))
        derived = self._stage_derived_payload(output)
        attachments = list(StageAttachmentService.list_for(
            project_id=project_id, stage=stage, workflow_id=workflow_id,
        ))
        return {
            "project_id": project_id, "workflow_id": workflow_id, "stage": stage,
            "output": output, "stage_result": stage_result,
            "rows": self._stage_review_rows(output),
            "attachments": attachments, "derived": derived,
        }

    @action(detail=False, methods=["get"], url_path="workflow-stage-diff")
    def workflow_stage_diff(self, request):
        """「差异与反查」：把人工确认报告里的改动还原成结构化差异（T11 / §8.2）。

        三件事一次给齐，因为它们必须基于同一次读取：差异（人改了什么）、
        反查链（这条改动从哪来）、归因工作区（平台怀疑是谁的问题）。
        拆成三个接口会出现"差异刷新了、反查还是上一版"的错位。
        """
        from .evidence_graph import reverse_trace
        from .operations import WorkflowGateService
        from .stage_diff import build_stage_diff
        from .trace_models import FailureAttribution

        ctx = self._stage_diff_inputs(request)
        diff = build_stage_diff(
            ctx["stage_result"], ctx["rows"],
            attachments=ctx["attachments"], quality=ctx["derived"]["quality"],
        )
        graph = ctx["derived"]["graph"] or {}
        reverse_traces = {
            item["item_id"]: reverse_trace(graph, item_id=item["item_id"])
            for item in diff["items"]
        }
        attributions = [
            self._attribution_payload(attribution)
            for attribution in FailureAttribution.objects
            .filter(output=ctx["output"]).select_related("confirmed_by")
            .order_by("state", "-created_at")
        ]
        return Response({
            "stage": ctx["stage"],
            "stage_label": WorkflowGateService.STAGE_LABELS.get(ctx["stage"], ctx["stage"]),
            "workflow_id": ctx["workflow_id"],
            "output_id": str(ctx["output"].pk),
            "diff": diff,
            "reverse_traces": reverse_traces,
            "derived": {
                "available": ctx["derived"]["available"],
                "graph_stale": ctx["derived"]["graph_stale"],
            },
            # 人没在平台上交过确认报告时差异必然为空，必须让页面能区分
            # "审完了没改"和"平台压根没拿到人工结论"。
            "review_rows_available": bool(ctx["rows"]),
            "attributions": attributions,
            "attribution_summary": {
                "proposed": sum(1 for item in attributions if item["state"] == "proposed"),
                "confirmed": sum(1 for item in attributions if item["state"] == "confirmed"),
                "rejected": sum(1 for item in attributions if item["state"] == "rejected"),
                "usable_for_content_patch": sum(
                    1 for item in attributions if item["usable_for_content_patch"]
                ),
            },
        })

    @action(detail=False, methods=["post"], url_path="workflow-stage-attribution-run")
    def workflow_stage_attribution_run(self, request):
        """「重新归因」：按当前人工差异重新生成**待确认**归因（T11）。

        只创建 ``proposed``：人改过不等于 Skill 有错（也可能只是偏好），
        自动落成 confirmed 会跳过唯一一次能拦住错误补丁的机会。
        环境类失败单独成条，供内容补丁环节整条剔除。
        """
        from rest_framework.exceptions import ValidationError

        from .attribution import HumanEditAttributionService

        ctx = self._stage_diff_inputs(request)
        if not ctx["rows"]:
            raise ValidationError(
                "尚无人工确认记录（没有上传过填写后的确认报告），没有差异可归因"
            )
        results = HumanEditAttributionService.run_for_output(
            ctx["output"], stage_result=ctx["stage_result"], rows=ctx["rows"],
            graph=ctx["derived"]["graph"], attachments=ctx["attachments"],
        )
        return Response({
            "workflow_id": ctx["workflow_id"],
            "stage": ctx["stage"],
            "created": len(results),
            "attributions": [self._attribution_payload(item) for item in results],
        })

    def _attribution_target(self, request):
        """按 id 定位归因并校验项目成员身份。"""
        from rest_framework.exceptions import ValidationError

        from .trace_models import FailureAttribution

        attribution_id = request.data.get("attribution_id")
        if not attribution_id:
            raise ValidationError({"attribution_id": "必须指定要处理的归因"})
        attribution = get_object_or_404(FailureAttribution, pk=attribution_id)
        self._check(request, attribution.project_id)
        return attribution

    @action(detail=False, methods=["post"], url_path="workflow-stage-attribution-decide")
    def workflow_stage_attribution_decide(self, request):
        """确认 / 驳回归因（T11 工作区）。**确认后才可用于派生候选。**

        采纳决定属测试负责人职责（与失败归因既有的 confirm/reject 口径一致）：
        驳回一条不成立的假设和批准一条成立的假设，后果不对称，门槛应当一致。
        """
        from rest_framework.exceptions import ValidationError

        from .attribution import AttributionService
        from projects.roles import ensure_test_lead

        attribution = self._attribution_target(request)
        action = str(request.data.get("action") or "").lower()
        if action not in ("confirm", "reject"):
            raise ValidationError({"action": "必须是 confirm 或 reject"})
        ensure_test_lead(request.user, attribution.project_id)
        updated = AttributionService.decide(
            attribution=attribution, actor=request.user,
            accepted=(action == "confirm"), note=str(request.data.get("note") or ""),
        )
        return Response(self._attribution_payload(updated))

    @action(detail=False, methods=["post"], url_path="workflow-stage-attribution-rewrite")
    def workflow_stage_attribution_rewrite(self, request):
        """改写归因（T11）：改类别必须重算责任层，否则候选补丁会改错文件。"""
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .attribution import AttributionService
        from projects.roles import ensure_test_lead

        attribution = self._attribution_target(request)
        ensure_test_lead(request.user, attribution.project_id)
        try:
            updated = AttributionService.rewrite(
                attribution, actor=request.user,
                category=str(request.data.get("category") or ""),
                hypothesis=str(request.data.get("hypothesis") or ""),
                note=str(request.data.get("note") or ""),
            )
        except DjangoValidationError as exc:
            return Response({"detail": _validation_payload(exc)}, status=400)
        return Response(self._attribution_payload(updated))

    # ---------------------------------------------------- 旁路产出纳管（T12）

    @staticmethod
    def _submission_output(request):
        """定位待纳管的产出。**只按 id 取、由调用方校验成员身份**。"""
        import uuid as _uuid

        from rest_framework.exceptions import ValidationError

        from .models import GenerationOutput

        raw = (
            request.data.get("output_id") or request.query_params.get("output_id")
            or request.data.get("output") or request.query_params.get("output")
        )
        if not raw:
            raise ValidationError({"output_id": "必须指定要纳入质量飞轮的产出"})
        try:
            output_id = _uuid.UUID(str(raw))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"output_id": "产出 id 不合法"})
        output = GenerationOutput.objects.filter(pk=output_id).select_related(
            "skill_version", "project",
        ).first()
        if output is None:
            raise ValidationError({"output_id": "产出不存在"})
        return output

    @action(detail=False, methods=["get"], url_path="workflow-submission-catalog")
    def workflow_submission_catalog(self, request):
        """纳管选项真值（去处 / 状态 / 阶段 / 可确认码），由后端下发。"""
        from .operations import WorkflowGateService
        from .submissions import CONFIRMABLE_CODES, SUBMISSION_STAGES
        from .workflow_models import SUBMISSION_STATE_LABELS, SUBMISSION_TARGET_LABELS

        return Response({
            "targets": [
                {"value": value, "label": label}
                for value, label in SUBMISSION_TARGET_LABELS.items()
            ],
            "states": [
                {"value": value, "label": label}
                for value, label in SUBMISSION_STATE_LABELS.items()
            ],
            "stages": [
                {"value": stage, "label": WorkflowGateService.STAGE_LABELS.get(stage, stage)}
                for stage in SUBMISSION_STAGES
            ],
            "confirmable_codes": sorted(CONFIRMABLE_CODES),
        })

    @action(detail=False, methods=["get"], url_path="workflow-stage-submission-preflight")
    def workflow_stage_submission_preflight(self, request):
        """「纳入质量飞轮」预检：先问后端能不能纳、会不会冲突。

        冲突必须先被知道：让用户点下去才发现要确认替换，等于把"这次替换会作废
        一次已完成的评审"藏在一 次点击之后。预检与提交走同一份判定，
        因此不会出现"预检说能、提交被拒"。
        """
        from .submissions import WorkflowSubmissionService
        from .workflow_models import SUBMISSION_TARGET_EXISTING

        output = self._submission_output(request)
        self._check(request, output.project_id)
        return Response(WorkflowSubmissionService.analyze(
            project=output.project, output=output,
            stage=str(request.query_params.get("stage") or ""),
            target=str(request.query_params.get("target") or SUBMISSION_TARGET_EXISTING),
            workflow_id=str(request.query_params.get("workflow_id") or ""),
            replace_output_id=str(request.query_params.get("replace_output_id") or ""),
        ))

    @action(detail=False, methods=["post"], url_path="workflow-stage-submit")
    def workflow_stage_submit(self, request):
        """「纳入质量飞轮」：把旁路产出接进流程（T12）。**不改产出协议。**"""
        from projects.roles import ensure_test_lead

        from .submissions import SubmissionRefused, WorkflowSubmissionService
        from .workflow_models import SUBMISSION_TARGET_EXISTING

        output = self._submission_output(request)
        self._check(request, output.project_id)
        # 纳管会把产出接进受控流程、并可能顶掉同阶段已有产出，属测试负责人职责。
        ensure_test_lead(request.user, output.project_id)
        # 纳管是"进入飞轮"的动作之一：未灰度的项目不许把产出接进来。
        _ensure_linkage_enabled(output.project_id)

        try:
            payload = WorkflowSubmissionService.admit(
                project=output.project, output=output, actor=request.user,
                stage=str(request.data.get("stage") or ""),
                target=str(request.data.get("target") or SUBMISSION_TARGET_EXISTING),
                workflow_id=str(request.data.get("workflow_id") or ""),
                replace_output_id=str(request.data.get("replace_output_id") or ""),
                confirm_replace=bool(request.data.get("confirm_replace")),
                note=str(request.data.get("note") or ""),
            )
        except SubmissionRefused as exc:
            # 拒绝要带码：前端据此决定"弹确认替换"还是"直接报错"。
            # 可确认的冲突用 409 —— 它不是"参数错了"，而是"当前状态冲突"。
            return Response(
                {"detail": exc.as_dict()}, status=409 if exc.confirmable else 400,
            )
        return Response(payload, status=201 if payload["created"] else 200)

    @action(detail=False, methods=["get"], url_path="workflow-stage-submissions")
    def workflow_stage_submissions(self, request):
        """已纳管绑定列表（按项目/流程/阶段/状态过滤）。"""
        from rest_framework.exceptions import ValidationError

        from .submissions import WorkflowSubmissionService

        raw_project = request.query_params.get("project")
        if not raw_project:
            raise ValidationError({"project": "必须指定项目"})
        project_id = int(raw_project)
        self._check(request, project_id)
        items = WorkflowSubmissionService.list_for(
            project_id=project_id,
            workflow_id=str(request.query_params.get("workflow_id") or ""),
            stage=str(request.query_params.get("stage") or ""),
            state=str(request.query_params.get("state") or ""),
        )
        results = [WorkflowSubmissionService.view(item) for item in items]
        return Response({"count": len(results), "results": results})

    @action(detail=False, methods=["post"], url_path="evaluate-workflow")
    def evaluate_workflow(self, request):
        """同时产出「阶段独立评测」与「四阶段端到端评测」（T16）。

        不改变门禁状态：评测是放行决定的输入，不是决定本身。
        结论落到该阶段门禁的 ``detail``，由 workflow-status 统一回给页面。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .report_gates import WorkflowEvaluationService
        from projects.models import Project

        project_id = int(request.data["project"]); self._check(request, project_id)
        project = get_object_or_404(Project, pk=project_id)
        try:
            payload = WorkflowEvaluationService.evaluate_and_record(
                project=project,
                workflow_id=str(request.data.get("workflow_id") or ""),
                # 不传 stage 就交给服务层取**该流程的收口阶段**（新流程=问题跟踪，
                # 存量=报告生成）。写死 report_generation 会让新链路跑完评测
                # 却落不到任何门禁上，页面看起来"跑了但没变化"。
                stage=str(request.data.get("stage") or ""),
                actor=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(payload)

    @action(detail=False, methods=["get"], url_path="responsibility")
    def responsibility(self, request):
        """下游 Badcase → 责任阶段 + SkillVersion（T16 / R8）。

        只读。``resolved`` 为 false 表示上游没有已确认归因，此时结论**不可用于**
        派生候选；接口如实返回这一点，而不是给一个看起来可用的阶段名。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .lineage import ResponsibilityService
        from projects.models import Project

        project_id = int(request.query_params["project"]); self._check(request, project_id)
        project = get_object_or_404(Project, pk=project_id)
        output_id = str(request.query_params.get("output") or "")
        if not output_id:
            raise ValidationError("必须提供 output（产出 ID）")
        try:
            payload = ResponsibilityService.locate(output_id, project=project)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(payload)

    @action(detail=False, methods=["post"], url_path="build-workflow-graph")
    def build_workflow_graph(self, request):
        from .operations import WorkflowGraphBuilder
        project_id = int(request.data["project"]); self._check(request, project_id)
        return Response(WorkflowGraphBuilder().build(project_id, str(request.data["workflow_id"])))


class KnowledgeAssetViewSet(viewsets.ModelViewSet):
    serializer_class = KnowledgeAssetSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "asset_type", "level", "status"]
    search_fields = ["key", "title"]
    ordering_fields = ["asset_type", "key", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = KnowledgeAsset.objects.select_related(
            "project", "current_version", "owner", "created_by", "updated_by"
        )
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=project.id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下创建知识资产")
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class KnowledgeVersionViewSet(viewsets.ModelViewSet):
    serializer_class = KnowledgeVersionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["asset", "status"]
    ordering_fields = ["version", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = KnowledgeVersion.objects.select_related(
            "asset", "source_snapshot", "previous_version",
            "approved_by", "second_approver", "created_by",
        ).prefetch_related("evidences")
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(asset__project_id__in=_project_ids(self.request.user))

    def perform_create(self, serializer):
        asset = serializer.validated_data["asset"]
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=asset.project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下创建知识版本")
        import hashlib
        content = serializer.validated_data.get("content", "")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        serializer.save(
            version=asset.next_version_number(),
            content_hash=content_hash,
            created_by=self.request.user,
        )


class KnowledgeConflictViewSet(viewsets.ModelViewSet):
    serializer_class = KnowledgeConflictSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "conflict_type", "state", "resolution"]
    ordering_fields = ["created_at", "updated_at"]

    def get_queryset(self):
        queryset = KnowledgeConflict.objects.select_related(
            "project", "left_asset", "right_asset",
            "left_version", "right_version", "resolved_by",
        )
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not self.request.user.is_superuser and not ProjectMember.objects.filter(
            user=self.request.user, project_id=project.id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下创建知识冲突")
        serializer.save()

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        conflict = self.get_object()
        if not request.user.is_superuser and not ProjectMember.objects.filter(
            user=request.user, project_id=conflict.project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权裁决该知识冲突")
        resolution = request.data.get("resolution")
        note = request.data.get("note", "")
        if not resolution:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"resolution": "必须指定裁决方式"})
        conflict.resolve(actor=request.user, resolution=resolution, note=note)
        return Response(KnowledgeConflictSerializer(conflict, context={"request": request}).data)


class KnowledgeEvidenceViewSet(viewsets.ModelViewSet):
    serializer_class = KnowledgeEvidenceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["version", "snapshot", "relation"]

    def get_queryset(self):
        queryset = KnowledgeEvidence.objects.select_related(
            "version", "snapshot"
        )
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(version__asset__project_id__in=_project_ids(self.request.user))


class KnowledgeAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = KnowledgeAuditLogSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["project", "action", "entity_type"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        queryset = KnowledgeAuditLog.objects.select_related("project", "actor")
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))


class KnowledgeRetrievalViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"], url_path="search")
    def search(self, request):
        project_id = request.data.get("project")
        query = request.data.get("query")
        if not project_id or not query:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"project|query": "必须指定 project 和 query"})
        project_id = int(project_id)
        if not request.user.is_superuser and not ProjectMember.objects.filter(
            user=request.user, project_id=project_id
        ).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("无权在该项目下检索")

        task_type = request.data.get("task_type", "knowledge_query")
        top_k = int(request.data.get("top_k", 10))
        time_budget_ms = int(request.data.get("time_budget_ms", 3000))
        token_budget = int(request.data.get("token_budget", 4000))
        selected_sources = request.data.get("selected_sources")
        required_levels = request.data.get("required_levels")
        graph_policy = request.data.get("graph_policy")
        policy_overrides = request.data.get("policy_overrides")

        from .retrieval_models import RetrievalPolicy
        policy = None
        policy_id = request.data.get("policy_id")
        if policy_id:
            policy = get_object_or_404(RetrievalPolicy, pk=policy_id)

        req = RetrievalRequest(
            project_id=project_id,
            query=str(query),
            task_type=str(task_type),
            principal=request.user,
            selected_sources=list(selected_sources) if selected_sources else None,
            required_levels=list(required_levels) if required_levels else None,
            time_budget_ms=time_budget_ms,
            token_budget=token_budget,
            graph_policy=graph_policy,
            top_k=top_k,
            policy=policy,
            policy_overrides=policy_overrides or {},
        )
        result = RetrievalOrchestrator().retrieve(req)
        return Response(result)
