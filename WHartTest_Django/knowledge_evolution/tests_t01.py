"""T01：阶段执行尝试（``StageExecutionAttempt``）模型、状态机与幂等。

每个用例对应一条会在真实链路上出问题的路：

- Agent 还没产出时，飞轮要能查到"这一轮跑过"——没有 attempt 的话，
  失败的运行只能靠翻日志；
- 重复点击「执行」不能变成两条并行 attempt（幂等键）；
- 失败记录必须保留，重试**新建**一条并指回去，而不是把 failed 改写成成功；
- 跨项目的流程锁、父产出、飞轮流程必须被拒（否则接口能返回 201，
  但 attempt 永远归不到正确流程，还会泄漏别的项目的产出 ID）；
- 已进入终态的 attempt 不能再被改（否则一次失败会从记录里消失）。
"""
from __future__ import annotations

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from knowledge_evolution.operations import StageExecutionAttemptService
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.workflow_models import (
    ATTEMPT_ACTIVE_STATES,
    ATTEMPT_TERMINAL_STATES,
    FlywheelRun,
    StageExecutionAttempt,
    WorkflowSkillLock,
    attempt_transition_allowed,
)

STAGE = "test_plan_generation"
STAGE2 = "testcase_generation"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AttemptBaseTests(SkillHubBaseTests):
    stage = STAGE

    def _dispatch(self, *, project=None, workflow_id="wf-t01", stage=None,
                  idempotency_key="idem-1", **kwargs):
        return StageExecutionAttemptService.dispatch(
            project=project or self.project,
            workflow_id=workflow_id,
            stage=stage or self.stage,
            actor=kwargs.pop("actor", self.lead),
            idempotency_key=idempotency_key,
            **kwargs,
        )

    def _lock(self, skill_version, *, project=None, workflow_id="wf-t01", stage=None):
        return WorkflowSkillLock.objects.create(
            project=project or self.project,
            workflow_id=workflow_id,
            lock_key=f"stage:{stage or self.stage}",
            scope="workflow",
            stage=stage or self.stage,
            skill=skill_version.skill,
            skill_version=skill_version,
            package_sha256=skill_version.package_sha256,
            locked_by=self.lead,
        )


class CreateAndQueryTests(AttemptBaseTests):
    """派发即落库：正式产出之前也要可查。"""

    def test_dispatched_attempt_exists_before_any_output(self):
        attempt, created = self._dispatch()

        self.assertTrue(created)
        self.assertEqual(attempt.status, "dispatched")
        self.assertIsNone(attempt.output_id)
        self.assertIsNotNone(attempt.dispatched_at)
        self.assertEqual(attempt.project_id, self.project.pk)
        self.assertEqual(attempt.workflow_id, "wf-t01")
        self.assertEqual(attempt.stage, STAGE)

    def test_planned_attempt_has_no_dispatch_timestamp(self):
        attempt, created = StageExecutionAttemptService.plan(
            project=self.project, workflow_id="wf-t01", stage=STAGE,
            actor=self.lead, idempotency_key="plan-1",
        )

        self.assertTrue(created)
        self.assertEqual(attempt.status, "planned")
        self.assertIsNone(attempt.dispatched_at)

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._dispatch(stage="not_a_stage")

    def test_blank_workflow_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._dispatch(workflow_id="   ")

    def test_detail_keeps_only_whitelisted_keys(self):
        """attempt 会被飞轮页面读出来展示，不能顺手把凭据/原始请求整包落库。"""
        attempt, _ = self._dispatch(detail={
            "channel": "agent_loop",
            "module_key": STAGE,
            "authorization": "Bearer secret-token",
            "raw_request": {"password": "p"},
        })

        self.assertEqual(attempt.detail.get("channel"), "agent_loop")
        self.assertEqual(attempt.detail.get("module_key"), STAGE)
        self.assertNotIn("authorization", attempt.detail)
        self.assertNotIn("raw_request", attempt.detail)


