"""T02：可信执行上下文与阶段派发接口。

这组用例盯的是**一条被篡改的 URL 能造成什么**：

- 飞轮派发之后，业务页面必须知道"这一轮属于哪条流程、锁的哪份包"，但这件事
  不能靠前端把 ``workflow_id`` / ``skill_version_id`` 拼在 URL 里带过去——
  那是用户可改的字符串。上下文因此存在服务端，前端只拿一个不透明 id。
- 上下文必须**重复解析幂等**：页面挂载解析一次、刷新再解析一次都是正常操作，
  把第二次当失败只会逼用户重新派发。
- 过期、跨项目、非成员、版本锁漂移四种情况必须各自被拒，且「不存在」与
  「存在但跨项目」要返回同一个结论——否则接口变成探测别项目 ID 的工具。
- 上一阶段没放行时不能派发下一阶段：这是"逐阶段推进"从界面约定变成真实约束的地方。
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from knowledge_evolution.operations import (
    StageExecutionContextService,
    WorkflowGateService,
)
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.workflow_models import (
    StageExecutionContext,
    StageExecutionAttempt,
    WorkflowSkillLock,
    WorkflowStageGate,
)

PLAN_STAGE = "test_plan_generation"
CASE_STAGE = "testcase_generation"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class DispatchContextBaseTests(SkillHubBaseTests):
    """派发 helper：与飞轮「执行本阶段」走同一条服务方法。"""

    workflow_id = "wf-t02"

    def _plan(self, *, stage=PLAN_STAGE, project=None, actor=None, workflow_id=None):
        return WorkflowGateService.plan_execution(
            project=project or self.project,
            workflow_id=workflow_id or self.workflow_id,
            stage=stage,
            actor=self.lead if actor is None else actor,
        )

    def _lock(self, skill_version, *, stage=PLAN_STAGE, project=None, workflow_id=None):
        return WorkflowSkillLock.objects.create(
            project=project or self.project,
            workflow_id=workflow_id or self.workflow_id,
            lock_key=f"stage:{stage}",
            scope="workflow",
            stage=stage,
            skill=skill_version.skill,
            skill_version=skill_version,
            package_sha256=skill_version.package_sha256,
            locked_by=self.lead,
        )

    def _release_previous_stage(self, *, stage=PLAN_STAGE, workflow_id=None):
        """把上一阶段标成放行，用来验证"放行之后就能派发下一阶段"。"""
        order = WorkflowGateService.stage_order_for(
            self.project.pk, workflow_id or self.workflow_id, stage=stage,
        )
        index = order.index(stage)
        assert index > 0, "本 helper 只用于非首阶段"
        WorkflowStageGate.objects.update_or_create(
            project=self.project, workflow_id=workflow_id or self.workflow_id,
            stage=order[index - 1],
            defaults={"status": "confirmed", "decided_by": self.lead},
        )


class DispatchPlanTests(DispatchContextBaseTests):
    """派发即产出「可追踪的 attempt + 短期上下文 + 跳转地址」。"""

    def test_plan_creates_attempt_and_context(self):
        plan = self._plan()

        attempt = StageExecutionAttempt.objects.get(pk=plan["attempt_id"])
        context = StageExecutionContext.objects.get(pk=plan["execution_context_id"])

        self.assertEqual(attempt.status, "dispatched")
        self.assertEqual(attempt.workflow_id, self.workflow_id)
        self.assertEqual(attempt.stage, PLAN_STAGE)
        self.assertEqual(attempt.entry_type, "flywheel")
        self.assertEqual(context.attempt_id, attempt.pk)
        self.assertEqual(context.project_id, self.project.pk)
        self.assertEqual(context.issued_to_id, self.lead.pk)
        self.assertFalse(context.is_expired())

    def test_launch_url_only_when_business_page_exists(self):
        """一期只有方案分析页；没有页面的阶段不返回一个会 404 的地址。"""
        plan = self._plan(stage=PLAN_STAGE)
        self.assertTrue(plan["launch_url"].startswith("/test-plans?execution_context_id="))
        self.assertIn(plan["execution_context_id"], plan["launch_url"])

        # 用例阶段一期还没有对应业务页面 → 地址为空，前端沿用原来的提示。
        self._release_previous_stage(stage=CASE_STAGE)
        case_plan = self._plan(stage=CASE_STAGE)
        self.assertEqual(case_plan["launch_url"], "")

    def test_repeat_dispatch_reuses_active_attempt(self):
        """重复点击「执行本阶段」不能变成两条并行 attempt。"""
        first = self._plan()
        second = self._plan()

        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(
            StageExecutionAttempt.objects.filter(
                project=self.project, workflow_id=self.workflow_id, stage=PLAN_STAGE,
            ).count(),
            1,
        )

    def test_blocked_stage_cannot_be_dispatched(self):
        """上一阶段未放行时派发下一阶段必须被拒。"""
        with self.assertRaises(ValidationError):
            self._plan(stage=CASE_STAGE)

        self.assertFalse(
            StageExecutionAttempt.objects.filter(
                project=self.project, stage=CASE_STAGE,
            ).exists()
        )

    def test_dispatch_after_previous_stage_released(self):
        self._release_previous_stage(stage=CASE_STAGE)
        plan = self._plan(stage=CASE_STAGE)
        self.assertEqual(plan["stage"], CASE_STAGE)

    def test_locked_skill_version_is_carried_into_attempt_and_context(self):
        skill, version = self.make_skill_version(stage=PLAN_STAGE, version="1.0.0")
        self._lock(version)

        plan = self._plan()
        context = StageExecutionContext.objects.get(pk=plan["execution_context_id"])

        self.assertTrue(plan["managed"])
        self.assertEqual(plan["skill_version"], "1.0.0")
        self.assertEqual(context.skill_version_id, version.pk)
        self.assertEqual(context.skill_package_sha256, version.package_sha256)


class ContextPayloadTests(DispatchContextBaseTests):
    """上下文是会被业务页面读回来的参数快照，只能落白名单。"""

    def test_payload_whitelist_drops_credentials(self):
        attempt = StageExecutionAttempt.objects.create(
            project=self.project, workflow_id=self.workflow_id, stage=PLAN_STAGE,
            status="dispatched", requested_by=self.lead,
        )
        context = StageExecutionContextService.issue(
            attempt=attempt, project=self.project, actor=self.lead,
            payload={
                "module_key": PLAN_STAGE,
                "knowledge_base_ids": ["kb-1"],
                "authorization": "Bearer secret",
                "raw_request": {"password": "p"},
            },
        )

        self.assertEqual(context.payload.get("module_key"), PLAN_STAGE)
        self.assertEqual(context.payload.get("knowledge_base_ids"), ["kb-1"])
        self.assertNotIn("authorization", context.payload)
        self.assertNotIn("raw_request", context.payload)


class ContextResolveTests(DispatchContextBaseTests):
    """解析上下文的四道校验。"""

    def _context(self, **kwargs):
        plan = self._plan(**kwargs)
        return StageExecutionContext.objects.get(pk=plan["execution_context_id"])

    def test_resolve_returns_trusted_params(self):
        skill, version = self.make_skill_version(stage=PLAN_STAGE, version="2.3.4")
        self._lock(version)
        context = self._context()

        payload = StageExecutionContextService.resolve(
            context_id=context.pk, project_id=self.project.pk, user=self.executor,
        )
        view = StageExecutionContextService.view(payload)

        self.assertEqual(view["workflow_id"], self.workflow_id)
        self.assertEqual(view["stage"], PLAN_STAGE)
        self.assertEqual(view["skill"]["skill_version_id"], str(version.pk))
        self.assertEqual(view["skill"]["version"], "2.3.4")
        self.assertTrue(view["managed"])
        self.assertFalse(view["expired"])
        # 页面需要的信息一次拿全，不必再 join SkillVersion 或回查流程。
        self.assertIn("attempt_id", view)
        self.assertIn("parent_output_ids", view)

    def test_repeat_resolve_is_idempotent(self):
        """刷新页面会重复解析；第二次不能被当成重放攻击。"""
        context = self._context()

        first = StageExecutionContextService.resolve(
            context_id=context.pk, project_id=self.project.pk, user=self.executor,
        )
        second = StageExecutionContextService.resolve(
            context_id=context.pk, project_id=self.project.pk, user=self.executor,
        )

        self.assertEqual(first.pk, second.pk)
        context.refresh_from_db()
        self.assertEqual(context.resolve_count, 2)

    def test_expired_context_is_rejected(self):
        context = self._context()
        StageExecutionContext.objects.filter(pk=context.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        with self.assertRaises(ValidationError) as ctx:
            StageExecutionContextService.resolve(
                context_id=context.pk, project_id=self.project.pk, user=self.lead,
            )
        self.assertIn("过期", str(ctx.exception))

    def test_cross_project_context_is_indistinguishable_from_missing(self):
        """「不属于本项目」不能与「不存在」给出不同结论——否则可被用来探测 ID。"""
        context = self._context()

        with self.assertRaises(ValidationError) as cross:
            StageExecutionContextService.resolve(
                context_id=context.pk, project_id=self.other_project.pk, user=self.lead,
            )
        with self.assertRaises(ValidationError) as missing:
            StageExecutionContextService.resolve(
                context_id="00000000-0000-0000-0000-000000000000",
                project_id=self.other_project.pk, user=self.lead,
            )

        self.assertEqual(str(cross.exception), str(missing.exception))

    def test_non_member_is_rejected(self):
        from django.contrib.auth import get_user_model

        outsider = get_user_model().objects.create_user(username="t02-outsider", password="pass")
        context = self._context()

        with self.assertRaises(PermissionDenied):
            StageExecutionContextService.resolve(
                context_id=context.pk, project_id=self.project.pk, user=outsider,
            )

    def test_lock_drift_invalidates_context(self):
        """流程锁被改到别的版本后，旧上下文不再可信（篡改 URL 换不来别的包）。"""
        skill, version = self.make_skill_version(stage=PLAN_STAGE, version="1.0.0")
        lock = self._lock(version)
        context = self._context()

        other_skill, other_version = self.make_skill_version(
            name="plan-other", stage=PLAN_STAGE, version="9.9.9",
        )
        lock.skill_version = other_version
        lock.skill = other_skill
        lock.save(update_fields=["skill_version", "skill"])

        with self.assertRaises(ValidationError) as ctx:
            StageExecutionContextService.resolve(
                context_id=context.pk, project_id=self.project.pk, user=self.lead,
            )
        self.assertIn("版本", str(ctx.exception))


class ContextApiTests(DispatchContextBaseTests):
    """``GET /execution-contexts/{id}/``：业务页面唯一的取值入口。"""

    def setUp(self):
        super().setUp()
        # SkillHubBaseTests 继承 Django TestCase，``self.client`` 没有
        # ``force_authenticate``；必须换成 DRF 的 APIClient。
        self.client = APIClient()

    def test_retrieve_returns_context(self):
        plan = self._plan()
        self.client.force_authenticate(self.executor)

        response = self.client.get(
            f"/api/knowledge-evolution/execution-contexts/{plan['execution_context_id']}/",
            {"project": self.project.pk},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        data = body.get("data", body)
        self.assertEqual(data["workflow_id"], self.workflow_id)
        self.assertEqual(data["stage"], PLAN_STAGE)
        self.assertEqual(data["attempt_id"], plan["attempt_id"])

    def test_retrieve_requires_project_param(self):
        plan = self._plan()
        self.client.force_authenticate(self.lead)

        response = self.client.get(
            f"/api/knowledge-evolution/execution-contexts/{plan['execution_context_id']}/",
        )
        self.assertEqual(response.status_code, 400)

    def test_retrieve_rejects_other_project(self):
        plan = self._plan()
        self.client.force_authenticate(self.lead)

        response = self.client.get(
            f"/api/knowledge-evolution/execution-contexts/{plan['execution_context_id']}/",
            {"project": self.other_project.pk},
        )
        self.assertEqual(response.status_code, 400)
