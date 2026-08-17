from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OperationLogViewSet,
    OperationLogCleanupAPIView,
    OperationLogSettingAPIView,
    AnonymizationRuleViewSet,
    AnonymizedDocumentViewSet,
    AnonymizationTemplateViewSet,
    FileAnonymizeAPIView,
)

router = DefaultRouter()
router.register(r'anonymization-docs', AnonymizedDocumentViewSet, basename='anonymized-document')
router.register(r'anonymization-rules', AnonymizationRuleViewSet, basename='anonymization-rule')
router.register(r'anonymization-templates', AnonymizationTemplateViewSet, basename='anonymization-template')
router.register(r'', OperationLogViewSet, basename='operation-log')

urlpatterns = [
    path('settings/', OperationLogSettingAPIView.as_view(), name='operation-log-settings'),
    path('cleanup-now/', OperationLogCleanupAPIView.as_view(), name='operation-log-cleanup-now'),
    path('anonymize-file/', FileAnonymizeAPIView.as_view(), name='file-anonymize'),
    path('', include(router.urls)),
]