class IdempotencyTests(AttemptBaseTests):
    """幂等键：重复派发只应得到同一条 attempt。"""

    def test_same_key_returns_same_attempt(self):
        first, created_first = self._dispatch()
        second, created_second = self._dispatch()

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            StageExecutionAttempt.objects.filter(
                project=self.project, workflow_id="wf-t01",
            ).count(),
            1,
        )

    def test_different_keys_create_separate_attempts(self):
        first, _ = self._dispatch(idempotency_key="idem-a")
        second, created = self._dispatch(idempotency_key="idem-b")

        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)

    def test_blank_key_does_not_collide(self):
        """空键表示调用方不要幂等保证（旁路入口），两条都该建出来。"""
        first, created_first = self._dispatch(idempotency_key="")
        second, created_second = self._dispatch(idempotency_key="")

        self.assertTrue(created_first)
        self.assertTrue(created_second)
        self.assertNotEqual(first.pk, second.pk)

    def test_same_key_in_other_project_is_independent(self):
        first, _ = self._dispatch()
        second, created = self._dispatch(project=self.other_project)

        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)


class IsolationTests(AttemptBaseTests):
    """跨项目的流程锁、父产出、飞轮流程一律拒绝。"""

    def test_parent_output_from_other_project_is_rejected(self):
        foreign = self.make_output(task_type=STAGE2, project=self.other_project)

        with self.assertRaises(ValidationError):
            self._dispatch(parent_output_ids=[str(foreign.pk)])

    def test_missing_parent_output_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._dispatch(parent_output_ids=["00000000-0000-0000-0000-000000000000"])

    def test_parent_output_in_same_project_is_accepted(self):
        own = self.make_output(task_type=STAGE2, project=self.project)

        attempt, _ = self._dispatch(parent_output_ids=[str(own.pk)])

        self.assertEqual(attempt.parent_output_ids, [str(own.pk)])

    def test_flywheel_run_from_other_project_is_rejected(self):
        foreign_run = FlywheelRun.objects.create(
            project=self.other_project, workflow_id="wf-t01", entry_type="flywheel",
        )

        with self.assertRaises(ValidationError):
            self._dispatch(flywheel_run=foreign_run)

    def test_flywheel_run_with_mismatched_workflow_is_rejected(self):
        run = FlywheelRun.objects.create(
            project=self.project, workflow_id="wf-other", entry_type="flywheel",
        )

        with self.assertRaises(ValidationError):
            self._dispatch(flywheel_run=run)

    def test_matching_flywheel_run_is_accepted(self):
        run = FlywheelRun.objects.create(
            project=self.project, workflow_id="wf-t01", entry_type="flywheel",
        )

        attempt, _ = self._dispatch(flywheel_run=run)

        self.assertEqual(attempt.flywheel_run_id, run.pk)

    def test_skill_version_must_match_workflow_lock(self):
        _skill, locked = self.make_skill_version(name="plan-skill", version="1.0.0", stage=STAGE)
        _skill2, other = self.make_skill_version(name="plan-skill", version="2.0.0", stage=STAGE)
        self._lock(locked)

        with self.assertRaises(ValidationError):
            self._dispatch(skill_version=other)

        attempt, _ = self._dispatch(skill_version=locked, idempotency_key="idem-lock")
        self.assertEqual(attempt.skill_version_id, locked.pk)

    def test_package_hash_falls_back_to_lock(self):
        """未显式给版本时，包哈希要沿用流程锁——否则 attempt 失去版本溯源。"""
        _skill, locked = self.make_skill_version(name="plan-skill", version="1.0.0", stage=STAGE)
        self._lock(locked)

        attempt, _ = self._dispatch()

        self.assertEqual(attempt.skill_package_sha256, locked.package_sha256)


