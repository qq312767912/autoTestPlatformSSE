from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project

from .capabilities import CapabilityReleaseService
from .capability_models import CapabilityRelease, PromotionDecision
from .evaluation_models import EvaluationResult, EvaluationRun
from .models import EvaluationCase, EvaluationSuite, GenerationOutput
from .operations import FlywheelMetricsService, KnowledgeHealthService, WorkflowGraphBuilder
from .protocol import ADAPTERS, EvaluationMode, OutputStage, publish_output


class OutputProtocolTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="protocol")
        self.user = get_user_model().objects.create_user(username="protocol-user", password="x")

    def test_all_eight_adapters_registered(self):
        self.assertEqual(set(ADAPTERS), {stage.value for stage in OutputStage})

    def test_workflow_stage_requires_workflow_id(self):
        envelope = ADAPTERS["risk_identification"].build(
            project=self.project, user=self.user, source_id="risk-1",
            input_summary="分析风险", output={"risks": []},
        )
        with self.assertRaises(ValueError):
            envelope.validate()

    def test_single_and_workflow_outputs_are_persisted_with_lineage(self):
        single = ADAPTERS["knowledge_query"].build(
            project=self.project, user=self.user, source_id="qa-1",
            input_summary="问答", output={"answer": "A"},
        )
        self.assertEqual(single.evaluation_mode, EvaluationMode.SINGLE)
        first = publish_output(single)
        workflow = ADAPTERS["risk_identification"].build(
            project=self.project, user=self.user, source_id="risk-1",
            workflow_id="wf-1", input_summary="风险", output={"risks": ["r1"]},
        )
        second = publish_output(workflow)
        output = GenerationOutput.objects.get(pk=second[1])
        self.assertEqual(output.metadata["protocol"]["workflow_id"], "wf-1")
        self.assertEqual(output.metadata["protocol"]["evaluation_mode"], "workflow")
        self.assertIsNotNone(first)

    def test_five_stage_workflow_builds_joint_graph(self):
        parent = None
        stages = ["risk_identification", "test_plan_generation", "testcase_generation", "test_execution", "issue_tracking"]
        for index, stage in enumerate(stages):
            envelope = ADAPTERS[stage].build(
                project=self.project, user=self.user, source_id=f"{stage}-{index}",
                workflow_id="wf-chain", input_summary=stage, output={"stage": stage},
                parent_output_ids=[parent] if parent else [],
            )
            _, parent = publish_output(envelope)
        report = WorkflowGraphBuilder().build(self.project.id, "wf-chain")
        self.assertEqual(report["node_count"], 5)
        self.assertEqual(report["edge_count"], 4)


class CapabilityReleaseTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="release")
        self.user = get_user_model().objects.create_user(username="release-user", password="x")
        self.suite = EvaluationSuite.objects.create(project=self.project, name="qa", suite_type="regression", task_type="knowledge_query")
        self.case = EvaluationCase.objects.create(suite=self.suite, case_number=1, task_type="knowledge_query")

    def _run(self, score, tokens=100, latency=100):
        run = EvaluationRun.objects.create(suite=self.suite, status="completed", metrics_summary={}, cost_summary={"total_tokens": tokens, "total_latency_ms": latency})
        EvaluationResult.objects.create(run=run, case=self.case, status="completed", l0_score=score, l1_score=score, l2_score=score, l3_score=score)
        return run

    def test_shadow_promote_and_rollback(self):
        baseline = CapabilityReleaseService.create(project=self.project, kind="prompt", name="qa", version="1", config={"prompt": "old"}, actor=self.user)
        baseline.state = "awaiting_approval"; baseline.gate_report = {"passed": True}; baseline.save()
        CapabilityReleaseService.promote(baseline, actor=self.user)
        candidate = CapabilityReleaseService.create(project=self.project, kind="prompt", name="qa", version="2", config={"prompt": "new"}, actor=self.user)
        report = CapabilityReleaseService.evaluate_shadow(candidate, self._run(.7), self._run(.8))
        self.assertTrue(report["passed"])
        CapabilityReleaseService.promote(candidate, actor=self.user)
        baseline.refresh_from_db()
        self.assertEqual(baseline.state, "retired")
        restored = CapabilityReleaseService.rollback(candidate, actor=self.user, reason="回归")
        self.assertEqual(restored.id, baseline.id)
        baseline.refresh_from_db(); candidate.refresh_from_db()
        self.assertEqual(baseline.state, "active")
        self.assertEqual(candidate.state, "rolled_back")
        self.assertEqual(PromotionDecision.objects.filter(release=candidate).count(), 2)

    def test_failed_gate_cannot_promote(self):
        release = CapabilityReleaseService.create(project=self.project, kind="prompt", name="qa", version="bad", config={})
        report = CapabilityReleaseService.evaluate_shadow(release, self._run(.8), self._run(.4))
        self.assertFalse(report["passed"])
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.promote(release, actor=self.user)

    def test_health_and_metrics_are_available(self):
        self.assertTrue(KnowledgeHealthService().inspect(self.project.id)["healthy"])
        metrics = FlywheelMetricsService().summarize(self.project.id)
        self.assertEqual(metrics["project_id"], self.project.id)

    def test_release_and_operations_api(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post("/api/knowledge-evolution/capability-releases/", {
            "project": self.project.id, "kind": "prompt", "name": "qa",
            "version": "api-v1", "config": {"prompt": "v1"},
        }, format="json")
        self.assertEqual(response.status_code, 201)
        metrics = client.get("/api/knowledge-evolution/operations/metrics/", {"project": self.project.id})
        health = client.get("/api/knowledge-evolution/operations/health/", {"project": self.project.id})
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(health.status_code, 200)
