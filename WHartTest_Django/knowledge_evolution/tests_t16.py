"""T16：报告契约校验、阶段/端到端评测与下游责任定位。

测试组织原则：每个用例只证明一件在真实流水线里会出错的事。

- 报告引用缺失/悬空/跨链路 → 报告门禁必须失败，且**失败原因要指名缺哪个阶段**；
- 报告正文缺"未闭环问题"→ 必须失败（否则"确实没有"和"忘了写"无法区分）；
- **文案评分满分不能救一份契约不合法的报告**（这是本任务的核心语义）；
- 阶段评测与端到端评测必须同时产出，且端到端要能指出是哪一段没放行；
- 下游 Badcase 必须能定位到责任阶段 + SkillVersion；**没有已确认归因时不得给可用结论**。
"""
from __future__ import annotations

import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.evaluation_models import EvaluationResult, EvaluationRun
from knowledge_evolution.lineage import ResponsibilityService
from knowledge_evolution.models import (
    EvaluationCase,
    EvaluationSuite,
    GenerationOutput,
)
from knowledge_evolution.operations import (
    DEFAULT_WORKFLOW_STAGE_ORDER,
    LEGACY_WORKFLOW_STAGE_ORDER,
    WorkflowGateService,
)
from knowledge_evolution.protocol import ADAPTERS, publish_output
from knowledge_evolution.report_gates import (
    ReportContractError,
    ReportGateService,
    WorkflowEvaluationService,
)
from knowledge_evolution.tests_t09_t13 import TEST_MEDIA_ROOT
from knowledge_evolution.tests_t15 import WorkflowBaseTests
from knowledge_evolution.workflow_models import WorkflowStageGate

# 本文件整体针对**历史链路**（方案 → 用例 → 执行 → 报告）的收口契约。
# 主链路口径已改为「风险识别 → 用例 → 执行 → 问题跟踪」，但报告契约并没有被删掉：
# 存量流程仍要靠它收口。所以这组用例继续按历史序列跑，而不是跟着新序列改写——
# 改写等于把"存量流程的报告还被校验着"这件事测掉。
STAGES = list(LEGACY_WORKFLOW_STAGE_ORDER)
UPSTREAM = STAGES[:3]
REPORT = STAGES[3]


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class T16BaseTests(WorkflowBaseTests):
    """在 T15 夹具之上补三个动作：发产出、打分、造报告正文。"""

    def _publish(self, *, stage, body, parents=None, workflow_id="wf-t16", enforce=False):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead,
            source_id=f"{stage}-{uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output=body, parent_output_ids=list(parents or []),
            extensions={"enforce_quality_gate": enforce},
        )
        result = publish_output(envelope)
        return GenerationOutput.objects.get(pk=result[1])

    def _publish_chain(self, *, workflow_id="wf-t16"):
        """造一条完整的上游三段（方案/用例/执行）。"""
        plan = self._publish(
            stage="test_plan_generation", body={"content": "方案"}, workflow_id=workflow_id,
        )
        cases = self._publish(
            stage="testcase_generation", body={"content": "用例"},
            parents=[plan.pk], workflow_id=workflow_id,
        )
        execution = self._publish(
            stage="test_execution", body={"content": "执行"},
            parents=[cases.pk], workflow_id=workflow_id,
        )
        return plan, cases, execution

    @staticmethod
    def _report_body(**overrides):
        body = {
            "coverage": 0.92,
            "pass_rate": 0.81,
            "failure_distribution": {"环境": 2, "数据": 1},
            "unclosed_issues": [],
        }
        body.update(overrides)
        return body

    def _publish_report(self, *, parents, body=None, workflow_id="wf-t16"):
        return self._publish(
            stage=REPORT, body=body if body is not None else self._report_body(),
            parents=parents, workflow_id=workflow_id,
        )

    def _score(self, output, *, l0=1.0, l1=0.9, l2=0.9, l3=0.9):
        """给某产出挂一条已完成的分层评测结果（门禁只认这种结果）。"""
        suite = EvaluationSuite.objects.create(
            project=self.project, name=f"s-{uuid.uuid4().hex[:8]}",
            suite_type="regression", task_type=output.task_type,
        )
        case = EvaluationCase.objects.create(
            suite=suite, case_number=1, task_type=output.task_type,
            input_payload={}, split="regression", source_output=output,
        )
        run = EvaluationRun.objects.create(suite=suite, status="completed")
        return EvaluationResult.objects.create(
            run=run, case=case, status="completed",
            l0_score=l0, l1_score=l1, l2_score=l2, l3_score=l3,
        )

    def _gate(self, *, stage, workflow_id="wf-t16", status="pending"):
        gate, _ = WorkflowStageGate.objects.get_or_create(
            project=self.project, workflow_id=workflow_id, stage=stage,
        )
        if gate.status != status:
            gate.status = status
            gate.save(update_fields=["status", "updated_at"])
        return gate


