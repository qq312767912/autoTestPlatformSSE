from django.db.models import Q
from projects.models import ProjectMember
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
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
    RetrievalTraceSerializer,
)


def _project_ids(user):
    return ProjectMember.objects.filter(user=user).values_list("project_id", flat=True)


class ProjectScopedReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def scoped(self, queryset):
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project_id__in=_project_ids(self.request.user))


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
