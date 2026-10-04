"""T15：四阶段 Skill 质量门禁主链路。

每个用例都对应一条会在真实流水线里出问题的路：

- 四个阶段的版本必须在**启动时**一次锁完，否则一条链路有可能横跨四份包；
- 项目没登记某阶段的 Skill 时要如实报告"无版本溯源"，而不是假装锁上了；
- 登记了但**包不可用**（被隔离/驳回/停用）时必须在启动点就拒绝，
  而不是跑两小时之后在报告阶段才炸；"没激活"不是拒绝理由；
- 门禁没通过不能进下一阶段；
- **门禁通过之后重复登记同一产出不能把门禁打回待测评**（否则重跑一次就再也过不去）；
- 链路中途激活新版本，历史流水线在页面上仍必须显示它当时用的旧包；
- 负责人放行必须留痕，且必须写原因。
"""
from __future__ import annotations

import tempfile

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.models import GenerationOutput
from knowledge_evolution.operations import WORKFLOW_STAGE_ORDER, WorkflowGateService
from knowledge_evolution.protocol import ADAPTERS, publish_output
from knowledge_evolution.task_binding import SkillBindingRefused
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.workflow_models import WorkflowSkillLock, WorkflowStageGate

STAGES = list(WORKFLOW_STAGE_ORDER)


