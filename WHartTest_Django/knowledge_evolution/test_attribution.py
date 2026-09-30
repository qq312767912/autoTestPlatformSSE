from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from projects.models import Project

from .attribution import AttributionService, SpanRecorder
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
