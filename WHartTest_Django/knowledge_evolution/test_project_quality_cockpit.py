from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

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
