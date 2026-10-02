"""T22：阶段模板双轨（当前：方案 → 用例 → 执行 → 报告）与「发起流程按阶段选包」。

三件事容易互相矛盾，这个文件把它们都钉住：

1. **口径真值**：主链路是哪四个阶段，以及"八类业务能力 / 九项任务类型 / 链路阶段五分区"
   这套加法是否仍然成立。加法一破，金标组织与门禁分区就会静默错位。
2. **存量兼容**：阶段序列按**每条流程自己的留痕**解析。用过历史模板的流程不能被口径变更
   打成砖——它还要能读出阶段、能继续推进、它的收口契约还要继续生效。
3. **向导的两块后端能力**：``stage_catalog``（按阶段给候选与默认包）与
   ``start_workflow(pins=...)``（按阶段锁定人选定的包）。

⚠️ 本文件**只在两条口径真值用例里写死阶段字面量**，其余一律从 ``capability_registry``
派生（``CURRENT`` / ``LEGACY`` / ``CURRENT_ONLY`` / ``LEGACY_ONLY``）。这不是洁癖：
2026-10-02 主链路口径翻错方向时，本文件到处硬编码"风险识别是新链路第一段"，
结果**改错了方向、整套测试仍然全绿**。模板可以换，每条用例测的"关系"不能跟着换。

刻意不写在 ``tests_t16`` 里：那一组整体针对报告契约的历史细节，
混进模板话题会变成"两边都测一半"。
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
from knowledge_evolution.evolution import DEFAULT_STAGE_WEIGHTS
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

CURRENT = list(WORKFLOW_STAGES)         # 当前主链路：方案 → 用例 → 执行 → 报告
LEGACY = list(LEGACY_WORKFLOW_STAGES)   # 历史模板：风险 → 用例 → 执行 → 问题

#: 只属于当前模板 / 只属于历史模板的阶段。构造"这条流程用过哪套模板"的证据必须用它：
#: 两套模板共用的 ``testcase_generation`` / ``test_execution`` 证明不了任何一边。
CURRENT_ONLY = [stage for stage in CURRENT if stage not in LEGACY]
LEGACY_ONLY = [stage for stage in LEGACY if stage not in CURRENT]


class ChainTemplateTruthTests(SimpleTestCase):
    """口径真值：加法必须还成立，链路阶段必须还要求五分区。"""

    def test_main_chain_is_the_current_four_stages(self):
        self.assertEqual(
            list(WORKFLOW_STAGES),
            [
                "test_plan_generation", "testcase_generation",
                "test_execution", "report_generation",
            ],
        )
        self.assertEqual(CURRENT_ONLY, ["test_plan_generation", "report_generation"])
        self.assertEqual(LEGACY_ONLY, ["risk_identification", "issue_tracking"])

    def test_legacy_chain_is_kept_for_existing_flows(self):
        """历史序列不许删：删了那批流程就没有"正确解释"可言。"""
        self.assertEqual(
            list(LEGACY_WORKFLOW_STAGES),
            [
                "risk_identification", "testcase_generation",
                "test_execution", "issue_tracking",
            ],
        )
        self.assertEqual(len(ALL_WORKFLOW_STAGES), 6)
        # 两套模板共用中间两段，这条不变量是整套"按留痕判模板"可行的前提。
        self.assertEqual(set(CURRENT) & set(LEGACY), {"testcase_generation", "test_execution"})

    def test_business_capabilities_still_add_up_to_eight(self):
        self.assertEqual(len(BUSINESS_CAPABILITY_STAGES), 8)
        self.assertEqual(len(ALL_TASK_TYPES), 9)
        # 风险识别与问题跟踪是 Skill 型**单次**能力：不再占链路阶段位，
        # 但也不该退回"只作数据源、不可进化"那一类——它们有包、能评测、能自进化。
        for stage in LEGACY_ONLY:
            self.assertEqual(capability_info(stage)["kind"], "skill", stage)
        self.assertNotIn("risk_identification", CURRENT)
        self.assertNotIn("issue_tracking", CURRENT)

    def test_chain_stages_require_all_five_partitions(self):
        for stage in CURRENT:
            self.assertEqual(capability_info(stage)["mode"], "workflow", stage)
            self.assertEqual(len(required_partitions(stage)), 5, stage)

    def test_off_chain_skills_stay_evolvable_at_three_partitions(self):
        """不在链路上 ≠ 删除：仍是 Skill 型能力，只是分区要求从五降到三。"""
        for stage in LEGACY_ONLY:
            self.assertEqual(capability_info(stage)["kind"], "skill", stage)
            self.assertEqual(capability_info(stage)["mode"], "single", stage)
            self.assertEqual(len(required_partitions(stage)), 3, stage)

    def test_default_stage_weights_cover_every_chain_stage(self):
        """权重表漏阶段 → 该阶段掉进 ``1.0/len(stages)`` 兜底，与同链路其它阶段口径不一致。"""
        for stage in ALL_WORKFLOW_STAGES:
            self.assertIn(stage, DEFAULT_STAGE_WEIGHTS, stage)

    def test_skills_manifest_valid_stages_match_the_registry(self):
        """``skills/validation.py::VALID_STAGES`` 是**只读副本**，必须与真值逐字一致。

        ``skills`` 不能反向 import 本模块（``knowledge_evolution`` 依赖 ``skills.models``，
        会成环），所以用这条断言代替 import。副本过期时的症状很难查：
        写进 manifest 的合法阶段会被拒，而报错只说"未知阶段"。
        """
        from skills.validation import VALID_STAGES

        self.assertEqual(set(VALID_STAGES), set(ALL_TASK_TYPES))

    def test_closing_stages_cover_both_templates(self):
        self.assertEqual(CLOSING_STAGES, {CURRENT[-1], LEGACY[-1]})
        self.assertEqual(CLOSING_STAGES, {"report_generation", "issue_tracking"})


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StageOrderResolutionTests(WorkflowBaseTests):
    """阶段序列按流程自己的留痕解析：当前模板一套，历史模板一套。"""

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
        self._gate(LEGACY_ONLY[0])

        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-order"),
            list(LEGACY_WORKFLOW_STAGE_ORDER),
        )

    def test_flow_with_only_overlapping_stages_uses_the_default_order(self):
        """只留痕了"两套模板都有"的阶段时，没有历史证据 → 按当前序列解释。"""
        self._gate("testcase_generation")
        self._gate("test_execution")

        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-order"),
            list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )

    def test_version_lock_alone_is_enough_to_detect_a_legacy_flow(self):
        """历史流程的识别主要靠版本锁：当年 ``start_workflow`` 一次性锁了四段。"""
        lock, _ = WorkflowSkillLock.objects.get_or_create(
            project=self.project, workflow_id="wf-locked",
            lock_key=f"stage:{LEGACY_ONLY[0]}",
            defaults={"scope": "workflow", "stage": LEGACY_ONLY[0]},
        )
        self.assertIsNotNone(lock)

        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-locked"),
            list(LEGACY_WORKFLOW_STAGE_ORDER),
        )

    def test_can_enter_follows_the_flow_order_in_both_templates(self):
        # 当前链路：方案阶段没放行 → 用例阶段进不去。
        self._gate(CURRENT[0], workflow_id="wf-new")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowGateService.assert_can_enter(self.project.pk, "wf-new", "testcase_generation")
        self.assertIn(CURRENT[0], str(ctx.exception.detail))

        # 历史模板：同一条规则要作用在风险识别 → 用例上。
        self._gate(LEGACY[0], workflow_id="wf-old")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowGateService.assert_can_enter(self.project.pk, "wf-old", "testcase_generation")
        self.assertIn(LEGACY[0], str(ctx.exception.detail))

    def test_legacy_flow_can_reach_its_own_first_stage(self):
        """历史流程的第一段是风险识别——按当前序列它会被当成"不在序列里"而跳过校验，
        按流程自己的序列则必须仍然认得它是第一段。"""
        self._gate(LEGACY[0], workflow_id="wf-old-first", status="pending")

        # 第一段不需要看前一段，所以不抛。
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-old-first", LEGACY[0])
        self.assertIn(
            LEGACY[0],
            WorkflowGateService.stage_order_for(self.project.pk, "wf-old-first"),
        )

    def test_unknown_stage_never_blocks_a_publish(self):
        """不是链路阶段的 stage 直接放过——否则单能力任务会被门禁拦住。"""
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-any", "case_review")
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-any", "not_a_stage")

    def test_status_lists_the_current_chain_for_a_new_flow(self):
        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-new-status",
        )
        self.assertEqual([item["stage"] for item in status["stages"]], CURRENT)
        self.assertEqual(status["stages"][0]["state"], "ready")
        self.assertEqual(status["current_stage"], CURRENT[0])

    def test_status_lists_the_legacy_chain_for_a_legacy_flow(self):
        output = GenerationOutput.objects.create(
            project=self.project, trace=self.make_trace(task_type=LEGACY_ONLY[0]),
            task_type=LEGACY_ONLY[0], task_id="risk-old", content="高风险点：鉴权绕过",
            output_hash="riskold".ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": LEGACY_ONLY[0],
                "workflow_id": "wf-old-status",
            }},
        )
        WorkflowGateService.register_output(output)

        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-old-status",
        )
        self.assertEqual([item["stage"] for item in status["stages"]], LEGACY)


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

    def test_catalog_lists_the_current_chain_in_order(self):
        catalog = self._catalog()
        self.assertEqual(catalog["stage_order"], CURRENT)
        self.assertEqual([item["stage"] for item in catalog["stages"]], CURRENT)

    def test_declared_package_becomes_the_stage_default(self):
        skill, version = self._skill(stage=CURRENT[0])

        stage = self._catalog()["stages"][0]

        self.assertEqual(stage["stage"], CURRENT[0])
        self.assertIsNotNone(stage["default"], stage)
        self.assertEqual(stage["default"]["skill_id"], str(skill.pk))
        self.assertEqual(stage["default"]["version"], version.version)
        self.assertEqual(stage["default"]["package_sha256"], version.package_sha256)
        self.assertTrue(stage["default"]["runnable"])

    def test_stage_without_any_declared_package_has_no_default(self):
        """没有包声明这个阶段 → 默认项为空，页面要如实显示，不许拿别的包顶替。"""
        stage = next(
            item for item in self._catalog()["stages"] if item["stage"] == CURRENT[-1]
        )
        self.assertIsNone(stage["default"])
        self.assertEqual(stage["declared_skill_ids"], [])

    def test_undeclared_packages_stay_available_as_candidates(self):
        """候选人选包不受 manifest 声明限制：跨声明使用由 ``pins`` 明确表达。"""
        skill, _version = self._skill(stage="case_review")

        listed = {item["skill_id"]: item for item in self._catalog()["skills"]}[str(skill.pk)]

        self.assertEqual(listed["declared_stage"], "case_review")
        self.assertTrue(listed["runnable"])

    def test_package_without_activation_is_runnable(self):
        """未激活的包也要算"可运行"：否则向导会把它判成锁不上，用户选不了。

        可用性不依赖激活（激活只是钉版手段），所以这类包必须显示版本与包哈希——
        页面承诺"选它就能锁上"，判据与运行时同源才做得到。
        """
        skill, version = self._skill(stage="testcase_generation", activate=False)

        listed = {item["skill_id"]: item for item in self._catalog()["skills"]}[str(skill.pk)]

        self.assertTrue(listed["runnable"])
        self.assertEqual(listed["version"], version.version)
        self.assertEqual(listed["package_sha256"], version.package_sha256)
        self.assertEqual(listed["declared_stage"], "testcase_generation")

    def test_quarantined_package_is_listed_but_not_runnable(self):
        """隔离是唯一的"在库却不能用"：页面要看得见它、且明确标成不可运行。"""
        from skills.versions import SkillVersionService

        skill, version = self._skill(stage="testcase_generation", activate=False)
        SkillVersionService.quarantine(version, actor=self.lead, reason="安全事件")

        listed = {item["skill_id"]: item for item in self._catalog()["skills"]}[str(skill.pk)]

        self.assertFalse(listed["runnable"])
        self.assertEqual(listed["version"], "")
        # 声明仍然要看得见：候选版本已经声明了阶段。
        self.assertEqual(listed["declared_stage"], "testcase_generation")

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkflowGateService.stage_catalog(
                project=self.project, stages=[CURRENT[0], "nope"],
            )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StartWorkflowPinsTests(WorkflowBaseTests):
    """发起流程按阶段选定包：``pins`` 的语义、留痕与拒绝条件。"""

    def test_pins_lock_the_flow_to_the_chosen_packages(self):
        first_skill, first_version = self._stage_skill(CURRENT[0])
        second_skill, second_version = self._stage_skill(CURRENT[1])

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-pin", actor=self.lead,
            pins={CURRENT[0]: str(first_skill.pk), CURRENT[1]: str(second_skill.pk)},
        )

        self.assertEqual(sorted(result["locked_stages"]), sorted([CURRENT[0], CURRENT[1]]))
        self.assertEqual(sorted(result["unmanaged_stages"]), sorted(CURRENT[2:]))
        for stage, version in (
            (CURRENT[0], first_version), (CURRENT[1], second_version),
        ):
            lock = WorkflowSkillLock.objects.get(
                workflow_id="wf-pin", lock_key=f"stage:{stage}",
            )
            self.assertEqual(str(lock.skill_version_id), str(version.pk))
            self.assertTrue(lock.detail.get("pinned"))
            self.assertFalse(lock.detail.get("stage_mismatch"))

    def test_pin_can_cross_a_manifest_declaration_but_leaves_evidence(self):
        """人选了声明另一个阶段的包：允许，但必须留下"跨声明使用"的证据。"""
        skill, version = self._stage_skill(LEGACY_ONLY[0])

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-cross", actor=self.lead,
            pins={CURRENT[0]: str(skill.pk)},
        )

        binding = result["bindings"][CURRENT[0]]
        self.assertEqual(binding["skill_version_id"], str(version.pk))
        self.assertTrue(binding["stage_mismatch"])
        self.assertEqual(binding["declared_stage"], LEGACY_ONLY[0])
        self.assertEqual(result["mismatched_stages"], [CURRENT[0]])
        lock = WorkflowSkillLock.objects.get(
            workflow_id="wf-cross", lock_key=f"stage:{CURRENT[0]}",
        )
        self.assertTrue(lock.detail["stage_mismatch"])
        self.assertEqual(lock.detail["declared_stage"], LEGACY_ONLY[0])

    def test_pinned_package_needs_no_activation(self):
        """人显式指定的包，未激活也能锁上——激活不是可用的前提。"""
        skill, version = self.make_skill_version(
            name="not-activated", version="1.0.0", stage=CURRENT[0],
        )

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-noactive", actor=self.lead,
            pins={CURRENT[0]: str(skill.pk)},
        )

        binding = result["bindings"][CURRENT[0]]
        self.assertEqual(binding["skill_version_id"], str(version.pk))
        self.assertTrue(binding["pinned"])

    def test_pinned_quarantined_package_is_refused(self):
        """显式指定可以放宽声明与激活要求，但"包不可用"这条不放宽。"""
        from skills.versions import SkillVersionService

        skill, version = self.make_skill_version(
            name="quarantined-pin", version="1.0.0", stage=CURRENT[0],
        )
        SkillVersionService.quarantine(version, actor=self.lead, reason="安全事件")

        with self.assertRaises(SkillBindingRefused):
            WorkflowGateService.start_workflow(
                project=self.project, workflow_id="wf-quarantined-pin", actor=self.lead,
                pins={CURRENT[0]: str(skill.pk)},
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

    def test_pinned_flow_reads_back_as_the_current_template(self):
        skill, _version = self._stage_skill(CURRENT[0])
        WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-pin-order", actor=self.lead,
            pins={CURRENT[0]: str(skill.pk)},
        )

        # 锁里只有当前模板独有的那一段，没有任何历史标记 → 必须解析成当前序列，
        # 而不是因为"只锁了一段"就回落到历史序列。
        self.assertEqual(
            WorkflowGateService.stage_order_for(self.project.pk, "wf-pin-order"),
            list(DEFAULT_WORKFLOW_STAGE_ORDER),
        )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class CurrentChainClosingContractTests(WorkflowBaseTests):
    """当前链路的收口是「报告产出」：四件套齐全才算过。"""

    def _publish(self, *, stage, body, parents=None, workflow_id="wf-t22", enforce=False):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead,
            source_id=f"{stage}-{uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output=body, parent_output_ids=list(parents or []),
            extensions={"enforce_quality_gate": enforce},
        )
        return GenerationOutput.objects.get(pk=publish_output(envelope)[1])

    def _upstream(self, *, workflow_id="wf-t22"):
        """按当前链路的顺序发布收口阶段之前的三段，返回 ``[产出]``（顺序同链路）。"""
        outputs = []
        parents: list = []
        for stage in CURRENT[:-1]:
            output = self._publish(
                stage=stage, body={"content": f"{stage} 产出"},
                parents=parents, workflow_id=workflow_id,
            )
            outputs.append(output)
            parents = [output.pk]
        return outputs

    def _full_body(self):
        return {
            "coverage": 0.9, "pass_rate": 0.95,
            "failure_distribution": {"环境": 1},
            "unclosed_issues": [{"id": "P1-001"}],
        }

    def test_closing_stage_requires_the_full_four_stats(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=CURRENT[-1],
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[item.pk for item in upstream],
        )

        result = ReportGateService.validate(closing)

        self.assertFalse(result["ok"])
        self.assertEqual(sorted(result["schema"]["missing_fields"]), ["coverage", "pass_rate"])
        self.assertEqual(result["upstream"]["stage"], CURRENT[-1])
        self.assertEqual(result["upstream"]["required_stages"], CURRENT[:3])

    def test_closing_stage_still_requires_the_upstream_references(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=CURRENT[-1], body=self._full_body(),
            parents=[item.pk for item in upstream[:2]],  # 少引用一段
        )

        result = ReportGateService.validate(closing)

        self.assertFalse(result["ok"])
        self.assertEqual(result["upstream"]["missing_stages"], [CURRENT[2]])

    def test_confirmed_contract_then_gate_can_pass_the_closing_stage(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=CURRENT[-1], body=self._full_body(),
            parents=[item.pk for item in upstream],
        )
        self.assertTrue(ReportGateService.validate(closing)["ok"], closing)

        gate = WorkflowGateService.register_output(closing)
        self.assertEqual(gate.stage, CURRENT[-1])
        # 登记只摆证据，不改状态——放行与否由 evaluate 决定。
        self.assertEqual(gate.status, "pending")
        self.assertTrue((gate.detail or {}).get("report_contract")["ok"])

    def test_broken_closing_contract_fails_the_gate_on_evaluate(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=CURRENT[-1],
            body={"unclosed_issues": []},  # 漏 failure_distribution 与两项比率
            parents=[item.pk for item in upstream],
        )
        gate = WorkflowGateService.register_output(closing)

        WorkflowGateService.evaluate(gate, actor=self.lead)
        gate.refresh_from_db()

        self.assertEqual(gate.status, "failed")
        self.assertIn("契约校验未通过", gate.reason)

    def test_end_to_end_evaluation_uses_the_current_template(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=CURRENT[-1], body=self._full_body(),
            parents=[item.pk for item in upstream],
        )
        WorkflowGateService.register_output(closing)
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="wf-t22",
        ).update(status="passed")

        result = WorkflowEvaluationService.evaluate_end_to_end(
            project=self.project, workflow_id="wf-t22",
        )

        self.assertEqual(result["stage_order"], CURRENT)
        self.assertEqual(result["coverage"], 1.0)
        self.assertTrue(result["report_contract"]["ok"], result["failures"])
        self.assertTrue(result["passed"], result["failures"])

    def test_evaluate_stage_reads_the_closing_contract_for_the_current_chain(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=CURRENT[-1], body=self._full_body(),
            parents=[item.pk for item in upstream],
        )

        payload = WorkflowEvaluationService.evaluate_stage(output=closing, actor=self.lead)

        self.assertEqual(payload["stage"], CURRENT[-1])
        self.assertTrue(payload["report_contract"]["ok"], payload)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LegacyChainClosingContractTests(WorkflowBaseTests):
    """历史模板的收口是「问题跟踪」：只要问题分布与未闭环清单，不要覆盖率与通过率。

    硬要求覆盖率只会逼出编造的数字——契约的意义是把编造拦在门外，不是逼人编造。
    """

    def _publish(self, *, stage, body, parents=None, workflow_id="wf-legacy-t22"):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead,
            source_id=f"{stage}-{uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output=body, parent_output_ids=list(parents or []),
            extensions={"enforce_quality_gate": False},
        )
        return GenerationOutput.objects.get(pk=publish_output(envelope)[1])

    def _upstream(self):
        outputs = []
        parents: list = []
        for stage in LEGACY[:-1]:
            output = self._publish(
                stage=stage, body={"content": f"{stage} 产出"}, parents=parents,
            )
            outputs.append(output)
            parents = [output.pk]
        return outputs

    def test_legacy_closing_contract_does_not_demand_coverage(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=LEGACY[-1],
            body={"failure_distribution": {"环境": 1}, "unclosed_issues": [{"id": "P1-001"}]},
            parents=[item.pk for item in upstream],
        )

        result = ReportGateService.validate(closing)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["upstream"]["stage"], LEGACY[-1])
        self.assertEqual(sorted(result["upstream"]["required_stages"]), sorted(LEGACY[:3]))
        self.assertIn("unclosed_issues", result["schema"]["required_fields"])
        self.assertNotIn("coverage", result["schema"]["required_fields"])

    def test_legacy_closing_still_requires_the_upstream_references(self):
        upstream = self._upstream()
        closing = self._publish(
            stage=LEGACY[-1],
            body={"failure_distribution": {}, "unclosed_issues": []},
            parents=[item.pk for item in upstream[:2]],
        )

        result = ReportGateService.validate(closing)

        self.assertFalse(result["ok"])
        self.assertEqual(result["upstream"]["missing_stages"], [LEGACY[2]])


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
        self.assertEqual(payload["stage_order"], CURRENT)
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
            {"project": self.project.pk, "workflow_id": "wf-api", "pins": [CURRENT[0]]},
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
