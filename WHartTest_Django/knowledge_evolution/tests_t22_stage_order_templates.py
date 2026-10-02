"""T22：主链路阶段模板（风险识别 → 用例 → 执行 → 问题跟踪）与「发起流程按阶段选包」。

这一版口径变更（2026-10-02）动了三件容易互相矛盾的事，这个文件把三件事都钉住：

1. **口径真值**：主链路换成新四阶段后，"八类业务能力 / 九项任务类型 / 链路阶段五分区"
   这套加法必须仍然成立。加法一破，金标组织与门禁分区就会静默错位。
2. **存量兼容**：阶段序列按**每条流程自己的留痕**解析。老流程不能被口径变更打成砖——
   它还要能读出阶段、能继续推进、报告契约还要继续生效。
3. **向导的两块后端能力**：``stage_catalog``（按阶段给候选与默认包）与
   ``start_workflow(pins=...)``（按阶段锁定人选定的包）。

刻意不写在 ``tests_t16`` 里：那一组整体针对历史链路的报告契约，
混进新链路会变成"两边都测一半"。
"""
import uuid

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from knowledge_evolution.capability_registry import (
    ALL_TASK_TYPES,
    ALL_WORKFLOW_STAGES,
    BUSINESS_CAPABILITY_STAGES,
    LEGACY_WORKFLOW_STAGES,
    WORKFLOW_STAGES,
    capability_info,
    required_partitions,
)
from knowledge_evolution.models import GenerationOutput
from knowledge_evolution.operations import (
    CLOSING_STAGES,
    DEFAULT_WORKFLOW_STAGE_ORDER,
    LEGACY_WORKFLOW_STAGE_ORDER,
    WorkflowGateService,
)
from knowledge_evolution.protocol import ADAPTERS, publish_output
from knowledge_evolution.report_gates import ReportGateService, WorkflowEvaluationService
from knowledge_evolution.task_binding import SkillBindingRefused
from knowledge_evolution.tests_t09_t13 import TEST_MEDIA_ROOT
from knowledge_evolution.tests_t15 import WorkflowBaseTests
from knowledge_evolution.workflow_models import WorkflowSkillLock, WorkflowStageGate

NEW = list(WORKFLOW_STAGES)          # 风险识别 / 用例 / 执行 / 问题跟踪
OLD = list(LEGACY_WORKFLOW_STAGES)   # 方案 / 用例 / 执行 / 报告（存量）