class StateMachineTests(AttemptBaseTests):
    """状态机：只能前进或转失败终态，终态不可再改。"""

    def test_happy_path_reaches_completed(self):
        attempt, _ = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt, session_id="sess-1")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "running")
        self.assertEqual(attempt.session_id, "sess-1")
        self.assertIsNotNone(attempt.started_at)

        StageExecutionAttemptService.mark_output_published(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "output_published")
        self.assertIsNotNone(attempt.output_published_at)

        StageExecutionAttemptService.complete(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "completed")
        self.assertIsNotNone(attempt.finished_at)
        self.assertTrue(attempt.is_terminal)

    def test_backward_transition_is_rejected(self):
        attempt, _ = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt)
        attempt.refresh_from_db()

        with self.assertRaises(ValidationError):
            StageExecutionAttemptService.transition(attempt, "dispatched")

    def test_terminal_attempt_cannot_be_updated(self):
        attempt, _ = self._dispatch()
        StageExecutionAttemptService.fail(attempt, error_code="boom", error_summary="模型超时")
        attempt.refresh_from_db()

        self.assertIn(attempt.status, ATTEMPT_TERMINAL_STATES)
        with self.assertRaises(ValidationError):
            StageExecutionAttemptService.complete(attempt)

    def test_skipping_running_is_rejected(self):
        attempt, _ = self._dispatch()

        with self.assertRaises(ValidationError):
            StageExecutionAttemptService.transition(attempt, "completed")

    def test_unknown_target_status_is_rejected(self):
        attempt, _ = self._dispatch()

        with self.assertRaises(ValidationError):
            StageExecutionAttemptService.transition(attempt, "finished")

    def test_same_status_is_idempotent_noop(self):
        """SSE 重连/回调重放会重复上报同一状态，重放不该被当成错误。"""
        attempt, _ = self._dispatch()

        result = StageExecutionAttemptService.transition(attempt, "dispatched")

        self.assertEqual(result.pk, attempt.pk)
        self.assertEqual(result.status, "dispatched")

    def test_failure_keeps_restricted_error_summary(self):
        attempt, _ = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt)
        attempt.refresh_from_db()

        StageExecutionAttemptService.fail(
            attempt, error_code="agent_error", error_summary="工具调用超时",
        )
        attempt.refresh_from_db()

        self.assertEqual(attempt.status, "failed")
        self.assertEqual(attempt.error_code, "agent_error")
        self.assertEqual(attempt.error_summary, "工具调用超时")
        self.assertIsNotNone(attempt.finished_at)

    def test_transition_table_is_self_consistent(self):
        """状态机真值自检：终态无出边，活跃态都能走到某个终态。"""
        for status in ATTEMPT_TERMINAL_STATES:
            for target in ATTEMPT_TERMINAL_STATES | ATTEMPT_ACTIVE_STATES:
                self.assertFalse(attempt_transition_allowed(status, target))
        for status in ATTEMPT_ACTIVE_STATES:
            self.assertTrue(
                any(
                    attempt_transition_allowed(status, target)
                    for target in ATTEMPT_TERMINAL_STATES
                ),
                f"{status} 无法走到任何终态",
            )