class ReportContractTests(T16BaseTests):
    """契约校验本体：引用完整性与正文四项统计。"""

    def test_valid_report_passes_contract(self):
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])

        result = ReportGateService.validate(report)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(
            sorted(result["upstream"]["covered_stages"]), sorted(UPSTREAM),
        )
        self.assertEqual(result["schema"]["missing_fields"], [])
        self.assertEqual(result["schema"]["stats"]["unclosed_issue_count"], 0)

    def test_missing_upstream_reference_fails(self):
        """只引用方案与用例、漏掉执行 → 必须失败并指名缺的是执行阶段。"""
        plan, cases, _execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk])

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertEqual(result["upstream"]["missing_stages"], ["test_execution"])
        self.assertIn("测试执行", "；".join(result["errors"]))

    def test_reference_to_nonexistent_output_fails(self):
        plan, cases, execution = self._publish_chain()
        ghost = str(uuid.uuid4())
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk, ghost])

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertIn(ghost, result["upstream"]["unresolved_output_ids"])

    def test_cross_project_reference_is_rejected(self):
        plan, cases, execution = self._publish_chain()
        foreign = self.make_output(task_type="test_execution", project=self.other_project)
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk, foreign.pk])

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertIn(str(foreign.pk), result["upstream"]["unresolved_output_ids"])

    def test_reference_to_another_workflow_is_rejected(self):
        """同项目但另一条链路的产出：引用它等于把别条链路的结论挪用过来。"""
        plan, cases, execution = self._publish_chain()
        other = self._publish(
            stage="test_execution", body={"content": "别的链路"}, workflow_id="wf-other",
        )
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk, other.pk])

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertIn(str(other.pk), result["upstream"]["foreign_output_ids"])

    def test_inline_reference_fields_are_accepted(self):
        """正文内联引用字段与协议 parent_output_ids 取并集，两种风格都要能过。"""
        plan, cases, execution = self._publish_chain()
        body = self._report_body(
            plan_output_ids=[str(plan.pk)],
            case_output_ids=[str(cases.pk)],
            execution_output_ids=[str(execution.pk)],
        )
        # 协议里**不带**父产出，全靠正文内联。
        report = self._publish_report(parents=[], body=body)

        result = ReportGateService.validate(report)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["upstream"]["from_body"], [
            str(plan.pk), str(cases.pk), str(execution.pk),
        ])

    def test_unparsable_body_fails(self):
        """正文不是 JSON —— 覆盖率/通过率根本无从校验。

        平台自身的产出正文一定是 ``json.dumps`` 出来的，所以这里必须**绕过协议
        直接改写正文**，才能造出"上游写的不是契约 JSON"这一真实场景
        （例如换了 producer、或人工补录了一份 markdown 报告）。
        """
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        report.content = "# 测试报告\n\n本轮共执行 120 条用例，通过 96 条。"
        report.save(update_fields=["content"])

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertFalse(result["schema"]["parsable"])
        self.assertIn("JSON", "；".join(result["errors"]))

    def test_missing_stat_field_fails(self):
        plan, cases, execution = self._publish_chain()
        body = self._report_body()
        body.pop("coverage")
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk], body=body)

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertEqual(result["schema"]["missing_fields"], ["coverage"])

    def test_wrong_type_stat_fails(self):
        plan, cases, execution = self._publish_chain()
        body = self._report_body(coverage="92%")
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk], body=body)

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertTrue(result["schema"]["type_errors"])

    def test_out_of_range_rate_fails(self):
        plan, cases, execution = self._publish_chain()
        body = self._report_body(pass_rate=1.5)
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk], body=body)

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertIn("pass_rate=1.5", result["schema"]["range_errors"])

    def test_boolean_is_not_a_valid_rate(self):
        """``True`` 是 ``int`` 的子类，不排掉就会被当成覆盖率 1.0 通过。"""
        plan, cases, execution = self._publish_chain()
        body = self._report_body(coverage=True)
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk], body=body)

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertTrue(result["schema"]["type_errors"])

    def test_empty_unclosed_issues_is_explicitly_accepted(self):
        """空列表是**合法**答案：它表示"确实没有未闭环问题"，与缺字段不是一回事。"""
        plan, cases, execution = self._publish_chain()
        body = self._report_body(unclosed_issues=[])
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk], body=body)

        result = ReportGateService.validate(report)

        self.assertTrue(result["ok"], result["errors"])
        self.assertIn("unclosed_issues", result["schema"]["stats"])

    def test_unclosed_issues_are_counted(self):
        plan, cases, execution = self._publish_chain()
        body = self._report_body(unclosed_issues=[
            {"id": "BUG-1", "owner": "张三"}, {"id": "BUG-2", "owner": ""},
        ])
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk], body=body)

        result = ReportGateService.validate(report)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["schema"]["stats"]["unclosed_issue_count"], 2)

    def test_assert_report_ready_raises_with_readable_errors(self):
        plan, _cases, _execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk])

        with self.assertRaises(ReportContractError) as ctx:
            ReportGateService.assert_report_ready(report)

        message = "；".join(ctx.exception.messages)
        self.assertIn("测试用例", message)
        self.assertIn("测试执行", message)


