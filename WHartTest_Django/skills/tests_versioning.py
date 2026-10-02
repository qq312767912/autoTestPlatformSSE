"""T02 / T03 / T04 验收测试：不可变版本、发布状态机、活跃指针、存量迁移。

覆盖 tasks.md 中这三个任务的全部验收项：

- T02：``Unique(skill, version)`` 与 ``Unique(skill, package_sha256)`` 生效，
  已入库版本无法被"内容相同但换版本号"的方式重复落库。
- T03：非法状态跳转被拒绝；同一目标最多一个 active 版本（数据库级兜底）；
  激活/回滚/隔离后活跃指针与发布状态保持一致；未过门禁不得激活。
- T04：存量迁移可重入、幂等、哈希不一致时停止，且迁移内联的哈希算法与
  ``skills.packaging`` 的实现逐字节一致（防止两处实现漂移）。
"""
import importlib
import os
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from knowledge_evolution.capability_models import CapabilityRelease
from knowledge_evolution.capabilities import (
    CapabilityReleaseService,
    assert_can_reach,
    assert_can_transition,
    can_reach,
)
from projects.models import Project
from skills import packaging
from skills.models import Skill, SkillVersion

# 迁移模块名以数字开头，无法直接 import 语句引用。
MIGRATION_MODULE = importlib.import_module(
    "skills.migrations.0004_migrate_existing_skills"
)


def make_release(project, name, version, *, state="draft", gate_passed=False, **extra):
    """构造一条 kind=skill 的发布单元，默认草稿态。"""
    return CapabilityRelease.objects.create(
        project=project,
        kind="skill",
        name=name,
        version=version,
        artifact_hash="0" * 64,
        state=state,
        gate_report={"passed": gate_passed} if gate_passed else {},
        **extra,
    )


class SkillVersionImmutabilityTests(TestCase):
    """T02：版本与包哈希的唯一约束。"""

    def setUp(self):
        self.project = Project.objects.create(name="版本约束项目")
        self.skill = Skill.objects.create(
            project=self.project, name="case-review", description="用例审查"
        )

    def _make_version(self, version, sha):
        return SkillVersion.objects.create(
            skill=self.skill,
            version=version,
            package_path=f"skills/{self.project.id}/{self.skill.id}/{version}",
            package_sha256=sha,
        )

    def test_same_version_twice_is_rejected(self):
        self._make_version("1.0.0", "a" * 64)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_version("1.0.0", "b" * 64)

    def test_same_package_content_cannot_be_stored_twice(self):
        """同一个包换个版本号也不允许再入库，避免用改名绕过不可变约束。"""
        self._make_version("1.0.0", "c" * 64)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_version("1.0.1", "c" * 64)

    def test_different_content_creates_new_version(self):
        self._make_version("1.0.0", "d" * 64)
        second = self._make_version("1.0.1", "e" * 64)
        self.assertEqual(self.skill.versions.count(), 2)
        self.assertEqual(second.version, "1.0.1")

    def test_state_falls_back_to_draft_without_release(self):
        version = self._make_version("1.0.0", "f" * 64)
        self.assertEqual(version.state, "draft")
        self.assertFalse(version.is_runnable)

    def test_is_runnable_requires_active_state(self):
        release = make_release(self.project, self.skill.name, "1.0.0", state="active")
        version = SkillVersion.objects.create(
            skill=self.skill, version="1.0.0", release=release,
            package_path="skills/x", package_sha256="1" * 64,
        )
        self.assertEqual(version.state, "active")
        self.assertTrue(version.is_runnable)

        release.state = "quarantined"
        release.save(update_fields=["state"])
        version.refresh_from_db()
        self.assertFalse(version.is_runnable)


