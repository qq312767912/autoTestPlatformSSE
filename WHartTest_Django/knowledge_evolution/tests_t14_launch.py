"""T14：全链路验收、失败补偿与灰度上线。

本文件钉住的是"上线当天会真的出问题"的那几件事，而不是把 T01–T13 的断言再抄一遍：

1. **灰度开关默认关闭，且只闸控制面入口。** 内网交付要"先灰度上证 e 投票方案阶段"，
   所以"未配置 = 开启"是错的——它等价于全量开放。同时，关掉开关**不能**影响业务
   方案分析与 Agent 调用：飞轮是可选的控制面，不是业务的前置依赖。
2. **登记失败必须看得见、可重试、可告警。** 只写日志的失败在"有没有漏登记"这个
   问题上是搜不出来的（日志里只有出现的反例，没有缺席的正例）。
3. **业务产出不因飞轮侧的故障而丢失或被回滚。** 用控制面的故障去宣告执行面失败，
   是这类联动里最容易犯、代价最大的一个错。
4. **上线就绪可以程序化复核**，而不是靠上线当天逐条勾一份 markdown。
5. **端到端链路真的接得上**：发起 → 锁版本 → 阶段产出 → 门禁登记 → 候选入队，
   每一跳的 id 都能往回走。
6. **§15 的六类验收场景**（A 受控方案生成 / C 最小人工确认 / F 失败补偿等）
   在这里各有一条真实断言，未覆盖的部分由 T09–T13 的用例承担。
"""
from __future__ import annotations

import uuid
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from .flywheel_context import FlywheelContextService
from .models import AssetCandidateEvent, GenerationOutput
from .operations import WorkflowGateService
from .protocol import ADAPTERS, publish_output
from .registration import FlywheelRegistrationService
from .review_reports import (
    HUMAN_COLUMNS, REVIEW_VERDICTS, VERDICT_REQUIRED_FIELDS, validate_submission,
)
from .rollout import (
    LINKAGE_DISABLED_MESSAGE, LaunchReadinessService, linkage_enabled, linkage_state,
)
from .history_models import ProjectFlywheelSetting
from .tests_t09_t13 import TEST_MEDIA_ROOT, SkillHubBaseTests
from .workflow_models import (
    FlywheelRegistrationFailure,
    REGISTRATION_DEAD_LETTER, REGISTRATION_FAILED, REGISTRATION_RESOLVED,
    WorkflowStageGate,
)

BASE = "/api/knowledge-evolution/"
OPS = "/api/knowledge-evolution/operations/"

