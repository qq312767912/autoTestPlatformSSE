from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from knowledge_evolution.knowledge_models import SourceSnapshot
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

    def _create_document_snapshot(self):
        return SourceSnapshot.objects.create(
            project=self.project,
            source_type="document",
            source_id="spec-login",
            content_hash="d" * 64,
            parsed_text="# 登录规范\n登录必须校验验证码。",
            location={"title": "登录规范"},
        )

    def test_list_enables_knowledge_document_when_snapshots_exist(self):
        self._create_document_snapshot()
        request = self.factory.get(f"/api/code-analysis/graph-sources/?project={self.project.pk}")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200)
        source_types = {s["value"]: s["enabled"] for s in response.data["source_types"]}
        self.assertTrue(source_types.get("knowledge_document"))
        doc_ids = [s["id"] for s in response.data["results"] if s["type"] == "knowledge_document"]
        self.assertEqual(len(doc_ids), 1)

    def test_retrieve_document_source(self):
        snapshot = self._create_document_snapshot()
        source_id = f"document:{snapshot.pk}"
        request = self.factory.get(f"/api/code-analysis/graph-sources/{source_id}/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "retrieve"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["type"], "knowledge_document")

    def test_graph_browses_document_graph(self):
        snapshot = self._create_document_snapshot()
        source_id = f"document:{snapshot.pk}"
        request = self.factory.get(f"/api/code-analysis/graph-sources/{source_id}/graph/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "graph"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertGreater(response.data["stats"]["nodes"], 0)
        self.assertEqual(response.data["source"]["type"], "knowledge_document")

    def _create_requirement_document(self):
        from requirements.models import RequirementDocument, RequirementModule

        document = RequirementDocument.objects.create(
            project=self.project,
            title="登录需求",
            document_type="txt",
            content="登录必须校验验证码",
            uploader=self.user,
        )
        RequirementModule.objects.create(
            document=document, title="验证码校验", content="输入正确验证码", order=1
        )
        return document

    def test_list_enables_requirement_and_test_case(self):
        self._create_requirement_document()
        from testcases.models import TestCase, TestCaseModule

        module = TestCaseModule.objects.create(project=self.project, name="登录模块", level=1)
        TestCase.objects.create(project=self.project, module=module, name="登录成功", creator=self.user)
        request = self.factory.get(f"/api/code-analysis/graph-sources/?project={self.project.pk}")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200)
        source_types = {s["value"]: s["enabled"] for s in response.data["source_types"]}
        self.assertTrue(source_types.get("requirement"))
        self.assertTrue(source_types.get("test_case"))
        self.assertEqual(
            len([s for s in response.data["results"] if s["type"] == "requirement"]), 1
        )
        self.assertEqual(
            len([s for s in response.data["results"] if s["type"] == "test_case"]), 1
        )

    def test_retrieve_requirement_source(self):
        document = self._create_requirement_document()
        source_id = f"requirement:{document.pk}"
        request = self.factory.get(f"/api/code-analysis/graph-sources/{source_id}/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "retrieve"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["type"], "requirement")

    def test_graph_browses_requirement_graph(self):
        document = self._create_requirement_document()
        source_id = f"requirement:{document.pk}"
        request = self.factory.get(f"/api/code-analysis/graph-sources/{source_id}/graph/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "graph"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertGreater(response.data["stats"]["nodes"], 0)
        self.assertEqual(response.data["source"]["type"], "requirement")

    def test_retrieve_test_case_source(self):
        source_id = f"test_case:{self.project.pk}"
        request = self.factory.get(f"/api/code-analysis/graph-sources/{source_id}/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "retrieve"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["type"], "test_case")

    def test_graph_browses_test_case_graph(self):
        from testcases.models import TestCase, TestCaseModule

        module = TestCaseModule.objects.create(project=self.project, name="登录模块", level=1)
        TestCase.objects.create(project=self.project, module=module, name="登录成功", creator=self.user)
        source_id = f"test_case:{self.project.pk}"
        request = self.factory.get(f"/api/code-analysis/graph-sources/{source_id}/graph/")
        force_authenticate(request, user=self.user)
        response = KnowledgeGraphSourceViewSet.as_view({"get": "graph"})(request, pk=source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertGreater(response.data["stats"]["nodes"], 0)
        self.assertEqual(response.data["source"]["type"], "test_case")