class ReportGateTests(T16BaseTests):
    """门禁层：契约不合法必须失败，且不能被高评分掩盖。"""

    def test_gate_fails_on_broken_report_even_with_full_scores(self):
        """核心语义：文案评分满分 **不能** 救一份引用了不存在产出的报告。"""
        plan, cases, execution = self._publish_chain()
        ghost = str(uuid.uuid4())
        report = self._publish_report(
            parents=[plan.pk, cases.pk, execution.pk, ghost],
        )
        self._score(report, l0=1.0, l1=1.0, l2=1.0, l3=1.0)
        gate = self._gate(stage=REPORT)

        gate = WorkflowGateService.evaluate(gate, actor=self.lead)

        self.assertEqual(gate.status, "failed")
        self.assertEqual(gate.scores, {})
        self.assertIn("不存在的产出 ID", gate.reason)

    def test_gate_fails_when_a_required_stage_is_not_referenced(self):
        plan, cases, _execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk])
        self._score(report, l1=1.0)
        gate = self._gate(stage=REPORT)

        gate = WorkflowGateService.evaluate(gate, actor=self.lead)

        self.assertEqual(gate.status, "failed")
        self.assertIn("测试执行", gate.reason)

    def test_gate_passes_on_valid_report_with_scores(self):
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        self._score(report, l0=1.0, l1=0.95, l2=0.9, l3=0.9)
        gate = self._gate(stage=REPORT)

        gate = WorkflowGateService.evaluate(gate, actor=self.lead)

        self.assertEqual(gate.status, "passed")
        self.assertIn("l1", gate.scores)

    def test_gate_fails_when_scores_below_threshold(self):
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        self._score(report, l1=0.4)
        gate = self._gate(stage=REPORT)

        gate = WorkflowGateService.evaluate(gate, actor=self.lead)

        self.assertEqual(gate.status, "failed")
        self.assertIn("分层评测自动计算", gate.reason)

    def test_non_report_stage_is_not_blocked_by_report_contract(self):
        """报告契约只约束报告阶段；其它阶段不得被顺带判死。"""
        output = self._publish(
            stage="testcase_generation", body={"content": "不是 JSON 契约的正文"},
        )
        self._score(output, l0=1.0, l1=0.9)
        gate = self._gate(stage="testcase_generation")

        gate = WorkflowGateService.evaluate(gate, actor=self.lead)

        self.assertEqual(gate.status, "passed")
        self.assertEqual((gate.detail or {}).get("report_contract"), None)

    def test_register_output_records_contract_without_changing_status(self):
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        gate = WorkflowStageGate.objects.get(
            project=self.project, workflow_id="wf-t16", stage=REPORT,
        )

        self.assertEqual(gate.status, "pending")
        contract = (gate.detail or {}).get("report_contract") or {}
        self.assertTrue(contract.get("ok"))
        self.assertEqual(contract["output_id"], str(report.pk))

    def test_passing_evaluation_keeps_the_contract_evidence(self):
        """契约结论必须在放行后仍然留在门禁上，否则页面会显示"无契约证据"。"""
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        self._score(report, l0=1.0, l1=0.95)
        gate = self._gate(stage=REPORT)

        gate = WorkflowGateService.evaluate(gate, actor=self.lead)
        gate.refresh_from_db()

        self.assertEqual(gate.status, "passed")
        self.assertTrue((gate.detail or {})["report_contract"]["ok"])

    def test_repeated_registration_of_the_same_report_is_idempotent(self):
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        gate = self._gate(stage=REPORT, status="passed")

        WorkflowGateService.register_output(report)
        gate.refresh_from_db()

        self.assertEqual(gate.status, "passed")


