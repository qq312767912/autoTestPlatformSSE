from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import FeedbackEventViewSet, GenerationOutputViewSet, RetrievalTraceViewSet

router = DefaultRouter()
router.register("retrieval-traces", RetrievalTraceViewSet, basename="retrieval-trace")
router.register("generation-outputs", GenerationOutputViewSet, basename="generation-output")
router.register("feedback", FeedbackEventViewSet, basename="knowledge-feedback")

urlpatterns = [path("", include(router.urls))]
