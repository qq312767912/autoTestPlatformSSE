"""T08 验收测试：SkillRuntimeResolver 与运行锁。

覆盖 tasks.md T08 的验收项——"新版本激活不改变运行中任务；解析 P95 小于 50ms；
解析失败阻止新任务但不污染原业务状态"，以及实现要点里的"按项目和能力解析 active
版本；任务创建时固化版本，拒绝跨项目、非 active、隔离或哈希不一致版本"。
"""
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from knowledge_evolution.capabilities import CapabilityReleaseService
from knowledge_evolution.models import GenerationOutput
from knowledge_evolution.services import record_task_output
from knowledge_evolution.workflow_models import WorkflowSkillLock
from projects.models import Project
from skills.models import Skill, SkillVersion
from skills.runtime import SkillRuntimeResolver, SkillRuntimeUnavailable
from skills.versions import SkillVersionService

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-runtime-")

SKILL_MD = """---
name: {name}
description: 用例审查能力
version: {version}
stage: case_review
---
# 用例审查

{body}
"""


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class RuntimeTestBase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="运行时项目")
        self.other_project = Project.objects.create(name="别的项目")
        self.user = User.objects.create_user(username="runtime-user", password="x")
        self.tmp = tempfile.mkdtemp(prefix="pkg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_package(self, *, name="case-review", version="1.0.0", body="规则 v1") -> Path:
        root = Path(self.tmp) / f"{name}-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(
            SKILL_MD.format(name=name, version=version, body=body), encoding="utf-8",
        )
        return root

    def create(self, *, project=None, name="case-review", version="1.0.0", body="规则"):
        project = project or self.project
        return SkillVersionService.create_candidate_from_dir(
            source_dir=self.make_package(name=name, version=version, body=body),
            project=project, actor=self.user,
        )

    def activate(self, version):
        """沿状态机合法边把版本推到 active 并刷新活跃指针。"""
        release = version.release
        release.gate_report = {"passed": True}
        release.save(update_fields=["gate_report"])
        for state in ("validating", "shadow", "awaiting_approval"):
            release.state = state
            release.save(update_fields=["state"])
        CapabilityReleaseService.promote(release, actor=self.user, reason="测试激活")
        version.refresh_from_db()
        return version


class ResolveActiveVersionTests(RuntimeTestBase):
    """新建任务：必须解析到当前 active 版本，否则拒绝启动。"""

    def test_resolves_active_version_by_name(self):
        _skill, version = self.create()
        self.activate(version)
        resolved = SkillRuntimeResolver.require_version(project=self.project, name="case-review")
        self.assertEqual(resolved.id, version.id)

    def test_draft_version_is_not_runnable(self):
        self.create()
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.require_version(project=self.project, name="case-review")

    def test_shadow_version_is_not_runnable(self):
        _skill, version = self.create()
        version.release.state = "shadow"
        version.release.save(update_fields=["state"])
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.require_version(project=self.project, name="case-review")

    def test_quarantined_version_is_not_runnable(self):
        _skill, version = self.create()
        self.activate(version)
        SkillVersionService.quarantine(version, actor=self.user, reason="风险")
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.require_version(project=self.project, name="case-review")

    def test_disabled_skill_is_not_runnable(self):
        skill, version = self.create()
        self.activate(version)
        skill.is_active = False
        skill.save(update_fields=["is_active"])
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.require_version(project=self.project, name="case-review")

    def test_cross_project_version_is_never_resolved(self):
        """同名 Skill 存在于两个项目时，各自只能解析到自己项目那一份。"""
        _s1, v1 = self.create(project=self.project, name="case-review", body="A")
        _s2, v2 = self.create(project=self.other_project, name="case-review", body="B")
        self.activate(v1)
        self.activate(v2)

        mine = SkillRuntimeResolver.require_version(project=self.project, name="case-review")
        theirs = SkillRuntimeResolver.require_version(project=self.other_project, name="case-review")
        self.assertEqual(mine.id, v1.id)
        self.assertEqual(theirs.id, v2.id)
        self.assertEqual(mine.skill.project_id, self.project.id)

    def test_resolve_by_capability(self):
        from knowledge_evolution.capability_models import CapabilityDefinition

        capability = CapabilityDefinition.objects.create(
            project=self.project, kind="skill", name="用例审查",
            stages=["case_review"],
        )
        skill, version = self.create()
        skill.capability = capability
        skill.save(update_fields=["capability"])
        self.activate(version)

        resolved = SkillRuntimeResolver.require_version(
            project=self.project, capability=capability,
        )
        self.assertEqual(resolved.id, version.id)

    def test_resolve_by_stage(self):
        _skill, version = self.create()
        self.activate(version)
        resolved = SkillRuntimeResolver.require_version(project=self.project, stage="case_review")
        self.assertEqual(resolved.id, version.id)

    def test_missing_active_version_returns_none_when_optional(self):
        self.create()
        self.assertIsNone(
            SkillRuntimeResolver.resolve_version(project=self.project, name="case-review")
        )

    def test_failed_resolution_writes_no_business_state(self):
        """解析失败只阻止新任务，不得留下任何业务记录。"""
        self.create()
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.require_version(project=self.project, name="case-review")
        self.assertEqual(WorkflowSkillLock.objects.count(), 0)
        self.assertEqual(GenerationOutput.objects.count(), 0)


