"""通用九维、动态 Sheet 指标、人工补评与冻结配置的回归。"""
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from openpyxl import Workbook

from knowledge_evolution.dual_evaluation import (
    assert_dual_current, evaluate_dual, evaluation_binding, frozen_config,
    record_human_metrics, refresh_dual_gate,
)
from knowledge_evolution.evolution_execution import EvolutionExecutionService, _stable_hash
from knowledge_evolution.optimization_models import SkillEvaluationProfile
from knowledge_evolution.paired_evaluation import EvaluationBudget
from knowledge_evolution.skill_assessment import (
    ASSESSMENT_SCHEMA, GENERAL_RUBRIC, BusinessMetricEvaluator, GeneralSkillEvaluator,
    compare_business, extract_sheet_metric, normalize_metrics, render_review_sheets,
    sheet_catalog, workbook_sheets,
)
from knowledge_evolution import test_paired_evolution as paired_tests
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests


def metric(**overrides):
    return {"id": "coverage", "name": "覆盖率", "source": "sheet", "direction": "higher",
            "required": True, "tolerance": 0, "target": None,
            "selector": {"sheet": "统计", "column": "B", "aggregate": "cell",
                         "match_column": "A", "match_value": "覆盖率"}, **overrides}


def general_response(score=8):
    return json.dumps({"dimensions": [{"id": item["id"], "score": score, "reason": "对应输入和产出证据",
                                       "evidence": ["SKILL.md 步骤2；产出 Sheet1 行3"]} for item in GENERAL_RUBRIC]})


