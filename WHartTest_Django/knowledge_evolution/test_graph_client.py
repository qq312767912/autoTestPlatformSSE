"""任务 8 扩展：文档/需求/测试用例图谱客户端测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project

from .graph_client import KnowledgeDocumentGraphClient, RequirementGraphClient, TestCaseGraphClient
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import SourceSnapshot


TEXT = """# 登录规范

登录必须校验验证码。

## 错误提示

错误码如下表：
| 码 | 含义 |
| 401 | 未授权 |
"""


class KnowledgeDocumentGraphClientTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("doc-graph-client", password="p")
        self.project = Project.objects.create(name="Doc Graph Client Project", creator=self.user)
        self.snapshot = SourceSnapshot.objects.create(
            project=self.project,
            source_type="document",
            source_id="doc-login",
            content_hash="h" * 64,
            parsed_text=TEXT,
            location={"title": "登录规范"},
        )
        self.client = KnowledgeDocumentGraphClient()

    def test_source_shape(self):
        source = self.client.source(self.snapshot)
        self.assertEqual(source["id"], f"document:{self.snapshot.pk}")
        self.assertEqual(source["type"], "knowledge_document")
        self.assertEqual(source["name"], "登录规范")
        self.assertIn("stats", source)

    def test_list_sources_returns_latest_per_source_id(self):
        SourceSnapshot.objects.create(
            project=self.project,
            source_type="document",
            source_id="doc-login",
            content_hash="x" * 64,
            parsed_text="updated",
            location={"title": "登录规范新版"},
        )
        sources = self.client.list_sources(self.project.pk)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["name"], "登录规范新版")

    def test_browse_auto_builds_graph(self):
        self.assertFalse(GraphNode.objects.filter(snapshot=self.snapshot).exists())
        payload = self.client.browse(snapshot=self.snapshot)
        self.assertEqual(payload["status"], "completed")
        self.assertGreater(payload["stats"]["nodes"], 0)
        self.assertIn("facets", payload)
        self.assertIn("document", payload["facets"]["node_kinds"])

    def test_browse_search(self):
        self.client.browse(snapshot=self.snapshot)
        payload = self.client.browse(snapshot=self.snapshot, search="登录规范")
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(any("登录规范" in (n["label"] or "") for n in payload["nodes"]))

    def test_browse_center_neighbors(self):
        self.client.browse(snapshot=self.snapshot)
        doc_node = GraphNode.objects.get(
            project=self.project, node_type="document",
            external_id__startswith=f"document:{self.snapshot.pk}",
        )
        payload = self.client.browse(snapshot=self.snapshot, center=str(doc_node.pk))
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(any(n["id"] == str(doc_node.pk) for n in payload["nodes"]))

    def test_browse_node_kinds_filter(self):
        self.client.browse(snapshot=self.snapshot)
        payload = self.client.browse(
            snapshot=self.snapshot, node_kinds=["section"], limit=120
        )
        self.assertTrue(all(n["kind"] == "section" for n in payload["nodes"]))


class RequirementGraphClientTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("req-graph-client", password="p")
        self.project = Project.objects.create(name="Req Graph Client Project", creator=self.user)
        from requirements.models import RequirementDocument, RequirementModule

        self.document = RequirementDocument.objects.create(
            project=self.project,
            title="登录需求",
            description="用户登录流程",
            document_type="txt",
            content="登录必须校验验证码",
            uploader=self.user,
        )
        RequirementModule.objects.create(
            document=self.document,
            title="验证码校验",
            content="登录时需要输入正确的验证码。",
            order=1,
        )
        self.client = RequirementGraphClient()

    def test_source_shape(self):
        source = self.client.source(self.document)
        self.assertEqual(source["id"], f"requirement:{self.document.pk}")
        self.assertEqual(source["type"], "requirement")
        self.assertEqual(source["name"], "登录需求")

    def test_list_sources(self):
        sources = self.client.list_sources(self.project.pk)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["type"], "requirement")

    def test_browse_auto_builds_graph(self):
        payload = self.client.browse(document=self.document)
        self.assertEqual(payload["status"], "completed")
        self.assertGreater(payload["stats"]["nodes"], 0)
        self.assertIn("requirement_document", payload["facets"]["node_kinds"])

    def test_browse_search(self):
        self.client.browse(document=self.document)
        payload = self.client.browse(document=self.document, search="验证码")
        self.assertTrue(any("验证码" in (n["label"] or "") for n in payload["nodes"]))


class TestCaseGraphClientTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tc-graph-client", password="p")
        self.project = Project.objects.create(name="TC Graph Client Project", creator=self.user)
        from testcases.models import TestCase, TestCaseModule, TestCaseStep

        self.module = TestCaseModule.objects.create(project=self.project, name="登录模块", level=1)
        self.case = TestCase.objects.create(
            project=self.project,
            module=self.module,
            name="登录成功",
            level="P0",
            test_type="functional",
            creator=self.user,
        )
        TestCaseStep.objects.create(
            test_case=self.case, step_number=1, description="输入用户名", expected_result="输入框有值"
        )
        self.client = TestCaseGraphClient()

    def test_source_shape(self):
        source = self.client.source(self.project)
        self.assertEqual(source["id"], f"test_case:{self.project.pk}")
        self.assertEqual(source["type"], "test_case")

    def test_list_sources(self):
        sources = self.client.list_sources(self.project.pk)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["type"], "test_case")

    def test_browse_auto_builds_graph(self):
        payload = self.client.browse(project=self.project)
        self.assertEqual(payload["status"], "completed")
        self.assertGreater(payload["stats"]["nodes"], 0)
        self.assertIn("test_case", payload["facets"]["node_kinds"])

    def test_browse_search(self):
        self.client.browse(project=self.project)
        payload = self.client.browse(project=self.project, search="登录成功")
        self.assertTrue(any("登录成功" in (n["label"] or "") for n in payload["nodes"]))
