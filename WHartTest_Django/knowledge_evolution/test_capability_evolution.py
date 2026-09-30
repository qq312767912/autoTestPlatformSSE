from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

from .capability_models import CapabilityDefinition
from .evolution import CapabilityEvolutionService
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace
from .protocol import ADAPTERS, publish_output


class CapabilityDefinitionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cap-user", password="pass")
        self.project = Project.objects.create(name="Cap Project", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="owner")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _data(self, response):
        payload = response.json()
        return payload.get("data", payload)

    def test_create_single_capability_definition(self):
        response = self.client.post("/api/knowledge-evolution/capability-definitions/", {
            "project": str(self.project.id),
            "kind": "skill",
            "name": "测试用例审查能力",
            "evaluation_mode": "single",
            "stages": ["case_review"],
            "gate_rules": {"min_mean_diff": 0.0},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = self._data(response)
        self.assertEqual(data["evaluation_mode"], "single")
        self.assertEqual(data["stages"], ["case_review"])

    def test_create_workflow_capability_definition_requires_two_stages(self):
        response = self.client.post("/api/knowledge-evolution/capability-definitions/", {
            "project": str(self.project.id),
            "kind": "agent",
            "name": "风险驱动测试生成",
            "evaluation_mode": "workflow",
            "stages": ["risk_identification"],
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_publish_output_tags_capability(self):
        definition = CapabilityDefinition.objects.create(
            project=self.project, kind="skill", name="tag-test",
            evaluation_mode="single", stages=["knowledge_query"], created_by=self.user,
        )
        envelope = ADAPTERS["knowledge_query"].build(
            project=self.project, user=self.user, source_id="qa-1",
            input_summary="问答", output={"answer": "A"}, capability=definition,
        )
        trace_id, output_id = publish_output(envelope)
        output = GenerationOutput.objects.get(pk=output_id)
        self.assertEqual(output.capability_id, definition.id)


class CapabilityEvolutionServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="evo-user", password="pass")
        self.project = Project.objects.create(name="Evo Project", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="owner")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.definition = CapabilityDefinition.objects.create(
            project=self.project, kind="agent", name="风险驱动测试生成",
            evaluation_mode="workflow",
            stages=["risk_identification", "test_plan_generation", "testcase_generation", "test_execution", "issue_tracking"],
            gate_rules={"min_mean_diff": 0.0, "max_latency_regression": 0.5, "max_token_regression": 0.5},
            created_by=self.user,
        )

    def _data(self, response):
        payload = response.json()
        return payload.get("data", payload)

    def _make_output(self, stage, workflow_id, capability=None):
        capability = capability or self.definition
        trace = RetrievalTrace.objects.create(
            project=self.project, task_type=stage, query=f"{stage}-q",
            task_id=f"{workflow_id}-{stage}",
        )
        return GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type=stage,
            task_id=f"{workflow_id}-{stage}", content="x",
            output_hash=f"{stage}-{workflow_id}-{capability.id}",
            capability=capability,
            metadata={"protocol": {"workflow_id": workflow_id, "stage": stage}},
        )

    def test_single_mode_scores_by_feedback(self):
        single_def = CapabilityDefinition.objects.create(
            project=self.project, kind="skill", name="用例审查能力",
            evaluation_mode="single", stages=["case_review"], created_by=self.user,
        )
        output = self._make_output("case_review", "wf-single", single_def)
        FeedbackEvent.objects.create(
            project=self.project, output=output, trace=output.trace,
            signal="accepted", value=1.0, idempotency_key="fb-1", actor=self.user,
        )
        report = CapabilityEvolutionService(single_def).run_evolution(
            triggered_by=self.user, version="v1",
        )
        self.assertEqual(report.case_count, 1)
        result = report.run.results.first()
        self.assertEqual(result.l1_score, 1.0)

    def test_workflow_mode_aggregates_stage_scores(self):
        for stage in self.definition.stages:
            output = self._make_output(stage, "wf-1")
            signal = "accepted" if stage != "test_execution" else "test_failed"
            FeedbackEvent.objects.create(
                project=self.project, output=output, trace=output.trace,
                signal=signal, value=1.0,
                idempotency_key=f"wf-1-{stage}", actor=self.user,
            )
        report = CapabilityEvolutionService(self.definition).run_evolution(
            triggered_by=self.user, version="v2",
        )
        self.assertEqual(report.case_count, 1)
        result = report.run.results.first()
        self.assertLess(result.l1_score, 1.0)
        self.assertGreater(result.l1_score, 0.0)

    def test_run_evolution_api(self):
        single_def = CapabilityDefinition.objects.create(
            project=self.project, kind="skill", name="API 测试能力",
            evaluation_mode="single", stages=["case_review"], created_by=self.user,
        )
        output = self._make_output("case_review", "wf-api", single_def)
        FeedbackEvent.objects.create(
            project=self.project, output=output, trace=output.trace,
            signal="rejected", value=1.0, idempotency_key="fb-rej", actor=self.user,
        )
        response = self.client.post(
            f"/api/knowledge-evolution/capability-definitions/{single_def.id}/run-evolution/",
            {"candidate_config": {"prompt": "new"}, "version": "v-api"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = self._data(response)
        self.assertEqual(data["case_count"], 1)
        self.assertTrue(data["created_release_id"])