class AssessmentCoreTests(SimpleTestCase):
    def test_nine_dimensions_weights_and_shortfalls(self):
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=general_response())))
        result = GeneralSkillEvaluator(factory).evaluate(
            package={"files": []}, output={"issues": []}, source={"rows": ["source"]},
            config=SimpleNamespace(name="test"), budget=EvaluationBudget(None),
        )
        self.assertEqual([item["weight"] for item in GENERAL_RUBRIC], [7, 12, 12, 6, 18, 4, 12, 23, 6])
        self.assertEqual(result["total"], 80)
        generic = GeneralSkillEvaluator(factory).evaluate(
            package={"files": []}, output={"report": "实际产出"}, source={"input": "需求文本"},
            config=SimpleNamespace(name="test"), budget=EvaluationBudget(None),
        )
        self.assertEqual(generic["status"], "completed")
        self.assertEqual(result["shortfalls"][0]["id"], "execution")
        self.assertEqual(result["shortfalls"][0]["weighted_gap"], 4.6)

    def test_general_refuses_missing_real_output_or_invalid_scores(self):
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=general_response(11))))
        evaluator = GeneralSkillEvaluator(factory)
        self.assertEqual(evaluator.evaluate(package={}, output={}, source={}, config=None, budget=EvaluationBudget(None))["status"], "failed")
        factory.assert_not_called()
        self.assertEqual(evaluator.evaluate(package={}, output={"issues": []}, source={"rows": [1]}, config=None, budget=EvaluationBudget(None))["status"], "failed")

    def test_sheet_unique_labels_formula_missing_and_zero_are_distinct(self):
        metrics = normalize_metrics([metric()])
        sheets = [{"name": "统计", "rows": [{"A": "覆盖率", "B": "95%"}]}]
        value, evidence = extract_sheet_metric(metrics[0], sheets)
        self.assertEqual(value, .95)
        self.assertEqual(evidence, ["统计!B1"])
        sheets[0]["rows"].append({"A": "覆盖率", "B": .9})
        with self.assertRaisesMessage(ValidationError, "唯一匹配"):
            extract_sheet_metric(metrics[0], sheets)
        workbook = Workbook()
        workbook.active.title = "统计"
        workbook.active.append(["覆盖率", "=1/2"])
        workbook.active.append(["零值", 0])
        stream = BytesIO();workbook.save(stream)
        parsed = workbook_sheets(stream.getvalue())
        with self.assertRaises(ValidationError):
            extract_sheet_metric(metrics[0], parsed)
        zero = normalize_metrics([metric(selector={"sheet": "统计", "cell": "B2"})])[0]
        self.assertEqual(extract_sheet_metric(zero, parsed)[0], 0)
        self.assertEqual(sheet_catalog(parsed)[0]["unresolved_formulas"], ["B1"])

    def test_sheet_aggregation_cannot_ignore_unknown_formula(self):
        item = normalize_metrics([metric(selector={"sheet": "统计", "column": "B", "aggregate": "sum"})])[0]
        sheets = [{"name": "统计", "rows": [{"B": 3}, {}], "unresolved_formulas": ["B2"]}]
        with self.assertRaisesMessage(ValidationError, "未计算公式"):
            extract_sheet_metric(item, sheets)
        mean_metric = normalize_metrics([metric(selector={"sheet": "统计", "column": "B", "aggregate": "mean", "start_row": 2})])[0]
        self.assertEqual(extract_sheet_metric(mean_metric, [{"name": "统计", "rows": [{"B": "标题"}, {"B": 10}, {"B": 20}]}])[0], 15)

    def test_dynamic_metrics_need_explicit_direction_and_criteria(self):
        for invalid in (metric(direction=""), metric(id="bad id"), metric(source="judge", criterion=""), metric(target=float("nan"))):
            with self.assertRaises(ValidationError):
                normalize_metrics([invalid])
        with self.assertRaises(ValidationError):
            normalize_metrics([metric(), metric()])

    def test_business_direction_target_tolerance_and_missing_gate(self):
        items = normalize_metrics([metric(tolerance=.01, target=.9)])
        self.assertTrue(compare_business(items, {"coverage": {"status": "completed", "value": .95}}, {"coverage": {"status": "completed", "value": .945}})["eligible"])
        self.assertFalse(compare_business(items, {"coverage": {"status": "completed", "value": .95}}, {"coverage": {"status": "completed", "value": .8}})["eligible"])
        self.assertFalse(compare_business(items, {}, {})["eligible"])
        self.assertFalse(compare_business([], {}, {})["eligible"])
        lower = normalize_metrics([metric(direction="lower", target=.2)])
        self.assertTrue(compare_business(lower, {"coverage": {"status": "completed", "value": .3}}, {"coverage": {"status": "completed", "value": .1}})["improved"])

    def test_judge_custom_metric_uses_user_criterion(self):
        item = normalize_metrics([metric(source="judge", criterion="0到10评分；每个可执行步骤计1分")])
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=json.dumps({"metrics": [
            {"id": "coverage", "status": "completed", "value": 7, "evidence": ["Sheet3 行4至10"]}]}))))
        result = BusinessMetricEvaluator(factory).evaluate(metrics=item, sheets=[], output={"issues": []}, source={"rows": [1]}, config=None, budget=EvaluationBudget(None))
        self.assertEqual(result["coverage"]["value"], 7)
        self.assertEqual(result["coverage"]["provenance"], "llm_assessed")
        self.assertIn("每个可执行步骤计1分", factory.return_value.invoke.call_args.args[0][1].content)

    def test_real_report_renderer_keeps_sheet_stats_and_deduplicates_batches(self):
        review = SimpleNamespace(source_name="source.xlsx", skill_name="dynamic-skill", get_review_mode_display=lambda: "指定 Skill")
        sheet = {"name": "业务统计", "columns": [{"key": "metric", "title": "指标"}, {"key": "score", "title": "统计值"}],
                 "rows": [{"metric": "覆盖率", "score": "90%"}]}
        output = {"issues": [], "chunks": [{"report_sheets": [sheet]}, {"report_sheets": [sheet]}]}
        sheets = render_review_sheets(source={"rows": [{"sheet": "Sheet1", "row": 2, "cells": []}]}, output=output, skill_prompt="", review=review)
        item = normalize_metrics([metric(selector={"sheet": "业务统计", "column": "B", "match_column": "A", "match_value": "覆盖率"})])[0]
        self.assertEqual(extract_sheet_metric(item, sheets)[0], .9)
        self.assertIn("审查摘要", [item["name"] for item in sheets])


