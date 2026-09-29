from django.db.models import Q
from projects.models import ProjectMember
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace
from .serializers import (
    FeedbackEventSerializer,
    GenerationOutputSerializer,
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