class TaskLockTests(RuntimeTestBase):
    """任务创建时固化版本：新版本激活不得改变运行中任务。"""

    def test_lock_pins_current_active_version(self):
        _skill, v1 = self.create(version="1.0.0", body="v1")
        self.activate(v1)

        lock = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-1", name="case-review", actor=self.user,
        )
        self.assertEqual(lock.skill_version_id, v1.id)
        self.assertEqual(lock.package_sha256, v1.package_sha256)
        self.assertEqual(lock.scope, "single")

    def test_lock_is_idempotent(self):
        _skill, v1 = self.create()
        self.activate(v1)
        first = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-1", name="case-review", actor=self.user,
        )
        second = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-1", name="case-review", actor=self.user,
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(WorkflowSkillLock.objects.count(), 1)

    def test_new_activation_does_not_change_running_task(self):
        """核心验收：跑着的任务继续用锁定的版本，新任务用新版本。"""
        skill, v1 = self.create(version="1.0.0", body="v1")
        self.activate(v1)
        lock = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-1", name="case-review", actor=self.user,
        )

        # 期间有人激活了新版本：v1 被退役。
        _s, v2 = self.create(version="2.0.0", body="v2")
        self.activate(v2)
        v1.refresh_from_db()
        self.assertEqual(v1.state, "retired")

        # 已启动任务：仍然解析到 v1（不重新解析活跃版本）。
        locked = SkillRuntimeResolver.resolve_locked(lock)
        self.assertEqual(locked.id, v1.id)

        # 新任务：解析到 v2。
        fresh = SkillRuntimeResolver.require_version(project=self.project, name="case-review")
        self.assertEqual(fresh.id, v2.id)

    def test_lock_survives_repeated_resolution(self):
        _skill, v1 = self.create()
        self.activate(v1)
        SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-2", name="case-review", actor=self.user,
        )
        SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-2", name="case-review", actor=self.user,
        )
        self.assertEqual(
            WorkflowSkillLock.objects.filter(workflow_id="wf-2").count(), 1,
        )

    def test_locked_quarantined_version_is_rejected(self):
        """隔离是安全事件：即使已锁定也必须让任务停下来。"""
        _skill, v1 = self.create()
        self.activate(v1)
        lock = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-3", name="case-review", actor=self.user,
        )
        SkillVersionService.quarantine(v1, actor=self.user, reason="疑似外泄")
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.resolve_locked(lock)

    def test_locked_hash_mismatch_is_rejected(self):
        _skill, v1 = self.create()
        self.activate(v1)
        lock = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-4", name="case-review", actor=self.user,
        )
        lock.package_sha256 = "f" * 64
        lock.save(update_fields=["package_sha256"])
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.resolve_locked(lock)

    def test_locked_missing_version_is_rejected(self):
        _skill, v1 = self.create()
        self.activate(v1)
        lock = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-5", name="case-review", actor=self.user,
        )
        lock.skill_version = None
        lock.skill_version_id = None
        lock.save(update_fields=["skill_version"])
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.resolve_locked(lock)

    def test_allow_missing_returns_none(self):
        self.create()
        lock = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-6", name="case-review",
            actor=self.user, allow_missing=True,
        )
        self.assertIsNone(lock)
        self.assertEqual(WorkflowSkillLock.objects.count(), 0)

    def test_lock_requires_workflow_id(self):
        _skill, v1 = self.create()
        self.activate(v1)
        with self.assertRaises(ValidationError):
            SkillRuntimeResolver.lock_for_task(
                project=self.project, workflow_id="", name="case-review",
            )

    def test_per_stage_lock_keys_are_independent(self):
        """四阶段：同一任务的不同阶段各占一把锁。"""
        _skill, v1 = self.create()
        self.activate(v1)
        first = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-7", stage="test_plan_generation",
            scope="workflow", actor=self.user, allow_missing=True,
        )
        second = SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-7", stage="testcase_generation",
            scope="workflow", actor=self.user, allow_missing=True,
        )
        # 该 Skill 的 manifest.stage 是 case_review，两个阶段都解析不到 → 都为 None，
        # 但 lock_key 必须不同，否则阶段之间会互相顶掉。
        self.assertEqual(
            SkillRuntimeResolver.lock_key_for(stage="test_plan_generation"),
            "stage:test_plan_generation",
        )
        self.assertNotEqual(
            SkillRuntimeResolver.lock_key_for(stage="test_plan_generation"),
            SkillRuntimeResolver.lock_key_for(stage="testcase_generation"),
        )
        self.assertIsNone(first)
        self.assertIsNone(second)

    def test_locks_for_workflow_lists_all(self):
        _skill, v1 = self.create()
        self.activate(v1)
        SkillRuntimeResolver.lock_for_task(
            project=self.project, workflow_id="wf-8", name="case-review", actor=self.user,
        )
        locks = SkillRuntimeResolver.locks_for_workflow(
            project=self.project, workflow_id="wf-8",
        )
        self.assertEqual(len(locks), 1)


