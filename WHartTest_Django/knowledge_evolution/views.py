from django.db.models import Q
from django.shortcuts import get_object_or_404
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
    KnowledgeAssetSerializer,
    KnowledgeAuditLogSerializer,
    KnowledgeConflictSerializer,
    KnowledgeEvidenceSerializer,
    KnowledgeVersionSerializer,
    RetrievalTraceSerializer,
    CapabilityReleaseSerializer,
    CapabilityDefinitionSerializer,
)
from .capability_models import CapabilityDefinition, CapabilityRelease
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
        baseline = EvaluationRun.objects.get(pk=request.data["baseline_run"])
        candidate = EvaluationRun.objects.get(pk=request.data["candidate_run"])
        report = CapabilityReleaseService.evaluate_shadow(release, baseline, candidate)
        return Response(report)

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None):
        from .capabilities import CapabilityReleaseService
        release = CapabilityReleaseService.promote(self.get_object(), actor=request.user, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["post"])
    def rollback(self, request, pk=None):
        from .capabilities import CapabilityReleaseService
        previous = CapabilityReleaseService.rollback(self.get_object(), actor=request.user, reason=request.data.get("reason", ""))
        return Response({"restored_release_id": str(previous.id) if previous else None})


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
