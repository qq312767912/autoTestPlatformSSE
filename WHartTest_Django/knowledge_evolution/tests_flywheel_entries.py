"""T06：四类入口统一 FlywheelRun + 四阶段产出联动。

覆盖 tasks.md T06 的两条验收：
1. 需求拆解 → 方案 → 用例 → 执行/报告形成单一可追溯链；
2. 任一入口产出都能进入同一项目候选审核队列。
"""
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .flywheel_context import FlywheelContextService
from .models import AssetCandidateEvent, GenerationOutput
from .protocol import ADAPTERS, publish_output
from .workflow_models import FlywheelRun
from projects.models import Project, ProjectMember

RUNS = "/api/knowledge-evolution/flywheel-runs/"


class FlywheelEntryTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username="entry-lead", password="pass")
        self.member = User.objects.create_user(username="entry-member", password="pass")
        self.outsider = User.objects.create_user(username="entry-outsider", password="pass")
        self.project = Project.objects.create(name="上证 e 投票", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.member, role="member")
        self.client = APIClient()
        # T14：飞轮入口受项目级灰度开关管辖，未配置视为关闭。本类测的是入口
        # 派生/幂等行为，显式开启，避免把"开关"混进"入口"的判定里。
        from .history_models import ProjectFlywheelSetting
        ProjectFlywheelSetting.objects.create(project=self.project, enabled=True)

    def test_open_derives_deterministic_id_and_is_idempotent(self):
        first = FlywheelContextService.open(
            project=self.project, entry_type="requirement", source_id="doc-9",
            actor=self.lead,
        )
        self.assertEqual(first["workflow_id"], "req:doc-9")
        self.assertTrue(first["created"])
        self.assertTrue(first["derived"])

        # 同一份需求重复进入必须落到同一条链上——随机 ID 会让上一阶段产出变孤儿。
        again = FlywheelContextService.open(
            project=self.project, entry_type="requirement", source_id="doc-9",
            actor=self.lead,
        )
        self.assertEqual(again["run_id"], first["run_id"])
        self.assertFalse(again["created"])
        self.assertEqual(FlywheelRun.objects.filter(project=self.project).count(), 1)

    def test_open_selects_existing_when_workflow_id_given(self):
        derived = FlywheelContextService.open(
            project=self.project, entry_type="requirement", source_id="doc-9",
            actor=self.lead,
        )
        joined = FlywheelContextService.open(
            project=self.project, entry_type="chat", source_id="session-1",
            workflow_id=derived["workflow_id"], actor=self.member,
        )
        self.assertEqual(joined["run_id"], derived["run_id"])
        self.assertEqual(joined["workflow_id"], "req:doc-9")
        self.assertFalse(joined["created"])
        self.assertFalse(joined["derived"])

    def test_four_entries_converge_on_a_single_run(self):
        base = FlywheelContextService.open(
            project=self.project, entry_type="requirement", source_id="doc-9",
            actor=self.lead,
        )["workflow_id"]
        seen = set()
        for entry in ("requirement", "chat", "test_management", "flywheel"):
            context = FlywheelContextService.open(
                project=self.project, entry_type=entry, source_id=f"{entry}-obj",
                workflow_id=base, actor=self.lead,
            )
            seen.add(context["run_id"])
            self.assertEqual(context["workflow_id"], base)
        self.assertEqual(len(seen), 1)
        self.assertEqual(FlywheelRun.objects.filter(project=self.project).count(), 1)

    def test_open_rejects_unknown_entry_type_and_missing_source(self):
        with self.assertRaisesMessage(ValidationError, "未知入口类型"):
            FlywheelContextService.open(
                project=self.project, entry_type="telepathy", source_id="x",
            )
        with self.assertRaisesMessage(ValidationError, "缺少业务对象标识"):
            FlywheelContextService.open(
                project=self.project, entry_type="requirement", source_id="",
            )

    def test_open_api_creates_then_reuses_run(self):
        self.client.force_authenticate(self.member)
        payload = {"project": self.project.pk, "entry_type": "flywheel",
                   "source_id": "manual-1"}
        first = self.client.post(f"{RUNS}open/", payload, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        body = first.json()["data"]
        self.assertEqual(body["workflow_id"], "fly:manual-1")
        self.assertTrue(body["created"])

        second = self.client.post(f"{RUNS}open/", payload, format="json")
        self.assertFalse(second.json()["data"]["created"])
        self.assertEqual(second.json()["data"]["run_id"], body["run_id"])

    def test_manual_flywheel_entry_derives_a_readable_id_without_source(self):
        """人工入口没有业务对象可锚定，也必须能派生——否则页面只能逼用户先编一个 ID。"""
        context = FlywheelContextService.open(
            project=self.project, entry_type="flywheel", source_id="", actor=self.member,
        )
        self.assertTrue(context["workflow_id"].startswith("fly:"))
        self.assertTrue(context["derived"])
        # 两次不填必须得到两条不同的链（人工入口不去重，否则并发发起会互相覆盖）。
        other = FlywheelContextService.open(
            project=self.project, entry_type="flywheel", source_id="", actor=self.member,
        )
        self.assertNotEqual(other["workflow_id"], context["workflow_id"])

    def test_open_api_rejects_non_member(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            f"{RUNS}open/",
            {"project": self.project.pk, "entry_type": "flywheel", "source_id": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requirement_document_entry_opens_the_run(self):
        from requirements.models import RequirementDocument

        document = RequirementDocument.objects.create(
            project=self.project, title="新版科技评价需求", uploader=self.lead,
        )
        self.client.force_authenticate(self.member)
        url = f"/api/requirements/documents/{document.id}/flywheel-run/"
        first = self.client.post(url, {}, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        data = first.json()["data"]
        self.assertEqual(data["workflow_id"], f"req:{document.id}")
        self.assertTrue(data["created"])

        again = self.client.post(url, {}, format="json")
        self.assertEqual(again.json()["data"]["run_id"], data["run_id"])
        self.assertFalse(again.json()["data"]["created"])

        run = FlywheelRun.objects.get(pk=data["run_id"])
        self.assertEqual(run.entry_type, "requirement")
        self.assertEqual(run.requirement_document_ids, [str(document.id)])


class ReviewWorkflowChainTests(TestCase):
    """用例审查必须汇入被审需求那条链，而不是自开一条。"""

    def _review(self, *, document_ids, summary=None):
        class _Review:
            pass

        review = _Review()
        review.requirement_document_ids = list(document_ids)
        review.summary = dict(summary or {})
        return review

    def test_single_requirement_document_pulls_review_into_its_chain(self):
        from testcases.review_service import _resolve_review_workflow_id

        self.assertEqual(
            _resolve_review_workflow_id(self._review(document_ids=["doc-1"])), "req:doc-1",
        )

    def test_explicit_workflow_id_wins(self):
        from testcases.review_service import _resolve_review_workflow_id

        review = self._review(
            document_ids=["doc-1"], summary={"flywheel_workflow_id": "req:doc-9"},
        )
        self.assertEqual(_resolve_review_workflow_id(review), "req:doc-9")

    def test_multiple_documents_fall_back_to_the_entry_default(self):
        from testcases.review_service import _resolve_review_workflow_id

        # 多份需求无法唯一归属，返回空串表示"由入口自建一条"，而不是随便挑一份。
        self.assertEqual(
            _resolve_review_workflow_id(self._review(document_ids=["d1", "d2"])), "",
        )


class FourStageLinkageTests(TestCase):
    """四阶段全部携带 project / workflow / stage / parent_output_ids / skill_version。"""

    def setUp(self):
        self.lead = User.objects.create_user(username="chain-lead", password="pass")
        self.project = Project.objects.create(name="链路项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")

    def _publish(self, *, stage, body, parents=None, workflow_id="req:chain-1"):
        envelope = ADAPTERS[stage].build(
            project=self.project, user=self.lead,
            source_id=f"{stage}-{uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id, input_summary=f"{stage} 输入",
            output=body, parent_output_ids=list(parents or []),
        )
        return GenerationOutput.objects.get(pk=publish_output(envelope)[1])

    def test_stage_context_carries_the_full_contract(self):
        run = FlywheelContextService.create(
            project=self.project, workflow_id="req:chain-1",
            entry_type="requirement", actor=self.lead,
        )
        for stage in ("test_plan_generation", "testcase_generation",
                      "test_execution", "report_generation"):
            context = FlywheelContextService.resolve_stage(run=run, stage=stage)
            self.assertEqual(context["project_id"], self.project.pk)
            self.assertEqual(context["workflow_id"], "req:chain-1")
            self.assertEqual(context["stage"], stage)
            self.assertEqual(context["parent_output_ids"], [])
            # 未登记 Skill 时也要把字段返回（空值），页面据此提示"该阶段无版本溯源"，
            # 而不是因为字段缺失而显示成"未锁定但可用"。
            self.assertIn("skill_version_id", context)
            self.assertFalse(context["managed"])

    def test_chain_outputs_record_parent_ids_and_land_in_one_queue(self):
        outputs = []
        parents: list = []
        for stage in ("test_plan_generation", "testcase_generation",
                      "test_execution", "report_generation"):
            output = self._publish(
                stage=stage, body={"content": f"{stage} 产出"}, parents=parents,
            )
            protocol = (output.metadata or {})["protocol"]
            self.assertEqual(protocol["workflow_id"], "req:chain-1")
            self.assertEqual(protocol["stage"], stage)
            self.assertEqual(protocol["parent_output_ids"], parents)
            outputs.append(output)
            parents = [str(output.pk)]

        # 四段产出形成一条链：每一段都指向上一段，而不是各自独立。
        self.assertEqual(
            [(output.metadata or {})["protocol"]["stage"] for output in outputs],
            ["test_plan_generation", "testcase_generation", "test_execution", "report_generation"],
        )
        # 任一入口的产出都进入同一项目的候选审核队列（T06 验收口径）。
        queued = AssetCandidateEvent.objects.filter(project=self.project)
        self.assertEqual(queued.count(), 4)
        self.assertEqual(
            set(queued.values_list("source_type", flat=True)), {"stage_output"},
        )
        self.assertEqual(
            {event.payload.get("workflow_id") for event in queued}, {"req:chain-1"},
        )

    def test_parent_output_from_another_workflow_is_rejected(self):
        run = FlywheelContextService.create(
            project=self.project, workflow_id="req:chain-1",
            entry_type="requirement", actor=self.lead,
        )
        foreign = self._publish(
            stage="test_plan_generation", body={"content": "别的链的方案"},
            workflow_id="req:other-2",
        )
        with self.assertRaisesMessage(ValidationError, "上游产出不属于当前流程"):
            FlywheelContextService.resolve_stage(
                run=run, stage="testcase_generation", parent_output_ids=[str(foreign.pk)],
            )