class ReleaseStateMachineTests(TestCase):
    """T03：状态机合法性与激活前置条件。"""

    def setUp(self):
        self.project = Project.objects.create(name="状态机项目")
        self.actor = User.objects.create_user(username="lead", password="x")

    def test_illegal_direct_transition_is_rejected(self):
        with self.assertRaises(ValidationError):
            assert_can_transition("draft", "active")
        with self.assertRaises(ValidationError):
            assert_can_transition("quarantined", "active")
        with self.assertRaises(ValidationError):
            assert_can_transition("rolled_back", "active")

    def test_legal_transition_passes(self):
        assert_can_transition("draft", "validating")
        assert_can_transition("shadow", "awaiting_approval")
        assert_can_transition("awaiting_approval", "active")
        assert_can_transition("active", "retired")

    def test_reachability_allows_multi_hop_but_blocks_revival(self):
        # 影子评测把 draft 一路推进到待审批是允许的（多跳可达）。
        assert_can_reach("draft", "awaiting_approval")
        self.assertTrue(can_reach("draft", "active"))
        # 被驳回/被隔离/已回滚的版本不得复活。
        self.assertFalse(can_reach("rejected", "active"))
        self.assertFalse(can_reach("quarantined", "awaiting_approval"))
        self.assertFalse(can_reach("rolled_back", "active"))
        with self.assertRaises(ValidationError):
            assert_can_reach("quarantined", "active")

    def test_promote_requires_passed_gate(self):
        release = make_release(
            self.project, "case-review", "1.0.0",
            state="awaiting_approval", gate_passed=False,
        )
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.promote(release, actor=self.actor, reason="试试")

    def test_promote_requires_awaiting_approval_state(self):
        release = make_release(
            self.project, "case-review", "1.0.0", state="draft", gate_passed=True
        )
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.promote(release, actor=self.actor, reason="抢跑")

    def test_promote_transitions_and_records_decision(self):
        release = make_release(
            self.project, "case-review", "1.0.0",
            state="awaiting_approval", gate_passed=True,
        )
        CapabilityReleaseService.promote(release, actor=self.actor, reason="通过")
        release.refresh_from_db()
        self.assertEqual(release.state, "active")
        self.assertEqual(release.approved_by_id, self.actor.id)
        self.assertIsNotNone(release.activated_at)
        self.assertEqual(release.decisions.filter(decision="approved").count(), 1)

    def test_database_blocks_two_active_releases_for_same_target(self):
        """同一 (project, kind, name) 最多一个 active —— 并发激活的数据库级兜底。"""
        make_release(self.project, "case-review", "1.0.0", state="active")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_release(self.project, "case-review", "2.0.0", state="active")

    def test_different_skills_may_both_be_active(self):
        """不同 Skill 各自有独立发布线，不能互相顶掉。"""
        make_release(self.project, "case-review", "1.0.0", state="active")
        make_release(self.project, "test-plan-generator", "1.0.0", state="active")
        active = CapabilityRelease.objects.filter(
            project=self.project, kind="skill", state="active"
        ).count()
        self.assertEqual(active, 2)

    def test_quarantine_requires_reason_and_blocks_runtime(self):
        release = make_release(self.project, "case-review", "1.0.0", state="active")
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.quarantine(release, actor=self.actor, reason="  ")
        CapabilityReleaseService.quarantine(release, actor=self.actor, reason="疑似密钥泄露")
        release.refresh_from_db()
        self.assertEqual(release.state, "quarantined")
        self.assertEqual(release.decisions.filter(decision="quarantine").count(), 1)

    def test_failed_transaction_leaves_no_half_state(self):
        """激活失败时不得留下半激活状态：原 active 版本保持不变。"""
        incumbent = make_release(self.project, "case-review", "1.0.0", state="active")
        challenger = make_release(
            self.project, "case-review", "2.0.0",
            state="awaiting_approval", gate_passed=False,
        )
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.promote(challenger, actor=self.actor, reason="")
        incumbent.refresh_from_db()
        challenger.refresh_from_db()
        self.assertEqual(incumbent.state, "active")
        self.assertEqual(challenger.state, "awaiting_approval")