class ChainTemplateTruthTests(SimpleTestCase):
    """口径真值：加法必须还成立，链路阶段必须还要求五分区。"""

    def test_main_chain_is_the_new_four_stages(self):
        self.assertEqual(
            list(WORKFLOW_STAGES),
            ["risk_identification", "testcase_generation", "test_execution", "issue_tracking"],
        )

    def test_legacy_chain_is_kept_for_existing_flows(self):
        """历史序列不许删：删了存量流程就没有"正确解释"可言。"""
        self.assertEqual(list(LEGACY_WORKFLOW_STAGES), OLD)
        self.assertEqual(set(OLD) - set(NEW), {"test_plan_generation", "report_generation"})
        self.assertEqual(len(ALL_WORKFLOW_STAGES), 6)

    def test_business_capabilities_still_add_up_to_eight(self):
        self.assertEqual(len(BUSINESS_CAPABILITY_STAGES), 8)
        self.assertEqual(len(ALL_TASK_TYPES), 9)
        # 风险识别与问题跟踪从"只作数据源"升为 Skill 型链路阶段之后，
        # 不该再出现在反馈数据源那一类里（否则它们会被算两遍）。
        self.assertEqual(capability_info("risk_identification")["kind"], "skill")
        self.assertEqual(capability_info("issue_tracking")["kind"], "skill")

    def test_chain_stages_require_all_five_partitions(self):
        for stage in NEW:
            self.assertEqual(capability_info(stage)["mode"], "workflow", stage)
            self.assertEqual(len(required_partitions(stage)), 5, stage)

    def test_demoted_stages_are_still_evolvable_single_capabilities(self):
        """降级不等于删除：方案与报告仍是 Skill 型能力，仍能评测与自进化。"""
        for stage in ("test_plan_generation", "report_generation"):
            self.assertEqual(capability_info(stage)["kind"], "skill", stage)
            self.assertEqual(capability_info(stage)["mode"], "single", stage)

    def test_closing_stages_cover_both_templates(self):
        self.assertEqual(CLOSING_STAGES, {"issue_tracking", "report_generation"})


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StageOrderResolutionTests(WorkflowBaseTests):
    """阶段序列按流程自己的留痕解析：新流程用新序列，存量流程用历史序列。"""

    def _gate(self, stage, workflow_id="wf-order", status="pending"):
        return WorkflowStageGate.objects.create(
            project=self.project, workflow_id=workflow_id, stage=stage, status=status,
        )

    def test_unknown_flow_falls_back_to_the_default_order(self):
        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-never-seen"),
            list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )

    def test_flow_with_a_legacy_marker_is_read_as_legacy(self):
        self._gate("report_generation")

        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-order"),
            list(LEGACY_WORKFLOW_STAGE_ORDER),
        )

    def test_flow_with_only_overlapping_stages_uses_the_default_order(self):
        """只留痕了"两套模板都有"的阶段时，没有历史证据 → 按新序列解释。"""
        self._gate("testcase_generation")
        self._gate("test_execution")

        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-order"),
            list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )

    def test_version_lock_alone_is_enough_to_detect_a_legacy_flow(self):
        """存量流程的识别主要靠版本锁：当年 ``start_workflow`` 一次性锁了四段。"""
        lock, _ = WorkflowSkillLock.objects.get_or_create(
            project=self.project, workflow_id="wf-locked", lock_key="stage:test_plan_generation",
            defaults={"scope": "workflow", "stage": "test_plan_generation"},
        )
        self.assertIsNotNone(lock)

        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-locked"),
            list(LEGACY_WORKFLOW_STAGE_ORDER),
        )

    def test_can_enter_follows_the_flow_order_in_both_templates(self):
        # 新链路：风险识别没放行 → 用例阶段进不去。
        self._gate("risk_identification", workflow_id="wf-new")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowGateService.assert_can_enter(self.project.pk, "wf-new", "testcase_generation")
        self.assertIn("risk_identification", str(ctx.exception.detail))

        # 存量链路：同一条规则要作用在方案 → 用例上。
        self._gate("test_plan_generation", workflow_id="wf-old")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowGateService.assert_can_enter(self.project.pk, "wf-old", "testcase_generation")
        self.assertIn("test_plan_generation", str(ctx.exception.detail))

    def test_legacy_flow_can_reach_its_own_first_stage(self):
        """存量流程的第一段是方案阶段——按新序列它会被当成"不在序列里"而跳过校验，
        按流程自己的序列则必须仍然认得它是第一段。"""
        self._gate("test_plan_generation", workflow_id="wf-old-first", status="pending")

        # 第一段不需要看前一段，所以不抛。
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-old-first", "test_plan_generation")
        self.assertIn(
            "test_plan_generation",
            WorkflowGateService.stage_order_for(self.project.pk, "wf-old-first"),
        )

    def test_unknown_stage_never_blocks_a_publish(self):
        """不是链路阶段的 stage 直接放过——否则单能力任务会被门禁拦住。"""
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-any", "case_review")
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-any", "not_a_stage")

    def test_status_lists_the_new_chain_for_a_new_flow(self):
        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-new-status",
        )
        self.assertEqual([item["stage"] for item in status["stages"]], NEW)
        self.assertEqual(status["stages"][0]["state"], "ready")
        self.assertEqual(status["current_stage"], "risk_identification")

    def test_status_lists_the_legacy_chain_for_a_legacy_flow(self):
        output = GenerationOutput.objects.create(
            project=self.project, trace=self.make_trace(task_type="report_generation"),
            task_type="report_generation", task_id="rep-old", content="报告",
            output_hash="repold".ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": "report_generation",
                "workflow_id": "wf-old-status",
            }},
        )
        WorkflowGateService.register_output(output)

        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-old-status",
        )
        self.assertEqual([item["stage"] for item in status["stages"]], OLD)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StageCatalogTests(WorkflowBaseTests):
    """向导第一步的数据源：按阶段给出候选包与默认包。"""

    def _skill(self, *, stage, name=None, activate=True):
        name = name or f"{stage}-pkg"
        skill, version = self.make_skill_version(name=name, version="1.0.0", stage=stage)
        if activate:
            self._activate(version)
        return skill, version

    def _catalog(self):
        return WorkflowGateService.stage_catalog(project=self.project)

    def test_catalog_lists_the_new_chain_in_order(self):
        catalog = self._catalog()
        self.assertEqual(catalog["stage_order"], NEW)
        self.assertEqual([item["stage"] for item in catalog["stages"]], NEW)

    def test_declared_package_becomes_the_stage_default(self):
        skill, version = self._skill(stage="risk_identification")

        stage = self._catalog()["stages"][0]

        self.assertEqual(stage["stage"], "risk_identification")
        self.assertIsNotNone(stage["default"], stage)
        self.assertEqual(stage["default"]["skill_id"], str(skill.pk))
        self.assertEqual(stage["default"]["version"], version.version)
        self.assertEqual(stage["default"]["package_sha256"], version.package_sha256)
        self.assertTrue(stage["default"]["runnable"])

    def test_stage_without_any_declared_package_has_no_default(self):
        """没有包声明这个阶段 → 默认项为空，页面要如实显示，不许拿别的包顶替。"""
        stage = next(
            item for item in self._catalog()["stages"] if item["stage"] == "issue_tracking"
        )
        self.assertIsNone(stage["default"])
        self.assertEqual(stage["declared_skill_ids"], [])

    def test_undeclared_packages_stay_available_as_candidates(self):
        """候选人选包不受 manifest 声明限制：跨声明使用由 ``pins`` 明确表达。"""
        skill, _version = self._skill(stage="test_plan_generation")

        catalog = self._catalog()
        listed = {item["skill_id"]: item for item in catalog["skills"]}

        self.assertIn(str(skill.pk), listed)
        self.assertEqual(listed[str(skill.pk)]["declared_stage"], "test_plan_generation")
        self.assertTrue(listed[str(skill.pk)]["runnable"])

    def test_package_without_active_version_is_listed_but_not_runnable(self):
        skill, _version = self._skill(stage="testcase_generation", activate=False)

        listed = {item["skill_id"]: item for item in self._catalog()["skills"]}[str(skill.pk)]

        self.assertFalse(listed["runnable"])
        self.assertEqual(listed["version"], "")
        # 声明仍然要看得见：候选版本已经声明了阶段，只是还没激活。
        self.assertEqual(listed["declared_stage"], "testcase_generation")

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkflowGateService.stage_catalog(
                project=self.project, stages=["risk_identification", "nope"],
            )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StartWorkflowPinsTests(WorkflowBaseTests):
    """发起流程按阶段选定包：``pins`` 的语义、留痕与拒绝条件。"""

    def test_pins_lock_the_flow_to_the_chosen_packages(self):
        risk_skill, risk_version = self._stage_skill("risk_identification")
        case_skill, case_version = self._stage_skill("testcase_generation")

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-pin", actor=self.lead,
            pins={
                "risk_identification": str(risk_skill.pk),
                "testcase_generation": str(case_skill.pk),
            },
        )

        self.assertEqual(sorted(result["locked_stages"]),
                         ["risk_identification", "testcase_generation"])
        self.assertEqual(sorted(result["unmanaged_stages"]),
                         ["issue_tracking", "test_execution"])
        for stage, version in (
            ("risk_identification", risk_version), ("testcase_generation", case_version),
        ):
            lock = WorkflowSkillLock.objects.get(
                workflow_id="wf-pin", lock_key=f"stage:{stage}",
            )
            self.assertEqual(str(lock.skill_version_id), str(version.pk))
            self.assertTrue(lock.detail.get("pinned"))
            self.assertFalse(lock.detail.get("stage_mismatch"))

    def test_pin_can_cross_a_manifest_declaration_but_leaves_evidence(self):
        """人选了声明另一个阶段的包：允许，但必须留下"跨声明使用"的证据。"""
        skill, version = self._stage_skill("test_plan_generation")

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-cross", actor=self.lead,
            pins={"risk_identification": str(skill.pk)},
        )

        binding = result["bindings"]["risk_identification"]
        self.assertEqual(binding["skill_version_id"], str(version.pk))
        self.assertTrue(binding["stage_mismatch"])
        self.assertEqual(binding["declared_stage"], "test_plan_generation")
        self.assertEqual(result["mismatched_stages"], ["risk_identification"])
        lock = WorkflowSkillLock.objects.get(
            workflow_id="wf-cross", lock_key="stage:risk_identification",
        )
        self.assertTrue(lock.detail["stage_mismatch"])
        self.assertEqual(lock.detail["declared_stage"], "test_plan_generation")

    def test_pinned_package_without_active_version_is_refused(self):
        """跨声明可以放宽，R13（无活跃版本拒绝执行）不放宽。"""
        skill, _version = self.make_skill_version(
            name="not-activated", version="1.0.0", stage="risk_identification",
        )

        with self.assertRaises(SkillBindingRefused):
            WorkflowGateService.start_workflow(
                project=self.project, workflow_id="wf-noactive", actor=self.lead,
                pins={"risk_identification": str(skill.pk)},
            )

    def test_unknown_pin_stage_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkflowGateService.start_workflow(
                project=self.project, workflow_id="wf-badpin", actor=self.lead,
                pins={"not_a_stage": str(uuid.uuid4())},
            )

    def test_start_without_pins_keeps_the_old_behaviour(self):
        """不传 pins 的调用方（历史页面、脚本）行为不变：按声明解析。"""
        self._stage_skill("testcase_generation")

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-nopin", actor=self.lead,
        )

        self.assertEqual(result["locked_stages"], ["testcase_generation"])
        self.assertEqual(result["mismatched_stages"], [])

    def test_pinned_flow_reads_back_as_the_new_template(self):
        skill, _version = self._stage_skill("risk_identification")
        WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-pin-order", actor=self.lead,
            pins={"risk_identification": str(skill.pk)},
        )

        # 锁里只有 risk_identification，没有任何历史标记 → 必须解析成新序列，
        # 而不是因为"只锁了一段"就回落到历史序列。
        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-pin-order"),
            list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class NewChainClosingContractTests(WorkflowBaseTests):
    """新链路的收口是「问题跟踪」：契约按阶段取，两套模板各判各的。"""

    def _publish(self, *, stage, body, parents=None, workflow_id="wf-t22", enforce=False):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead,
            source_id=f"{stage}-{uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output=body, parent_output_ids=list(parents or []),
            extensions={"enforce_quality_gate": enforce},
        )
        return GenerationOutput.objects.get(pk=publish_output(envelope)[1])

    def _new_chain(self, *, workflow_id="wf-t22"):
        risk = self._publish(
            stage="risk_identification", body={"content": "高风险点：鉴权绕过"},
            workflow_id=workflow_id,
        )
        cases = self._publish(
            stage="testcase_generation", body={"content": "用例"}, parents=[risk.pk],
            workflow_id=workflow_id,
        )
        execution = self._publish(
            stage="test_execution", body={"content": "执行"}, parents=[cases.pk],
            workflow_id=workflow_id,
        )
        return risk, cases, execution

    def test_issue_tracking_contract_does_not_demand_coverage(self):
        """问题跟踪是"问题清单 + 闭环状态"：没有覆盖率/通过率这两个概念。

        硬要求只会逼出编造的数字——契约的意义是把编造拦在门外，不是逼人编造。
        """
        risk, cases, execution = self._new_chain()
        closing = self._publish(
            stage="issue_tracking",
            body={"failure_distribution": {"环境": 1}, "unclosed_issues": [{"id": "P1-001"}]},
            parents=[risk.pk, cases.pk, execution.pk],
        )

        result = ReportGateService.validate(closing)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["upstream"]["stage"], "issue_tracking")
        self.assertEqual(
            sorted(result["upstream"]["required_stages"]), sorted(NEW[:3]),
        )
        self.assertIn("unclosed_issues", result["schema"]["required_fields"])
        self.assertNotIn("coverage", result["schema"]["required_fields"])

    def test_issue_tracking_still_requires_the_upstream_references(self):
        risk, cases, _execution = self._new_chain()
        closing = self._publish(
            stage="issue_tracking",
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[risk.pk, cases.pk],
        )

        result = ReportGateService.validate(closing)

        self.assertFalse(result["ok"])
        self.assertEqual(result["upstream"]["missing_stages"], ["test_execution"])
        self.assertIn("测试执行", "；".join(result["errors"]))

    def test_report_generation_contract_is_unchanged_for_existing_flows(self):
        """存量流程的报告契约一分不减：仍要四件套齐全。"""
        plan = self._publish(
            stage="test_plan_generation", body={"content": "方案"}, workflow_id="wf-old-t22",
        )
        cases = self._publish(
            stage="testcase_generation", body={"content": "用例"}, parents=[plan.pk],
            workflow_id="wf-old-t22",
        )
        execution = self._publish(
            stage="test_execution", body={"content": "执行"}, parents=[cases.pk],
            workflow_id="wf-old-t22",
        )
        report = self._publish(
            stage="report_generation",
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[plan.pk, cases.pk, execution.pk], workflow_id="wf-old-t22",
        )

        result = ReportGateService.validate(report)

        self.assertFalse(result["ok"])
        self.assertEqual(sorted(result["schema"]["missing_fields"]), ["coverage", "pass_rate"])
        self.assertEqual(result["upstream"]["required_stages"], OLD[:3])

    def test_confirmed_contract_then_gate_can_pass_the_closing_stage(self):
        risk, cases, execution = self._new_chain()
        closing = self._publish(
            stage="issue_tracking",
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[risk.pk, cases.pk, execution.pk],
        )
        self.assertTrue(ReportGateService.validate(closing)["ok"])

        gate = WorkflowGateService.register_output(closing)
        self.assertEqual(gate.stage, "issue_tracking")
        # 登记只摆证据，不改状态——放行与否由 evaluate 决定。
        self.assertEqual(gate.status, "pending")
        self.assertTrue((gate.detail or {}).get("report_contract")["ok"])

    def test_broken_closing_contract_fails_the_gate_on_evaluate(self):
        risk, cases, execution = self._new_chain()
        closing = self._publish(
            stage="issue_tracking",
            body={"unclosed_issues": []},  # 漏 failure_distribution
            parents=[risk.pk, cases.pk, execution.pk],
        )
        gate = WorkflowGateService.register_output(closing)

        WorkflowGateService.evaluate(gate, actor=self.lead)
        gate.refresh_from_db()

        self.assertEqual(gate.status, "failed")
        self.assertIn("契约校验未通过", gate.reason)

    def test_end_to_end_evaluation_uses_the_new_template(self):
        risk, cases, execution = self._new_chain()
        closing = self._publish(
            stage="issue_tracking",
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[risk.pk, cases.pk, execution.pk],
        )
        WorkflowGateService.register_output(closing)
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-t22",
        ).update(status="passed")

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t22",
        )

        self.assertEqual(result["stage_order"], NEW)
        self.assertEqual(result["coverage"], 1.0)
        self.assertTrue(result["report_contract"]["ok"], result["failures"])
        self.assertTrue(result["passed"], result["failures"])

    def test_evaluate_stage_reads_the_closing_contract_for_the_new_chain(self):
        risk, cases, execution = self._new_chain()
        closing = self._publish(
            stage="issue_tracking",
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[risk.pk, cases.pk, execution.pk],
        )

        payload = WorkflowEvaluationService.evaluate_stage(output=closing, actor=self.lead)

        self.assertEqual(payload["stage"], "issue_tracking")
        self.assertTrue(payload["report_contract"]["ok"], payload)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StageCatalogAPITests(TestCase):
    """接口层：向导数据源可读、要项目成员、pins 走接口也生效。"""

    def setUp(self):
        from django.contrib.auth.models import User

        from projects.models import Project, ProjectMember

        self.lead = User.objects.create_user(username="t22-lead", password="pass")
        self.executor = User.objects.create_user(username="t22-exec", password="pass")
        self.project = Project.objects.create(name="T22 项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.client = APIClient()
        self.url = "/api/knowledge-evolution/operations/"

    def test_member_can_read_the_stage_catalog(self):
        self.client.force_authenticate(self.executor)
        response = self.client.get(
            f"{self.url}workflow-stage-catalog/", {"project": self.project.pk},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["data"]
        self.assertEqual(payload["stage_order"], NEW)
        self.assertEqual(len(payload["stages"]), 4)

    def test_non_member_cannot_read_the_stage_catalog(self):
        from django.contrib.auth.models import User

        outsider = User.objects.create_user(username="t22-outsider", password="pass")
        self.client.force_authenticate(outsider)
        response = self.client.get(
            f"{self.url}workflow-stage-catalog/", {"project": self.project.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_malformed_pins_are_rejected(self):
        self.client.force_authenticate(self.lead)
        response = self.client.post(
            f"{self.url}start-workflow/",
            {"project": self.project.pk, "workflow_id": "wf-api", "pins": ["risk_identification"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_executor_cannot_start_a_workflow_with_pins(self):
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"{self.url}start-workflow/",
            {"project": self.project.pk, "workflow_id": "wf-api", "pins": {}},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
