from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project
from .models import AnalysisTask, ProjectRepository
from .views import KnowledgeGraphSourceViewSet


class KnowledgeGraphSourceViewSetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("graph-admin", password="secret")
        self.project = Project.objects.create(name="Graph Project", creator=self.user)
        self.repository = ProjectRepository.objects.create(
            project=self.project, source_type="local_git", local_path=".",
            name="demo-repository", path_with_namespace="demo/repository",
        )
        self.task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a" * 40, head_sha="b" * 40,
            status="completed", title="Graph snapshot",
            change_report={"graph_context": {
                "status": "completed", "graph_commit": "b" * 40, "crg_version": "2.3.9",
                "counts": {"changed_symbols": 2, "affected_files": 3, "related_tests": 1},
                "diagnostics": {"node_count": 50, "edge_count": 70},
            }},
        )
        self.factory = APIRequestFactory()

    def test_list_exposes_latest_code_source_and_future_adapter_type(self):
        request = self.factory.get("/api/code-analysis/graph-sources/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], f"code:{self.task.pk}")
        self.assertEqual(response.data["results"][0]["stats"]["nodes"], 50)
        self.assertFalse(response.data["source_types"][1]["enabled"])

    @patch("code_analysis.graph_client.CodeReviewGraphClient.browse")
    def test_graph_proxy_uses_authorized_task_identity(self, browse):
        browse.return_value = {"status": "completed", "nodes": [], "edges": []}
        source_id = f"code:{self.task.pk}"
        request = self.factory.get(
            f"/api/code-analysis/graph-sources/{source_id}/graph/",
            {"search": "service", "node_kinds": "Function,Test", "limit": "80"},
        )
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "graph"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        browse.assert_called_once_with(
            self.task, search="service", node_kinds=["Function", "Test"], edge_kinds=[],
            center="", depth=1, limit="80",
        )
        self.assertEqual(response.data["source"]["type"], "code_repository")
