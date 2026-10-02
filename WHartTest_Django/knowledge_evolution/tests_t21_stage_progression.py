"""T21：门禁的"无评分 / 人工确认 / 人工评分 / 逐阶段执行"语义。

本文件把四件容易被做成"看起来能用"的事钉死成断言：

1. **没有评分 ≠ 不通过**：``evaluate`` 拿不到分时必须落 ``unscored``，
   否则链路会被一条并不存在的负面结论挡住；
2. **没评分也能推进**：``confirm`` 从 ``pending`` / ``unscored`` 出发放行，
   且下一阶段的可进入性必须跟着变——这正是"不要求打分了才能进入下一步"；
3. **人工评分是一份有结论的判断**：>= 阈值才通过，< 阈值仍然是 ``failed``
   （人工评分不等于人工放行），且人工结论不会被一次自动重算抹掉；
4. **逐阶段执行是真约束**：越阶段"执行"必须被拒，已派发未产出的阶段要让
   步骤条显示 ``running`` 而不是 ``pending``。
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

from .models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    GenerationOutput,
    RetrievalTrace,
)
from .operations import WORKFLOW_STAGE_ORDER, WorkflowGateService
from .workflow_models import GATE_PASSING_STATES, WorkflowStageGate

PLAN, CASES, EXECUTION, REPORT = WORKFLOW_STAGE_ORDER
BASE = "/api/knowledge-evolution/operations/"


class StageProgressionBaseTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.lead = user_model.objects.create_user("lead", password="x")
        self.executor = user_model.objects.create_user("executor", password="x")
        self.project = Project.objects.create(name="逐阶段推进项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.client = APIClient()
        self.client.force_authenticate(self.executor)

    # ---- 夹具 ----------------------------------------------------------

    def publish(self, stage, workflow_id="wf-1"):
        """把某阶段的产出按统一协议登记进来（走真实的 gate 创建路径）。"""
        trace = RetrievalTrace.objects.create(
            project=self.project, user=self.executor, task_type=stage,
            task_id=f"{workflow_id}-{stage}", query=stage,
        )
        output = GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type=stage,
            task_id=trace.task_id, content="{}", output_hash=f"{workflow_id}-{stage}",
            metadata={"protocol": {"workflow_id": workflow_id, "stage": stage}},
        )
        WorkflowGateService.register_output(output)
        return output

    def gate(self, stage, workflow_id="wf-1"):
        return WorkflowStageGate.objects.get(
            project=self.project, workflow_id=workflow_id, stage=stage
        )

    def status(self, workflow_id="wf-1"):
        response = self.client.get(
            f"{BASE}workflow-status/",
            {"project": self.project.id, "workflow_id": workflow_id},
        )
        self.assertEqual(response.status_code, 200)
        return {item["stage"]: item for item in response.data["stages"]}

    def score_api(self, stage, value, workflow_id="wf-1", **extra):
        payload = {
            "project": self.project.id, "workflow_id": workflow_id,
            "stage": stage, "score": value,
        }
        payload.update(extra)
        return self.client.post(f"{BASE}score-workflow-stage/", payload, format="json")


class UnscoredSemanticsTests(StageProgressionBaseTests):
    """"没有评分"必须与"评了不达标"分开。"""

    def test_evaluate_without_scores_lands_unscored_not_failed(self):
        self.publish(PLAN)
        gate = WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)
        self.assertEqual(gate.status, "unscored")
        self.assertEqual(gate.scores, {})
        self.assertIn("无评分", gate.reason)

    def test_unscored_blocks_next_stage_but_message_points_at_both_ways_out(self):
        self.publish(PLAN)
        WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)
        with self.assertRaises(Exception) as ctx:
            WorkflowGateService.assert_can_enter(self.project.id, "wf-1", CASES)
        message = str(ctx.exception)
        self.assertIn(PLAN, message)
        self.assertIn("无法进入 testcase_generation", message)
        # 提示必须给出可执行动作，而不是只说"状态是 unscored"：
        # 没有下一步动作的报错，运维看到只会卡在那里。
        self.assertIn("人工确认", message)

    def test_unscored_stage_is_still_observable_as_unscored(self):
        self.publish(PLAN)
        WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)
        stages = self.status()
        self.assertEqual(stages[PLAN]["state"], "unscored")
        self.assertTrue(stages[PLAN]["gate"]["confirmable"])
        self.assertTrue(stages[PLAN]["gate"]["scorable"])
        self.assertFalse(stages[PLAN]["gate"]["passed"])
        # 下一阶段仍被挡住，且后端如实标出 blocked_at。
        self.assertEqual(stages[CASES]["state"], "blocked")


class ConfirmProgressionTests(StageProgressionBaseTests):
    """人工确认：不要求先有评分，确认后下一阶段即可进入。"""

    def confirm_api(self, stage, workflow_id="wf-1", **extra):
        payload = {"project": self.project.id, "workflow_id": workflow_id, "stage": stage}
        payload.update(extra)
        return self.client.post(f"{BASE}confirm-workflow-stage/", payload, format="json")

    def test_confirm_from_unscored_unlocks_next_stage(self):
        self.publish(PLAN)
        WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)

        response = self.confirm_api(PLAN, reason="评测未接通，先推进")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "confirmed")
        self.assertEqual(response.data["decided_by"], "executor")

        stages = self.status()
        self.assertEqual(stages[PLAN]["state"], "confirmed")
        self.assertTrue(stages[PLAN]["gate"]["passed"])
        # 核心断言：确认之后下一阶段从 blocked 变成 ready。
        self.assertEqual(stages[CASES]["state"], "ready")
        self.assertTrue(stages[CASES]["can_enter"])
        # 而且真的能进入（接口层不会再挡）。
        WorkflowGateService.assert_can_enter(self.project.id, "wf-1", CASES)

    def test_confirm_from_pending_is_allowed(self):
        self.publish(PLAN)
        response = self.confirm_api(PLAN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "confirmed")
        # 没填原因时也要留下可读的默认留痕，不能是空字符串。
        self.assertTrue(self.gate(PLAN).reason)

    def test_confirm_is_refused_on_failed_gate(self):
        """评测已经判"不达标"时，一次普通点击不该把它变成"已通过"。"""
        self.publish(PLAN)
        WorkflowGateService.score(self.gate(PLAN), self.executor, 30)
        self.assertEqual(self.gate(PLAN).status, "failed")

        response = self.confirm_api(PLAN)
        self.assertEqual(response.status_code, 400)
        self.assertIn("强制放行", str(response.data))

    def test_confirmed_gate_is_not_reverted_by_reevaluate(self):
        self.publish(PLAN)
        self.confirm_api(PLAN)
        gate = WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)
        self.assertEqual(gate.status, "confirmed")
        self.assertIn(gate.status, GATE_PASSING_STATES)

    def test_new_output_resets_confirmed_gate_to_pending(self):
        """产出换了 = 人工判断作废，新内容必须重新过门禁。"""
        self.publish(PLAN)
        self.confirm_api(PLAN)
        self.publish(PLAN)  # 重跑该阶段，产出被替换
        gate = self.gate(PLAN)
        self.assertEqual(gate.status, "pending")
        self.assertNotIn("manual_score", gate.detail or {})


class ManualScoreTests(StageProgressionBaseTests):
    """人工评分：百分制输入、0-1 存储、按阈值判通过。"""

    def test_score_at_or_above_threshold_passes(self):
        self.publish(PLAN)
        response = self.score_api(PLAN, 85, reason="方案覆盖完整")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "passed")
        # 对外百分制、对内 0-1：量纲必须与自动评测一致，否则阈值 0.7 有两种解释。
        self.assertAlmostEqual(self.gate(PLAN).scores["manual"], 0.85, places=4)
        self.assertEqual(self.gate(PLAN).detail["manual_score"]["value"], 85.0)
        self.assertEqual(self.status()[CASES]["state"], "ready")

    def test_score_below_threshold_fails_and_blocks_next_stage(self):
        self.publish(PLAN)
        response = self.score_api(PLAN, 60)
        self.assertEqual(response.data["status"], "failed")
        stages = self.status()
        self.assertEqual(stages[CASES]["state"], "blocked")
        # 未通过时"人工确认"按钮不应该出现，只应给强制放行这条路。
        self.assertFalse(stages[PLAN]["gate"]["confirmable"])

    def test_score_rejects_out_of_range_and_non_numeric(self):
        self.publish(PLAN)
        self.assertEqual(self.score_api(PLAN, 120).status_code, 400)
        self.assertEqual(self.score_api(PLAN, -1).status_code, 400)
        self.assertEqual(self.score_api(PLAN, "优秀").status_code, 400)

    def test_score_is_refused_after_confirm(self):
        self.publish(PLAN)
        self.client.post(
            f"{BASE}confirm-workflow-stage/",
            {"project": self.project.id, "workflow_id": "wf-1", "stage": PLAN},
            format="json",
        )
        response = self.score_api(PLAN, 90)
        self.assertEqual(response.status_code, 400)

    def test_manual_score_can_be_corrected(self):
        self.publish(PLAN)
        self.score_api(PLAN, 40)
        self.assertEqual(self.gate(PLAN).status, "failed")
        self.score_api(PLAN, 90)
        self.assertEqual(self.gate(PLAN).status, "passed")

    def test_manual_score_survives_reevaluate_without_scores(self):
        """自动重算不得抹掉人工分：状态 passed 而 scores 空，页面解释不通。"""
        self.publish(PLAN)
        self.score_api(PLAN, 88)
        gate = WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)
        self.assertEqual(gate.status, "passed")
        self.assertAlmostEqual(gate.scores["manual"], 0.88, places=4)
        self.assertEqual(gate.detail.get("auto_scores"), {})

    def test_manual_score_is_not_overwritten_by_real_auto_scores(self):
        """有真实分层评分时，人工结论仍优先，机器分落 detail 作补充证据。"""
        output = self.publish(PLAN)
        self.score_api(PLAN, 55)  # 人工判不合格
        suite = EvaluationSuite.objects.create(
            project=self.project, name="套件", suite_type="regression",
            task_type=output.task_type,
        )
        case = EvaluationCase.objects.create(
            suite=suite, case_number=1, task_type=output.task_type,
            input_payload={}, split="regression", source_output=output,
        )
        run = EvaluationRun.objects.create(suite=suite, status="completed")
        EvaluationResult.objects.create(
            run=run, case=case, status="completed",
            l0_score=1.0, l1_score=1.0, l2_score=1.0, l3_score=1.0,
        )
        gate = WorkflowGateService.evaluate(self.gate(PLAN), actor=self.lead)
        self.assertEqual(gate.status, "failed")  # 人工结论优先
        self.assertAlmostEqual(gate.scores["manual"], 0.55, places=4)
        self.assertEqual(gate.detail["auto_scores"]["l0"], 1.0)


class StageExecutionPlanTests(StageProgressionBaseTests):
    """逐阶段执行：越阶段必须被拒，"已派发未产出"要显示 running。"""

    def execute_api(self, stage, workflow_id="wf-1"):
        return self.client.post(
            f"{BASE}execute-workflow-stage/",
            {"project": self.project.id, "workflow_id": workflow_id, "stage": stage},
            format="json",
        )

    def test_execute_first_stage_returns_dispatch_plan(self):
        response = self.execute_api(PLAN)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["channel"], "agent")
        self.assertEqual(response.data["module_key"], PLAN)
        self.assertEqual(response.data["requested_by"], "executor")

    def test_execute_out_of_order_is_refused(self):
        """第 3 阶段在第 2 阶段没放行时不能"执行"——否则步骤条只是装饰。"""
        response = self.execute_api(EXECUTION)
        self.assertEqual(response.status_code, 400)

    def test_execute_passes_upstream_output_id_to_the_executor(self):
        plan_output = self.publish(PLAN)
        self.score_api(PLAN, 90)
        response = self.execute_api(CASES)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["parent_output_ids"], [str(plan_output.pk)])

    def test_test_execution_uses_platform_channel(self):
        self.publish(PLAN)
        self.score_api(PLAN, 90)
        self.publish(CASES)
        self.score_api(CASES, 90)
        response = self.execute_api(EXECUTION)
        self.assertEqual(response.status_code, 201)
        # 测试执行有平台内实现（TestExecution + Celery），通道必须是 platform，
        # 页面才会把人引到测试执行页，而不是让他去找 agent。
        self.assertEqual(response.data["channel"], "platform")
        self.assertEqual(response.data["module_key"], "")

    def test_dispatched_stage_shows_running_until_output_arrives(self):
        self.execute_api(PLAN)
        stages = self.status()
        self.assertEqual(stages[PLAN]["state"], "running")
        self.assertEqual(stages[PLAN]["execution"]["requested_by"], "executor")
        # 派发不等于产出：不能把"没产出"说成"已有产出"。
        self.assertIsNone(stages[PLAN]["output"])

        self.publish(PLAN)
        stages = self.status()
        self.assertEqual(stages[PLAN]["state"], "pending")
        self.assertIsNotNone(stages[PLAN]["output"])

    def test_repeat_execution_is_flagged_as_replacing_existing_output(self):
        self.publish(PLAN)
        self.score_api(PLAN, 90)
        response = self.execute_api(PLAN)
        self.assertTrue(response.data["replaces_output"])


class StageOutputViewTests(StageProgressionBaseTests):
    """「查看结果」：三元定位，越项目读不到。"""

    def test_output_endpoint_returns_content_and_gate_evidence(self):
        trace = RetrievalTrace.objects.create(
            project=self.project, user=self.executor, task_type=PLAN,
            task_id="wf-view-1", query=PLAN,
        )
        GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type=PLAN, task_id="wf-view-1",
            content="# 测试方案\n覆盖登录与支付", output_hash="h-view-1",
            metadata={"protocol": {"workflow_id": "wf-view", "stage": PLAN}},
        )
        response = self.client.get(
            f"{BASE}workflow-stage-output/",
            {"project": self.project.id, "workflow_id": "wf-view", "stage": PLAN},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("测试方案", response.data["content"])
        self.assertFalse(response.data["truncated"])

    def test_output_endpoint_refuses_unknown_stage_and_missing_output(self):
        self.assertEqual(
            self.client.get(
                f"{BASE}workflow-stage-output/",
                {"project": self.project.id, "workflow_id": "wf-1", "stage": "no_such_stage"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f"{BASE}workflow-stage-output/",
                {"project": self.project.id, "workflow_id": "wf-1", "stage": PLAN},
            ).status_code,
            400,
        )
