"""T14：把用例审查单次能力接到质量飞轮上（版本锁 → 产出绑定 → 闭环溯源）。

每个用例只证明一件在真实业务里会出错的事：

- 锁有没有真的锁在**用户选中的那个** Skill 上（同名多条时按名字猜会静默用错包）；
- 登记了 Skill 但没有活跃版本时，任务有没有被**真的拦住**（R13 的实际拦截点）；
- 根本没登记 Skill 的项目还能不能用（不能因为引入版本管理把功能整个关掉）；
- 锁定之后有人激活了新版本，跑着的任务会不会被换包；
- 产出有没有把版本一路带下去（断了的话飞轮后半段全都定位不到版本）；
- 溯源能不能指出**第一个断点**，而不是笼统说一句"闭环未完成"。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from knowledge_evolution.lineage import OutputLineageService, STAGE_ORDER
from knowledge_evolution.models import FeedbackEvent
from knowledge_evolution.protocol import ADAPTERS, publish_output
from knowledge_evolution.task_binding import (
    DEFAULT_CASE_REVIEW_SKILL_NAME,
    SkillBindingRefused,
    TaskSkillBindingService,
)
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.workflow_models import WorkflowSkillLock
from skills.runtime import SkillRuntimeUnavailable
from skills.versions import SkillVersionService
from testcases.models import TestCaseReview

#: 用例审查任务的源文件目录同样要落进临时媒体根，不能写项目数据目录。
REVIEW_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t14-")


@override_settings(MEDIA_ROOT=REVIEW_MEDIA_ROOT)
class ReviewBindingTests(SkillHubBaseTests):
    """版本锁策略：锁在谁身上、什么时候拒绝、什么时候回落。"""

    def make_review(self, *, skill=None, skill_name="", source_name="用例.xlsx"):
        return TestCaseReview.objects.create(
            project=self.project, creator=self.lead,
            source_file=SimpleUploadedFile(source_name, b"placeholder"),
            source_name=source_name,
            selected_skill=skill,
            skill_name=skill_name or DEFAULT_CASE_REVIEW_SKILL_NAME,
        )

    def _activate(self, version):
        from knowledge_evolution.capabilities import CapabilityReleaseService

        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")

    # ------------------------------------------------------------ 正常路径

    def test_lock_points_at_the_active_version_of_selected_skill(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review = self.make_review(skill=skill)

        binding = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        self.assertTrue(binding["managed"])
        self.assertEqual(str(binding["skill_version"].pk), str(version.pk))
        lock = WorkflowSkillLock.objects.get(
            project=self.project, workflow_id=str(review.pk),
            lock_key=f"skill:{skill.pk}",
        )
        self.assertEqual(str(lock.skill_version_id), str(version.pk))
        self.assertEqual(lock.package_sha256, version.package_sha256)

    def test_explicit_skill_wins_over_name_lookup(self):
        """项目里有多条候选 Skill 时，必须锁用户选中的那条。

        按名称去猜在"同名/近名"场景下会静默锁错包，而错误的包照样能跑出结果——
        这是最难被发现的一类缺陷：没有任何报错，只有版本溯源对不上。
        """
        wanted, wanted_version = self.make_skill_version(name="custom-review-skill")
        self._activate(wanted_version)
        other, other_version = self.make_skill_version(
            name=DEFAULT_CASE_REVIEW_SKILL_NAME, version="2.0.0",
        )
        self._activate(other_version)
        review = self.make_review(skill=wanted)

        binding = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        self.assertEqual(str(binding["skill_version"].skill_id), str(wanted.pk))
        self.assertNotEqual(str(binding["skill_version"].skill_id), str(other.pk))

    def test_relocking_the_same_task_returns_the_same_version(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review = self.make_review(skill=skill)

        first = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)
        second = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        self.assertEqual(str(first["lock"].pk), str(second["lock"].pk))
        self.assertEqual(WorkflowSkillLock.objects.filter(workflow_id=str(review.pk)).count(), 1)

    # ------------------------------------------------------------ 拒绝路径

    def test_registered_skill_without_active_version_refuses_start(self):
        """登记了 Skill 但没有任何活跃版本 → 必须拒绝，且不留下锁。"""
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        review = self.make_review(skill=skill)

        with self.assertRaises(SkillBindingRefused):
            TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        self.assertFalse(
            WorkflowSkillLock.objects.filter(workflow_id=str(review.pk)).exists()
        )
        version.refresh_from_db()
        self.assertNotEqual(version.state, "active")

    def test_quarantined_skill_is_not_usable(self):
        """被隔离的版本绝不能因为"历史上是活跃的"就继续放行。"""
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        SkillVersionService.quarantine(version, actor=self.lead, reason="安全事件")
        skill.refresh_from_db()
        review = self.make_review(skill=skill)

        with self.assertRaises(SkillBindingRefused):
            TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

    def test_project_without_registered_skill_falls_back(self):
        """项目根本没登记用例审查 Skill → 不拒绝，回落到内置规则。"""
        review = self.make_review()

        binding = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        self.assertFalse(binding["managed"])
        self.assertIsNone(binding["skill_version"])
        self.assertFalse(WorkflowSkillLock.objects.filter(workflow_id=str(review.pk)).exists())

    def test_review_not_left_running_when_start_is_refused(self):
        """拒绝启动时任务必须仍停在 pending，不能留下 running 孤儿。"""
        from testcases.review_service import run_testcase_review

        skill, _version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        review = self.make_review(skill=skill)

        with self.assertRaises(SkillBindingRefused):
            run_testcase_review(review.pk)

        review.refresh_from_db()
        self.assertEqual(review.status, "pending")

    def test_locked_version_survives_a_new_activation(self):
        """锁定后有人激活了新版本，跑着的任务仍用旧包（R4/R8）。"""
        from skills.runtime import SkillRuntimeResolver

        skill, first = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(first)
        review = self.make_review(skill=skill)
        binding = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        _skill, second = self.make_skill_version(
            name=DEFAULT_CASE_REVIEW_SKILL_NAME, version="9.9.9", body="新增护栏",
        )
        self._activate(second)

        resolved = SkillRuntimeResolver.resolve_locked(binding["lock"])
        self.assertEqual(str(resolved.pk), str(first.pk))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class OutputBindingTests(SkillHubBaseTests):
    """产出必须把版本带下去；带了之后溯源要能读到。"""

    def _active_version(self, *, name=DEFAULT_CASE_REVIEW_SKILL_NAME):
        from knowledge_evolution.capabilities import CapabilityReleaseService

        skill, version = self.make_skill_version(name=name)
        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")
        version.refresh_from_db()
        return skill, version

    def test_publish_output_persists_skill_version_and_hash(self):
        skill, version = self._active_version()
        envelope = ADAPTERS["case_review"].build(
            project=self.project, user=self.lead, source_id="review-1",
            workflow_id="wf-1", input_summary="审查用例",
            output={"findings": [], "issues_count": 0},
            skill_version=version,
        )

        result = publish_output(envelope)
        from knowledge_evolution.models import GenerationOutput

        output = GenerationOutput.objects.get(pk=result[1])
        self.assertEqual(str(output.skill_version_id), str(version.pk))
        self.assertEqual(output.skill_package_sha256, version.package_sha256)
        protocol = output.metadata["protocol"]
        self.assertEqual(protocol["skill"]["skill_version_id"], str(version.pk))
        self.assertEqual(protocol["skill"]["package_sha256"], version.package_sha256)
        self.assertEqual(protocol["workflow_id"], "wf-1")

    def test_binding_lookup_reads_the_lock_of_that_task(self):
        skill, version = self._active_version()
        lock = WorkflowSkillLock.objects.create(
            project=self.project, workflow_id="wf-2", lock_key=f"skill:{skill.pk}",
            scope="single", skill=skill, skill_version=version,
            release=version.release, package_sha256=version.package_sha256,
            locked_by=self.lead,
        )

        found = TaskSkillBindingService.binding_for_workflow(
            project=self.project, workflow_id="wf-2",
        )

        self.assertTrue(found["managed"])
        self.assertEqual(found["locks"][0]["id"], str(lock.pk))
        self.assertEqual(found["locks"][0]["version"], version.version)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LineageTests(SkillHubBaseTests):
    """闭环溯源：七段走没走完、断在哪一段。"""

    def make_output_row(self, *, skill_version=None, workflow_id=""):
        from knowledge_evolution.models import GenerationOutput

        trace = self.make_trace(task_type="case_review")
        return GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type="case_review",
            task_id="t14-task", content="产出正文",
            output_hash="t14".ljust(64, "0"),
            skill_version=skill_version,
            metadata={"protocol": {
                "schema_version": "platform-output/v1", "stage": "case_review",
                "workflow_id": workflow_id,
            }},
        )

    def test_unbound_output_does_not_claim_version_binding(self):
        output = self.make_output_row()
        trace = OutputLineageService.trace(output)

        self.assertFalse(trace["stages"]["binding"]["ok"])
        # 第一个断点必须就是版本绑定，而不是被后面的环节盖过去。
        self.assertEqual(trace["broken_at"], "binding")
        self.assertFalse(trace["closed_loop"])

    def test_first_broken_stage_is_the_earliest_gap(self):
        """有反馈没金标 → 断点必须是金标，不能报成更靠后的环节。"""
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        output = self.make_output_row(skill_version=version, workflow_id="wf-lineage")
        FeedbackEvent.objects.create(
            project=self.project, output=output, signal="accepted",
            idempotency_key="t14-fb-1", actor=self.executor,
        )

        trace = OutputLineageService.trace(output)

        self.assertTrue(trace["stages"]["feedback"]["ok"])
        self.assertFalse(trace["stages"]["gold"]["ok"])
        self.assertEqual(trace["broken_at"], "gold")

    def test_closed_loop_requires_release_to_reach_active(self):
        """七段全通才算闭环；只到"候选"不算。"""
        from knowledge_evolution.capabilities import CapabilityReleaseService

        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        output = self.make_output_row(skill_version=version, workflow_id="wf-closed")
        FeedbackEvent.objects.create(
            project=self.project, output=output, signal="defect_confirmed",
            idempotency_key="t14-fb-2", actor=self.executor,
            evidence=[{"reason": "缺少空值检查"}],
        )
        attribution = self.make_attribution(
            output=output, category="prompt_error", state="confirmed",
        )
        from knowledge_evolution.optimization import OptimizationProposalService

        OptimizationProposalService.generate(
            attributions=[attribution], actor=self.lead,
        )

        release = version.release
        release.gate_report = {"passed": True}
        release.state = "awaiting_approval"
        release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(release, actor=self.lead, reason="激活")

        # 产出 → 发布单元的关联靠"同一版本"建立，这里直接补一条锁记录表达这层关系。
        WorkflowSkillLock.objects.create(
            project=self.project, workflow_id="wf-closed",
            lock_key=f"skill:{skill.pk}", scope="single",
            skill=skill, skill_version=version, release=release,
            package_sha256=version.package_sha256, locked_by=self.lead,
        )

        trace = OutputLineageService.trace(output)

        self.assertTrue(trace["stages"]["proposal"]["ok"], trace["stages"])
        self.assertEqual(trace["release"][0]["state"], "active")
        # 金标在真实闭环里由人工标注产生，本用例没有造，因此断点停在 gold，
        # 而不是被 proposal/release 的通过状态掩盖。
        self.assertEqual(trace["broken_at"], "gold")

    def test_stage_order_covers_every_reported_stage(self):
        output = self.make_output_row()
        trace = OutputLineageService.trace(output)
        self.assertEqual(set(trace["stages"]), {code for code, _label in STAGE_ORDER})

    def test_lineage_rejects_unknown_output(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            OutputLineageService.trace("00000000-0000-0000-0000-000000000000")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ReviewServiceIntegrationTests(SkillHubBaseTests):
    """整链路自检：绑定 → 产出 → 溯源 用的必须是同一个版本。"""

    def tearDown(self):
        shutil.rmtree(REVIEW_MEDIA_ROOT, ignore_errors=True)
        super().tearDown()

    def test_locked_version_is_the_one_that_reaches_the_output(self):
        from knowledge_evolution.capabilities import CapabilityReleaseService
        from knowledge_evolution.lineage import OutputLineageService

        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")

        review = TestCaseReview.objects.create(
            project=self.project, creator=self.lead,
            source_file=SimpleUploadedFile("用例.xlsx", b"placeholder"),
            source_name="用例.xlsx", selected_skill=skill, skill_name=skill.name,
        )
        binding = TaskSkillBindingService.bind_case_review(review=review, actor=self.lead)

        envelope = ADAPTERS["case_review"].build(
            project=review.project, user=review.creator, source_id=str(review.pk),
            workflow_id=str(review.pk), input_summary="审查用例",
            output={"findings": [], "issues_count": 0},
            skill_version=binding["skill_version"],
        )
        result = publish_output(envelope)

        from knowledge_evolution.models import GenerationOutput

        output = GenerationOutput.objects.get(pk=result[1])
        trace = OutputLineageService.trace(output)
        self.assertEqual(trace["skill"]["skill_version_id"], str(version.pk))
        self.assertTrue(trace["stages"]["binding"]["ok"])
        self.assertEqual(trace["bindings"][0]["source"], "lock")
        self.assertEqual(trace["bindings"][0]["skill_version_id"], str(version.pk))
