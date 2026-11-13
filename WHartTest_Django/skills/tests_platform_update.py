"""受损旧包只能通过独立源包发布新版本修复，不能重新盖章旧包。"""
import tempfile
from pathlib import Path
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from projects.models import Project
from skills.models import SkillVersion
from skills.versions import SkillVersionService


class PlatformSkillUpdateTests(TestCase):
    def test_reviewed_source_replaces_damaged_default_and_keeps_old_package(self):
        with tempfile.TemporaryDirectory() as temporary, override_settings(MEDIA_ROOT=temporary + '/media'):
            root = Path(temporary) / 'reviewed-source'
            root.mkdir()
            (root / 'SKILL.md').write_text('---\nname: platform-update-test\ndescription: 测试\nversion: 1.0.0\nstage: testcase_generation\n---\n审查后的内容\n')
            actor = User.objects.create_user(username='platform-update-user')
            project = Project.objects.create(name='Update')
            skill, old = SkillVersionService.create_candidate_from_dir(source_dir=root, project=project, actor=actor, source_type='project_copy')
            skill.active_version = old
            skill.skill_path = old.package_path
            skill.save()
            damaged = Path(old.get_full_path()) / 'SKILL.md'
            damaged.write_text(damaged.read_text() + '\n旧包被改写\n')
            before = damaged.read_bytes()
            self.assertFalse(SkillVersionService.verify_package_integrity(old)['ok'])
            call_command('publish_platform_skill_update', skill_id=skill.pk, source_dir=str(root))
            skill.refresh_from_db()
            self.assertEqual(skill.active_version_id, old.pk)
            call_command('publish_platform_skill_update', skill_id=skill.pk, source_dir=str(root), apply=True)
            skill.refresh_from_db()
            self.assertNotEqual(skill.active_version_id, old.pk)
            self.assertTrue(SkillVersionService.verify_package_integrity(skill.active_version)['ok'])
            self.assertFalse(skill.active_version.source_metadata['previous_package_integrity'])
            self.assertEqual(damaged.read_bytes(), before)
            old.refresh_from_db()
            self.assertFalse(SkillVersionService.verify_package_integrity(old)['ok'])
            call_command('publish_platform_skill_update', skill_id=skill.pk, source_dir=str(root), apply=True)
            self.assertEqual(SkillVersion.objects.filter(skill=skill).count(), 2)
            (root / 'SKILL.md').write_text((root / 'SKILL.md').read_text().replace('platform-update-test', 'wrong-identity'))
            with self.assertRaises(CommandError):
                call_command('publish_platform_skill_update', skill_id=skill.pk, source_dir=str(root), apply=True)
