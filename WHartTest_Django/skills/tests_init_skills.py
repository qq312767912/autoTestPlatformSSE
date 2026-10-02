"""预置技能同步（``init_skills``）的不可变性护栏测试。

这一层测的是**启动副作用**：``entrypoint.sh`` 每次容器启动都会跑 ``init_skills``，
如果它往活跃版本的不可变包目录里写文件，就会造成两个连锁后果——版本包哈希漂移
（``verify_package_integrity`` 永久报 ``package_tampered``），以及 skill 自进化从
第二次起被"基线包被篡改"中止。

所以这里的断言语义都落在"字节有没有被改"上，而不是"日志有没有打出来"。
"""
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from projects.models import Project
from skills.models import Skill
from skills.versions import SkillVersionService

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-initskills-")

SKILL_MD = (
    "---\n"
    "name: {name}\n"
    "description: 预置技能 {name}\n"
    "stage: case_review\n"
    "---\n"
    "# {name}\n\n预置正文 v1\n"
)


class InitSkillsBase(TestCase):
    def setUp(self):
        self.bundled = Path(tempfile.mkdtemp(prefix="bundled-"))
        self.project = Project.objects.create(name="预置项目")
        # init_skills 只认超级管理员作为 creator，拿不到就整体跳过。
        self.admin = User.objects.create_superuser(
            username="init-admin", email="init@example.com", password="x",
        )

    def tearDown(self):
        shutil.rmtree(self.bundled, ignore_errors=True)

    def make_bundled(self, name, *, script=None, extra=None) -> Path:
        root = self.bundled / name
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(SKILL_MD.format(name=name), encoding="utf-8")
        if script is not None:
            (root / "scripts").mkdir(exist_ok=True)
            (root / "scripts" / "main.py").write_text(script, encoding="utf-8")
        for relative, payload in (extra or {}).items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        return root

    def run_sync(self):
        call_command("init_skills", "--skills-dir", str(self.bundled), verbosity=0)

    def snapshot(self, root: Path) -> dict:
        """目录快照：{相对路径: 字节内容}。用内容比对而非 mtime，mtime 会被保留策略干扰。"""
        if not root.is_dir():
            return {}
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ImmutablePackageGuardTests(InitSkillsBase):
    """主护栏：目标落在版本包目录时，预置同步必须整体让路。"""

    def _activate_on_package(self, *, name="case-review-guard", body="规则 v1"):
        """造一个 Skill 并把它激活到版本包目录（``skill_path`` 指向包目录）。"""
        source = Path(tempfile.mkdtemp(prefix="pkg-")) / "pkg"
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text(SKILL_MD.format(name=name), encoding="utf-8")
        (source / "scripts" / "main.py").write_text("# entrypoint\nprint('v1')\n", encoding="utf-8")
        skill, version = SkillVersionService.create_candidate_from_dir(
            source_dir=source, project=self.project, actor=self.admin,
        )
        # 激活的副作用就是"skill_path 指向活跃版本包目录"，这里直接落这个状态。
        skill.skill_path = version.package_path
        skill.active_version = version
        skill.description = "上传时的描述"
        skill.save(update_fields=["skill_path", "active_version", "description"])
        return skill, version

    def test_activated_skill_package_is_not_overwritten(self):
        skill, version = self._activate_on_package()
        root = Path(version.get_full_path())
        before = self.snapshot(root)
        self.assertTrue(before, "前置条件：版本包目录应当有内容")

        # 预置目录里放同名技能，且内容与版本包不同、还多带了 references/。
        self.make_bundled(
            skill.name,
            extra={"references/review-rules.md": "# 镜像自带的审查规则\n"},
        )
        self.run_sync()

        after = self.snapshot(root)
        self.assertEqual(before, after, "预置同步不得改动任何版本包文件")
        self.assertNotIn(
            "references/review-rules.md", after,
            "镜像自带的额外文件不得被塞进版本包（补一个文件同样会让哈希漂移）",
        )

    def test_package_integrity_still_holds_after_sync(self):
        skill, version = self._activate_on_package(name="case-review-integrity")
        self.assertTrue(SkillVersionService.verify_package_integrity(version)["ok"])
        self.make_bundled(skill.name, script="# entrypoint\nprint('v2')\n")
        self.run_sync()
        result = SkillVersionService.verify_package_integrity(version)
        self.assertTrue(result["ok"], f"同步后包完整性必须仍然成立：{result}")

    def test_skip_does_not_touch_db_fields(self):
        skill, version = self._activate_on_package(name="case-review-dbfields")
        self.make_bundled(skill.name)
        self.run_sync()
        skill.refresh_from_db()
        # 描述若被镜像覆盖，库里记录就与活跃版本包内容不一致了，同样是旁路改动。
        self.assertEqual(skill.description, "上传时的描述")
        self.assertEqual(skill.skill_path, version.package_path)

    def test_repeated_sync_is_stable(self):
        """每次重启都会跑一次；连跑三次都不该积累任何差异。"""
        skill, version = self._activate_on_package(name="case-review-repeat")
        root = Path(version.get_full_path())
        before = self.snapshot(root)
        self.make_bundled(skill.name, extra={"references/notes.md": "镜像备注\n"})
        for _ in range(3):
            self.run_sync()
        self.assertEqual(before, self.snapshot(root))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LegacyBehaviourPreservedTests(InitSkillsBase):
    """护栏不能扩大化：其余两类技能必须照旧被同步。"""

    def test_legacy_skill_path_is_still_synced(self):
        """``skill_path`` 指向 legacy 根目录的存量技能，同步行为保持不变。"""
        skill = Skill.objects.create(
            project=self.project, creator=self.admin, name="legacy-skill",
            description="旧描述", skill_content="", skill_path="", is_active=True,
        )
        storage_path = f"skills/{self.project.id}/{skill.id}"
        skill.skill_path = storage_path
        skill.save(update_fields=["skill_path"])
        target = Path(TEST_MEDIA_ROOT) / storage_path

        self.make_bundled("legacy-skill", extra={"references/rules.md": "旧规则\n"})
        self.run_sync()

        self.assertTrue((target / "SKILL.md").is_file())
        self.assertTrue((target / "references" / "rules.md").is_file())
        skill.refresh_from_db()
        self.assertEqual(skill.description, "预置技能 legacy-skill")

    def test_new_bundled_skill_is_imported(self):
        """库里没有的预置技能，照旧创建记录并落盘，且不带版本包语义。"""
        self.make_bundled("brand-new-skill", script="# entrypoint\nprint('ok')\n")
        self.run_sync()

        skill = Skill.objects.get(name="brand-new-skill")
        self.assertEqual(skill.skill_path, f"skills/{self.project.id}/{skill.id}")
        self.assertTrue((Path(skill.get_full_path()) / "SKILL.md").is_file())
        self.assertFalse(skill.versions.exists(), "预置导入不该顺带生成版本包")