class ActivePointerTests(TestCase):
    """T03：Skill.active_version 必须与发布状态同事务刷新。"""

    def setUp(self):
        self.project = Project.objects.create(name="指针项目")
        self.actor = User.objects.create_user(username="lead2", password="x")
        self.skill = Skill.objects.create(
            project=self.project, name="case-review", description="用例审查"
        )
        self.other_skill = Skill.objects.create(
            project=self.project, name="test-plan-generator", description="测试方案"
        )

    def _activate(self, skill, version, sha):
        release = make_release(
            self.project, skill.name, version,
            state="awaiting_approval", gate_passed=True,
        )
        SkillVersion.objects.create(
            skill=skill, version=version, release=release,
            package_path=f"skills/{skill.id}/{version}", package_sha256=sha,
        )
        CapabilityReleaseService.promote(release, actor=self.actor, reason="通过")
        return release

    def test_promote_updates_active_version_pointer(self):
        self._activate(self.skill, "1.0.0", "a" * 64)
        self.skill.refresh_from_db()
        self.assertIsNotNone(self.skill.active_version_id)
        self.assertEqual(self.skill.active_version.version, "1.0.0")

    def test_new_version_retires_previous_and_moves_pointer(self):
        first = self._activate(self.skill, "1.0.0", "a" * 64)
        second = self._activate(self.skill, "2.0.0", "b" * 64)
        first.refresh_from_db()
        self.skill.refresh_from_db()
        self.assertEqual(first.state, "retired")
        self.assertEqual(second.state, "active")
        self.assertEqual(self.skill.active_version.version, "2.0.0")

    def test_activating_one_skill_does_not_retire_another(self):
        """回归：退役范围必须是 (project, kind, name)，不能是 (project, kind)。"""
        other_release = self._activate(self.other_skill, "1.0.0", "c" * 64)
        self._activate(self.skill, "1.0.0", "d" * 64)
        other_release.refresh_from_db()
        self.other_skill.refresh_from_db()
        self.assertEqual(other_release.state, "active")
        self.assertEqual(self.other_skill.active_version.version, "1.0.0")

    def test_rollback_moves_pointer_back(self):
        first = self._activate(self.skill, "1.0.0", "a" * 64)
        second = self._activate(self.skill, "2.0.0", "b" * 64)
        CapabilityReleaseService.rollback(second, actor=self.actor, reason="生产回归")
        first.refresh_from_db()
        second.refresh_from_db()
        self.skill.refresh_from_db()
        self.assertEqual(second.state, "rolled_back")
        self.assertEqual(first.state, "active")
        self.assertEqual(self.skill.active_version.version, "1.0.0")

    def test_quarantine_clears_pointer(self):
        release = self._activate(self.skill, "1.0.0", "a" * 64)
        CapabilityReleaseService.quarantine(release, actor=self.actor, reason="紧急隔离")
        self.skill.refresh_from_db()
        self.assertIsNone(self.skill.active_version_id)


