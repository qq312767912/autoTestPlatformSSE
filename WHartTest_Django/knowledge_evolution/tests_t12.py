"""T12：旁路产出纳管与替换语义。

这一层最容易出的三类错：

- **纳管顺手改了产出**：为了让链路能查到 ``workflow_id``，直接往
  ``GenerationOutput.metadata`` 里补写一个。代价是历史协议不再可信 ——
  一份"从页面直接生成"的产出会看起来从来都在受控流程里。所以有一组用例
  专门断言纳管前后产出元数据逐字节不变。
- **静默顶掉同阶段产出**：人点一次"纳入"，上一次已通过的评审与门禁结论
  就没了。所以替换必须显式：指明对象 + 确认，且旧门禁结论要留档。
- **版本串号**：纳进来的产出来自另一个 Skill 版本，之后所有归因与候选
  都指向错的对象。所以版本与流程锁不一致必须直接拒。
"""
from __future__ import annotations

import copy
import tempfile
import uuid

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.capability_registry import ALL_WORKFLOW_STAGES
from knowledge_evolution.models import FeedbackEvent, GenerationOutput
from knowledge_evolution.submissions import (
    CONFIRMABLE_CODES,
    REFUSE_CROSS_PROJECT,
    REFUSE_PARENT_MISSING,
    REFUSE_REPLACE_NOT_CONFIRMED,
    REFUSE_REPLACE_TARGET_MISMATCH,
    REFUSE_STAGE_CONFLICT,
    REFUSE_UNKNOWN_STAGE,
    REFUSE_UNKNOWN_TARGET,
    REFUSE_VERSION_MISMATCH,
    REFUSE_WORKFLOW_ALREADY_EXISTS,
    REFUSE_WORKFLOW_NOT_FOUND,
    SUBMISSION_STAGES,
    SubmissionRefused,
    WorkflowSubmissionService,
)
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.workflow_models import (
    SUBMISSION_STATE_ADMITTED,
    SUBMISSION_STATE_SUPERSEDED,
    SUBMISSION_STATE_LABELS,
    SUBMISSION_STATES,
    SUBMISSION_TARGET_EXISTING,
    SUBMISSION_TARGET_LABELS,
    SUBMISSION_TARGET_NEW,
    SUBMISSION_TARGETS,
    WorkflowSkillLock,
    WorkflowStageGate,
    WorkflowStageSubmission,
)

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t12-")

STAGE = "test_plan_generation"


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------- 取值真值


class SubmissionTruthTests(SimpleTestCase):
    def test_targets_and_states_are_module_level_truth(self):
        self.assertEqual(
            SUBMISSION_TARGETS, (SUBMISSION_TARGET_NEW, SUBMISSION_TARGET_EXISTING),
        )
        self.assertEqual(set(SUBMISSION_TARGET_LABELS), set(SUBMISSION_TARGETS))
        self.assertEqual(
            SUBMISSION_STATES, (SUBMISSION_STATE_ADMITTED, SUBMISSION_STATE_SUPERSEDED),
        )
        self.assertEqual(set(SUBMISSION_STATE_LABELS), set(SUBMISSION_STATES))

    def test_stages_follow_the_shared_registry(self):
        """阶段真值只有一处：另抄一份必然出现"能选但提交 400"。"""
        self.assertEqual(SUBMISSION_STAGES, tuple(ALL_WORKFLOW_STAGES))

    def test_confirmable_codes_mark_resumable_refusals(self):
        """可确认的码 = 前端该弹确认框；硬错码不该混进去。"""
        self.assertTrue(CONFIRMABLE_CODES)
        self.assertIn(REFUSE_REPLACE_NOT_CONFIRMED, CONFIRMABLE_CODES)
        self.assertNotIn(REFUSE_VERSION_MISMATCH, CONFIRMABLE_CODES)
        self.assertNotIn(REFUSE_REPLACE_TARGET_MISMATCH, CONFIRMABLE_CODES)


