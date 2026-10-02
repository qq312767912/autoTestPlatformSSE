import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from langgraph_integration.models import LLMConfig
from projects.models import Project

from .evaluation_models import EvaluationResult, EvaluationRun
from .evaluation_v2_models import EvaluationRubric, JudgeResult
from .evaluators import LayeredEvaluationService
from .llm_judges import LLMJuryService
from .models import EvaluationCase, EvaluationSuite, GenerationOutput, RetrievalTrace


class _Response:
    usage_metadata = {"total_tokens": 12}

    def __init__(self, score):
        self.content = json.dumps({
            "score": score, "passed": True, "confidence": 0.9,
            "dimensions": {"correctness": score}, "evidence": [{"line": 1}],
            "rationale": "证据充分",
        })


class _LLM:
    def __init__(self, score):
        self.score = score

    def invoke(self, _prompt):
        return _Response(self.score)


class LLMJuryServiceTest(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="llm-jury", password="x")
        project = Project.objects.create(name="LLM Jury", creator=user)
        suite = EvaluationSuite.objects.create(
            project=project, name="jury", suite_type="gold", task_type="code_review",
        )
        case = EvaluationCase.objects.create(
            suite=suite, case_number=1, task_type="code_review",
            expected_payload={"answer": "应识别空指针"},
        )
        run = EvaluationRun.objects.create(suite=suite)
        self.result = EvaluationResult.objects.create(run=run, case=case)
        trace = RetrievalTrace.objects.create(project=project, task_type="code_review", task_id="1")
        self.output = GenerationOutput.objects.create(
            project=project, trace=trace, task_type="code_review", task_id="1",
            content="存在空指针风险", output_hash="c" * 64,
            metadata={"protocol": {"schema_version": "platform-output/v1", "stage": "code_review"}},
        )
        self.rubric = EvaluationRubric.objects.create(
            project=project, task_type="code_review", name="review", version="v1",
            dimensions=[{"name": "correctness"}], jury_config={"max_spread": 0.25},
        )
        for index in range(2):
            LLMConfig.objects.create(
                config_name=f"judge-{index}", name=f"model-{index}",
                api_url="http://example.com/v1", is_active=index == 0,
            )

    def test_two_real_judge_calls_are_persisted_and_aggregated(self):
        scores = iter([0.8, 0.9])
        jury = LLMJuryService(llm_factory=lambda _config: _LLM(next(scores)))
        LayeredEvaluationService.evaluate(
            evaluation_result=self.result, output=self.output, rubric=self.rubric,
            jury_service=jury,
        )
        judges = JudgeResult.objects.filter(
            evaluation_result=self.result, evaluator_type="llm_judge",
        )
        self.assertEqual(judges.count(), 2)
        self.assertEqual(sum(item.token_usage for item in judges), 24)
        self.result.refresh_from_db()
        self.assertAlmostEqual(self.result.l1_score, 0.85)

    def test_failed_judge_does_not_abort_evaluation(self):
        class Broken:
            def invoke(self, _prompt):
                raise RuntimeError("judge unavailable")

        jury = LLMJuryService(llm_factory=lambda _config: Broken())
        LayeredEvaluationService.evaluate(
            evaluation_result=self.result, output=self.output, rubric=self.rubric,
            jury_service=jury,
        )
        self.assertEqual(
            JudgeResult.objects.filter(
                evaluation_result=self.result, evaluator_type="llm_judge", status="failed",
            ).count(), 2,
        )
        self.result.refresh_from_db()
        self.assertIsNotNone(self.result.l1_score)