class RetryTests(AttemptBaseTests):
    """重试：保留原记录，新建并关联。"""

    def test_retry_creates_new_linked_attempt(self):
        original, _ = self._dispatch()
        StageExecutionAttemptService.mark_running(original)
        original.refresh_from_db()
        StageExecutionAttemptService.fail(original, error_code="boom", error_summary="失败")
        original.refresh_from_db()

        retry_attempt, created = StageExecutionAttemptService.retry(original, actor=self.lead)

        self.assertTrue(created)
        self.assertNotEqual(retry_attempt.pk, original.pk)
        self.assertEqual(retry_attempt.retry_of_id, original.pk)
        self.assertEqual(retry_attempt.status, "dispatched")
        self.assertEqual(retry_attempt.workflow_id, original.workflow_id)
        self.assertEqual(retry_attempt.stage, original.stage)
        # 原记录保持终态，不被改写
        original.refresh_from_db()
        self.assertEqual(original.status, "failed")
        self.assertEqual(original.retries.count(), 1)

    def test_retry_requires_terminal_state(self):
        attempt, _ = self._dispatch()

        with self.assertRaises(ValidationError):
            StageExecutionAttemptService.retry(attempt, actor=self.lead)

    def test_repeated_retry_of_same_attempt_is_idempotent(self):
        original, _ = self._dispatch()
        StageExecutionAttemptService.fail(original, error_code="boom", error_summary="失败")
        original.refresh_from_db()

        first, _ = StageExecutionAttemptService.retry(original, actor=self.lead)
        second, created = StageExecutionAttemptService.retry(original, actor=self.lead)

        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)

    def test_cancelled_attempt_can_be_retried(self):
        original, _ = self._dispatch()
        StageExecutionAttemptService.cancel(original, error_summary="用户取消")
        original.refresh_from_db()

        retry_attempt, created = StageExecutionAttemptService.retry(original, actor=self.lead)

        self.assertTrue(created)
        self.assertEqual(retry_attempt.retry_of_id, original.pk)


class AttemptApiTests(AttemptBaseTests):
    url = "/api/knowledge-evolution/stage-attempts/"

    def setUp(self):
        super().setUp()
        self.attempt, _ = self._dispatch()
        # 显式换成 DRF 的 APIClient：基类是 Django TestCase，
        # 默认的 self.client 没有 force_authenticate。
        self.client = APIClient()
        self.client.force_authenticate(self.lead)

    def _rows(self, response):
        payload = response.json()["data"]
        if isinstance(payload, dict):
            return payload.get("results", [])
        return payload

    def test_retrieve_returns_status_and_skill_readouts(self):
        response = self.client.get(f"{self.url}{self.attempt.pk}/")

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "dispatched")
        self.assertEqual(data["stage"], STAGE)
        self.assertIsNone(data["output"])
        self.assertIs(data["is_terminal"], False)

    def test_failed_attempt_is_still_visible(self):
        StageExecutionAttemptService.fail(
            self.attempt, error_code="boom", error_summary="失败",
        )

        response = self.client.get(f"{self.url}{self.attempt.pk}/")

        data = response.json()["data"]
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["error_code"], "boom")
        self.assertIs(data["is_terminal"], True)

    def test_list_is_project_scoped(self):
        foreign, _ = self._dispatch(project=self.other_project, idempotency_key="idem-other")

        response = self.client.get(self.url, {"project": self.project.pk})
        ids = {row["id"] for row in self._rows(response)}

        self.assertIn(str(self.attempt.pk), ids)
        self.assertNotIn(str(foreign.pk), ids)

    def test_retry_requires_test_lead(self):
        StageExecutionAttemptService.fail(self.attempt, error_code="boom", error_summary="失败")

        self.client.force_authenticate(self.executor)
        response = self.client.post(f"{self.url}{self.attempt.pk}/retry/", {}, format="json")
        self.assertEqual(response.status_code, 403, response.content)

        self.client.force_authenticate(self.lead)
        response = self.client.post(f"{self.url}{self.attempt.pk}/retry/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["retry_of"], str(self.attempt.pk))

    def test_retry_rejects_live_attempt(self):
        self.client.force_authenticate(self.lead)

        response = self.client.post(f"{self.url}{self.attempt.pk}/retry/", {}, format="json")

        self.assertEqual(response.status_code, 400, response.content)


class AttemptModelTests(TestCase):
    """纯模型层自检：默认值与终态判定不依赖服务层。"""

    def test_defaults(self):
        attempt = StageExecutionAttempt()
        self.assertEqual(attempt.status, "planned")
        self.assertEqual(attempt.entry_type, "flywheel")
        self.assertEqual(attempt.parent_output_ids, [])
        self.assertEqual(attempt.detail, {})
        self.assertFalse(attempt.is_terminal)