# ---------------------------------------------------------------------- 纳管


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SubmissionAdmitTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.output = self.make_output(task_type=STAGE)

    def _admit(self, **overrides):
        payload = {
            "project": self.project, "output": self.output, "actor": self.lead,
            "target": SUBMISSION_TARGET_NEW,
        }
        payload.update(overrides)
        return WorkflowSubmissionService.admit(**payload)

    def _analyze(self, **overrides):
        payload = {
            "project": self.project, "output": self.output,
            "target": SUBMISSION_TARGET_NEW,
        }
        payload.update(overrides)
        return WorkflowSubmissionService.analyze(**payload)

    # ---------------------------------------------------------------- 新流程

    def test_admits_into_a_new_workflow(self):
        result = self._admit()

        self.assertTrue(result["created"])
        self.assertTrue(result["workflow_id"].startswith("wf-"))
        self.assertEqual(result["target"], SUBMISSION_TARGET_NEW)
        self.assertEqual(result["state"], SUBMISSION_STATE_ADMITTED)
        self.assertEqual(result["stage"], STAGE)
        self.assertEqual(result["gate_status"], "pending")

        gate = WorkflowStageGate.objects.get(
            project=self.project, workflow_id=result["workflow_id"], stage=STAGE,
        )
        self.assertEqual(gate.output_id, self.output.pk)

    def test_admit_does_not_touch_output_protocol(self):
        """纳管是新增绑定，不是改写产出：metadata 是产出当时的协议快照。"""
        before = copy.deepcopy(self.output.metadata)

        self._admit()

        self.output.refresh_from_db()
        self.assertEqual(self.output.metadata, before)
        self.assertEqual(
            (self.output.metadata.get("protocol") or {}).get("workflow_id"), "",
        )

    def test_new_workflow_must_not_reuse_an_existing_id(self):
        # 先造一条已存在的流程（有一份产出的门禁就算存在）。
        old = self.make_output(task_type=STAGE, workflow_id="")
        WorkflowStageGate.objects.create(
            project=self.project, workflow_id="wf-taken", stage=STAGE, output=old,
        )

        analysis = self._analyze(target=SUBMISSION_TARGET_NEW, workflow_id="wf-taken")
        self.assertIn(REFUSE_WORKFLOW_ALREADY_EXISTS, analysis["codes"])
        self.assertFalse(analysis["admissible"])

    # -------------------------------------------------------------- 已有流程

    def test_existing_workflow_must_exist(self):
        analysis = self._analyze(
            target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-nope",
        )
        self.assertIn(REFUSE_WORKFLOW_NOT_FOUND, analysis["codes"])

        with self.assertRaises(SubmissionRefused) as ctx:
            self._admit(target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-nope")
        self.assertEqual(ctx.exception.code, REFUSE_WORKFLOW_NOT_FOUND)
        self.assertFalse(ctx.exception.confirmable)

    def test_admits_into_an_existing_workflow(self):
        WorkflowStageGate.objects.create(
            project=self.project, workflow_id="wf-exist", stage="test_execution",
        )

        result = self._admit(
            target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-exist",
        )

        self.assertEqual(result["workflow_id"], "wf-exist")
        self.assertEqual(result["target"], SUBMISSION_TARGET_EXISTING)

    # ---------------------------------------------------------------- 版本锁

    def test_version_lock_mismatch_is_refused(self):
        _, locked_version = self.make_skill_version(name="plan-locked")
        _, other_version = self.make_skill_version(name="plan-other")
        WorkflowSkillLock.objects.create(
            project=self.project, workflow_id="wf-lock",
            lock_key=f"stage:{STAGE}", stage=STAGE, skill_version=locked_version,
        )
        output = self.make_output(task_type=STAGE, skill_version=other_version)

        analysis = self._analyze(
            output=output, target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-lock",
        )
        self.assertIn(REFUSE_VERSION_MISMATCH, analysis["codes"])
        self.assertTrue(analysis["version_lock"]["locked"])
        self.assertFalse(analysis["version_lock"]["matches"])

    def test_matching_version_lock_is_admissible(self):
        _, locked_version = self.make_skill_version(name="plan-same")
        WorkflowSkillLock.objects.create(
            project=self.project, workflow_id="wf-lock2",
            lock_key=f"stage:{STAGE}", stage=STAGE, skill_version=locked_version,
        )
        output = self.make_output(task_type=STAGE, skill_version=locked_version)

        analysis = self._analyze(
            output=output, target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-lock2",
        )
        self.assertEqual(analysis["codes"], [])
        self.assertTrue(analysis["admissible"])

    # ---------------------------------------------------------------- 父产出

    def test_missing_parent_is_refused(self):
        output = self.make_output(task_type=STAGE, parents=[_new_uuid()])

        analysis = self._analyze(output=output)
        self.assertIn(REFUSE_PARENT_MISSING, analysis["codes"])

    def test_existing_parent_is_accepted(self):
        parent = self.make_output(task_type="testcase_generation")
        output = self.make_output(task_type=STAGE, parents=[str(parent.pk)])

        analysis = self._analyze(output=output)
        self.assertEqual(analysis["codes"], [])

    # ------------------------------------------------------------------ 冲突

    def _occupy(self, *, workflow_id: str = "wf-rep"):
        old = self.make_output(task_type=STAGE, workflow_id="")
        gate = WorkflowStageGate.objects.create(
            project=self.project, workflow_id=workflow_id, stage=STAGE,
            output=old, status="passed", scores={"manual": 0.92}, reason="人工确认通过",
        )
        return old, gate

    def test_stage_conflict_requires_explicit_replacement(self):
        old, _gate = self._occupy()

        analysis = self._analyze(target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep")
        self.assertIn(REFUSE_STAGE_CONFLICT, analysis["codes"])
        self.assertTrue(analysis["requires_replace_confirmation"])
        self.assertEqual(analysis["stage_conflict"]["output_id"], str(old.pk))

        with self.assertRaises(SubmissionRefused) as ctx:
            self._admit(target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep")
        self.assertEqual(ctx.exception.code, REFUSE_REPLACE_NOT_CONFIRMED)
        # 可确认 = 前端该弹确认框，而不是直接报错。
        self.assertTrue(ctx.exception.confirmable)

    def test_replace_without_confirmation_is_refused(self):
        old, _gate = self._occupy()

        with self.assertRaises(SubmissionRefused) as ctx:
            self._admit(
                target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep",
                replace_output_id=str(old.pk), confirm_replace=False,
            )
        self.assertEqual(ctx.exception.code, REFUSE_REPLACE_NOT_CONFIRMED)
        self.assertTrue(ctx.exception.confirmable)

    def test_replace_with_wrong_target_is_a_hard_error(self):
        self._occupy()

        analysis = self._analyze(
            target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep",
            replace_output_id=_new_uuid(),
        )
        self.assertIn(REFUSE_REPLACE_TARGET_MISMATCH, analysis["codes"])

        with self.assertRaises(SubmissionRefused) as ctx:
            self._admit(
                target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep",
                replace_output_id=_new_uuid(), confirm_replace=True,
            )
        # 硬错优先：参数错了就不该再让用户去确认一次注定失败的替换。
        self.assertEqual(ctx.exception.code, REFUSE_REPLACE_TARGET_MISMATCH)
        self.assertFalse(ctx.exception.confirmable)

    def test_confirmed_replacement_keeps_history_and_resets_gate(self):
        old, _gate = self._occupy()
        FeedbackEvent.objects.create(
            project=self.project, output=old, trace=old.trace,
            signal="accepted", idempotency_key="t12-old-feedback", actor=self.lead,
        )

        result = self._admit(
            target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep",
            replace_output_id=str(old.pk), confirm_replace=True,
        )

        self.assertEqual(result["supersedes_output_id"], str(old.pk))
        # 旧门禁的结论在重置之前被留档：不存这一步，"保留旧门禁"就只剩一句注释。
        self.assertEqual(result["superseded_gate"]["status"], "passed")
        self.assertEqual(result["superseded_gate"]["scores"], {"manual": 0.92})
        self.assertEqual(result["superseded_gate"]["reason"], "人工确认通过")

        gate = WorkflowStageGate.objects.get(
            project=self.project, workflow_id="wf-rep", stage=STAGE,
        )
        self.assertEqual(gate.output_id, self.output.pk)
        # 新产出必须重新过门禁，旧结论不能顺延。
        self.assertEqual(gate.status, "pending")

        # 旧产出与旧反馈原地保留。
        self.assertTrue(GenerationOutput.objects.filter(pk=old.pk).exists())
        self.assertTrue(
            FeedbackEvent.objects.filter(idempotency_key="t12-old-feedback").exists()
        )

    def test_replaced_output_binding_is_downgraded(self):
        old, _gate = self._occupy()
        WorkflowSubmissionService.admit(
            project=self.project, output=old, actor=self.lead,
            target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep",
        )

        self._admit(
            target=SUBMISSION_TARGET_EXISTING, workflow_id="wf-rep",
            replace_output_id=str(old.pk), confirm_replace=True,
        )

        old_binding = WorkflowStageSubmission.objects.get(
            project=self.project, output=old, stage=STAGE,
        )
        self.assertEqual(old_binding.state, SUBMISSION_STATE_SUPERSEDED)
        self.assertFalse(old_binding.is_admitted)

    # ------------------------------------------------------------------ 幂等

    def test_readmitting_is_idempotent(self):
        first = self._admit()
        second = self._admit()

        self.assertFalse(second["created"])
        self.assertEqual(first["submission_id"], second["submission_id"])
        self.assertEqual(
            WorkflowStageSubmission.objects.filter(
                project=self.project, output=self.output, stage=STAGE,
            ).count(),
            1,
        )
        self.assertEqual(second["history"][0]["action"], "readmitted")

    # ---------------------------------------------------------------- 权限

    def test_non_member_cannot_admit(self):
        from django.contrib.auth.models import User
        from rest_framework.exceptions import PermissionDenied

        outsider = User.objects.create_user(username="t12-outsider", password="p")
        with self.assertRaises(PermissionDenied):
            self._admit(actor=outsider)

    def test_cross_project_output_is_refused(self):
        output = self.make_output(task_type=STAGE, project=self.other_project)

        analysis = WorkflowSubmissionService.analyze(
            project=self.project, output=output, target=SUBMISSION_TARGET_NEW,
        )
        self.assertIn(REFUSE_CROSS_PROJECT, analysis["codes"])

    # ------------------------------------------------------------ 预检一致性

    def test_preflight_and_admit_share_the_same_judgement(self):
        """预检说不能纳管，提交就必须是同码拒绝 —— 否则按钮可用性无从判断。"""
        output = self.make_output(task_type=STAGE, parents=[_new_uuid()])

        analysis = self._analyze(output=output)
        self.assertFalse(analysis["admissible"])
        with self.assertRaises(SubmissionRefused) as ctx:
            self._admit(output=output)
        self.assertEqual(ctx.exception.code, analysis["codes"][0])
        self.assertEqual(ctx.exception.message, analysis["messages"][0])

    def test_unknown_stage_and_target_are_reported(self):
        self.assertIn(REFUSE_UNKNOWN_STAGE, self._analyze(stage="nope")["codes"])
        self.assertIn(REFUSE_UNKNOWN_TARGET, self._analyze(target="nope")["codes"])

    def test_submission_lookup_for_lineage(self):
        self._admit()
        found = WorkflowSubmissionService.submission_for(self.output, stage=STAGE)
        self.assertIsNotNone(found)
        self.assertEqual(found.output_id, self.output.pk)


# ---------------------------------------------------------------------- 接口


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class T12ApiTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)
        self.base = "/api/knowledge-evolution/operations/"
        self.output = self.make_output(task_type=STAGE)
        # T14 起「纳管」入口受项目级灰度开关管辖（未配置视为关闭）。
        # 本类测的是纳管本身的行为，不是开关，所以显式把项目灰度开启。
        from .history_models import ProjectFlywheelSetting
        ProjectFlywheelSetting.objects.create(project=self.project, enabled=True)

    def _post(self, payload: dict):
        return self.client.post(
            f"{self.base}workflow-stage-submit/", payload, format="json",
        )

    def test_catalog_returns_truth_from_backend(self):
        from knowledge_evolution.operations import WorkflowGateService

        response = self.client.get(f"{self.base}workflow-submission-catalog/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["value"] for item in response.data["targets"]], list(SUBMISSION_TARGETS),
        )
        self.assertEqual(
            [item["value"] for item in response.data["stages"]], list(SUBMISSION_STAGES),
        )
        self.assertEqual(
            [item["label"] for item in response.data["stages"]],
            [WorkflowGateService.STAGE_LABELS[stage] for stage in SUBMISSION_STAGES],
        )
        self.assertEqual(set(response.data["confirmable_codes"]), set(CONFIRMABLE_CODES))

    def test_preflight_then_submit(self):
        preflight = self.client.get(
            f"{self.base}workflow-stage-submission-preflight/",
            {"project": self.project.pk, "output_id": str(self.output.pk),
             "target": SUBMISSION_TARGET_NEW},
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertTrue(preflight.data["admissible"])

        response = self._post({
            "project": self.project.pk, "output_id": str(self.output.pk),
            "target": SUBMISSION_TARGET_NEW, "note": "页面直接生成补纳管",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["stage"], STAGE)
        self.assertEqual(response.data["gate_status"], "pending")
        self.assertTrue(response.data["output_protocol_untouched"])
        self.assertEqual(response.data["history"][0]["action"], "admitted")

    def test_conflict_returns_409_with_confirmable_detail(self):
        old = self.make_output(task_type=STAGE, workflow_id="")
        WorkflowStageGate.objects.create(
            project=self.project, workflow_id="wf-api", stage=STAGE, output=old,
        )

        response = self._post({
            "project": self.project.pk, "output_id": str(self.output.pk),
            "target": SUBMISSION_TARGET_EXISTING, "workflow_id": "wf-api",
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"]["code"], REFUSE_REPLACE_NOT_CONFIRMED,
        )
        self.assertTrue(response.data["detail"]["confirmable"])
        self.assertEqual(
            response.data["detail"]["analysis"]["stage_conflict"]["output_id"], str(old.pk),
        )

        confirmed = self._post({
            "project": self.project.pk, "output_id": str(self.output.pk),
            "target": SUBMISSION_TARGET_EXISTING, "workflow_id": "wf-api",
            "replace_output_id": str(old.pk), "confirm_replace": True,
        })
        self.assertEqual(confirmed.status_code, 201)
        self.assertEqual(confirmed.data["supersedes_output_id"], str(old.pk))

    def test_hard_refusal_returns_400(self):
        response = self._post({
            "project": self.project.pk, "output_id": str(self.output.pk),
            "target": SUBMISSION_TARGET_EXISTING, "workflow_id": "wf-nope",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"]["code"], REFUSE_WORKFLOW_NOT_FOUND)
        self.assertFalse(response.data["detail"]["confirmable"])

    def test_missing_output_is_400(self):
        response = self._post({"project": self.project.pk})
        self.assertEqual(response.status_code, 400)

    def test_bad_output_id_is_400(self):
        response = self._post({"project": self.project.pk, "output_id": "not-a-uuid"})
        self.assertEqual(response.status_code, 400)

    def test_list_returns_admitted_bindings(self):
        self._post({
            "project": self.project.pk, "output_id": str(self.output.pk),
            "target": SUBMISSION_TARGET_NEW,
        })

        response = self.client.get(
            f"{self.base}workflow-stage-submissions/",
            {"project": self.project.pk, "stage": STAGE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["output_id"], str(self.output.pk))

    def test_list_requires_project(self):
        response = self.client.get(f"{self.base}workflow-stage-submissions/")
        self.assertEqual(response.status_code, 400)

    def test_executor_cannot_admit(self):
        """纳管会顶掉同阶段已有产出，属测试负责人职责。"""
        self.client.force_authenticate(self.executor)
        response = self._post({
            "project": self.project.pk, "output_id": str(self.output.pk),
            "target": SUBMISSION_TARGET_NEW,
        })
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_preflight(self):
        from django.contrib.auth.models import User

        outsider = User.objects.create_user(username="t12-api-outsider", password="p")
        self.client.force_authenticate(outsider)
        response = self.client.get(
            f"{self.base}workflow-stage-submission-preflight/",
            {"project": self.project.pk, "output_id": str(self.output.pk)},
        )
        self.assertEqual(response.status_code, 403)


# --------------------------------------------------------------- 链路集成


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SubmissionLineageTests(SkillHubBaseTests):
    def test_lineage_reports_submission_and_unbroken_binding(self):
        from knowledge_evolution.lineage import OutputLineageService

        output = self.make_output(task_type=STAGE)
        before = OutputLineageService.trace(output)
        self.assertIsNone(before["submission"])
        self.assertFalse(before["stages"]["binding"]["ok"])

        WorkflowSubmissionService.admit(
            project=self.project, output=output, actor=self.lead,
            target=SUBMISSION_TARGET_NEW,
        )
        after = OutputLineageService.trace(output)

        self.assertIsNotNone(after["submission"])
        self.assertEqual(after["submission"]["stage"], STAGE)
        self.assertTrue(after["stages"]["binding"]["ok"])
        self.assertTrue(after["stages"]["binding"]["submitted"])
        # 版本绑定通了，下一个断点应当是"还没有反馈"。
        self.assertEqual(after["broken_at"], "feedback")
