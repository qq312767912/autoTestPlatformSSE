from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

from .capability_registry import LEGACY_WORKFLOW_STAGES, WORKFLOW_STAGES
from .models import GenerationOutput, RetrievalTrace
from .operations import WorkflowGateService
from .protocol import ADAPTERS, publish_output
from .workflow_models import WorkflowStageGate


class ProjectQualityCockpitTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.lead = user_model.objects.create_user("lead", password="x")
        self.executor = user_model.objects.create_user("executor", password="x")
        self.project_admin = user_model.objects.create_user("project-admin", password="x")
        self.project = Project.objects.create(name="质量门禁项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        ProjectMember.objects.create(project=self.project, user=self.project_admin, role="admin")
        self.client = APIClient()
        self.client.force_authenticate(self.executor)

    def _output(self, stage="test_plan_generation", workflow_id="wf-1"):
        trace = RetrievalTrace.objects.create(
            project=self.project, user=self.executor, task_type=stage,
            task_id=f"{workflow_id}-{stage}", query=stage,
        )
        output = GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type=stage,
            task_id=trace.task_id, content="{}", output_hash=f"{workflow_id}-{stage}",
            metadata={"protocol": {"workflow_id": workflow_id, "stage": stage}},
        )
        WorkflowGateService.register_output(output)
        return output

    def test_cockpit_returns_real_project_people_and_blocked_pipeline(self):
        self._output()
        response = self.client.get(
            "/api/knowledge-evolution/operations/cockpit/", {"project": self.project.id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["people"]["leads"][0]["username"], "lead")
        self.assertEqual(response.data["people"]["executors"][0]["username"], "executor")
        people = response.data["people"]["leads"] + response.data["people"]["executors"]
        self.assertNotIn("project-admin", [person["username"] for person in people])
        stages = response.data["workflows"][0]["stages"]
        self.assertEqual(stages[0]["status"], "pending")
        self.assertEqual(stages[1]["status"], "blocked")

    def test_cockpit_workflow_carries_the_metadata_the_version_list_needs(self):
        """左侧流程版本列表要的四项元信息必须来自后端，不能靠前端从 stages 里猜。

        "何时发起"根本不在 stages 里，前端推不出来；"锁了几个阶段"推得出、但会和
        门禁口径各写一遍。这里把这四项**逐个断言**，缺任何一项左栏就会退化成
        一排长得一样的 workflow_id。
        """
        self._output(stage=LEGACY_WORKFLOW_STAGES[0], workflow_id="wf-legacy")
        response = self.client.get(
            "/api/knowledge-evolution/operations/cockpit/", {"project": self.project.id}
        )
        flow = next(
            item for item in response.data["workflows"] if item["workflow_id"] == "wf-legacy"
        )
        # 阶段序列要是这条流程自己的那一套，且被正确标成历史模板——
        # 标错的话用户会以为它"少了两个阶段"。
        self.assertEqual(list(flow["stage_order"]), list(LEGACY_WORKFLOW_STAGES))
        self.assertEqual(flow["stage_template"], "legacy")
        self.assertEqual(flow["passed_count"], 0)
        self.assertFalse(flow["completed"])
        # 刚登记了产出，时间足迹必须有值；为空等于左栏显示"发起 -"。
        self.assertIsNotNone(flow["created_at"])
        self.assertIsNotNone(flow["updated_at"])
        self.assertGreaterEqual(flow["updated_at"], flow["created_at"])
        # 未发起任何版本锁定 → 0（而不是 None，否则前端要写两套默认值）。
        self.assertEqual(flow["locked_version_count"], 0)

    def test_cockpit_marks_new_chain_flows_as_current_template(self):
        """新链路发起的流程必须标成 current，否则"当前/历史"的区分没有区分度。"""
        self._output(stage=WORKFLOW_STAGES[0], workflow_id="wf-new")
        response = self.client.get(
            "/api/knowledge-evolution/operations/cockpit/", {"project": self.project.id}
        )
        flow = next(
            item for item in response.data["workflows"] if item["workflow_id"] == "wf-new"
        )
        self.assertEqual(list(flow["stage_order"]), list(WORKFLOW_STAGES))
        self.assertEqual(flow["stage_template"], "current")
        self.assertEqual(response.data["stage_order"], list(WORKFLOW_STAGES))

    def test_executor_cannot_override_but_owner_can_with_reason(self):
        self._output()
        url = "/api/knowledge-evolution/operations/override-workflow-stage/"
        payload = {"project": self.project.id, "workflow_id": "wf-1", "stage": "test_plan_generation", "reason": "负责人确认"}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        self.client.force_authenticate(self.lead)
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "overridden")

    def test_protocol_enforces_previous_gate_when_enabled(self):
        first = ADAPTERS["test_plan_generation"].build(
            project=self.project, user=self.executor, source_id="plan-1",
            workflow_id="wf-strict", input_summary="方案", output={"plan": []},
            extensions={"enforce_quality_gate": True},
        )
        publish_output(first)
        second = ADAPTERS["testcase_generation"].build(
            project=self.project, user=self.executor, source_id="case-1",
            workflow_id="wf-strict", input_summary="用例", output={"cases": []},
            extensions={"enforce_quality_gate": True},
        )
        with self.assertRaises(Exception) as ctx:
            publish_output(second)
        # 断言"哪个阶段挡住的 + 挡住的后果"，而不是某一句固定文案：
        # 提示文案会按"没产出 / 测评没过 / 还没测评"三种原因分别细化，
        # 但被挡住的实质不变。
        message = str(ctx.exception)
        self.assertIn("test_plan_generation", message)
        self.assertIn("无法进入 testcase_generation", message)
        self.assertFalse(WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-strict", stage="testcase_generation"
        ).exists())
        gate = WorkflowStageGate.objects.get(project=self.project, workflow_id="wf-strict", stage="test_plan_generation")
        WorkflowGateService.override(gate, self.lead, "业务负责人确认")
        publish_output(second)
        self.assertTrue(WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-strict", stage="testcase_generation"
        ).exists())