class ExistingSkillMigrationTests(TestCase):
    """T04：存量迁移的可重入性、幂等性与哈希一致性。"""

    def setUp(self):
        self.project = Project.objects.create(name="迁移项目")
        self.creator = User.objects.create_user(username="migrator", password="x")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _make_skill_with_package(self, name, *, is_active=True, files=None):
        """在临时 MEDIA_ROOT 下真实落盘一个包，并建对应的 Skill 记录。"""
        media_root = self.temp_dir.name
        skill = Skill.objects.create(
            project=self.project, creator=self.creator,
            name=name, description=f"{name} 描述", is_active=is_active,
        )
        relative = f"skills/{self.project.id}/{skill.id}"
        package_dir = Path(media_root) / relative
        package_dir.mkdir(parents=True, exist_ok=True)
        payload = files or {
            "SKILL.md": "---\nname: %s\ndescription: 描述\n---\n\n正文\n" % name,
            "scripts/main.py": "print('hello')\n",
        }
        for file_name, content in payload.items():
            target = package_dir / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        skill.skill_path = relative
        skill.save(update_fields=["skill_path"])
        return skill, package_dir

    def test_migration_creates_version_and_active_binding(self):
        skill, package_dir = self._make_skill_with_package("case-review")
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(__import__("django.apps", fromlist=["apps"]).apps, None)

        version = SkillVersion.objects.get(skill=skill, version="0.0.0-migrated")
        self.assertEqual(version.source_type, "migration")
        self.assertEqual(version.package_path, skill.skill_path)
        self.assertEqual(version.release.state, "active")
        self.assertTrue(version.manifest["migrated"])
        # 老包缺 stage/entrypoint，必须显式标记待人工补齐。
        self.assertTrue(version.manifest["needs_manual_stage_binding"])
        skill.refresh_from_db()
        self.assertEqual(skill.active_version_id, version.id)
        # 原文件一个都不能少、不能被改动。
        self.assertTrue((Path(package_dir) / "SKILL.md").exists())
        self.assertTrue((Path(package_dir) / "scripts" / "main.py").exists())

    def test_migration_is_idempotent(self):
        skill, _ = self._make_skill_with_package("case-review")
        apps = __import__("django.apps", fromlist=["apps"]).apps
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(apps, None)
            MIGRATION_MODULE.migrate_existing_skills(apps, None)

        self.assertEqual(SkillVersion.objects.filter(skill=skill).count(), 1)
        self.assertEqual(
            CapabilityRelease.objects.filter(project=self.project, kind="skill").count(), 1
        )

    def test_inactive_skill_migrates_to_draft(self):
        skill, _ = self._make_skill_with_package("case-review", is_active=False)
        apps = __import__("django.apps", fromlist=["apps"]).apps
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(apps, None)

        version = SkillVersion.objects.get(skill=skill)
        self.assertEqual(version.release.state, "draft")
        skill.refresh_from_db()
        self.assertIsNone(skill.active_version_id)

    def test_migration_stops_when_existing_hash_differs(self):
        """已入库版本与磁盘内容不一致时停止，不静默覆盖。"""
        skill, package_dir = self._make_skill_with_package("case-review")
        apps = __import__("django.apps", fromlist=["apps"]).apps
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(apps, None)
            # 模拟"包被就地改动"（这正是不可变约束要阻止的场景）。
            (Path(package_dir) / "SKILL.md").write_text("被改过的内容", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                MIGRATION_MODULE.migrate_existing_skills(apps, None)

    def test_inline_only_skill_gets_draft_version_and_stays_non_runnable(self):
        """只有内联 SKILL.md 的 Skill 也要有版本记录，但必须是 draft，绝不能变 active。

        R13 要求为每条记录生成初始版本，同时"无活跃版本就拒绝执行"——所以这类
        记录必须留下可查询的版本痕迹，又不能被运行时当成可运行版本。
        """
        skill = Skill.objects.create(
            project=self.project, name="inline-only", description="无包目录",
            skill_path="skills/does-not-exist",
            skill_content="---\nname: inline-only\n---\n正文\n",
            is_active=True,
        )
        apps = __import__("django.apps", fromlist=["apps"]).apps
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(apps, None)

        version = SkillVersion.objects.get(skill=skill)
        self.assertEqual(version.release.state, "draft")
        self.assertEqual(version.package_path, "")
        self.assertTrue(version.manifest["inline_only"])
        self.assertTrue(version.manifest["needs_package_rebuild"])
        self.assertFalse(version.is_runnable)
        self.assertFalse(version.validation_report["package_complete"])
        skill.refresh_from_db()
        self.assertIsNone(skill.active_version_id)
        # 迁移只读，不得凭空创建包目录。
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "skills")))

    def test_inline_only_hash_matches_single_file_package(self):
        """内联版本的哈希必须等于"把同样内容落盘成只含 SKILL.md 的包"的哈希。"""
        content = "---\nname: inline-hash\ndescription: 描述\n---\n正文\n"
        skill = Skill.objects.create(
            project=self.project, name="inline-hash", description="x",
            skill_path="", skill_content=content, is_active=False,
        )
        apps = __import__("django.apps", fromlist=["apps"]).apps
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(apps, None)

        version = SkillVersion.objects.get(skill=skill)
        rebuilt_dir = Path(self.temp_dir.name) / "rebuilt"
        rebuilt_dir.mkdir(parents=True, exist_ok=True)
        (rebuilt_dir / "SKILL.md").write_text(content, encoding="utf-8")
        self.assertEqual(
            version.package_sha256, packaging.compute_package_sha256(rebuilt_dir)
        )

    def test_migration_hash_matches_packaging_module(self):
        """守护两处哈希实现不漂移：迁移内联实现 == skills.packaging 正式实现。"""
        _, package_dir = self._make_skill_with_package(
            "case-review",
            files={
                "SKILL.md": "---\nname: case-review\ndescription: 描述\n---\n\n正文\n",
                "scripts/main.py": "print('hello')\n",
                # 以下文件必须被两套实现一致地排除掉。
                ".env": "SECRET=1\n",
                "__pycache__/main.cpython-313.pyc": "binary-ish",
                "logs/run.log": "noise\n",
            },
        )
        migration_hash = MIGRATION_MODULE._compute_package_sha256(Path(package_dir))
        packaging_hash = packaging.compute_package_sha256(package_dir)
        self.assertEqual(migration_hash, packaging_hash)
        # 排除项确实没参与计算：加一个日志文件不改变哈希。
        (Path(package_dir) / "logs" / "extra.log").write_text("more noise", encoding="utf-8")
        self.assertEqual(
            MIGRATION_MODULE._compute_package_sha256(Path(package_dir)), migration_hash
        )

    def test_rollback_keeps_original_skill_records(self):
        skill, _ = self._make_skill_with_package("case-review")
        apps = __import__("django.apps", fromlist=["apps"]).apps
        with self.settings(MEDIA_ROOT=self.temp_dir.name):
            MIGRATION_MODULE.migrate_existing_skills(apps, None)
            MIGRATION_MODULE.rollback_migration(apps, None)

        self.assertTrue(Skill.objects.filter(pk=skill.pk).exists())
        self.assertEqual(SkillVersion.objects.filter(skill=skill).count(), 0)
        skill.refresh_from_db()
        self.assertIsNone(skill.active_version_id)


