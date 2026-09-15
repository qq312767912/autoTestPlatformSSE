from rest_framework.routers import DefaultRouter

from .views import AnalysisTaskViewSet, CodeAnalysisLLMConfigViewSet, CredentialViewSet, GitLabConnectionViewSet, ProjectRepositoryViewSet, TestRequirementDraftViewSet

router = DefaultRouter()
router.register("connections", GitLabConnectionViewSet, basename="code-analysis-connections")
router.register("llm-config", CodeAnalysisLLMConfigViewSet, basename="code-analysis-llm-config")
router.register("repositories", ProjectRepositoryViewSet, basename="code-analysis-repositories")
router.register("credentials", CredentialViewSet, basename="code-analysis-credentials")
router.register("tasks", AnalysisTaskViewSet, basename="code-analysis-tasks")
router.register("test-requirements", TestRequirementDraftViewSet, basename="code-analysis-test-requirements")
urlpatterns = router.urls
