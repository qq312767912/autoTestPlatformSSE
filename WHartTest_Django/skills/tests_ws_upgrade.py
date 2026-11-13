import tempfile
from pathlib import Path
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from projects.models import Project
from skills.models import SkillVersion
from skills.versions import SkillVersionService
from skills.packaging import compute_package_sha256


class UiWsUpgradeTests(TestCase):
    def test_upgrade_preserves_old_package_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary, override_settings(MEDIA_ROOT=temporary + "/media"):
            root = Path(temporary) / "source"
            root.mkdir()
            (root / "SKILL.md").write_text("---\nname: ws-upgrade-test\ndescription: 测试\nversion: 1.0.0\nstage: case_review\n---\n测试\n")
            (root / "ui_automation_tools.py").write_text("def _base_url():\n    return 'http://backend'\ndef execute_testcase():\n    ws_url = _base_url().replace('http://', 'ws://').replace('https://', 'wss://') + '/ws/ui/web/'\n    return ws_url\nif __name__ == '__main__':\n    execute_testcase()\n")
            if getattr(self, "legacy_constants", False):
                script = root / "ui_automation_tools.py"
                script.write_text(script.read_text().replace("def _base_url():\n    return 'http://backend'\n", "BASE_URL = 'http://backend'\nAPI_KEY = ''\n").replace("_base_url().replace", "BASE_URL.replace"))
            template = Path(temporary) / "template.py"
            template.write_text("def _get_ws_token():\n    return 'test-token'\ndef _ws_url_with_token():\n    return '/ws/ui/web/?token=' + _get_ws_token()\n")
            actor = User.objects.create_user(username="upgrade-user")
            project = Project.objects.create(name="Upgrade")
            skill, old = SkillVersionService.create_candidate_from_dir(source_dir=root, project=project, actor=actor)
            skill.active_version = old
            skill.skill_path = old.package_path
            skill.save()
            old_hash = compute_package_sha256(Path(old.get_full_path()))
            call_command("upgrade_ui_ws_auth", tool_source=str(template), apply=True)
            skill.refresh_from_db()
            self.assertNotEqual(skill.active_version_id, old.pk)
            self.assertEqual(skill.active_version.skill_id, skill.pk)
            self.assertEqual(compute_package_sha256(Path(old.get_full_path())), old_hash)
            updated_script = (Path(skill.get_full_path()) / "ui_automation_tools.py").read_text()
            self.assertLess(updated_script.index("def _get_ws_token"), updated_script.index("if __name__"))
            call_command("upgrade_ui_ws_auth", tool_source=str(template), apply=True)
            self.assertEqual(SkillVersion.objects.filter(skill=skill).count(), 2)

    def test_legacy_constant_script_is_upgraded_without_overwriting(self):
        self.legacy_constants = True
        self.test_upgrade_preserves_old_package_and_is_idempotent()
