import io
import os
import tempfile
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from projects.models import Project
from skills.models import Skill


class SkillZipUploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='skill_tester', password='secret')
        self.project = Project.objects.create(
            name='skill-upload-project',
            description='test project',
            creator=self.user,
        )

    def _build_zip_file(self, file_map: dict[str, str], name: str = 'skill.zip') -> SimpleUploadedFile:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path, content in file_map.items():
                zf.writestr(path, content)
        buffer.seek(0)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='application/zip')

    def test_create_from_zip_supports_deeply_nested_skill_root(self):
        zip_file = self._build_zip_file({
            'repo-main/packages/agent/demo-skill/SKILL.md': """---
name: nested-skill
description: nested zip skill
---

# Nested Skill
""",
            'repo-main/packages/agent/demo-skill/scripts/run.py': "print('ok')\n",
        })

        with tempfile.TemporaryDirectory() as temp_media_root:
            with self.settings(MEDIA_ROOT=temp_media_root):
                skills = Skill.create_from_zip(zip_file=zip_file, project=self.project, creator=self.user)
                self.assertEqual(len(skills), 1)
                skill = skills[0]

                self.assertEqual(skill.name, 'nested-skill')
                self.assertIn('nested zip skill', skill.description)
                # 文件落在**不可变版本目录**里；skill_path 要等版本被激活后才指向它
                # （未激活时这个旧指针保持为空，但包照样能被运行时解析到）。
                version = skill.versions.get()
                self.assertTrue(os.path.exists(
                    os.path.join(version.get_full_path(), 'scripts', 'run.py')
                ))
                self.assertIsNone(skill.get_full_path())

    def test_create_from_zip_supports_multiple_skills_in_one_archive(self):
        zip_file = self._build_zip_file({
            'bundle/skill-a/SKILL.md': """---
name: skill-a
description: skill a
---
""",
            'bundle/skill-a/run.py': "print('a')\n",
            'bundle/skill-b/SKILL.md': """---
name: skill-b
description: skill b
---
""",
            'bundle/skill-b/lib/main.py': "print('b')\n",
        })

        with tempfile.TemporaryDirectory() as temp_media_root:
            with self.settings(MEDIA_ROOT=temp_media_root):
                skills = Skill.create_from_zip(zip_file=zip_file, project=self.project, creator=self.user)

                self.assertEqual(len(skills), 2)
                self.assertEqual({skill.name for skill in skills}, {'skill-a', 'skill-b'})
                # 每个 Skill 的文件各自落在自己的版本目录下，互不串包。
                version_dirs = [skill.versions.get().get_full_path() for skill in skills]
                self.assertTrue(any(
                    os.path.exists(os.path.join(directory, 'run.py')) for directory in version_dirs
                ))
                self.assertTrue(any(
                    os.path.exists(os.path.join(directory, 'lib', 'main.py'))
                    for directory in version_dirs
                ))