class PackagingDigestTests(TestCase):
    """包哈希算法的确定性：顺序无关、内容敏感、排除项不参与。"""

    def test_hash_is_order_independent_and_content_sensitive(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            for root in (first_dir, second_dir):
                (Path(root) / "b.txt").write_text("B", encoding="utf-8")
                (Path(root) / "a.txt").write_text("A", encoding="utf-8")
            self.assertEqual(
                packaging.compute_package_sha256(first_dir),
                packaging.compute_package_sha256(second_dir),
            )
            (Path(second_dir) / "a.txt").write_text("A2", encoding="utf-8")
            self.assertNotEqual(
                packaging.compute_package_sha256(first_dir),
                packaging.compute_package_sha256(second_dir),
            )

    def test_boundary_between_path_and_content_is_unambiguous(self):
        """长度前缀必须让 (路径,内容) 的不同切分产生不同哈希。"""
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            (Path(first_dir) / "ab").write_text("c", encoding="utf-8")
            (Path(second_dir) / "a").write_text("bc", encoding="utf-8")
            self.assertNotEqual(
                packaging.compute_package_sha256(first_dir),
                packaging.compute_package_sha256(second_dir),
            )

    def test_symlink_is_ignored(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            (Path(root) / "real.txt").write_text("real", encoding="utf-8")
            secret = Path(outside) / "secret.txt"
            secret.write_text("secret", encoding="utf-8")
            os.symlink(secret, Path(root) / "link.txt")
            files = packaging.iter_package_files(root)
            self.assertIn("real.txt", files)
            self.assertNotIn("link.txt", files)