class PerformanceTests(RuntimeTestBase):
    """解析性能目标：P95 < 50ms。"""

    def test_resolution_p95_under_50ms(self):
        for index in range(10):
            _skill, version = self.create(name=f"skill-{index}", version="1.0.0")
            self.activate(version)

        samples = []
        for _ in range(40):
            started = time.perf_counter()
            resolved = SkillRuntimeResolver.resolve_version(
                project=self.project, name="skill-5",
            )
            samples.append((time.perf_counter() - started) * 1000)
            self.assertIsNotNone(resolved)

        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        self.assertLess(p95, 50.0, msg=f"P95={p95:.1f}ms samples={samples[-3:]}")


class OutputProtocolTests(RuntimeTestBase):
    """统一产出协议写入 skill_id / skill_version_id / capability_id / package_sha256。"""

    def test_output_records_locked_skill_version(self):
        skill, version = self.create()
        self.activate(version)

        result = record_task_output(
            project=self.project, user=self.user, task_type="case_review",
            task_id="task-1", query="审查这批用例", content="审查结论：通过",
            skill_version=version,
        )
        self.assertIsNotNone(result, "产出记录不应失败")

        output = GenerationOutput.objects.get(task_id="task-1")
        self.assertEqual(output.skill_version_id, version.id)
        self.assertEqual(output.skill_package_sha256, version.package_sha256)
        self.assertEqual(output.metadata["skill_version_id"], str(version.id))
        self.assertEqual(output.metadata["skill_id"], str(skill.id))
        self.assertEqual(output.metadata["package_sha256"], version.package_sha256)

    def test_output_version_is_not_overwritten_by_later_report(self):
        """同一份产出重复上报时补齐溯源，但不得被后来的版本改写历史。"""
        _skill, v1 = self.create(version="1.0.0", body="v1")
        self.activate(v1)
        record_task_output(
            project=self.project, user=self.user, task_type="case_review",
            task_id="task-2", query="q", content="同样的结论", skill_version=v1,
        )

        _s, v2 = self.create(version="2.0.0", body="v2")
        self.activate(v2)
        record_task_output(
            project=self.project, user=self.user, task_type="case_review",
            task_id="task-2", query="q", content="同样的结论", skill_version=v2,
        )

        output = GenerationOutput.objects.get(task_id="task-2")
        self.assertEqual(output.skill_version_id, v1.id)

    def test_output_without_skill_version_still_works(self):
        record_task_output(
            project=self.project, user=self.user, task_type="knowledge_query",
            task_id="task-3", query="q", content="答案",
        )
        output = GenerationOutput.objects.get(task_id="task-3")
        self.assertIsNone(output.skill_version_id)
        self.assertEqual(output.skill_package_sha256, "")
