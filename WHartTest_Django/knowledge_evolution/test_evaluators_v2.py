from django.contrib.auth import get_user_model
from django.test import TestCase
from projects.models import Project

from .evaluation_models import EvaluationResult, EvaluationRun
from .evaluation_v2_models import EvaluationRubric, JudgeResult
from .evaluators import EvaluationContext, JuryAggregator, LayeredEvaluationService, SensitiveDataEvaluator
from .models import EvaluationCase, EvaluationSuite, FeedbackEvent, GenerationOutput, RetrievalTrace


User = get_user_model()


class LayeredEvaluatorTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="judge-user", password="pass")
        self.project = Project.objects.create(name="Judge Project", creator=self.user)
        self.suite = EvaluationSuite.objects.create(
            project=self.project, name="V2 suite", suite_type="regression", task_type="code_review"
        )
        self.case = EvaluationCase.objects.create(
            suite=self.suite, case_number=1, task_type="code_review",
            input_payload={}, expected_payload={}, split="regression",
        )
        self.run = EvaluationRun.objects.create(suite=self.suite, name="v2-run")
        self.result = EvaluationResult.objects.create(run=self.run, case=self.case, status="completed")
        self.trace = RetrievalTrace.objects.create(
            project=self.project, task_type="code_review", task_id="review-v2", query="review"
        )
        self.output = GenerationOutput.objects.create(
            project=self.project, trace=self.trace, task_type="code_review", task_id="review-v2",
            content="发现空值检查缺失", output_hash="b" * 64,
            metadata={"protocol": {"schema_version": "platform-output/v1", "stage": "code_review", "evaluation_mode": "single"}},
        )
        FeedbackEvent.objects.create(
            project=self.project, output=self.output, trace=self.trace,
            signal="defect_confirmed", idempotency_key="judge-feedback", actor=self.user,
        )
        self.rubric = EvaluationRubric.objects.create(
            project=self.project, task_type="code_review", name="代码审查量表", version="v1",
            required_items=["空值检查"], forbidden_items=["无需修改"],
            jury_config={"max_spread": 0.25}, created_by=self.user,
        )

    def test_layers_use_different_sources_and_persist_judges(self):
        LayeredEvaluationService.evaluate(
            evaluation_result=self.result, output=self.output, rubric=self.rubric,
            jury_votes=[{"judge": "j1", "score": 0.8}, {"judge": "j2", "score": 0.9}],
        )
        self.result.refresh_from_db()
        self.assertEqual(self.result.l0_score, 1.0)
        self.assertAlmostEqual(self.result.l1_score, 0.85)
        self.assertEqual(self.result.l2_score, 1.0)
        self.assertAlmostEqual(self.result.l3_score, 0.0)
        self.assertEqual(JudgeResult.objects.filter(evaluation_result=self.result).count(), 10)
        self.assertEqual(self.result.raw_scores["evaluator_version"], "layered-v2")

    def test_jury_disagreement_requires_review(self):
        evidence = JuryAggregator().evaluate(EvaluationContext(
            output=self.output, rubric=self.rubric,
            jury_votes=[{"judge": "j1", "score": 0.1}, {"judge": "j2", "score": 0.9}],
        ))
        self.assertEqual(evidence.status, "needs_review")
        self.assertFalse(evidence.passed)

    def test_sensitive_protocol_metadata_fails_l0(self):
        self.output.metadata = {"protocol": {"authorization": "Bearer abcdefghijklmnop"}}
        self.output.save(update_fields=["metadata"])
        evidence = SensitiveDataEvaluator().evaluate(EvaluationContext(output=self.output))
        self.assertFalse(evidence.passed)
        self.assertEqual(evidence.score, 0.0)
