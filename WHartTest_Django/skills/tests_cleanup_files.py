"""防止旧式清理命令误删版本包根目录。"""
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from django.core.management import call_command

class CleanupVersionSafetyTests(SimpleTestCase):
    def test_preserves_version_tree_and_referenced_legacy_directory(self):
        with tempfile.TemporaryDirectory() as root:
            versions = Path(root) / "skills/1/versions/7/v1"
            legacy = Path(root) / "skills/1/8"
            orphan = Path(root) / "skills/1/9"
            for directory in (versions, legacy, orphan):
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text("keep")
            with override_settings(MEDIA_ROOT=root), patch("skills.management.commands.cleanup_skill_files.Skill.objects") as skills, patch("skills.management.commands.cleanup_skill_files.SkillVersion.objects") as packages:
                skills.exclude.return_value.values_list.return_value = ["skills/1/8"]
                packages.exclude.return_value.values_list.return_value = ["skills/1/versions/7/v1"]
                call_command("cleanup_skill_files")
            self.assertTrue((versions / "SKILL.md").is_file())
            self.assertTrue((legacy / "SKILL.md").is_file())
            self.assertFalse(orphan.exists())
