from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import TestHostMappingViewSet, TestHostVersionViewSet, agent_export, agent_report


router = DefaultRouter()
router.register("mappings", TestHostMappingViewSet, basename="test-host-mapping")
router.register("versions", TestHostVersionViewSet, basename="test-host-version")

urlpatterns = [
    path("", include(router.urls)),
    path("agent/export/", agent_export, name="test-host-agent-export"),
    path("agent/report/", agent_report, name="test-host-agent-report"),
]