#: 一期灰度目标阶段（设计 §14）。
PILOT_STAGE = "test_plan_generation"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class RolloutSwitchTests(SkillHubBaseTests):
    """项目级灰度开关：默认关闭、只闸入口、不碰执行面。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _enable(self, project=None):
        return ProjectFlywheelSetting.objects.create(
            project=project or self.project, enabled=True, rollout_note="灰度首批",
        )

    def test_unconfigured_project_is_closed(self):
        """"没配过"必须是关闭，不能是开启——否则"先灰度 e 投票"就无从谈起。"""
        self.assertFalse(linkage_enabled(self.project))
        state = linkage_state(self.project)
        self.assertFalse(state["enabled"])
        self.assertFalse(state["configured"])
        # 状态里必须带 pilot_stage：前端要能说明"这次灰度到哪个阶段"，
        # 否则用户看到的是"飞轮不能用"，而不是"飞轮先开了方案阶段"。
        self.assertEqual(state["pilot_stage"], PILOT_STAGE)

    def test_closed_switch_blocks_every_control_plane_entry(self):
        """四个入口一个都不能漏：漏一个就等于开关失效。"""
        self.client.force_authenticate(self.lead)
        output = self.make_output(task_type=PILOT_STAGE, workflow_id="")

        runs = self.client.post(f"{BASE}flywheel-runs/", {
            "project": self.project.pk, "workflow_id": "wf-closed", "entry_type": "chat",
        }, format="json")
        opened = self.client.post(f"{BASE}flywheel-runs/open/", {
            "project": self.project.pk, "entry_type": "chat", "source_id": "s-1",
        }, format="json")
        started = self.client.post(f"{OPS}start-workflow/", {
            "project": self.project.pk, "workflow_id": "wf-closed",
        }, format="json")
        submitted = self.client.post(f"{OPS}workflow-stage-submit/", {
            "output": str(output.pk), "stage": PILOT_STAGE, "target": "new",
        }, format="json")

        for response in (runs, opened, started, submitted):
            self.assertEqual(response.status_code, 403, response.content)
        # 统一响应包装层会把 detail 挪到自己的结构里，所以断言原文而不是某个键名：
        # 要证明的是"用户能读到为什么被拒"，不是"某个 JSON 键恰好叫 detail"。
        self.assertIn("灰度", opened.content.decode("utf-8"))
        # 被拒的入口不能在库里留下半成品：run 建一半会让"这条链到底存不存在"
        # 变成靠猜，而后续所有阶段都会挂在一个没人认领的 workflow_id 上。
        from .workflow_models import FlywheelRun
        self.assertFalse(FlywheelRun.objects.filter(project=self.project).exists())

    def test_closed_switch_leaves_business_generation_untouched(self):
        """验收：关闭开关后原有方案分析和 Agent 调用不受影响。"""
        self.assertFalse(linkage_enabled(self.project))
        output = self._publish(stage=PILOT_STAGE, workflow_id="req:business-direct")

        self.assertEqual(output.task_type, PILOT_STAGE)
        self.assertTrue(GenerationOutput.objects.filter(pk=output.pk).exists())
        # 产出的 content 是业务信封的序列化，不是人读的摘要——这里断言的是
        # "业务内容原样落库"，不是"人能读懂".
        self.assertIn("方案项", output.content)
        # 执行面不闸：关着开关，业务产出照样登记进门禁。
        self.assertIsNotNone(
            WorkflowStageGate.objects.filter(
                project=self.project, workflow_id="req:business-direct",
                stage=PILOT_STAGE,
            ).first()
        )

    def test_outsider_cannot_learn_the_switch_state(self):
        """非成员拿到的必须是"无权"，不能是开关文案。

        闸门顺序若反了（先判开关后判成员），非成员会因为"项目没开开关"拿到 403，
        成员也拿到同一状态码 —— 等于顺手把"这个项目是否已灰度"告诉了不该知道的人。
        """
        outsider = User.objects.create_user(username="t14-outsider", password="pass")
        self.client.force_authenticate(outsider)

        response = self.client.post(f"{BASE}flywheel-runs/", {
            "project": self.project.pk, "workflow_id": "wf-x", "entry_type": "chat",
        }, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("灰度", response.content.decode("utf-8"))

    def test_only_lead_flips_the_switch_and_members_can_read_it(self):
        self.client.force_authenticate(self.executor)
        denied = self.client.post(f"{BASE}flywheel-settings/set/", {
            "project": self.project.pk, "enabled": True,
        }, format="json")
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.lead)
        accepted = self.client.post(f"{BASE}flywheel-settings/set/", {
            "project": self.project.pk, "enabled": True, "rollout_note": "先灰度方案阶段",
        }, format="json")
        self.assertEqual(accepted.status_code, 200, accepted.content)

        # 执行人员必须能读到状态：按钮为什么是灰的，得有人能解释。
        self.client.force_authenticate(self.executor)
        state = self.client.get(
            f"{BASE}flywheel-settings/state/", {"project": self.project.pk},
        )
        self.assertEqual(state.status_code, 200, state.content)
        self.assertTrue(state.json()["data"]["enabled"])
        self.assertEqual(state.json()["data"]["rollout_note"], "先灰度方案阶段")

    def test_inflight_chain_survives_the_switch_being_turned_off(self):
        """开关控制"要不要开始用"，不是"用到一半把人踢出去"。

        在途链路若因为运维事后关开关而中断，表现是"用户跑了一半、产出登记不上"，
        而这类事故的根因（一次运维开关动作）在业务页面上是查不出来的。
        """
        self._enable()
        self.client.force_authenticate(self.lead)
        opened = self.client.post(f"{BASE}flywheel-runs/open/", {
            "project": self.project.pk, "entry_type": "chat", "source_id": "s-2",
        }, format="json")
        self.assertEqual(opened.status_code, 200, opened.content)
        workflow_id = opened.json()["data"]["workflow_id"]

        ProjectFlywheelSetting.objects.filter(project=self.project).update(enabled=False)
        self.assertFalse(linkage_enabled(self.project))

        output = self._publish(stage=PILOT_STAGE, workflow_id=workflow_id)

        gate = WorkflowStageGate.objects.filter(
            project=self.project, workflow_id=workflow_id, stage=PILOT_STAGE,
        ).first()
        self.assertIsNotNone(gate)
        self.assertEqual(gate.output_id, output.pk)
        self.assertFalse(
            FlywheelRegistrationFailure.objects.filter(output=output).exists()
        )

    # ------------------------------------------------------------ helper

    def _publish(self, *, stage, workflow_id):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead,
            source_id=f"{stage}-{uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output={"items": [{"id": "p-1", "title": "方案项"}]},
        )
        return GenerationOutput.objects.get(pk=publish_output(envelope)[1])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class RegistrationCompensationTests(SkillHubBaseTests):
    """登记失败的补偿队列（设计 §13 / R15）。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _output(self, *, workflow_id="req:reg-1"):
        return GenerationOutput.objects.create(
            project=self.project, trace=self.make_trace(),
            task_type=PILOT_STAGE, task_id="t-reg", content="产出正文",
            output_hash="reg-hash".ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1",
                "stage": PILOT_STAGE, "workflow_id": workflow_id,
                "parent_output_ids": [],
            }},
        )

    def _failing_register(self):
        return mock.patch.object(
            WorkflowGateService, "register_output",
            side_effect=RuntimeError("门禁写入失败：连接被重置"),
        )

    def test_failure_is_recorded_and_not_raised(self):
        output = self._output()
        with self._failing_register():
            result = FlywheelRegistrationService.register(output, actor=self.lead)

        self.assertFalse(result["registered"])
        self.assertEqual(result["reason"], "registration_failed")
        record = FlywheelRegistrationFailure.objects.get(
            output=output, workflow_id="req:reg-1", stage=PILOT_STAGE,
        )
        self.assertEqual(str(record.pk), result["compensation_id"])
        self.assertEqual(record.attempts, 1)
        self.assertEqual(record.status, REGISTRATION_FAILED)
        self.assertIn("连接被重置", record.last_error)

    def test_business_output_stays_accessible_after_failure(self):
        """验收：业务生成成功但飞轮登记失败时，业务文件仍可访问。"""
        output = self._output()
        with self._failing_register():
            FlywheelRegistrationService.register(output, actor=self.lead)

        output.refresh_from_db()
        self.assertEqual(output.content, "产出正文")
        self.assertTrue(GenerationOutput.objects.filter(pk=output.pk).exists())

    def test_repeated_failure_is_idempotent_and_escalates_to_dead_letter(self):
        """三次失败只留一条记录：告警数字一旦失真就再没人信它。"""
        output = self._output()
        with self._failing_register():
            for _ in range(3):
                FlywheelRegistrationService.register(output, actor=self.lead)

        records = FlywheelRegistrationFailure.objects.filter(output=output)
        self.assertEqual(records.count(), 1)
        record = records.get()
        self.assertEqual(record.attempts, 3)
        self.assertEqual(record.status, REGISTRATION_DEAD_LETTER)
        self.assertTrue(record.needs_human)
        # 每一条尝试都要留痕：只给一个次数，无法回答"到底卡在哪一步"。
        self.assertEqual(len(record.history), 3)

        summary = FlywheelRegistrationService.status_summary(self.project.pk)
        self.assertEqual(summary["dead_letter"], 1)
        self.assertTrue(summary["alert"])

    def test_retry_success_closes_the_record(self):
        output = self._output()
        with self._failing_register():
            FlywheelRegistrationService.register(output, actor=self.lead)
        record = FlywheelRegistrationFailure.objects.get(output=output)

        retried = FlywheelRegistrationService.retry(record, actor=self.lead)

        self.assertEqual(retried.status, REGISTRATION_RESOLVED)
        self.assertIsNotNone(retried.resolved_at)
        summary = FlywheelRegistrationService.status_summary(self.project.pk)
        self.assertEqual(summary["open"], 0)
        self.assertFalse(summary["alert"])

    def test_failure_after_resolution_reopens_the_same_record(self):
        """修好又坏：必须复用同一条记录，不能长出两条时间线。"""
        output = self._output()
        with self._failing_register():
            FlywheelRegistrationService.register(output, actor=self.lead)
        record = FlywheelRegistrationFailure.objects.get(output=output)
        FlywheelRegistrationService.retry(record, actor=self.lead)

        with self._failing_register():
            FlywheelRegistrationService.register(output, actor=self.lead)

        record.refresh_from_db()
        self.assertEqual(record.status, REGISTRATION_FAILED)
        self.assertEqual(record.attempts, 2)
        self.assertIsNone(record.resolved_at)
        self.assertEqual(
            FlywheelRegistrationFailure.objects.filter(output=output).count(), 1,
        )

    def test_publish_output_does_not_raise_when_registration_fails(self):
        """端到端契约：登记失败绝不反向炸掉业务产出。"""
        envelope = ADAPTERS[PILOT_STAGE].build(
            project=self.project, user=self.lead,
            source_id="plan-reg-fail", workflow_id="req:reg-2",
            input_summary="方案输入", output={"items": []},
        )

        with self._failing_register():
            result = publish_output(envelope)

        self.assertIsNotNone(result)
        output = GenerationOutput.objects.get(pk=result[1])
        self.assertTrue(
            FlywheelRegistrationFailure.objects.filter(output=output).exists()
        )

    def test_non_workflow_output_is_not_a_failure(self):
        """旁路产出"没登记"是正常的，不能变成补偿噪声。

        验收要求"审计无未解释的跨项目引用、版本漂移和无主反馈"，
        若把正常情况也记成失败，死信队列会被噪声淹没，真正的问题反而看不见。
        """
        output = self._output(workflow_id="")
        result = FlywheelRegistrationService.register(output, actor=self.lead)

        self.assertFalse(result["registered"])
        self.assertEqual(result["reason"], "not_workflow_output")
        self.assertFalse(FlywheelRegistrationFailure.objects.filter(output=output).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class RegistrationCompensationApiTests(SkillHubBaseTests):
    """补偿队列的控制台入口与项目隔离。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _fail_once(self, *, project, workflow_id):
        output = GenerationOutput.objects.create(
            project=project, trace=self.make_trace(project=project),
            task_type=PILOT_STAGE, task_id=f"t-{workflow_id}", content="正文",
            output_hash=f"{workflow_id}".ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": PILOT_STAGE,
                "workflow_id": workflow_id, "parent_output_ids": [],
            }},
        )
        with mock.patch.object(
            WorkflowGateService, "register_output",
            side_effect=RuntimeError("写入失败"),
        ):
            FlywheelRegistrationService.register(output, actor=self.lead)
        return output

    def test_console_lists_and_retries_open_failures(self):
        self._fail_once(project=self.project, workflow_id="req:api-1")
        self.client.force_authenticate(self.lead)

        listing = self.client.get(
            f"{OPS}registration-failures/",
            {"project": self.project.pk, "detail": "1"},
        )
        self.assertEqual(listing.status_code, 200, listing.content)
        payload = listing.json()["data"]
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertIn("写入失败", payload["items"][0]["last_error"])

        retried = self.client.post(f"{OPS}registration-failures-retry/", {
            "project": self.project.pk,
        }, format="json")
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertEqual(retried.json()["data"]["retried"], 1)
        self.assertEqual(retried.json()["data"]["summary"]["open"], 0)

    def test_retry_requires_a_test_lead(self):
        self._fail_once(project=self.project, workflow_id="req:api-2")
        self.client.force_authenticate(self.executor)

        response = self.client.post(f"{OPS}registration-failures-retry/", {
            "project": self.project.pk,
        }, format="json")

        self.assertEqual(response.status_code, 403)

    def test_cross_project_data_is_not_readable(self):
        self._fail_once(project=self.other_project, workflow_id="req:api-3")
        self.client.force_authenticate(self.lead)

        listing = self.client.get(
            f"{OPS}registration-failures/", {"project": self.other_project.pk},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"]["failed"], 1)

        # 本项目视角下看不到别的项目的失败——隔离是"看不见"，不是"看见了但挡住"。
        mine = self.client.get(
            f"{OPS}registration-failures/", {"project": self.project.pk},
        )
        self.assertEqual(mine.json()["data"]["failed"], 0)

        outsider = User.objects.create_user(username="t14-reg-out", password="pass")
        self.client.force_authenticate(outsider)
        denied = self.client.get(
            f"{OPS}registration-failures/", {"project": self.other_project.pk},
        )
        self.assertEqual(denied.status_code, 403)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LaunchReadinessTests(SkillHubBaseTests):
    """上线就绪自检：能自动判的全部自动判。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_report_shape_and_isolation_gate(self):
        report = LaunchReadinessService.check(self.project)
        codes = {item["code"] for item in report["checks"]}
        self.assertEqual(codes, {
            "migrations_applied", "linkage_flag_configured",
            "no_cross_project_reference", "no_dead_letter",
            "runbooks_present", "linkage_switch_state",
        })
        self.assertEqual(report["isolation"]["total"], 0)

    def test_switch_state_does_not_block_go_live(self):
        """开关开着还是关着是运营动作，不是上线缺陷。

        若把"未开启"也算不 ready，上线前一天必须先开开关才能跑自检，
        而"开开关"本身才是需要自检之后才做的事——这会绕成一个死循环。
        """
        report = LaunchReadinessService.check(self.project)
        self.assertFalse(report["linkage"]["enabled"])
        self.assertTrue(report["ready"], report["checks"])

    def test_runbooks_check_is_honest_about_what_it_cannot_judge(self):
        """手册齐备 → ok；目录不可达 → **skipped**，而不是假装 ok。

        手册是发布物的一部分，不在后端运行时镜像里。把"我判不了"写成"通过"，
        等于用一条假绿抹掉上线检查表上真正该人工确认的那一项。
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in LaunchReadinessService.RUNBOOKS:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# 手册占位\n", encoding="utf-8")
            with override_settings(FLYWHEEL_RUNBOOK_DIR=str(root)):
                item = LaunchReadinessService._runbooks_check()
            self.assertTrue(item["ok"])
            self.assertFalse(item["skipped"])

            # 只留两份：缺一份必须被判出来。
            (root / LaunchReadinessService.RUNBOOKS[0]).unlink()
            with override_settings(FLYWHEEL_RUNBOOK_DIR=str(root)):
                missing = LaunchReadinessService._runbooks_check()
            self.assertFalse(missing["ok"])
            self.assertIn(LaunchReadinessService.RUNBOOKS[0], missing["detail"])

        with override_settings(FLYWHEEL_RUNBOOK_DIR="/nonexistent-runbook-dir-t14"):
            report = LaunchReadinessService.check(self.project)
        item = next(
            entry for entry in report["checks"] if entry["code"] == "runbooks_present"
        )
        self.assertTrue(item["skipped"])
        # 前提是"兜底祖先目录搜索"确实没找到源码树里的 specs（容器内即如此）；
        # 若在源码树里跑，这条会拿到真目录并给出 skipped=False 的结论。
        if item["skipped"]:
            self.assertIn("人工确认", item["detail"])

    def test_api_is_member_readable(self):
        self.client.force_authenticate(self.executor)
        response = self.client.get(
            f"{OPS}launch-readiness/", {"project": self.project.pk},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("checks", response.json()["data"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class FlywheelEndToEndTests(SkillHubBaseTests):
    """链路 A / C：飞轮发起 → 锁版本 → 方案产出 → 门禁登记 → 候选入队。"""

    def setUp(self):
        super().setUp()
        ProjectFlywheelSetting.objects.create(project=self.project, enabled=True)
        self.client = APIClient()
        self.client.force_authenticate(self.lead)

    def test_chain_from_launch_to_candidate_queue_is_traceable(self):
        _skill, version = self.make_skill_version(
            name="ev-plan", version="1.0.0", stage=PILOT_STAGE,
        )

        opened = self.client.post(f"{BASE}flywheel-runs/open/", {
            "project": self.project.pk, "entry_type": "requirement",
            "source_id": "doc-e2e",
        }, format="json")
        self.assertEqual(opened.status_code, 200, opened.content)
        workflow_id = opened.json()["data"]["workflow_id"]

        started = self.client.post(f"{OPS}start-workflow/", {
            "project": self.project.pk, "workflow_id": workflow_id,
            "pins": {PILOT_STAGE: str(version.pk)},
        }, format="json")
        self.assertEqual(started.status_code, 201, started.content)
        self.assertNotIn(PILOT_STAGE, started.json()["data"]["unmanaged_stages"])

        run_id = opened.json()["data"]["run_id"]
        context = self.client.post(
            f"{BASE}flywheel-runs/{run_id}/resolve-stage/",
            {"stage": PILOT_STAGE}, format="json",
        )
        self.assertEqual(context.status_code, 200, context.content)
        self.assertTrue(context.json()["data"]["managed"])
        self.assertEqual(context.json()["data"]["skill_version_id"], str(version.pk))

        envelope = ADAPTERS[PILOT_STAGE].build(
            project=self.project, user=self.lead, source_id="plan-e2e-1",
            workflow_id=workflow_id, input_summary="方案输入",
            output={"items": [{"id": "p-1", "title": "方案项"}]},
            skill_version=version,
        )
        output = GenerationOutput.objects.get(pk=publish_output(envelope)[1])

        # 每一跳都要能往回走：产出 → 门禁 → Skill 版本 → 候选队列。
        gate = WorkflowStageGate.objects.get(
            project=self.project, workflow_id=workflow_id, stage=PILOT_STAGE,
        )
        self.assertEqual(gate.output_id, output.pk)
        self.assertEqual(output.skill_version_id, version.pk)
        self.assertFalse(
            FlywheelRegistrationFailure.objects.filter(output=output).exists()
        )
        queue = AssetCandidateEvent.objects.filter(
            project=self.project, payload__output_id=str(output.pk),
        )
        self.assertTrue(queue.exists(), "正式阶段产出必须进入候选队列")

    def test_minimal_confirmation_rejects_blank_but_allows_draft(self):
        """验收 C：存在空白时可保存草稿，但不能正式提交。"""
        rows = [{column: "" for column in HUMAN_COLUMNS}]
        rows[0]["方案项 ID"] = "p-1"

        blank = validate_submission(rows)
        self.assertTrue(blank["ok"])
        self.assertEqual(blank["blank_count"], 1)

        rows[0]["人工结论"] = "修改后采纳"
        rows[0]["修改类型"] = "覆盖不足"
        rows[0]["修改内容"] = "补充边界值"
        filled = validate_submission(rows)
        self.assertTrue(filled["ok"], filled["errors"])
        self.assertEqual(filled["blank_count"], 0)

        # 「修改后采纳」缺"修改内容"必须被拒 —— 只说结论不说改了什么，
        # 这条反馈对归因毫无价值，却会被算作"已确认"。
        rows[0]["修改内容"] = ""
        missing = validate_submission(rows)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["errors"][0]["code"], "missing_required_field")

        # 这些真值本身也要钉住：前端与后端各写一份必然分叉。
        self.assertEqual(REVIEW_VERDICTS, ("采纳", "修改后采纳", "删除"))
        self.assertEqual(VERDICT_REQUIRED_FIELDS["删除"], ("修改类型",))

    def test_member_can_read_the_trace_but_outsider_cannot(self):
        """验收 B（权限侧）：实时轨迹只对项目成员可见。"""
        attempt = self._make_attempt()
        endpoint = f"{BASE}stage-attempts/{attempt.pk}/trace/"

        member = self.client.get(endpoint)
        self.assertEqual(member.status_code, 200, member.content)
        # 未登记 Span 时也要返回结构，不能因为空就 404——
        # 页面会把 404 显示成"轨迹功能坏了"。
        self.assertIn("spans", member.json()["data"])

        outsider = User.objects.create_user(username="t14-trace-out", password="pass")
        self.client.force_authenticate(outsider)
        # 404 而不是 403：查询集按项目隔离，非成员看不到这条记录，
        # 系统也就不该**告诉**他"这个 id 存在但你没权限"——那本身是信息泄露。
        self.assertEqual(self.client.get(endpoint).status_code, 404)

        self.assertEqual(
            self.client.get(f"{BASE}stage-attempts/{uuid.uuid4()}/trace/").status_code,
            404,
        )

    def _make_attempt(self):
        from .operations import StageExecutionAttemptService

        attempt, _created = StageExecutionAttemptService.plan(
            project=self.project, workflow_id="req:e2e-trace",
            stage=PILOT_STAGE, actor=self.lead,
        )
        return attempt


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class FailureCompensationTests(SkillHubBaseTests):
    """验收 F：Agent 中途失败、SSE 断线、飞轮登记失败各走各的补偿。"""

    def test_agent_failure_keeps_trace_and_retry_links_back(self):
        """失败尝试必须保留轨迹、不得冒充产出，重试新建一条并指回原记录。"""
        from .attribution import AttemptTraceService
        from .operations import StageExecutionAttemptService
        from .workflow_models import ATTEMPT_TERMINAL_STATES

        attempt, _created = StageExecutionAttemptService.plan(
            project=self.project, workflow_id="req:f-1", stage=PILOT_STAGE,
            actor=self.lead,
        )
        StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="req:f-1", stage=PILOT_STAGE,
            attempt=attempt, actor=self.lead,
        )
        StageExecutionAttemptService.mark_running(attempt, actor=self.lead)
        AttemptTraceService.record_span(
            attempt=attempt, step_type="tool_call", status="completed",
            tool_name="read_requirement", latency_ms=12, output_hash="h" * 64,
        )
        # SSE 断线：模型跑到一半连接没了，这轮没有任何正式产出。
        StageExecutionAttemptService.fail(
            attempt, error_code="stream_broken", error_summary="SSE 连接中断",
        )

        attempt.refresh_from_db()
        self.assertIn(attempt.status, ATTEMPT_TERMINAL_STATES)
        self.assertEqual(attempt.status, "failed")
        self.assertIsNone(attempt.output_id, "失败尝试不得冒充正式产出")
        self.assertEqual(
            len(AttemptTraceService.spans_for(attempt)), 1,
            "失败不得清空已有轨迹——那正是排查这一轮所需的唯一证据",
        )

        retried, created = StageExecutionAttemptService.retry(attempt, actor=self.lead)
        self.assertTrue(created)
        self.assertNotEqual(retried.pk, attempt.pk, "重试必须新建记录")
        self.assertEqual(retried.retry_of_id, attempt.pk)
        # 重试是一条**新的一轮**：不能原地把失败改写成成功，否则"重试过几次、
        # 每次为什么失败"就丢了；也不该停在终态，否则这一轮没人会去跑。
        self.assertNotIn(retried.status, ATTEMPT_TERMINAL_STATES)

    def test_publish_failure_is_not_swallowed_into_a_fake_output(self):
        """产出发布失败：门禁不得凭空建出来。

        如果发布失败时门禁已经建好，页面会显示"这一阶段待评测"，
        而实际上根本没有产出可评——运维会去查评测服务，方向从一开始就错了。
        """
        output = GenerationOutput.objects.create(
            project=self.project, trace=self.make_trace(),
            task_type=PILOT_STAGE, task_id="t-pub", content="正文",
            output_hash="pub".ljust(64, "0"),
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": PILOT_STAGE,
                "workflow_id": "req:f-2", "parent_output_ids": [],
            }},
        )
        with mock.patch.object(
            WorkflowGateService, "register_output",
            side_effect=RuntimeError("门禁写入失败"),
        ):
            FlywheelRegistrationService.register(output, actor=self.lead)

        self.assertFalse(
            WorkflowStageGate.objects.filter(
                project=self.project, workflow_id="req:f-2", stage=PILOT_STAGE,
            ).exists()
        )
        record = FlywheelRegistrationFailure.objects.get(output=output)
        self.assertEqual(record.last_error, "门禁写入失败")
