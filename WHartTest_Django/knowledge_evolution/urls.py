from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EvaluationResultViewSet,
    EvaluationRunViewSet,
    EvaluationSuiteViewSet,
    FeedbackEventViewSet,
    GenerationOutputViewSet,
    KnowledgeCandidateViewSet,
    KnowledgeAssetViewSet,
    KnowledgeAuditLogViewSet,
    KnowledgeConflictViewSet,
    KnowledgeEvidenceViewSet,
    KnowledgeRetrievalViewSet,
    KnowledgeVersionViewSet,
    RetrievalTraceViewSet,
    CapabilityReleaseViewSet,
    CapabilityDefinitionViewSet,
    FlywheelOperationsViewSet,
)

router = DefaultRouter()
router.register("retrieval-traces", RetrievalTraceViewSet, basename="retrieval-trace")
router.register("generation-outputs", GenerationOutputViewSet, basename="generation-output")
router.register("feedback", FeedbackEventViewSet, basename="knowledge-feedback")
router.register("evaluation-suites", EvaluationSuiteViewSet, basename="evaluation-suite")
router.register("evaluation-runs", EvaluationRunViewSet, basename="evaluation-run")
router.register("evaluation-results", EvaluationResultViewSet, basename="evaluation-result")
router.register("knowledge-candidates", KnowledgeCandidateViewSet, basename="knowledge-candidate")
router.register("knowledge-assets", KnowledgeAssetViewSet, basename="knowledge-asset")
router.register("knowledge-versions", KnowledgeVersionViewSet, basename="knowledge-version")
router.register("knowledge-conflicts", KnowledgeConflictViewSet, basename="knowledge-conflict")
router.register("knowledge-evidence", KnowledgeEvidenceViewSet, basename="knowledge-evidence")
router.register("knowledge-audit-logs", KnowledgeAuditLogViewSet, basename="knowledge-audit-log")
router.register("knowledge-retrieval", KnowledgeRetrievalViewSet, basename="knowledge-retrieval")
router.register("capability-releases", CapabilityReleaseViewSet, basename="capability-release")
router.register("capability-definitions", CapabilityDefinitionViewSet, basename="capability-definition")
router.register("operations", FlywheelOperationsViewSet, basename="flywheel-operations")

urlpatterns = [path("", include(router.urls))]
