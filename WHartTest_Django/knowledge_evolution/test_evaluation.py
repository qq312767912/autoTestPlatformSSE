from django.test import TestCase

from projects.models import Project

from .evaluation import EvaluationEngine
from .evaluation_models import EvaluationResult, EvaluationRun
from .models import EvaluationCase, EvaluationSuite


class EvaluationEngineTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="eval-test")
        self.suite = EvaluationSuite.objects.create(
            project=self.project,
            name="unit-suite",
            suite_type="seed",
            task_type="knowledge_query",
            split_ratio={"gold": 0.25, "regression": 0.25, "fresh": 0.25, "challenge": 0.25},
        )

    def _make_case(self, number, split, input_payload, expected_payload=None, golden_labels=None):
        return EvaluationCase.objects.create(
            suite=self.suite,
            case_number=number,
            task_type=self.suite.task_type,
            split=split,
            input_payload=input_payload,
            expected_payload=expected_payload or {},
            golden_labels=golden_labels or {},
        )

    def _perfect_executor(self, payload):
        return {
            "output": payload.get("query", ""),
            "citations": payload.get("expected_doc_ids", []),
            "actions": payload.get("expected_actions", []),
            "token_usage": 100,
        }

    def _bad_executor(self, payload):
        return {
            "output": "完全无关的回答",
            "citations": ["doc-999"],
            "actions": ["wrong-action"],
            "token_usage": 80,
        }

    def test_engine_creates_run_and_results(self):
        self._make_case(
            1, "gold",
            {"query": "登录必须验证码"},
            {"expected_output": "登录必须验证码", "expected_doc_ids": ["doc-1"]},
            {"must_contain": ["验证码"], "expected_actions": ["配置验证码"]},
        )

        engine = EvaluationEngine(self.suite, self._perfect_executor)
        run = engine.start_run()
        engine.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.results.filter(status="completed").count(), 1)

        result = run.results.first()
        self.assertIsNotNone(result.l1_score)
        self.assertGreater(result.l1_score, 0.0)

    def test_metrics_by_split(self):
        self._make_case(
            1, "gold",
            {"query": "登录必须验证码", "expected_doc_ids": ["doc-1"], "expected_actions": ["配置验证码"]},
            {"expected_output": "登录必须验证码", "expected_doc_ids": ["doc-1"]},
            {"must_contain": ["验证码"], "expected_actions": ["配置验证码"]},
        )
        self._make_case(
            2, "challenge",
            {"query": "数据库连接池", "expected_doc_ids": ["doc-2"], "expected_actions": ["调整池大小"]},
            {"expected_output": "数据库连接池", "expected_doc_ids": ["doc-2"]},
            {"must_contain": ["连接池"], "expected_actions": ["调整池大小"]},
        )

        engine = EvaluationEngine(self.suite, self._perfect_executor)
        run = engine.start_run()
        engine.execute(run)
        run.refresh_from_db()

        self.assertIn("overall", run.metrics_summary)
        self.assertIn("by_split", run.metrics_summary)
        self.assertEqual(run.metrics_summary["overall"]["sample_count"], 2)
        self.assertIn("gold", run.metrics_summary["by_split"])
        self.assertIn("challenge", run.metrics_summary["by_split"])

    def test_cost_summary(self):
        self._make_case(1, "regression", {"query": "x"}, {}, {})
        engine = EvaluationEngine(self.suite, self._perfect_executor, price_per_1k_tokens=0.01)
        run = engine.start_run()
        engine.execute(run)
        run.refresh_from_db()

        self.assertIn("total_cost_usd", run.cost_summary)
        self.assertEqual(run.cost_summary["total_tokens"], 100)
        self.assertAlmostEqual(run.cost_summary["total_cost_usd"], 0.001, places=6)

    def test_executor_failure_recorded(self):
        def broken_executor(payload):
            raise RuntimeError("boom")

        self._make_case(1, "regression", {"query": "x"})
        engine = EvaluationEngine(self.suite, broken_executor)
        run = engine.start_run()
        engine.execute(run)

        result = run.results.first()
        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.error_message)
        self.assertEqual(run.status, "completed")

    def test_compare_runs(self):
        case = self._make_case(
            1, "regression",
            {"query": "登录必须验证码"},
            {"expected_output": "登录必须验证码", "expected_doc_ids": ["doc-1"]},
            {"must_contain": ["验证码"], "expected_actions": ["配置验证码"]},
        )

        engine_good = EvaluationEngine(self.suite, self._perfect_executor)
        run_baseline = engine_good.start_run(name="baseline")
        engine_good.execute(run_baseline)

        engine_bad = EvaluationEngine(self.suite, self._bad_executor)
        run_candidate = engine_bad.start_run(name="candidate")
        engine_bad.execute(run_candidate)

        comp = EvaluationEngine.compare_runs(
            run_baseline.results.all(),
            run_candidate.results.all(),
            level="l1",
        )
        self.assertEqual(comp["n"], 1)
        self.assertIsNotNone(comp["mean_diff"])
        self.assertLess(comp["mean_diff"], 0)

    def test_l2_must_not_contain(self):
        self._make_case(
            1, "regression",
            {"query": "x"},
            {"expected_output": "正确回答"},
            {"must_contain": ["正确"], "must_not_contain": ["错误"]},
        )

        def executor_with_wrong(payload):
            return {"output": "错误回答", "token_usage": 10}

        engine = EvaluationEngine(self.suite, executor_with_wrong)
        run = engine.start_run()
        engine.execute(run)
        result = run.results.first()
        raw = result.raw_scores["l2"]
        self.assertEqual(raw["must_not_contain_violation"], 1)
        self.assertLess(result.l2_score, 1.0)

    def test_l3_action_f1(self):
        self._make_case(
            1, "regression",
            {"query": "x"},
            {},
            {"expected_actions": ["a", "b"]},
        )

        def partial_executor(payload):
            return {"output": "ok", "actions": ["a", "c"], "token_usage": 10}

        engine = EvaluationEngine(self.suite, partial_executor)
        run = engine.start_run()
        engine.execute(run)
        result = run.results.first()
        raw = result.raw_scores["l3"]
        self.assertEqual(raw["tp"], 1)
        self.assertEqual(raw["fp"], 1)
        self.assertEqual(raw["fn"], 1)
