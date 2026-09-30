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
    AnnotationConflictViewSet,
    GoldAnnotationViewSet,
    GoldCaseViewSet,
    GoldDatasetViewSet,
    GoldDatasetVersionViewSet,
    EvaluationRubricViewSet,
    JudgeResultViewSet,
    ExecutionSpanViewSet,
    FailureAttributionViewSet,
    OptimizationExperimentViewSet,
    OptimizationProposalViewSet,
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
router.register("gold-datasets", GoldDatasetViewSet, basename="gold-dataset")
router.register("gold-dataset-versions", GoldDatasetVersionViewSet, basename="gold-dataset-version")
router.register("gold-cases", GoldCaseViewSet, basename="gold-case")
router.register("gold-annotations", GoldAnnotationViewSet, basename="gold-annotation")
router.register("annotation-conflicts", AnnotationConflictViewSet, basename="annotation-conflict")
router.register("evaluation-rubrics", EvaluationRubricViewSet, basename="evaluation-rubric")
router.register("judge-results", JudgeResultViewSet, basename="judge-result")
router.register("execution-spans", ExecutionSpanViewSet, basename="execution-span")
router.register("failure-attributions", FailureAttributionViewSet, basename="failure-attribution")
router.register("optimization-proposals", OptimizationProposalViewSet, basename="optimization-proposal")
router.register("optimization-experiments", OptimizationExperimentViewSet, basename="optimization-experiment")

urlpatterns = [path("", include(router.urls))]