class WorkflowEvaluationTests(T16BaseTests):
    """阶段评测 + 端到端评测必须同时产出，且端到端能指出卡在哪一段。"""

    def _complete_chain(self, *, workflow_id="wf-t16", pass_gates=True):
        plan, cases, execution = self._publish_chain(workflow_id=workflow_id)
        report = self._publish_report(
            parents=[plan.pk, cases.pk, execution.pk], workflow_id=workflow_id,
        )
        if pass_gates:
            WorkflowStageGate.objects.filter(
                project=self.project, workflow_id=workflow_id,
            ).update(status="passed")
        return plan, cases, execution, report

    def test_stage_evaluation_reports_scores_and_samples(self):
        plan, _cases, _execution = self._publish_chain()
        self._score(plan, l0=1.0, l1=0.8, l2=0.6, l3=0.4)

        result = WorkflowEvaluationService.evaluate_stage(output=plan)

        self.assertEqual(result["scope"], "stage")
        self.assertEqual(result["stage"], "test_plan_generation")
        self.assertTrue(result["judged"])
        self.assertEqual(result["scores"]["l1"], 0.8)
        self.assertEqual(result["sample_count"], 1)

    def test_stage_evaluation_is_honest_when_there_are_no_results(self):
        plan, _cases, _execution = self._publish_chain()

        result = WorkflowEvaluationService.evaluate_stage(output=plan)

        self.assertFalse(result["judged"])
        self.assertIsNone(result["passed"])
        self.assertEqual(result["scores"], {})

    def test_end_to_end_reports_missing_stage(self):
        plan, cases, execution = self._publish_chain()
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-t16",
        ).update(status="passed")

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t16",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_stages"], [REPORT])
        self.assertEqual(result["coverage"], 0.75)
        # 提示必须点名**是哪个**阶段缺产出（收口阶段按模板不同可能是报告生成，
        # 也可能是问题跟踪），否则存量流程与新流程看到同一句话却缺的是两样东西。
        self.assertIn("尚无报告生成阶段产出", "；".join(result["failures"]))

    def test_end_to_end_fails_when_a_gate_is_still_pending(self):
        plan, cases, execution, report = self._complete_chain(pass_gates=False)
        # 上游放行、报告阶段留待评测。
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-t16", stage__in=UPSTREAM,
        ).update(status="passed")

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t16",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["open_gates"], [REPORT])
        self.assertEqual(result["coverage"], 1.0)

    def test_end_to_end_fails_when_report_contract_broken(self):
        plan, cases, execution = self._publish_chain()
        self._publish_report(parents=[plan.pk, cases.pk])  # 漏执行阶段
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-t16",
        ).update(status="passed")

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t16",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["open_gates"], [])
        self.assertFalse(result["report_contract"]["ok"])
        self.assertIn("契约校验未通过", "；".join(result["failures"]))

    def test_end_to_end_passes_on_complete_chain(self):
        self._complete_chain()

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t16",
        )

        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["open_gates"], [])
        self.assertTrue(result["report_contract"]["ok"])

    def test_end_to_end_reports_version_pins_from_locks(self):
        versions = {stage: self._stage_skill(stage)[1] for stage in STAGES}
        WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-t16", actor=self.lead,
        )
        plan, cases, execution = self._publish_chain()
        self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-t16",
        ).update(status="passed")

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t16",
        )

        pins = {pin["stage"]: pin for pin in result["version_pins"]}
        self.assertEqual(len(pins), 4)
        self.assertTrue(result["passed"], result["failures"])
        for stage in STAGES:
            self.assertEqual(pins[stage]["skill_version_id"], str(versions[stage].pk))
            self.assertEqual(pins[stage]["package_sha256"], versions[stage].package_sha256)
            self.assertTrue(pins[stage]["version_locked"])

    def test_evaluate_and_record_writes_both_scopes_to_the_gate(self):
        plan, cases, execution, report = self._complete_chain()
        self._score(report, l0=1.0, l1=0.9)

        payload = WorkflowEvaluationService.evaluate_and_record(
            project=self.project, workflow_id="wf-t16", stage=REPORT, actor=self.lead,
        )

        self.assertTrue(payload["recorded"])
        self.assertEqual(payload["stage"]["scope"], "stage")
        self.assertEqual(payload["end_to_end"]["scope"], "end_to_end")
        gate = WorkflowStageGate.objects.get(
            project=self.project, workflow_id="wf-t16", stage=REPORT,
        )
        self.assertEqual(gate.detail["evaluation"]["stage"]["stage"], REPORT)
        self.assertTrue(gate.detail["evaluation"]["end_to_end"]["passed"])

    def test_evaluate_and_record_does_not_change_gate_status(self):
        """评测是放行决定的输入，不是决定本身——不得顺手把门禁改成 passed。"""
        self._complete_chain(pass_gates=False)

        WorkflowEvaluationService.evaluate_and_record(
            project=self.project, workflow_id="wf-t16", stage=REPORT, actor=self.lead,
        )

        gate = WorkflowStageGate.objects.get(
            project=self.project, workflow_id="wf-t16", stage=REPORT,
        )
        self.assertEqual(gate.status, "pending")

    def test_evaluate_and_record_without_output_records_unjudged_stage(self):
        payload = WorkflowEvaluationService.evaluate_and_record(
            project=self.project, workflow_id="wf-missing", stage=REPORT, actor=self.lead,
        )

        self.assertFalse(payload["recorded"])
        self.assertFalse(payload["stage"]["judged"])
        # 这条流程连一条留痕都没有，没有依据判断它属于哪个模板，于是按**新链路默认序列**
        # 解释——所以缺的是新四阶段，而不是本文件其他地方用的历史序列。
        # 存量流程只要锁过版本（``start_workflow`` 当年一次性锁四段），就一定会带上
        # ``report_generation`` 这类历史标记，从而被正确识别（见 ``stage_order_for``）。
        self.assertEqual(
            payload["end_to_end"]["missing_stages"], list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )


