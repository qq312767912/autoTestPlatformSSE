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
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    GoldAnnotation,
    GoldCase,
    GoldDataset,
    GoldDatasetVersion,
    KnowledgeCandidate,
    RetrievalTrace,
)
from .serializers import (
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


class ProjectScopedReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def scoped(self, queryset):
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))


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
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(GoldAnnotationSerializer(annotation).data)


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

        请求可带 ``pins``：``{"阶段": "Skill 的 UUID"}``——向导里人**逐阶段选定**的包。
        不传则沿用"按 manifest 声明解析活跃版本"的旧行为，两种入口都要能用。
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        from .operations import WorkflowGateService
        from projects.models import Project

        project_id = int(request.data["project"]); self._check(request, project_id)
        _ensure_test_lead(request.user, project_id)
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

        定位刻意走 **project + workflow_id + stage 三者一起**，而不是让页面按
        output id 直接取：否则任何项目成员都能靠猜 id 读到别的项目的产出正文，
        权限口径就变成"取决于 id 是否被猜中"。这里多一次三元定位，
        换来的是越权读取在这个接口上不可能发生。
        """
        from rest_framework.exceptions import ValidationError

        from .models import GenerationOutput
        from .operations import ALL_WORKFLOW_STAGE_SET, WorkflowGateService
        from .workflow_models import WorkflowStageGate

        project_id = int(request.query_params["project"]); self._check(request, project_id)
        workflow_id = str(request.query_params.get("workflow_id") or "")
        stage = str(request.query_params.get("stage") or "")
        # 用**全集**而不是新链路默认序列：存量流程还停在方案/报告阶段，
        # 只认新序列会让它们的产出在页面上永远"查不到"。
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")

        gate = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id=workflow_id, stage=stage
        ).select_related("output", "output__skill_version").first()
        output = gate.output if gate is not None and gate.output_id else None
        if output is None:
            # 兜底：旁路产出（有产出但门禁还没建）也要能查看，与 workflow-status 的兜底一致。
            for candidate in GenerationOutput.objects.filter(
                project_id=project_id
            ).select_related("skill_version"):
                protocol = (candidate.metadata or {}).get("protocol") or {}
                if (
                    str(protocol.get("workflow_id") or "") == workflow_id
                    and str(protocol.get("stage") or candidate.task_type) == stage
                ):
                    output = candidate
                    break
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
