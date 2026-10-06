"""T04：Agent 运行状态与正式产出事件。

这组用例盯的是"飞轮能不能如实说出这一轮现在到哪了"：

- attempt 必须先被推进到 ``running`` 并绑上 session——否则用户看到的一直是
  "已派发"，而"已派发"和"正在跑"在页面上没有区别；
- 正式产出发布之后 attempt 要走到 ``completed`` 并绑 output；
- Agent 中途失败也要留痕，且**错误摘要必须受限**（attempt 会被页面读出来展示，
  异常全文可能带路径与凭据）；
- 请求里带的 attempt 必须过项目、阶段、终态三道校验，否则"拿 A 阶段的 attempt
  跑 B 阶段"或"重放一个已结束的 attempt"都会静默发生。
"""
from __future__ import annotations

from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from knowledge_evolution.operations import StageExecutionAttemptService
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.workflow_models import StageExecutionAttempt
from orchestrator_integration.agent_loop_view import AgentLoopStreamAPIView

STAGE = "test_plan_generation"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AttemptWriteBackBase(SkillHubBaseTests):
    def _dispatch(self, *, stage=STAGE, workflow_id="wf-t04"):
        attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id=workflow_id, stage=stage,
            actor=self.lead, idempotency_key=f"t04-{stage}",
        )
        return attempt


class AdvanceOnPublishTests(AttemptWriteBackBase):
    """产出发布后 attempt 必须走到终态并绑定 output。"""

    def test_running_then_completed_with_output(self):
        attempt = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt, session_id="sess-1")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "running")
        self.assertEqual(attempt.session_id, "sess-1")
        self.assertIsNotNone(attempt.started_at)

        output = self.make_output(task_type=STAGE, workflow_id="wf-t04", skill_version=None)
        request = SimpleNamespace(_flywheel_attempt=attempt)
        async_to_sync(AgentLoopStreamAPIView()._advance_attempt_on_publish)(
            request, ("trace-id", str(output.pk)),
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "completed")
        self.assertEqual(attempt.output_id, output.pk)
        self.assertIsNotNone(attempt.output_published_at)
        self.assertIsNotNone(attempt.finished_at)

    def test_no_attempt_is_a_safe_noop(self):
        """旁路入口没有 attempt，回写逻辑必须安静地什么都不做。"""
        request = SimpleNamespace(_flywheel_attempt=None)
        result = async_to_sync(AgentLoopStreamAPIView()._advance_attempt_on_publish)(
            request, ("trace-id", "some-output"),
        )
        self.assertEqual(result, ("trace-id", "some-output"))

    def test_missing_output_id_is_a_safe_noop(self):
        attempt = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt)
        request = SimpleNamespace(_flywheel_attempt=attempt)

        async_to_sync(AgentLoopStreamAPIView()._advance_attempt_on_publish)(request, None)
        async_to_sync(AgentLoopStreamAPIView()._advance_attempt_on_publish)(request, ())

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "running")


class FailureRecordingTests(AttemptWriteBackBase):
    """失败留痕，且摘要受限。"""

    def test_failure_records_code_and_limited_summary(self):
        attempt = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt, session_id="sess-2")
        request = SimpleNamespace(_flywheel_attempt=attempt)

        exc = RuntimeError("模型调用超时\nAuthorization: Bearer super-secret-token")
        async_to_sync(AgentLoopStreamAPIView()._mark_attempt_failed)(request, exc)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(attempt.error_code, "RuntimeError")
        # 摘要只留首行：attempt 会被飞轮页面直接展示，整段异常可能带凭据与路径。
        self.assertIn("模型调用超时", attempt.error_summary)
        self.assertNotIn("super-secret-token", attempt.error_summary)
        self.assertEqual(len(attempt.error_summary.splitlines()), 1)

    def test_failure_on_terminal_attempt_does_not_raise(self):
        """产出发完之后再出别的错（例如会话标题总结）不应把接口炸掉。"""
        attempt = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt)
        StageExecutionAttemptService.complete(attempt)
        request = SimpleNamespace(_flywheel_attempt=attempt)

        async_to_sync(AgentLoopStreamAPIView()._mark_attempt_failed)(request, RuntimeError("后置步骤失败"))

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "completed")

    def test_failure_without_attempt_is_a_safe_noop(self):
        request = SimpleNamespace(_flywheel_attempt=None)
        async_to_sync(AgentLoopStreamAPIView()._mark_attempt_failed)(request, RuntimeError("x"))


class AttemptRequestGuardTests(AttemptWriteBackBase):
    """请求里的 attempt_id 必须过项目 / 阶段 / 终态三道校验。

    这些校验发生在加载 LLM 之前，因此不需要真实模型即可验证——
    这正是"把不可信输入挡在花钱之前"的意义。
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        # agent-loop 是一个普通 Django View，走的是自己实现的 JWT 校验
        # （``HTTP_AUTHORIZATION: Bearer <token>``），不认 DRF 的
        # ``force_authenticate``——那条路径只对 DRF 请求对象生效。
        token = RefreshToken.for_user(self.lead).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.url = "/api/orchestrator/agent-loop/"

    def _post(self, **overrides):
        payload = {
            "message": "生成测试方案",
            "project_id": self.project.pk,
            "module_key": STAGE,
            "workflow_id": "wf-t04",
            "stream": False,
        }
        payload.update(overrides)
        return self.client.post(self.url, payload, format="json")

    def test_unknown_attempt_is_rejected(self):
        response = self._post(attempt_id="00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 400)

    def test_cross_project_attempt_is_rejected(self):
        attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.other_project, workflow_id="wf-other", stage=STAGE,
            actor=self.lead, idempotency_key="t04-other",
        )

        response = self._post(attempt_id=str(attempt.pk))
        self.assertEqual(response.status_code, 400)

    def test_stage_mismatch_is_rejected(self):
        attempt = self._dispatch(stage="testcase_generation", workflow_id="wf-t04b")

        response = self._post(attempt_id=str(attempt.pk))
        self.assertEqual(response.status_code, 400)

    def test_terminal_attempt_is_rejected(self):
        attempt = self._dispatch()
        StageExecutionAttemptService.mark_running(attempt)
        StageExecutionAttemptService.complete(attempt)

        response = self._post(attempt_id=str(attempt.pk))
        self.assertEqual(response.status_code, 409)

    def test_malformed_attempt_id_is_rejected(self):
        response = self._post(attempt_id="not-a-uuid")
        self.assertEqual(response.status_code, 400)