class ResponsibilityTests(T16BaseTests):
    """下游 Badcase → 责任阶段 + SkillVersion。"""

    def _chain_with_attribution(self, *, at="testcase_generation", state="confirmed"):
        """造 方案 -> 用例 -> 执行 -> 报告 的链条，并在指定阶段挂一条归因。"""
        plan, cases, execution = self._publish_chain()
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        target = {
            "test_plan_generation": plan, "testcase_generation": cases,
            "test_execution": execution, REPORT: report,
        }[at]
        self.make_attribution(
            output=target, category="prompt_error",
            state=state, hypothesis="用例生成缺少边界值指令",
        )
        return plan, cases, execution, report

    def test_confirmed_attribution_locates_upstream_stage(self):
        _plan, _cases, _execution, report = self._chain_with_attribution(
            at="testcase_generation",
        )

        result = ResponsibilityService.locate(report)

        self.assertTrue(result["resolved"])
        self.assertEqual(result["responsible_stage"], "testcase_generation")
        self.assertEqual(result["responsible_stage_label"], "测试用例生成")
        self.assertEqual(result["depth"], 1)

    def test_responsible_skill_version_is_reported(self):
        """责任阶段锁了 Skill 版本时，必须把版本号与包哈希一并给出。"""
        _skill, version = self._stage_skill("testcase_generation")
        plan, cases, execution = self._publish_chain()
        cases.skill_version = version
        cases.skill_package_sha256 = version.package_sha256
        cases.save(update_fields=["skill_version", "skill_package_sha256"])
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        self.make_attribution(output=cases, state="confirmed")

        result = ResponsibilityService.locate(report)

        self.assertEqual(result["responsible_skill_version_id"], str(version.pk))
        self.assertEqual(result["responsible_skill_version"], version.version)
        self.assertEqual(result["responsible_package_sha256"], version.package_sha256)
        self.assertIn(version.version, result["reason"])

    def test_unconfirmed_attribution_is_not_usable(self):
        """只有 proposed 归因 → ``resolved`` 必须为假，且不给责任阶段名。"""
        _plan, _cases, _execution, report = self._chain_with_attribution(
            at="testcase_generation", state="proposed",
        )

        result = ResponsibilityService.locate(report)

        self.assertFalse(result["resolved"])
        self.assertEqual(result["responsible_stage"], "")
        self.assertEqual(result["responsible_skill_version_id"], "")
        self.assertIn("不能据此生成候选", result["reason"])

    def test_current_stage_can_be_responsible(self):
        _plan, _cases, _execution, report = self._chain_with_attribution(at=REPORT)

        result = ResponsibilityService.locate(report)

        self.assertTrue(result["resolved"])
        self.assertEqual(result["responsible_stage"], REPORT)
        self.assertEqual(result["depth"], 0)
        self.assertIn("当前产出", result["reason"])

    def test_chain_is_project_scoped(self):
        """他项目的产出即便被写进引用，也不得混进链路。"""
        plan, cases, execution = self._publish_chain()
        outsider = self.make_output(
            task_type="testcase_generation", project=self.other_project,
        )
        report = self._publish_report(
            parents=[plan.pk, cases.pk, execution.pk, outsider.pk],
        )

        result = ResponsibilityService.locate(report)

        self.assertNotIn(str(outsider.pk), [item["output_id"] for item in result["chain"]])

    def test_locate_refuses_cross_project_output(self):
        from django.core.exceptions import ValidationError

        outsider = self.make_output(task_type="case_review", project=self.other_project)

        with self.assertRaises(ValidationError):
            ResponsibilityService.locate(outsider, project=self.project)

    def test_chain_exposes_versions_for_every_level(self):
        _skill, version = self._stage_skill("test_plan_generation")
        plan, cases, execution = self._publish_chain()
        plan.skill_version = version
        plan.skill_package_sha256 = version.package_sha256
        plan.save(update_fields=["skill_version", "skill_package_sha256"])
        report = self._publish_report(parents=[plan.pk, cases.pk, execution.pk])
        self.make_attribution(output=plan, state="confirmed")

        result = ResponsibilityService.locate(report)

        entry = next(
            item for item in result["chain"] if item["stage"] == "test_plan_generation"
        )
        self.assertEqual(entry["skill_version_id"], str(version.pk))
        self.assertEqual(entry["package_sha256"], version.package_sha256)


