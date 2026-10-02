from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from projects.models import Project

from langgraph_integration.models import LLMConfig

from .attribution import AttributionService, LLMAssistedAttributionService, SpanRecorder
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


User = get_user_model()


class AttributionServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="trace-user", password="pass")
        self.project = Project.objects.create(name="Trace Project", creator=self.user)
        self.trace = RetrievalTrace.objects.create(
            project=self.project, task_type="code_review", task_id="r1", query="review"
        )
        self.output = GenerationOutput.objects.create(
            project=self.project, trace=self.trace, task_type="code_review", task_id="r1",
            content="result", output_hash="c" * 64,
            metadata={"protocol": {"workflow_id": "wf-1"}},
        )

    def test_failed_tool_span_produces_deterministic_attribution(self):
        span = SpanRecorder.record(
            trace=self.trace, stage="code_review", step_type="tool", status="failed",
            workflow_id="wf-1", tool_name="crg", error_type="timeout",
        )
        first = AttributionService.run_for_output(self.output)
        second = AttributionService.run_for_output(self.output)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(first[0].category, "tool_error")
        self.assertEqual(first[0].span_id, span.id)

    def test_negative_feedback_without_failed_span_falls_back_to_generation(self):
        SpanRecorder.record(
            trace=self.trace, stage="code_review", step_type="model", status="completed",
        )
        FeedbackEvent.objects.create(
            project=self.project, output=self.output, trace=self.trace,
            signal="false_positive", idempotency_key="attr-feedback", actor=self.user,
        )
        result = AttributionService.run_for_output(self.output)
        self.assertEqual(result[0].category, "generation_error")
        AttributionService.decide(attribution=result[0], actor=self.user, accepted=True)
        result[0].refresh_from_db()
        self.assertEqual(result[0].state, "confirmed")

    def test_parent_span_must_belong_to_same_trace(self):
        other_trace = RetrievalTrace.objects.create(
            project=self.project, task_type="code_review", task_id="r2", query="other"
        )
        parent = SpanRecorder.record(
            trace=other_trace, stage="code_review", step_type="planning", status="completed"
        )
        with self.assertRaises(ValidationError):
            SpanRecorder.record(
                trace=self.trace, stage="code_review", step_type="tool",
                status="completed", parent_span=parent,
            )

    def test_human_edit_records_hashes_and_diff_without_raw_content(self):
        span = SpanRecorder.record_human_edit(
            output=self.output, actor=self.user,
            before={"risk": "old", "private": "secret-a"},
            after={"risk": "new", "private": "secret-b", "owner": "qa"},
            comment="人工修正风险",
        )
        self.assertEqual(span.step_type, "human_edit")
        self.assertTrue(span.input_hash and span.output_hash)
        self.assertEqual(span.evidence[0]["changed_keys"], ["private", "risk"])
        self.assertNotIn("secret-a", str(span.evidence))

    def test_later_success_is_counterevidence_for_failed_tool(self):
        SpanRecorder.record(
            trace=self.trace, stage="code_review", step_type="tool", status="failed",
            sequence=1, tool_name="crg",
        )
        SpanRecorder.record(
            trace=self.trace, stage="code_review", step_type="tool", status="completed",
            sequence=2, tool_name="crg",
        )
        result = AttributionService.run_for_output(self.output)[0]
        self.assertEqual(result.counterevidence[0]["type"], "later_recovery")
        self.assertLessEqual(result.confidence, 0.6)

    def test_reverse_attribution_follows_parent_outputs(self):
        parent_trace = RetrievalTrace.objects.create(
            project=self.project, task_type="risk_identification", task_id="parent",
        )
        parent = GenerationOutput.objects.create(
            project=self.project, trace=parent_trace, task_type="risk_identification",
            task_id="parent", content="risk", output_hash="d" * 64,
            metadata={"protocol": {"workflow_id": "wf-1", "parent_output_ids": []}},
        )
        SpanRecorder.record(
            trace=parent_trace, stage="risk_identification", step_type="planning",
            status="failed", sequence=1,
        )
        self.output.metadata = {"protocol": {
            "workflow_id": "wf-1", "parent_output_ids": [str(parent.id)],
        }}
        self.output.save(update_fields=["metadata"])
        linked = AttributionService.run_reverse_for_output(self.output)
        self.assertEqual(linked[0].category, "planning_error")
        self.assertIn("上游 risk_identification", linked[0].hypothesis)

    def test_llm_attribution_is_capped_and_requires_human_confirmation(self):
        LLMConfig.objects.create(
            config_name="attribution-judge", name="judge", api_url="http://example.com/v1",
            is_active=True,
        )

        class Response:
            content = '{"hypotheses":[{"category":"prompt_error","confidence":0.95,' \
                      '"hypothesis":"Prompt遗漏边界条件","evidence":[],' \
                      '"counterevidence":[{"reason":"工具已成功"}]}]}'

        class LLM:
            def invoke(self, _prompt): return Response()

        results = LLMAssistedAttributionService(llm_factory=lambda _config: LLM()).run(self.output)
        self.assertEqual(results[0].source, "llm")
        self.assertEqual(results[0].state, "proposed")
        self.assertEqual(results[0].confidence, 0.8)
        self.assertTrue(results[0].counterevidence)