class AssessmentIntegrationTests(SkillHubBaseTests):
    experiment = paired_tests.PairedPublicationTests.experiment

    def attach_dual(self, *, human=True):
        experiment, winner = self.experiment()
        metrics = normalize_metrics([metric(source="human" if human else "judge", criterion="0到10按产出覆盖性评分")])
        experiment.assessment_config = {"schema": ASSESSMENT_SCHEMA, "general_rubric": GENERAL_RUBRIC, "business_metrics": metrics}
        report = experiment.evaluation_report
        variants = {item.label: item for item in experiment.variants.select_related("skill_version")}
        general = SimpleNamespace(evaluate=Mock(return_value={"status": "completed", "total": 80}))
        business = BusinessMetricEvaluator() if human else SimpleNamespace(evaluate=Mock(return_value={"coverage": {"status": "completed", "value": 8}}))
        evaluate_dual(experiment=experiment, report=report, variants=variants, source=report["source_snapshot"],
                      config=SimpleNamespace(name="test"), budget=EvaluationBudget(None), general_evaluator=general, business_evaluator=business)
        experiment.save()
        return experiment, winner

    def test_human_metrics_pending_then_confirmed_without_rerunning_model(self):
        experiment, _ = self.attach_dual()
        self.assertFalse(experiment.evaluation_report["comparisons"]["A"]["eligible"])
        with self.assertRaises(ValidationError):
            EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="不能跳过业务测评")
        experiment = record_human_metrics(experiment=experiment, values={
            "baseline": {"coverage": {"value": 6, "evidence": "基线 Sheet1 有6项"}},
            "A": {"coverage": {"value": 8, "evidence": "A Sheet1 有8项"}},
        }, actor=self.lead)
        self.assertTrue(experiment.evaluation_report["comparisons"]["A"]["eligible"])
        self.assertEqual(experiment.evaluation_report["manual_values"]["A"]["coverage"]["actor_id"], str(self.lead.pk))
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="对照产出证据后确认")

    def test_human_score_cannot_override_sheet_metric_or_fake_missing_evidence(self):
        experiment, _ = self.attach_dual()
        for value in ({"unknown": {"value": 10, "evidence": "x"}}, {"coverage": {"value": 10, "evidence": ""}}, {"coverage": {"value": True, "evidence": "x"}}):
            with self.assertRaises(ValidationError):
                record_human_metrics(experiment=experiment, values={"A": value}, actor=self.lead)

    def test_business_regression_blocks_even_with_high_general_score(self):
        experiment, _ = self.attach_dual(human=False)
        report = experiment.evaluation_report
        report["general"]["results"]["A"]["total"] = 100
        report["business"]["results"]["A"] = {"coverage": {"status": "completed", "value": 2}}
        refresh_dual_gate(experiment, report)
        self.assertFalse(report["comparisons"]["A"]["eligible"])

    def test_config_and_score_bindings_are_checked_before_release(self):
        experiment, _ = self.attach_dual(human=False)
        experiment.assessment_config["business_metrics"][0]["direction"] = "lower"
        experiment.save()
        with self.assertRaisesMessage(ValidationError, "不可复用"):
            EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="不能复用旧评测")

    def test_profile_is_per_skill_and_experiment_snapshot_does_not_follow_edits(self):
        skill, version = self.make_skill_version(name="skill-one")
        _, other_version = self.make_skill_version(name="skill-two")
        profile = SkillEvaluationProfile.objects.create(project=self.project, skill=skill, metrics=normalize_metrics([metric()]))
        snapshot = frozen_config(project=self.project, skill_version=version)
        profile.metrics = normalize_metrics([metric(direction="lower")]);profile.save()
        self.assertEqual(snapshot["business_metrics"][0]["direction"], "higher")
        self.assertEqual(frozen_config(project=self.project, skill_version=version)["business_metrics"][0]["direction"], "lower")
        self.assertEqual(frozen_config(project=self.project, skill_version=other_version)["business_metrics"], [])

    def test_full_replay_produces_both_assessments_from_real_sheets(self):
        from knowledge_evolution.paired_evaluation import PairedOutputEvaluator
        experiment, review = paired_tests.PairedPublicationTests.prepare_rerun(self)
        experiment.assessment_config = {"schema": ASSESSMENT_SCHEMA, "general_rubric": GENERAL_RUBRIC,
                                        "business_metrics": normalize_metrics([metric()])}
        experiment.save()
        chunk_results = [{"issues": [], "report_sheets": [{"name": "统计", "columns": [
            {"key": "name", "title": "指标"}, {"key": "value", "title": "统计值"}],
            "rows": [{"name": "覆盖率", "value": value}]}]} for value in ("90%", "95%", "80%")]
        votes = [paired_tests.vote("Y"), paired_tests.vote("X"), paired_tests.vote("Y")] * 2
        paired_factory = Mock(side_effect=[SimpleNamespace(invoke=Mock(return_value=value)) for value in votes])
        general_factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=general_response())))
        with patch("testcases.review_service._read_rows", return_value=[{"sheet": "Sheet1", "row": 2, "cells": []}]), \
                patch("testcases.review_service._get_testcase_review_llm_config", return_value=SimpleNamespace(name="test")), \
                patch("knowledge_evolution.evolution_execution.bounded_llm"), \
                patch("testcases.review_service._review_chunk", side_effect=chunk_results):
            EvolutionExecutionService.rerun_same_input(
                experiment=experiment, evaluator=PairedOutputEvaluator(paired_factory),
                general_evaluator=GeneralSkillEvaluator(general_factory),
            )
        experiment.refresh_from_db()
        self.assertEqual(experiment.state, "awaiting_choice", experiment.evaluation_report.get("stop_reason"))
        self.assertEqual(experiment.evaluation_report["general"]["results"]["A"]["total"], 80)
        self.assertEqual(experiment.evaluation_report["business"]["results"]["A"]["coverage"]["value"], .9)
        self.assertEqual(experiment.evaluation_report["business"]["results"]["baseline"]["coverage"]["value"], .8)
        self.assertTrue(experiment.evaluation_report["comparisons"]["A"]["eligible"])
        self.assertEqual(experiment.evaluation_report["budget"]["calls"], 12)
        EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="核对两类结果与源行")

    def test_profile_api_saves_only_the_selected_skills_metrics(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from knowledge_evolution.views import CaseReviewEvolutionViewSet
        from testcases.models import TestCaseReview
        _, version = self.make_skill_version(name="api-profile")
        output = self.make_output(skill_version=version)
        review = TestCaseReview.objects.create(project=self.project, creator=self.lead, source_file="source.xlsx",
                                              source_name="source.xlsx", summary={"output_id": str(output.pk)})
        view = CaseReviewEvolutionViewSet.as_view({"get": "evaluation_profile", "post": "evaluation_profile"})
        factory = APIRequestFactory()
        request = factory.post("/evaluation-profile/", {"project": self.project.pk, "review_id": review.pk,
                                                        "metrics": [metric()]}, format="json")
        force_authenticate(request, user=self.lead)
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["metrics"][0]["id"], "coverage")
        self.assertEqual(SkillEvaluationProfile.objects.get(project=self.project, skill=version.skill).updated_by_id, self.lead.pk)
        request = factory.get("/evaluation-profile/", {"project": self.other_project.pk, "review_id": review.pk})
        force_authenticate(request, user=self.lead)
        self.assertEqual(view(request).status_code, 404)
