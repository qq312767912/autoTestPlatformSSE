from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EvaluationResultViewSet,
    EvaluationRunViewSet,
    EvaluationSuiteViewSet,
    FeedbackEventViewSet,
    GenerationOutputViewSet,
    KnowledgeCandidateViewSet,
    RetrievalTraceViewSet,
)

router = DefaultRouter()
router.register("retrieval-traces", RetrievalTraceViewSet, basename="retrieval-trace")
router.register("generation-outputs", GenerationOutputViewSet, basename="generation-output")
router.register("feedback", FeedbackEventViewSet, basename="knowledge-feedback")
router.register("evaluation-suites", EvaluationSuiteViewSet, basename="evaluation-suite")
router.register("evaluation-runs", EvaluationRunViewSet, basename="evaluation-run")
router.register("evaluation-results", EvaluationResultViewSet, basename="evaluation-result")
router.register("knowledge-candidates", KnowledgeCandidateViewSet, basename="knowledge-candidate")

urlpatterns = [path("", include(router.urls))]
