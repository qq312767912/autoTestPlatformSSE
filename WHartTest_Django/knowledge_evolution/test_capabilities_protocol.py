from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

from .capabilities import CapabilityReleaseService
from .capability_models import CapabilityRelease, PromotionDecision, ReleaseObservation
from .evaluation_models import EvaluationResult, EvaluationRun
from .models import EvaluationCase, EvaluationSuite, GenerationOutput
from .operations import FlywheelMetricsService, KnowledgeHealthService, WorkflowGraphBuilder
from .protocol import ADAPTERS, EvaluationMode, OutputStage, publish_output


class OutputProtocolTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="protocol")
        self.user = get_user_model().objects.create_user(username="protocol-user", password="x")

    def test_all_business_adapters_registered(self):
        self.assertEqual(set(ADAPTERS), {stage.value for stage in OutputStage})

    def test_workflow_stage_requires_workflow_id(self):
        envelope = ADAPTERS["test_plan_generation"].build(
            project=self.project, user=self.user, source_id="plan-1",
            input_summary="生成方案", output={"plan": []},
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
        workflow = ADAPTERS["test_plan_generation"].build(
            project=self.project, user=self.user, source_id="plan-1",
            workflow_id="wf-1", input_summary="方案", output={"plan": ["p1"]},
        )
        second = publish_output(workflow)
        output = GenerationOutput.objects.get(pk=second[1])
        self.assertEqual(output.metadata["protocol"]["workflow_id"], "wf-1")
        self.assertEqual(output.metadata["protocol"]["evaluation_mode"], "workflow")
        self.assertIsNotNone(first)

    def test_four_stage_skill_workflow_builds_joint_graph(self):
        parent = None
        stages = ["test_plan_generation", "testcase_generation", "test_execution", "report_generation"]
        for index, stage in enumerate(stages):
            envelope = ADAPTERS[stage].build(
                project=self.project, user=self.user, source_id=f"{stage}-{index}",
                workflow_id="wf-chain", input_summary=stage, output={"stage": stage},
                parent_output_ids=[parent] if parent else [],
            )
            _, parent = publish_output(envelope)
        report = WorkflowGraphBuilder().build(self.project.id, "wf-chain")
        self.assertEqual(report["node_count"], 4)
        self.assertEqual(report["edge_count"], 3)

    def test_all_eight_business_outputs_create_root_and_validation_spans(self):
        for index, (stage, adapter) in enumerate(ADAPTERS.items()):
            envelope = adapter.build(
                project=self.project, user=self.user, source_id=f"span-{index}",
                workflow_id="wf-span" if stage in {
                    "test_plan_generation", "testcase_generation",
                    "test_execution", "report_generation",
                } else "",
                input_summary=stage, output={"stage": stage},
                producer={"model_version": "test-model", "prompt_version": "p1"},
            )
            trace_id, _ = publish_output(envelope)
            step_types = set(
                GenerationOutput.objects.get(trace_id=trace_id).trace.spans.values_list(
                    "step_type", flat=True,
                )
            )
            self.assertTrue({"intent", "model", "validation"}.issubset(step_types))

    def test_real_tool_calls_create_idempotent_child_spans_without_raw_payload(self):
        envelope = ADAPTERS["testcase_generation"].build(
            project=self.project, user=self.user, source_id="tool-span",
            workflow_id="wf-tool", input_summary="生成用例", output={"count": 2},
            extensions={"execution_spans": [{
                "tool_name": "save_testcases", "call_id": "call-1",
                "input_hash": "a" * 64, "output_hash": "b" * 64,
                "status": "completed", "step": 1,
            }]},
        )
        trace_id, _ = publish_output(envelope)
        publish_output(envelope)
        spans = GenerationOutput.objects.get(trace_id=trace_id).trace.spans.filter(
            step_type="tool", tool_name="save_testcases", metadata__granularity="real_tool_call",
        )
        self.assertEqual(spans.count(), 1)
        self.assertEqual(spans.get().input_hash, "a" * 64)
        self.assertNotIn("生成用例", str(spans.get().metadata))


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

    def _active_and_canary(self):
        active = CapabilityReleaseService.create(
            project=self.project, kind="prompt", name="qa", version="active",
            config={"prompt": "old"}, actor=self.user,
        )
        active.state = "awaiting_approval"
        active.gate_report = {"passed": True}
        active.save()
        CapabilityReleaseService.promote(active, actor=self.user)
        candidate = CapabilityReleaseService.create(
            project=self.project, kind="prompt", name="qa", version="canary",
            config={"prompt": "new", "canary": {"min_observations": 3}}, actor=self.user,
        )
        candidate.state = "awaiting_approval"
        candidate.gate_report = {"passed": True}
        candidate.save()
        CapabilityReleaseService.approve_for_canary(candidate, actor=self.user)
        return active, candidate

    def test_canary_needs_healthy_windows_before_activation(self):
        active, candidate = self._active_and_canary()
        for index in range(3):
            observation = CapabilityReleaseService.record_observation(
                candidate, window_key=f"window-{index}",
                metrics={"error_rate": 0.01, "l1_score": 0.9}, actor=self.user,
            )
            self.assertEqual(observation.status, "healthy")
        CapabilityReleaseService.complete_canary(candidate, actor=self.user)
        active.refresh_from_db(); candidate.refresh_from_db()
        self.assertEqual(candidate.state, "active")
        self.assertEqual(active.state, "retired")

    def test_canary_threshold_breach_is_rejected(self):
        active, candidate = self._active_and_canary()
        observation = CapabilityReleaseService.record_observation(
            candidate, window_key="bad-window",
            metrics={"error_rate": 0.20, "l1_score": 0.4}, actor=self.user,
        )
        candidate.refresh_from_db(); active.refresh_from_db()
        self.assertEqual(observation.status, "breached")
        self.assertEqual(candidate.state, "rejected")
        self.assertEqual(active.state, "active")
        self.assertTrue(PromotionDecision.objects.filter(
            release=candidate, decision="rejected",
        ).exists())

    def test_active_release_breach_automatically_rolls_back(self):
        active, candidate = self._active_and_canary()
        for index in range(3):
            CapabilityReleaseService.record_observation(
                candidate, window_key=f"healthy-{index}",
                metrics={"error_rate": 0.0, "l1_score": 0.95}, actor=self.user,
            )
        CapabilityReleaseService.complete_canary(candidate, actor=self.user)
        CapabilityReleaseService.record_observation(
            candidate, window_key="production-breach",
            metrics={"latency_regression": 0.8}, actor=self.user,
        )
        active.refresh_from_db(); candidate.refresh_from_db()
        self.assertEqual(candidate.state, "rolled_back")
        self.assertEqual(active.state, "active")
        self.assertEqual(ReleaseObservation.objects.filter(release=candidate).count(), 4)

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

    def test_executor_cannot_create_or_promote_release(self):
        executor = get_user_model().objects.create_user(username="release-executor", password="x")
        ProjectMember.objects.create(project=self.project, user=executor, role="member")
        client = APIClient(); client.force_authenticate(executor)
        created = client.post("/api/knowledge-evolution/capability-releases/", {
            "project": self.project.id, "kind": "prompt", "name": "forbidden",
            "version": "v1", "config": {"prompt": "private-content"},
        }, format="json")
        self.assertEqual(created.status_code, 403)

        release = CapabilityReleaseService.create(
            project=self.project, kind="prompt", name="qa", version="executor-denied",
            config={"prompt": "new"}, actor=self.user,
        )
        release.state = "awaiting_approval"
        release.gate_report = {"passed": True}
        release.save(update_fields=["state", "gate_report", "updated_at"])
        promoted = client.post(
            f"/api/knowledge-evolution/capability-releases/{release.id}/promote/", {},
            format="json",
        )
        self.assertEqual(promoted.status_code, 403)

    def test_test_lead_can_create_release(self):
        lead = get_user_model().objects.create_user(username="release-lead", password="x")
        ProjectMember.objects.create(project=self.project, user=lead, role="admin")
        client = APIClient(); client.force_authenticate(lead)
        response = client.post("/api/knowledge-evolution/capability-releases/", {
            "project": self.project.id, "kind": "prompt", "name": "lead-release",
            "version": "v1", "config": {"prompt": "safe"},
        }, format="json")
        self.assertEqual(response.status_code, 201)
