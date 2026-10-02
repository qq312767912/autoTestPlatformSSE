"""T06 验收测试：候选创建、版本 diff 与来源追踪。

覆盖 tasks.md T06 的验收项——"三种来源创建行为一致；包内容变化必然生成新哈希；
重复请求不重复落库"，以及实现要点里的不可变落盘、来源提交号/校验和、父版本、
变更原因与回滚目标。
"""
import shutil
import tempfile
import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from knowledge_evolution.knowledge_models import KnowledgeAuditLog
from projects.models import Project
from skills.models import Skill, SkillVersion
from skills.packaging import compute_package_sha256
from skills.versions import SkillVersionService

#: 版本包会落进 MEDIA_ROOT，测试必须隔离出去。
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-versions-")

SKILL_FRONT = "---\nname: {name}\ndescription: 用例审查能力\n{version_line}stage: case_review\n---\n"
SKILL_BODY = "# 用例审查\n\n{body}\n"


def front_line(version):
    return f"version: {version}\n" if version else ""


class VersionTestBase(TestCase):
    """版本相关测试的公共夹具。"""

    def setUp(self):
        self.project = Project.objects.create(name="版本项目")
        self.other_project = Project.objects.create(name="另一个项目")
        self.user = User.objects.create_user(username="version-user", password="x")
        self.tmp = tempfile.mkdtemp(prefix="pkg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_package(self, *, name="case-review", version="1.0.0", body="规则 v1",
                     script=None, extra=None) -> Path:
        """在临时目录里造一个解压好的技能包。"""
        root = Path(self.tmp) / f"{name}-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        content = SKILL_FRONT.format(name=name, version_line=front_line(version)) + \
            SKILL_BODY.format(body=body)
        (root / "SKILL.md").write_text(content, encoding="utf-8")
        if script is not None:
            (root / "scripts").mkdir(exist_ok=True)
            (root / "scripts" / "main.py").write_text(script, encoding="utf-8")
        for relative, payload in (extra or {}).items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        return root

    def create(self, source, *, project=None, source_type="upload", **kwargs):
        return SkillVersionService.create_candidate_from_dir(
            source_dir=source,
            project=project or self.project,
            actor=self.user,
            source_type=source_type,
            **kwargs,
        )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class CandidateCreationTests(VersionTestBase):
    """候选版本创建的核心行为。"""

    def test_creates_skill_and_draft_version(self):
        skill, version = self.create(self.make_package())
        self.assertEqual(skill.name, "case-review")
        self.assertEqual(version.version, "1.0.0")
        self.assertEqual(version.state, "draft")
        # 入库即候选，但候选**可以直接用**：可用性不依赖审批（激活只是钉版手段）。
        self.assertTrue(version.is_runnable)
        self.assertEqual(len(version.package_sha256), 64)

    def test_new_version_is_never_auto_activated(self):
        """连"第一个版本"也不得自动激活（R7：候选不得绕过评测与审批）。"""
        skill, version = self.create(self.make_package())
        self.assertIsNone(skill.active_version_id)
        self.assertIsNotNone(version.release_id)
        self.assertEqual(version.release.state, "draft")

    def test_package_is_materialized_into_immutable_dir(self):
        _skill, version = self.create(self.make_package())
        root = Path(version.get_full_path())
        self.assertTrue(root.is_dir())
        self.assertTrue((root / "SKILL.md").is_file())
        # 目录名里带哈希前缀，同一内容重复落盘会命中同一目录。
        self.assertIn(version.package_sha256[:16], root.name)
        # 与历史包根平级，不嵌套：否则旧包哈希会把新版本目录算进去。
        self.assertIn("/versions/", root.as_posix())

    def test_three_sources_produce_same_shape(self):
        """三种来源必须收敛到同一套版本语义。"""
        shapes = {}
        for source_type in ("upload", "git", "store"):
            project = Project.objects.create(name=f"项目-{source_type}")
            _skill, version = self.create(
                self.make_package(name=f"skill-{source_type}"),
                project=project, source_type=source_type,
            )
            shapes[source_type] = (
                version.state, bool(version.release_id),
                len(version.package_sha256), version.manifest.get("stage"),
            )
        self.assertEqual(len(set(shapes.values())), 1, msg=shapes)
        self.assertEqual(
            set(SkillVersion.objects.values_list("source_type", flat=True)),
            {"upload", "git", "store"},
        )

    # ------------------------------------------------------------ 幂等与哈希

    def test_same_content_is_idempotent(self):
        """重复提交同一份内容：返回同一版本，不重复落库、不重复落盘。"""
        package = self.make_package()
        _skill, first = self.create(package)
        _skill2, second = self.create(package)
        self.assertEqual(first.id, second.id)
        self.assertEqual(SkillVersion.objects.count(), 1)

    def test_content_change_produces_new_hash_and_version(self):
        _skill, first = self.create(self.make_package(version="1.0.0", body="规则 v1"))
        _skill2, second = self.create(self.make_package(version="1.0.1", body="规则 v2"))
        self.assertNotEqual(first.package_sha256, second.package_sha256)
        self.assertEqual(SkillVersion.objects.count(), 2)

    def test_same_version_different_content_is_rejected(self):
        """已入库版本不可原地覆盖：同号不同内容必须换号。"""
        self.create(self.make_package(version="1.0.0", body="规则 v1"))
        with self.assertRaises(ValidationError):
            self.create(self.make_package(version="1.0.0", body="规则 v2"))

    def test_whitespace_change_changes_hash(self):
        _skill, first = self.create(self.make_package(version="1.0.0", body="规则 v1"))
        _skill2, second = self.create(self.make_package(version="1.0.1", body="规则 v1 "))
        self.assertNotEqual(first.package_sha256, second.package_sha256)

    def test_excluded_dirs_do_not_affect_hash(self):
        """缓存、版本库、日志不得进入包，也不得影响哈希。"""
        plain = self.make_package(version="1.0.0")
        noisy = self.make_package(version="1.0.0", extra={
            ".git/config": "[core]\n",
            "__pycache__/x.pyc": "junk",
            "run.log": "log line\n",
            "node_modules/pkg/index.js": "module.exports = 1\n",
        })
        self.assertEqual(compute_package_sha256(plain), compute_package_sha256(noisy))

    def test_api_key_injection_does_not_change_hash(self):
        """注入的 API Key 属于环境配置，不参与内容指纹。

        这样"注入 -> 导出（脱敏）-> 重新上传"三步算出的哈希完全一致，
        重新上传才能幂等命中同一版本。
        """
        package = self.make_package(
            name="whart-test", version="1.0.0",
            # 用空默认值：真实包在注入前就是这个形态，也不会被 assigned_secret 命中。
            script='API_KEY = ""\n',
        )
        expected = compute_package_sha256(package, redact_secrets=True)

        _skill, version = self.create(package, api_key="platform-real-key-123")

        self.assertEqual(version.package_sha256, expected)
        stored = Path(version.get_full_path()) / "scripts" / "main.py"
        self.assertIn("platform-real-key-123", stored.read_text(encoding="utf-8"))
        # 磁盘内容确实被注入了真实 Key，但归一化之后哈希仍与入库值一致。
        self.assertEqual(compute_package_sha256(package, redact_secrets=True), expected)
        self.assertTrue(SkillVersionService.verify_package_integrity(version)["ok"])

    def test_internal_skill_requires_api_key(self):
        package = self.make_package(name="whart-test", version="1.0.0")
        with self.assertRaises(ValidationError):
            self.create(package)

    # ------------------------------------------------------------ 版本号

    def test_version_is_auto_inferred_when_missing(self):
        """存量指令型 Skill 没有 version 字段，平台必须补一个可用号而非拒绝。"""
        _skill, version = self.create(self.make_package(version=None))
        self.assertTrue(version.version)
        self.assertTrue(version.manifest.get("version_inferred"))

    def test_auto_version_skips_taken_number(self):
        self.create(self.make_package(version="1.0.0", body="a"))
        _skill, second = self.create(self.make_package(version=None, body="b"))
        self.assertNotEqual(second.version, "1.0.0")
        self.assertEqual(len(SkillVersion.objects.filter(skill=second.skill)), 2)

    # ------------------------------------------------------------ 来源追踪

    def test_source_metadata_and_change_reason_recorded(self):
        _skill, version = self.create(
            self.make_package(),
            source_type="git",
            source_metadata={"git_url": "https://example.com/x.git", "branch": "dev"},
            change_reason="补充覆盖规则",
            expected_benefit="减少漏报",
            impact_scope="全部用例审查任务",
        )
        self.assertEqual(version.source_type, "git")
        self.assertEqual(version.source_metadata["git_url"], "https://example.com/x.git")
        self.assertEqual(version.source_metadata["change_reason"], "补充覆盖规则")
        self.assertEqual(version.source_metadata["expected_benefit"], "减少漏报")
        self.assertEqual(version.source_metadata["impact_scope"], "全部用例审查任务")

    def test_previous_version_and_rollback_target_linked(self):
        _skill, first = self.create(self.make_package(version="1.0.0", body="a"))
        _skill2, second = self.create(self.make_package(version="1.0.1", body="b"))
        self.assertEqual(second.previous_version_id, first.id)
        self.assertEqual(second.source_metadata["rollback_target"], str(first.id))

    def test_create_writes_audit_log(self):
        _skill, version = self.create(self.make_package(), change_reason="首次入库")
        log = KnowledgeAuditLog.objects.filter(
            entity_type="SkillVersion", entity_id=str(version.id), action="create",
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.to_state, "draft")
        self.assertEqual(log.project_id, self.project.id)

    def test_rejected_package_creates_nothing(self):
        package = self.make_package(version="1.0.0")
        (package / "SKILL.md").write_text(
            "---\nname: case-review\nversion: 1.0.0\n---\n", encoding="utf-8",
        )
        with self.assertRaises(ValidationError):
            self.create(package)
        self.assertEqual(Skill.objects.count(), 0)
        self.assertEqual(SkillVersion.objects.count(), 0)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class VersionDiffTests(VersionTestBase):
    """文件级差异与可读文本 diff。"""

    def test_first_version_lists_everything_as_added(self):
        _skill, version = self.create(self.make_package())
        diff = SkillVersionService.diff(None, version)
        self.assertEqual(diff["base"], {})
        self.assertIn("SKILL.md", diff["files"]["added"])
        self.assertEqual(diff["files"]["removed"], [])

    def test_added_removed_modified_categories(self):
        _skill, first = self.create(self.make_package(
            version="1.0.0", body="rules", extra={"docs/old.md": "old\n"},
        ))
        _skill2, second = self.create(self.make_package(
            version="1.0.1", body="rules updated", extra={"docs/new.md": "new\n"},
        ))
        diff = SkillVersionService.diff(first, second)
        self.assertIn("docs/new.md", diff["files"]["added"])
        self.assertIn("docs/old.md", diff["files"]["removed"])
        self.assertEqual(diff["files"]["unchanged_count"], 0)
        modified_paths = {item["path"] for item in diff["files"]["modified"]}
        self.assertIn("SKILL.md", modified_paths)
        self.assertIn("新增 1、删除 1、修改 1、未变 0", diff["summary"])

    def test_unchanged_files_are_counted(self):
        _skill, first = self.create(self.make_package(
            version="1.0.0", body="a", extra={"docs/keep.md": "same\n"},
        ))
        _skill2, second = self.create(self.make_package(
            version="1.0.1", body="b", extra={"docs/keep.md": "same\n"},
        ))
        diff = SkillVersionService.diff(first, second)
        self.assertEqual(diff["files"]["unchanged_count"], 1)
        self.assertEqual(diff["files"]["added"], [])
        self.assertEqual(diff["files"]["removed"], [])

    def test_text_diff_is_produced_for_modified_text_file(self):
        _skill, first = self.create(self.make_package(version="1.0.0", body="规则 A"))
        _skill2, second = self.create(self.make_package(version="1.0.1", body="规则 B"))
        diff = SkillVersionService.diff(first, second)
        entry = next(item for item in diff["text_diffs"] if item["path"] == "SKILL.md")
        self.assertIn("+", entry["unified_diff"])
        self.assertIn("规则 B", entry["unified_diff"])

    def test_diff_metadata_carries_both_sides(self):
        _skill, first = self.create(self.make_package(version="1.0.0", body="a"))
        _skill2, second = self.create(self.make_package(version="1.0.1", body="b"))
        diff = SkillVersionService.diff(first, second)
        self.assertEqual(diff["base"]["version"], "1.0.0")
        self.assertEqual(diff["candidate"]["version"], "1.0.1")
        self.assertEqual(diff["base"]["package_sha256"], first.package_sha256)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class VersionLifecycleTests(VersionTestBase):
    """隔离与审计。"""

    def test_quarantine_requires_reason(self):
        _skill, version = self.create(self.make_package())
        with self.assertRaises(ValidationError):
            SkillVersionService.quarantine(version, actor=self.user, reason="  ")

    def test_quarantine_moves_state_and_writes_audit(self):
        _skill, version = self.create(self.make_package())
        SkillVersionService.quarantine(version, actor=self.user, reason="疑似外泄")
        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")
        self.assertTrue(KnowledgeAuditLog.objects.filter(
            entity_type="SkillVersion", entity_id=str(version.id), action="quarantine",
        ).exists())

    def test_quarantine_clears_active_pointer(self):
        """隔离后 Skill 不得再指向该版本。"""
        skill, version = self.create(self.make_package())
        skill.active_version = version
        skill.save(update_fields=["active_version"])

        SkillVersionService.quarantine(version, actor=self.user, reason="风险版本")
        skill.refresh_from_db()
        self.assertIsNone(skill.active_version_id)

    def test_record_validate_and_download_write_audit(self):
        _skill, version = self.create(self.make_package())
        SkillVersionService.record_validate(
            version, actor=self.user, report={"ok": True, "errors": [], "warnings": []},
        )
        SkillVersionService.record_download(version, actor=self.user)
        actions = set(KnowledgeAuditLog.objects.filter(
            entity_id=str(version.id),
        ).values_list("action", flat=True))
        self.assertIn("validate", actions)
        self.assertIn("download", actions)

    def test_verify_package_integrity_detects_tampering(self):
        _skill, version = self.create(self.make_package())
        root = Path(version.get_full_path())
        (root / "SKILL.md").write_text("tampered", encoding="utf-8")
        check = SkillVersionService.verify_package_integrity(version)
        self.assertFalse(check["ok"])
        self.assertEqual(check["reason"], "package_tampered")

    def test_verify_package_integrity_reports_missing_dir(self):
        _skill, version = self.create(self.make_package())
        shutil.rmtree(version.get_full_path(), ignore_errors=True)
        check = SkillVersionService.verify_package_integrity(version)
        self.assertFalse(check["ok"])
        self.assertEqual(check["reason"], "package_dir_missing")
