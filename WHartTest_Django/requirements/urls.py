from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .comparison_views import RequirementComparisonViewSet
from .views import (
    RequirementDocumentViewSet,
    RequirementModuleViewSet,
    ReviewReportViewSet,
    ReviewIssueViewSet,
    ModuleReviewResultViewSet,
)

# 创建路由器并注册各个 ViewSet。
router = DefaultRouter()
router.register(
    r"documents", RequirementDocumentViewSet, basename="requirement-documents"
)
router.register(r"modules", RequirementModuleViewSet, basename="requirement-modules")
router.register(r"reports", ReviewReportViewSet, basename="review-reports")
router.register(r"issues", ReviewIssueViewSet, basename="review-issues")
router.register(
    r"module-results", ModuleReviewResultViewSet, basename="module-review-results"
)

router.register(r'comparisons', RequirementComparisonViewSet, basename='requirement-comparisons')

# API 路由地址由 router 自动生成。
urlpatterns = [
    path("", include(router.urls)),
]
