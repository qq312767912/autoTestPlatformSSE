from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project
from requirements.models import RequirementDocument, RequirementModule
from testcases.models import TestCase, TestCaseModule, TestCaseStep

from .graph_adapters import RequirementGraphAdapter, TestCaseGraphAdapter
from .graph_models import GraphEdge, GraphNode


User = get_user_model()


class RequirementGraphAdapterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="req-graph-user")
        self.project = Project.objects.create(name="req-graph-project", creator=self.user)
        self.document = RequirementDocument.objects.create(
            project=self.project,
            title="登录需求",
            description="用户登录流程",
            document_type="txt",
            content="登录必须校验验证码",
            uploader=self.user,
        )
        self.module1 = RequirementModule.objects.create(
            document=self.document,
            title="验证码校验",
            content="登录时需要输入正确的验证码。",
            order=1,
        )
        self.module2 = RequirementModule.objects.create(
            document=self.document,
            title="密码校验",
            content="密码长度不少于 8 位。",
            order=2,
        )

    def test_build_from_document_creates_nodes_and_edges(self):
        adapter = RequirementGraphAdapter()
        result = adapter.build_from_document(self.document)
        self.assertEqual(result["module_count"], 2)
        self.assertTrue(GraphNode.objects.filter(external_id=f"req_doc:{self.document.pk}").exists())
        self.assertTrue(GraphNode.objects.filter(external_id=f"req_module:{self.module1.pk}").exists())
        self.assertTrue(
            GraphEdge.objects.filter(
                from_node__external_id=f"req_doc:{self.document.pk}",
                to_node__external_id=f"req_module:{self.module1.pk}",
                relation="CONTAINS",
            ).exists()
        )
        # 顺序边
        self.assertTrue(
            GraphEdge.objects.filter(
                from_node__external_id=f"req_module:{self.module1.pk}",
                to_node__external_id=f"req_module:{self.module2.pk}",
                relation="NEXT",
            ).exists()
        )

    def test_clear_document_removes_nodes(self):
        adapter = RequirementGraphAdapter()
        adapter.build_from_document(self.document)
        self.assertGreater(GraphNode.objects.filter(project=self.project).count(), 0)
        adapter.clear_document(self.document)
        self.assertEqual(GraphNode.objects.filter(project=self.project).count(), 0)


class TestCaseGraphAdapterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tc-graph-user")
        self.project = Project.objects.create(name="tc-graph-project", creator=self.user)
        self.module = TestCaseModule.objects.create(project=self.project, name="登录模块", level=1)
        self.case = TestCase.objects.create(
            project=self.project,
            module=self.module,
            name="登录成功",
            level="P0",
            test_type="functional",
            creator=self.user,
        )
        self.step1 = TestCaseStep.objects.create(
            test_case=self.case, step_number=1, description="输入用户名", expected_result="输入框有值"
        )
        self.step2 = TestCaseStep.objects.create(
            test_case=self.case, step_number=2, description="点击登录", expected_result="登录成功"
        )

    def test_build_from_project_creates_nodes_and_edges(self):
        adapter = TestCaseGraphAdapter()
        result = adapter.build_from_project(self.project.pk)
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["module_count"], 1)
        self.assertTrue(GraphNode.objects.filter(external_id=f"test_module:{self.module.pk}").exists())
        self.assertTrue(GraphNode.objects.filter(external_id=f"test_case:{self.case.pk}").exists())
        self.assertTrue(GraphNode.objects.filter(external_id=f"test_step:{self.step1.pk}").exists())
        self.assertTrue(
            GraphEdge.objects.filter(
                from_node__external_id=f"test_module:{self.module.pk}",
                to_node__external_id=f"test_case:{self.case.pk}",
                relation="CONTAINS",
            ).exists()
        )
        self.assertTrue(
            GraphEdge.objects.filter(
                from_node__external_id=f"test_case:{self.case.pk}",
                to_node__external_id=f"test_step:{self.step1.pk}",
                relation="CONTAINS",
            ).exists()
        )
        self.assertTrue(
            GraphEdge.objects.filter(
                from_node__external_id=f"test_step:{self.step1.pk}",
                to_node__external_id=f"test_step:{self.step2.pk}",
                relation="NEXT",
            ).exists()
        )

    def test_clear_project_removes_nodes(self):
        adapter = TestCaseGraphAdapter()
        adapter.build_from_project(self.project.pk)
        self.assertGreater(GraphNode.objects.filter(project=self.project).count(), 0)
        adapter.clear_project(self.project.pk)
        self.assertEqual(GraphNode.objects.filter(project=self.project).count(), 0)
