"""Darwin 适配的发布闸口与成对评测回归；不调用外部模型。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from knowledge_evolution.evolution_execution import EvolutionExecutionService, _stable_hash
from knowledge_evolution.optimization_models import EvolutionExperiment, EvolutionVariant
from knowledge_evolution.paired_evaluation import (
    BudgetExceeded, EvaluationBudget, PairedOutputEvaluator, normalize_policy,
)
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests


def vote(preference="Y", margin="clear", regression=False):
    return json.dumps({"preference": preference, "margin": margin, "regression": regression,
                       "reason": "输出保留有效发现且补充了源行3的证据", "evidence": ["Sheet1:3"]})


class PairedEvaluatorTests(SimpleTestCase):
    def compare(self, responses):
        factory = Mock(side_effect=[SimpleNamespace(invoke=Mock(return_value=value)) for value in responses])
        result = PairedOutputEvaluator(factory).compare(
            baseline={"issues": ["old"]}, candidate={"issues": ["new"]},
            source={"rows": ["source"]}, config=SimpleNamespace(name="test-model"),
            budget=EvaluationBudget(None),
        )
        return result, factory

    def test_swapped_order_maps_votes_back_to_candidate(self):
        result, factory = self.compare([vote("Y"), vote("X"), vote("Y")])
        self.assertTrue(result["eligible"])
        self.assertTrue(result["clear_gain"])
        self.assertEqual(result["better"], 3)
        # 每个评委有独立实例，源数据和两个实际产出在同一个请求中。
        self.assertEqual(factory.call_count, 3)

    def test_identical_outputs_skip_judges_and_cannot_be_kept(self):
        factory = Mock()
        result = PairedOutputEvaluator(factory).compare(
            baseline={"issues": []}, candidate={"issues": []}, source={},
            config=SimpleNamespace(name="test"), budget=EvaluationBudget(None),
        )
        self.assertFalse(result["eligible"])
        factory.assert_not_called()

    def test_ties_never_count_as_improvement(self):
        result, _ = self.compare([vote("tie", "none")] * 3)
        self.assertFalse(result["eligible"])
        result, _ = self.compare([vote("Y"), vote("tie", "none"), vote("tie", "none")])
        self.assertFalse(result["eligible"])

    def test_failed_judge_and_regression_fail_closed(self):
        for responses in ([vote("Y"), "not-json", vote("Y")],
                          [vote("Y"), vote("X", regression=True), vote("Y")]):
            result, _ = self.compare(responses)
            self.assertFalse(result["eligible"])

    def test_slight_majority_can_be_reviewed_but_stops_iteration(self):
        result, _ = self.compare([vote("Y", "slight"), vote("X", "slight"), vote("Y", "slight")])
        self.assertTrue(result["eligible"])
        self.assertFalse(result["clear_gain"])

    def test_budget_prevents_extra_model_call(self):
        budget = EvaluationBudget({"max_calls": 1})
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=vote())))
        with self.assertRaises(BudgetExceeded):
            PairedOutputEvaluator(factory).compare(
                baseline={"old": True}, candidate={"new": True}, source={}, config=SimpleNamespace(name="test"), budget=budget,
            )
        self.assertEqual(factory.call_count, 1)

    def test_runtime_adapter_disables_sdk_retries(self):
        from knowledge_evolution.paired_evaluation import bounded_llm
        config = SimpleNamespace(name="local-test", provider="openai_compatible", request_timeout=300,
                                 api_url="http://127.0.0.1:1/v1", api_key="test-only-key")
        llm = bounded_llm(config, max_tokens=100)
        self.assertEqual(llm.max_retries, 0)
        self.assertEqual(llm.max_tokens, 100)
        self.assertEqual(llm.root_client.max_retries, 0)

    def test_policy_time_and_token_limits(self):
        for invalid in ({"max_calls": True}, {"max_calls": 1.5}, {"max_rounds": 4},
                        {"max_seconds": float("nan")}, {"unknown": 1}):
            with self.assertRaises(ValidationError):
                normalize_policy(invalid)
        budget = EvaluationBudget({"max_seconds": 1}, clock=Mock(side_effect=[0, 2]))
        with self.assertRaisesMessage(BudgetExceeded, "时间"):
            budget.reserve("input")
        with self.assertRaisesMessage(BudgetExceeded, "Token"):
            EvaluationBudget({"max_estimated_tokens": 5}).reserve("123456")


class PairedPublicationTests(SkillHubBaseTests):
    def experiment(self):
        _, version = self.make_skill_version()
        output = self.make_output(skill_version=version)
        experiment = EvolutionExperiment.objects.create(
            project=self.project, output=output, baseline_skill_version=version,
            baseline_sha256=version.package_sha256, created_by=self.lead, state="awaiting_choice",
            policy=normalize_policy(),
        )
        source = {"rows": [{"sheet": "Sheet1", "row": 3}], "business_context": "context"}
        for label in ("baseline", "A", "B"):
            actual = {"issues": [{"description": label}]}
            EvolutionVariant.objects.create(
                experiment=experiment, label=label, skill_version=version, mutation_strategy="test",
                sandbox_key=f"{experiment.pk}:{label}", fingerprint=_stable_hash([str(experiment.pk), label]),
                rollback_target=str(version.pk), status="completed",
                run_result={"input_hash": _stable_hash(source), "output": actual,
                            "output_hash": _stable_hash(actual), "package_sha256": version.package_sha256},
            )
        baseline = experiment.variants.select_related("skill_version").get(label="baseline")
        candidate = experiment.variants.select_related("skill_version").get(label="A")
        experiment.evaluation_report = {
            "source_snapshot": source, "input_hash": _stable_hash(source), "evaluation_mode": "full_test",
            "can_iterate": True,
            "comparisons": {"A": {"eligible": True, "clear_gain": True,
                                   "binding": EvolutionExecutionService._run_binding(baseline, candidate)}},
        }
        experiment.save()
        return experiment, candidate

    def test_valid_comparison_still_requires_manual_choice(self):
        experiment, candidate = self.experiment()
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="核查证据")
        experiment.refresh_from_db()
        self.assertEqual(experiment.state, "winner_selected")
        candidate.skill_version.refresh_from_db()
        self.assertNotEqual(candidate.skill_version.state, "active")

    def test_missing_evaluation_and_input_output_drift_block_choice(self):
        for corruption in ("evaluation", "input", "output", "snapshot"):
            with self.subTest(corruption=corruption):
                experiment, candidate = self.experiment()
                if corruption == "evaluation":
                    experiment.evaluation_report = {}
                elif corruption == "snapshot":
                    experiment.evaluation_report["source_snapshot"] = {"changed": True}
                else:
                    candidate.run_result["input_hash" if corruption == "input" else "output"] = "changed"
                    candidate.save()
                experiment.save()
                with self.assertRaises(ValidationError):
                    EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="核查证据")

    def test_package_tampering_blocks_choice_and_submission(self):
        experiment, candidate = self.experiment()
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="核查证据")
        experiment.refresh_from_db()
        with patch("skills.versions.SkillVersionService.verify_package_integrity", return_value={"ok": False}):
            with self.assertRaisesMessage(ValidationError, "版本包已改变"):
                EvolutionExecutionService.submit_winner(experiment=experiment, actor=self.lead)

    def test_rerun_cannot_overwrite_completed_experiment(self):
        experiment, _ = self.experiment()
        with self.assertRaisesMessage(ValidationError, "禁止重复评测"):
            EvolutionExecutionService.rerun_same_input(experiment=experiment)

    def test_next_round_requires_clear_gain_and_preserves_parent(self):
        experiment, candidate = self.experiment()
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="核查证据")
        experiment.refresh_from_db()
        with patch.object(EvolutionExecutionService, "create_experiment", return_value="child") as create:
            self.assertEqual(EvolutionExecutionService.next_iteration(experiment=experiment, actor=self.lead), "child")
        self.assertEqual(create.call_args.kwargs["skill_version"].pk, candidate.skill_version_id)
        self.assertEqual(create.call_args.kwargs["parent"].pk, experiment.pk)

    def prepare_rerun(self, *, max_calls=120):
        from testcases.models import TestCaseReview
        experiment, _ = self.experiment()
        experiment.state = "ready"
        experiment.policy = normalize_policy({"max_calls": max_calls})
        experiment.evaluation_report = {}
        experiment.save()
        experiment.variants.update(status="ready", run_result={})
        review = TestCaseReview.objects.create(
            project=self.project, creator=self.lead, source_file="paired-test.xlsx",
            source_name="paired-test.xlsx", summary={"output_id": str(experiment.output_id)},
            business_context="原业务背景", custom_rules="原自定义规则",
        )
        return experiment, review

    def test_real_replay_pipeline_binds_inputs_and_runs_three_judges_per_candidate(self):
        experiment, _ = self.prepare_rerun()
        factory = Mock(side_effect=[SimpleNamespace(invoke=Mock(return_value=v))
                                   for v in [vote("Y"), vote("X"), vote("Y")] * 2])
        evaluator = PairedOutputEvaluator(factory)
        with patch("testcases.review_service._read_rows", return_value=[{"row": 3}]), \
                patch("testcases.review_service._get_testcase_review_llm_config", return_value=SimpleNamespace(name="test")), \
                patch("knowledge_evolution.evolution_execution.bounded_llm"), \
                patch("testcases.review_service._review_chunk", side_effect=[{"issues": [{"description": label}], "report_sheets": [{"name": "扩展"}]} for label in ("A", "B", "baseline")]) as run:
            EvolutionExecutionService.rerun_same_input(experiment=experiment, evaluator=evaluator)
        experiment.refresh_from_db()
        self.assertEqual(experiment.state, "awaiting_choice")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(factory.call_count, 6, list(experiment.variants.values("status", "run_result")))
        self.assertTrue(experiment.evaluation_report["comparisons"]["A"]["eligible"])
        self.assertEqual(experiment.evaluation_report["budget"]["calls"], 9)
        for call in run.call_args_list:
            self.assertIn("原自定义规则", call.args[1])
            self.assertEqual(call.args[3], "原业务背景")
        hashes = {v.run_result["input_hash"] for v in experiment.variants.all()}
        self.assertEqual(len(hashes), 1)
        self.assertEqual(experiment.variants.get(label="A").run_result["output"]["chunks"][0]["report_sheets"][0]["name"], "扩展")
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="人工确认源行与差异")

    def test_budget_exhaustion_preserves_completed_output_and_blocks_publication(self):
        experiment, _ = self.prepare_rerun(max_calls=1)
        with patch("testcases.review_service._read_rows", return_value=[{"row": 3}]), \
                patch("testcases.review_service._get_testcase_review_llm_config", return_value=SimpleNamespace(name="test")), \
                patch("knowledge_evolution.evolution_execution.bounded_llm"), \
                patch("testcases.review_service._review_chunk", return_value={"issues": []}):
            EvolutionExecutionService.rerun_same_input(experiment=experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.state, "failed")
        self.assertIn("调用预算", experiment.evaluation_report["stop_reason"])
        self.assertEqual(experiment.variants.filter(status="completed").count(), 1)
        with self.assertRaises(ValidationError):
            EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="不能绕过")

    def test_next_round_does_not_reread_mutated_source(self):
        parent, _ = self.experiment()
        parent.state = "winner_selected"
        parent.winner_label = "A"
        parent.save()
        child, review = self.prepare_rerun()
        child.parent = parent
        child.round_number = 2
        child.save()
        with patch("testcases.review_service._read_rows") as reader, \
                patch("testcases.review_service._get_testcase_review_llm_config", return_value=SimpleNamespace(name="test")), \
                patch("knowledge_evolution.evolution_execution.bounded_llm"), \
                patch("testcases.review_service._review_chunk", return_value={"issues": []}), \
                patch.object(PairedOutputEvaluator, "compare", return_value={"eligible": False, "clear_gain": False}):
            EvolutionExecutionService.rerun_same_input(experiment=child)
        reader.assert_not_called()
        child.refresh_from_db()
        self.assertEqual(child.evaluation_report["input_hash"], parent.evaluation_report["input_hash"])
        self.assertFalse(child.evaluation_report["can_iterate"])

    def test_actual_derivation_keeps_original_active_and_next_round_lineage(self):
        from pathlib import Path
        from knowledge_evolution.evolution_execution import SkillCreatorAdapter
        from knowledge_evolution.tests_evolution_execution_v2 import ExecutablePatchV2Tests

        class Creator(SkillCreatorAdapter):
            def build(self, *, skill_version, attributions, budget=None, minimal=False, feedback=None):
                text = (Path(skill_version.get_full_path()) / "SKILL.md").read_text(encoding="utf-8")
                return {"edits": [{"path": "SKILL.md", "content": text + (
                    "\n明确检查输入为空的情况。\n" if minimal else "\n输入不足时列出待确认项，不补造规则。\n"
                )}]}

        skill, version = self.make_skill_version()
        skill.active_version = version
        skill.save(update_fields=["active_version"])
        output = self.make_output(skill_version=version)
        attribution = ExecutablePatchV2Tests.attribution(self, output, suffix="derive-lineage")
        from knowledge_evolution.optimization_models import SkillEvaluationProfile
        SkillEvaluationProfile.objects.create(project=self.project, skill=skill, metrics=[{
            "id": "business", "name": "业务量表", "source": "judge", "direction": "higher",
            "criterion": "0–10 按实际产出可执行性评分", "required": True,
        }])
        experiment = EvolutionExecutionService.create_experiment(
            output=output, skill_version=version, attributions=[attribution], actor=self.lead, skill_creator=Creator(),
        )
        source = {"rows": [{"row": 2}]}
        for variant in experiment.variants.all():
            actual = {"issues": [{"description": variant.label}]}
            EvolutionExecutionService.record_run(experiment=experiment, label=variant.label, result={
                "input_hash": _stable_hash(source), "output": actual, "output_hash": _stable_hash(actual),
                "package_sha256": variant.skill_version.package_sha256,
            })
        baseline = experiment.variants.select_related("skill_version").get(label="baseline")
        winner = experiment.variants.select_related("skill_version").get(label="A")
        experiment.refresh_from_db()
        experiment.evaluation_report = {"input_hash": _stable_hash(source), "source_snapshot": source,
            "evaluation_mode": "full_test", "can_iterate": True,
            "comparisons": {"A": {"eligible": True, "clear_gain": True,
                                   "binding": EvolutionExecutionService._run_binding(baseline, winner)}}}
        from knowledge_evolution.dual_evaluation import evaluate_dual
        evaluate_dual(experiment=experiment, report=experiment.evaluation_report,
                      variants={v.label: v for v in experiment.variants.select_related("skill_version")},
                      source=source, config=SimpleNamespace(name="test"), budget=EvaluationBudget(None),
                      general_evaluator=SimpleNamespace(evaluate=Mock(return_value={"status": "completed"})),
                      business_evaluator=SimpleNamespace(evaluate=Mock(return_value={"business": {"status": "completed", "value": 8}})))
        experiment.save()
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="人工核查")
        experiment.refresh_from_db()
        child = EvolutionExecutionService.next_iteration(experiment=experiment, actor=self.lead, skill_creator=Creator())
        skill.refresh_from_db()
        self.assertEqual(skill.active_version_id, version.id)
        self.assertEqual(child.baseline_skill_version_id, winner.skill_version_id)
        self.assertEqual(child.output_id, output.id)
        self.assertEqual(child.parent_id, experiment.id)
        self.assertEqual(child.round_number, 2)
        self.assertEqual(child.variants.count(), 3)
        with self.assertRaisesMessage(ValidationError, "不能重复创建"):
            EvolutionExecutionService.next_iteration(experiment=experiment, actor=self.lead, skill_creator=Creator())

    def test_history_endpoint_enforces_project_boundary_and_hides_source_snapshot(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from knowledge_evolution.views import CaseReviewEvolutionViewSet
        experiment, _ = self.experiment()
        view = CaseReviewEvolutionViewSet.as_view({"get": "experiments"})
        factory = APIRequestFactory()
        request = factory.get("/experiments/", {"project": self.project.pk, "experiment_id": str(experiment.pk)})
        force_authenticate(request, user=self.lead)
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("source_snapshot", response.data["evaluation_report"])
        request = factory.get("/experiments/", {"project": self.other_project.pk, "experiment_id": str(experiment.pk)})
        force_authenticate(request, user=self.lead)
        self.assertEqual(view(request).status_code, 404)

    def test_iteration_stops_on_small_gain_or_round_limit(self):
        from knowledge_evolution.tests_evolution_execution_v2 import ExecutablePatchV2Tests
        for reason in ("small_gain", "round_limit"):
            experiment, _ = self.experiment()
            attribution = ExecutablePatchV2Tests.attribution(self, experiment.output, suffix=reason)
            experiment.attributions.add(attribution)
            experiment.state = "winner_selected"
            experiment.winner_label = "A"
            if reason == "small_gain":
                experiment.evaluation_report["comparisons"]["A"]["clear_gain"] = False
            else:
                experiment.round_number = 3
            experiment.save()
            with self.assertRaisesMessage(ValidationError, "收益不足或已达到最大轮数"):
                EvolutionExecutionService.next_iteration(experiment=experiment, actor=self.lead)
            self.assertFalse(EvolutionExperiment.objects.filter(parent=experiment).exists())