class WorkflowBaseTests(SkillHubBaseTests):
    def _activate(self, version):
        from knowledge_evolution.capabilities import CapabilityReleaseService

        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")
        version.refresh_from_db()
        return version

    def _stage_skill(self, stage, *, version="1.0.0"):
        skill, skill_version = self.make_skill_version(
            name=f"{stage}-skill", version=version, stage=stage,
        )
        self._activate(skill_version)
        return skill, skill_version

    def _publish_stage(self, *, stage, workflow_id="wf-1", enforce=True, content="产出正文"):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead, source_id=f"{stage}-src",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output={"content": content},
            extensions={"enforce_quality_gate": enforce},
        )
        return publish_output(envelope)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StartWorkflowTests(WorkflowBaseTests):
    """启动即锁定四阶段版本。"""

    def test_start_workflow_locks_every_registered_stage(self):
        versions = {stage: self._stage_skill(stage)[1] for stage in STAGES}

        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-lock", actor=self.lead,
        )

        self.assertEqual(sorted(result["locked_stages"]), sorted(STAGES))
        self.assertEqual(result["unmanaged_stages"], [])
        for stage in STAGES:
            lock = WorkflowSkillLock.objects.get(workflow_id="wf-lock", lock_key=f"stage:{stage}")
            self.assertEqual(str(lock.skill_version_id), str(versions[stage].pk))
            self.assertEqual(lock.scope, "workflow")
            self.assertEqual(lock.package_sha256, versions[stage].package_sha256)

    def test_projects_can_lock_different_versions_from_public_skill_hub(self):
        """公开仓库共享版本；项目隔离的是选择和流程锁。"""
        from skills.runtime import SkillRuntimeResolver

        stage = STAGES[0]
        _skill, version_v1 = self.make_skill_version(
            name="public-stage-skill", version="1.0.0", stage=stage,
        )
        _skill, version_v2 = self.make_skill_version(
            name="public-stage-skill", version="2.0.0", stage=stage,
        )

        WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-project-a", actor=self.lead,
            stages=[stage], pins={stage: str(version_v1.pk)},
        )
        WorkflowGateService.start_workflow(
            project=self.other_project, workflow_id="wf-project-b", actor=self.lead,
            stages=[stage], pins={stage: str(version_v2.pk)},
        )

        lock_a = WorkflowSkillLock.objects.get(
            project=self.project, workflow_id="wf-project-a", stage=stage,
        )
        lock_b = WorkflowSkillLock.objects.get(
            project=self.other_project, workflow_id="wf-project-b", stage=stage,
        )
        self.assertEqual(lock_a.skill_version_id, version_v1.pk)
        self.assertEqual(lock_b.skill_version_id, version_v2.pk)
        self.assertEqual(SkillRuntimeResolver.resolve_locked(lock_b).pk, version_v2.pk)

        # 商店出现更新版本，也不能让已启动流程漂移。
        self.make_skill_version(name="public-stage-skill", version="3.0.0", stage=stage)
        repeated = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-project-a", actor=self.lead,
            stages=[stage], pins={stage: str(version_v2.pk)},
        )
        self.assertEqual(
            repeated["bindings"][stage]["skill_version_id"], str(version_v1.pk),
        )

    def test_unregistered_stages_are_reported_not_faked(self):
        """项目一个阶段的 Skill 都没登记 → 必须如实报"未纳入版本管理"。"""
        result = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-empty", actor=self.lead,
        )

        self.assertEqual(result["locked_stages"], [])
        self.assertEqual(sorted(result["unmanaged_stages"]), sorted(STAGES))
        self.assertFalse(WorkflowSkillLock.objects.filter(workflow_id="wf-empty").exists())

    def test_unactivated_stage_skill_is_locked_without_activation(self):
        """登记了但没人激活 → 照样锁上，不再拒绝启动。

        "上传即可用"落在四阶段上就是这个意思：一条链路的门禁不该被
        "还没人点激活"卡死。锁里的版本仍是那一份不可变包，溯源不受影响。
        """
        _skill, first = self._stage_skill("test_plan_generation")
        _s, second = self.make_skill_version(
            name="testcase-skill", stage="testcase_generation",
        )

        WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-unactivated", actor=self.lead,
        )

        for stage, version in (
            ("test_plan_generation", first), ("testcase_generation", second),
        ):
            lock = WorkflowSkillLock.objects.get(
                workflow_id="wf-unactivated", lock_key=f"stage:{stage}",
            )
            self.assertEqual(str(lock.skill_version_id), str(version.pk))

    def test_quarantined_stage_skill_refuses_start(self):
        """包真的不可用（被隔离）时必须在启动点就拦住，而不是跑到一半才炸。"""
        from skills.versions import SkillVersionService

        _skill, version = self.make_skill_version(
            name="testcase-skill", stage="testcase_generation",
        )
        SkillVersionService.quarantine(version, actor=self.lead, reason="安全事件")

        with self.assertRaises(SkillBindingRefused):
            WorkflowGateService.start_workflow(
                project=self.project, workflow_id="wf-blocked", actor=self.lead,
            )

    def test_start_workflow_is_idempotent(self):
        for stage in STAGES:
            self._stage_skill(stage)

        first = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-twice", actor=self.lead,
        )
        second = WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-twice", actor=self.lead,
        )

        self.assertEqual(
            {stage: item["lock_id"] for stage, item in first["bindings"].items()},
            {stage: item["lock_id"] for stage, item in second["bindings"].items()},
        )
        self.assertEqual(WorkflowSkillLock.objects.filter(workflow_id="wf-twice").count(), 4)

    def test_unknown_stage_is_rejected(self):
        from rest_framework.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            WorkflowGateService.start_workflow(
                project=self.project, workflow_id="wf-bad", actor=self.lead,
                stages=["test_plan_generation", "not_a_stage"],
            )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StageGateTests(WorkflowBaseTests):
    """逐阶段门禁：没通过不能进下一步。"""

    def _gate(self, *, stage, workflow_id="wf-gate", status="pending"):
        return WorkflowStageGate.objects.create(
            project=self.project, workflow_id=workflow_id, stage=stage, status=status,
        )

    def test_next_stage_is_blocked_until_previous_passes(self):
        from rest_framework.exceptions import ValidationError

        self._gate(stage="test_plan_generation", status="pending")

        with self.assertRaises(ValidationError):
            WorkflowGateService.assert_can_enter(
                self.project.pk, "wf-gate", "testcase_generation",
            )

        WorkflowStageGate.objects.filter(
            workflow_id="wf-gate", stage="test_plan_generation"
        ).update(status="passed")
        # 放行后不再抛错——这就是"门禁未通过不能进入下一步"的反面。
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-gate", "testcase_generation")

    def test_first_stage_never_checks_a_previous_gate(self):
        WorkflowGateService.assert_can_enter(self.project.pk, "wf-none", "test_plan_generation")

    def test_missing_previous_output_is_explained(self):
        """上一阶段连产出都没有时，错误提示要指向"先完成那一阶段"。"""
        from rest_framework.exceptions import ValidationError

        with self.assertRaises(ValidationError) as ctx:
            WorkflowGateService.assert_can_enter(
                self.project.pk, "wf-missing", "report_generation",
            )
        message = str(ctx.exception)
        self.assertIn("test_execution", message)
        self.assertIn("尚未产生产出", message)

    def test_override_requires_reason_and_records_who(self):
        from rest_framework.exceptions import ValidationError

        gate = self._gate(stage="test_plan_generation", status="failed")

        with self.assertRaises(ValidationError):
            WorkflowGateService.override(gate, self.lead, "   ")

        gate = WorkflowGateService.override(gate, self.lead, "已人工复核，风险可控")
        gate.refresh_from_db()
        self.assertEqual(gate.status, "overridden")
        self.assertEqual(gate.decided_by_id, self.lead.id)
        self.assertIsNotNone(gate.decided_at)
        self.assertEqual(gate.reason, "已人工复核，风险可控")

    def test_publish_refuses_when_gate_flag_is_set_and_previous_not_passed(self):
        from rest_framework.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self._publish_stage(stage="testcase_generation", enforce=True)

    def test_publish_without_gate_flag_is_not_blocked(self):
        """未声明强制门禁的调用方不受影响（单能力与历史链路）。"""
        result = self._publish_stage(stage="case_review", workflow_id="", enforce=False)
        self.assertIsNotNone(result)
        self.assertTrue(GenerationOutput.objects.filter(pk=result[1]).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class RegisterOutputTests(WorkflowBaseTests):
    """产出登记必须幂等，且换产出才重置门禁。"""

    def _output(self, *, stage="testcase_generation", workflow_id="wf-reg", content="v1"):
        return GenerationOutput.objects.create(
            project=self.project, trace=self.make_trace(task_type=stage),
            task_type=stage, task_id=f"t-{content}", content=content,
            output_hash=str(content).ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": stage,
                "workflow_id": workflow_id,
            }},
        )

    def test_repeated_registration_keeps_a_passed_gate_untouched(self):
        """门禁通过后把同一产出再登记一次，状态必须仍是 passed。

        这是"重发一次结果就再也过不去门禁"的真实故障场景。
        """
        output = self._output()
        gate = WorkflowGateService.register_output(output)
        WorkflowStageGate.objects.filter(pk=gate.pk).update(status="passed")

        again = WorkflowGateService.register_output(output)

        self.assertEqual(again.pk, gate.pk)
        again.refresh_from_db()
        self.assertEqual(again.status, "passed")

    def test_new_output_for_same_stage_resets_the_gate(self):
        first = self._output(content="v1")
        gate = WorkflowGateService.register_output(first)
        WorkflowStageGate.objects.filter(pk=gate.pk).update(status="passed")

        second = self._output(content="v2")
        reset = WorkflowGateService.register_output(second)

        self.assertEqual(reset.pk, gate.pk)
        reset.refresh_from_db()
        self.assertEqual(reset.status, "pending")
        self.assertEqual(str(reset.output_id), str(second.pk))

    def test_register_does_not_create_gate_for_non_workflow_stage(self):
        output = self._output(stage="case_review", workflow_id="")
        self.assertIsNone(WorkflowGateService.register_output(output))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class WorkflowStatusTests(WorkflowBaseTests):
    """页面与接口读同一份状态；版本一律读当时锁定的那一份。"""

    def test_status_shows_locked_version_after_a_new_version_is_activated(self):
        """链路跑到一半激活新版本，历史流水线仍显示当时的旧包。"""
        skill, first = self._stage_skill("testcase_generation", version="1.0.0")
        WorkflowGateService.start_workflow(
            project=self.project, workflow_id="wf-hist", actor=self.lead,
            stages=["testcase_generation"],
        )

        _skill, second = self.make_skill_version(
            name=skill.name, version="2.0.0", stage="testcase_generation", body="新增护栏",
        )
        self._activate(second)
        skill.refresh_from_db()
        self.assertEqual(str(skill.active_version_id), str(second.pk))

        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-hist",
        )
        item = next(s for s in status["stages"] if s["stage"] == "testcase_generation")
        self.assertEqual(item["version"]["version"], "1.0.0")
        self.assertEqual(item["version"]["skill_version_id"], str(first.pk))
        self.assertEqual(item["version"]["package_sha256"], first.package_sha256)

    def test_status_marks_ready_and_blocked_in_order(self):
        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-fresh",
        )
        states = [item["state"] for item in status["stages"]]
        self.assertEqual(states[0], "ready")
        self.assertEqual(states[1:], ["blocked", "blocked", "blocked"])
        self.assertEqual(status["blocked_at"], "testcase_generation")
        self.assertFalse(status["completed"])

    def test_status_completes_only_when_report_stage_passed(self):
        output = GenerationOutput.objects.create(
            project=self.project, trace=self.make_trace(task_type="report_generation"),
            task_type="report_generation", task_id="rep-1", content="报告",
            output_hash="rep".ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": "report_generation",
                "workflow_id": "wf-done",
            }},
        )
        gate = WorkflowGateService.register_output(output)
        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-done",
        )
        self.assertFalse(status["completed"])

        WorkflowStageGate.objects.filter(pk=gate.pk).update(status="passed")
        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-done",
        )
        self.assertTrue(status["completed"])

    def test_status_covers_every_stage_in_order(self):
        status = WorkflowGateService.workflow_status(
            project=self.project, workflow_id="wf-order",
        )
        self.assertEqual([item["stage"] for item in status["stages"]], STAGES)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class WorkflowAPITests(TestCase):
    """接口层：负责人权限与项目隔离。"""

    def setUp(self):
        from django.contrib.auth.models import User

        from projects.models import Project, ProjectMember

        self.lead = User.objects.create_user(username="t15-lead", password="pass")
        self.executor = User.objects.create_user(username="t15-exec", password="pass")
        self.project = Project.objects.create(name="T15 项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.other_project = Project.objects.create(name="T15 他项目", creator=self.lead)
        ProjectMember.objects.create(project=self.other_project, user=self.lead, role="owner")
        self.client = APIClient()
        self.url = "/api/knowledge-evolution/operations/"

    def test_executor_cannot_start_a_workflow(self):
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"{self.url}start-workflow/",
            {"project": self.project.pk, "workflow_id": "wf-api"}, format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_lead_can_start_a_workflow(self):
        """负责人可启动；平台统一响应包装层把业务数据放在 ``data`` 里。"""
        self.client.force_authenticate(self.lead)
        response = self.client.post(
            f"{self.url}start-workflow/",
            {"project": self.project.pk, "workflow_id": "wf-api"}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()["data"]
        self.assertEqual(payload["workflow_id"], "wf-api")
        self.assertEqual(
            sorted(payload["unmanaged_stages"]), sorted(payload["stage_order"]),
        )

    def test_status_is_readable_by_a_member(self):
        self.client.force_authenticate(self.executor)
        response = self.client.get(
            f"{self.url}workflow-status/",
            {"project": self.project.pk, "workflow_id": "wf-api"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["data"]
        # 这条流程没有任何留痕，按**新链路默认序列**解释（口径真值见
        # ``capability_registry.WORKFLOW_STAGES``，不在测试里再抄一遍字面量）。
        self.assertEqual([item["stage"] for item in payload["stages"]], STAGES)
        self.assertEqual(payload["stages"][0]["state"], "ready")

    def test_non_member_cannot_read_status(self):
        from django.contrib.auth.models import User

        outsider = User.objects.create_user(username="t15-outsider", password="pass")
        self.client.force_authenticate(outsider)
        response = self.client.get(
            f"{self.url}workflow-status/",
            {"project": self.project.pk, "workflow_id": "wf-api"},
        )
        self.assertEqual(response.status_code, 403)
