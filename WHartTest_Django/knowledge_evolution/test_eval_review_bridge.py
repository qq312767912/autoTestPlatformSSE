from django.test import TestCase

from projects.models import Project

from .eval_review_bridge import EvaluationReviewBridge
from .evaluation import EvaluationEngine
from .evaluation_models import EvaluationResult, EvaluationRun
from .knowledge_models import KnowledgeCandidate, KnowledgeVersion
from .models import EvaluationCase, EvaluationSuite, GenerationOutput, RetrievalTrace


class EvaluationReviewBridgeTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="bridge-test")
        self.suite = EvaluationSuite.objects.create(
            project=self.project,
            name="bridge-suite",
            suite_type="regression",
            task_type="code_review",
        )

    def _make_run_with_results(self, scores):
        run = EvaluationRun.objects.create(
            suite=self.suite,
            name="run",
            status="completed",
        )
        for i, score in enumerate(scores, 1):
            case = EvaluationCase.objects.create(
                suite=self.suite,
                case_number=i,
                task_type=self.suite.task_type,
                split="regression" if i % 2 == 0 else "gold",
                input_payload={"query": f"q{i}"},
            )
            EvaluationResult.objects.create(
                run=run,
                case=case,
                status="completed",
                l0_score=score,
                l1_score=score,
                l2_score=score,
                l3_score=score,
                predicted_payload={"output": f"out{i}"},
            )
        return run

    def test_find_failures(self):
        run = self._make_run_with_results([0.9, 0.3, 0.8, 0.2])
        bridge = EvaluationReviewBridge(run, thresholds={"l1": 0.5})
        failures = bridge.find_failures()
        self.assertEqual(len(failures), 2)
        self.assertEqual({f.case.case_number for f in failures}, {2, 4})

    def test_generate_candidates(self):
        run = self._make_run_with_results([0.9, 0.3])
        bridge = EvaluationReviewBridge(run, thresholds={"l1": 0.5})
        candidates = bridge.generate_candidates()
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.origin, "evaluation_failure")
        self.assertEqual(candidate.kind, "rule")
        self.assertEqual(candidate.state, "pending")
        self.assertIn("l1", candidate.payload["failure_level"])
        self.assertEqual(candidate.project, self.project)

    def test_min_failure_count(self):
        run = self._make_run_with_results([0.9, 0.3])
        bridge = EvaluationReviewBridge(run, thresholds={"l1": 0.5}, min_failure_count=5)
        self.assertEqual(bridge.generate_candidates(), [])

    def test_build_review_report(self):
        run = self._make_run_with_results([0.9, 0.3, 0.8, 0.2])
        bridge = EvaluationReviewBridge(run, thresholds={"l1": 0.5})
        report = bridge.build_review_report()
        self.assertEqual(report["total_failures"], 2)
        self.assertIn("l1", report["by_level"])
        self.assertEqual(len(report["sample_links"]), 2)

    def test_bridge_with_trace_and_output(self):
        trace = RetrievalTrace.objects.create(
            project=self.project,
            task_type="code_review",
            query="登录验证码",
        )
        output = GenerationOutput.objects.create(
            project=self.project,
            trace=trace,
            task_type="code_review",
            content="内容",
            output_hash="a" * 64,
        )
        run = self._make_run_with_results([0.3])
        result = run.results.first()
        result.case.source_trace = trace
        result.case.source_output = output
        result.case.save()

        bridge = EvaluationReviewBridge(run, thresholds={"l1": 0.5})
        candidates = bridge.generate_candidates()
        candidate = candidates[0]
        trace_ev = next((e for e in candidate.evidence if e["type"] == "retrieval_trace"), None)
        output_ev = next((e for e in candidate.evidence if e["type"] == "generation_output"), None)
        self.assertIsNotNone(trace_ev)
        self.assertIsNotNone(output_ev)
        self.assertEqual(trace_ev["trace_id"], str(trace.id))