class ReportGateAPITests(TestCase):
    """接口层：权限、项目隔离与响应结构。"""

    def setUp(self):
        from django.contrib.auth.models import User

        from projects.models import Project, ProjectMember

        self.lead = User.objects.create_user(username="t16-lead", password="pass")
        self.executor = User.objects.create_user(username="t16-exec", password="pass")
        self.project = Project.objects.create(name="T16 项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.other_project = Project.objects.create(name="T16 他项目", creator=self.lead)
        ProjectMember.objects.create(project=self.other_project, user=self.lead, role="owner")
        self.client = APIClient()
        self.url = "/api/knowledge-evolution/operations/"

    def test_member_can_run_end_to_end_evaluation(self):
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"{self.url}evaluate-workflow/",
            {"project": self.project.pk, "workflow_id": "wf-api", "stage": REPORT},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["data"]
        self.assertEqual(payload["end_to_end"]["workflow_id"], "wf-api")
        # 无留痕 → 按新链路默认序列解释（同 ``test_evaluate_and_record_without_output...``）。
        self.assertEqual(
            payload["end_to_end"]["missing_stages"], list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )

    def test_non_member_cannot_run_evaluation(self):
        from django.contrib.auth.models import User

        outsider = User.objects.create_user(username="t16-outsider", password="pass")
        self.client.force_authenticate(outsider)
        response = self.client.post(
            f"{self.url}evaluate-workflow/",
            {"project": self.project.pk, "workflow_id": "wf-api"}, format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_responsibility_requires_output_id(self):
        self.client.force_authenticate(self.lead)
        response = self.client.get(
            f"{self.url}responsibility/", {"project": self.project.pk},
        )
        self.assertEqual(response.status_code, 400)

    def test_responsibility_rejects_unknown_output(self):
        self.client.force_authenticate(self.lead)
        response = self.client.get(
            f"{self.url}responsibility/",
            {"project": self.project.pk, "output": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_workflow_status_carries_the_report_contract(self):
        """页面与接口必须读同一份契约结论，否则会算出第二种"是否可完成"。"""
        from knowledge_evolution.protocol import ADAPTERS, publish_output

        plan_envelope = ADAPTERS["test_plan_generation"].build(
            project=self.project, user=self.lead, source_id="p1",
            workflow_id="wf-api", input_summary="方案", output={"content": "方案"},
        )
        plan_id = publish_output(plan_envelope)[1]
        report_envelope = ADAPTERS[REPORT].build(
            project=self.project, user=self.lead, source_id="r1",
            workflow_id="wf-api", input_summary="报告",
            output={"coverage": 0.1, "pass_rate": 0.1,
                    "failure_distribution": {}, "unclosed_issues": []},
            parent_output_ids=[plan_id],
        )
        publish_output(report_envelope)

        self.client.force_authenticate(self.lead)
        response = self.client.get(
            f"{self.url}workflow-status/",
            {"project": self.project.pk, "workflow_id": "wf-api"},
        )
        payload = response.json()["data"]
        self.assertFalse(payload["report_contract"]["ok"])
        self.assertFalse(payload["completed"])
        self.assertEqual(
            payload["report_contract"]["upstream"]["missing_stages"],
            ["testcase_generation", "test_execution"],
        )
        report_stage = next(
            item for item in payload["stages"] if item["stage"] == REPORT
        )
        self.assertFalse(report_stage["gate"]["report_contract"]["ok"])
