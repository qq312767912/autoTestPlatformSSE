"""展示名称（``display_name``）的行为回归。

上传/导入时可以给 Skill 起一个给人看的名字，但这件事有三个容易做错的地方，
每个都由下面的用例钉住：

1. **一个名字只能对应一条 Skill** —— 一次导入可以带多个 Skill，把同一个名字
   套到多条上会互相覆盖，列表上还会出现两条同标题的条目；
2. **不能碰 ``Skill.name``** —— 它是 Skill Hub 的跨项目归并键，也是版本包认
   逻辑身份的依据，改它会让同名正本裂开、让同一个包再次上传时重造一条新 Skill；
3. **归并时别把人工确认的名字丢掉** —— 同名副本归并成「正本」时，同优先级下
   应当优先选有展示名的那条，否则列表又退回包内的英文标识符。
"""

import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase

from projects.models import Project
from skills.canonical import pick_canonical
from skills.models import Skill
from skills.views import _apply_import_metadata, _package_skill_names


def _skill_md(name: str) -> str:
    return f'---\nname: {name}\ndescription: {name} 的说明\n---\n\n# {name}\n'


class ApplyImportMetadataTests(TestCase):
    """导入后写元数据（分类 / 简介 / 展示名）的落库规则。"""

    def setUp(self):
        self.user = User.objects.create_user(username='display-name-tester', password='secret')
        self.project = Project.objects.create(
            name='display-name-project', description='test project', creator=self.user,
        )

    def _skill(self, name: str) -> Skill:
        return Skill.objects.create(
            project=self.project, creator=self.user, name=name,
            description='原始描述', skill_content='', skill_path='', is_active=True,
        )

    def test_single_skill_gets_the_confirmed_display_name(self):
        skill = self._skill('sse-evote-test-plan')

        _apply_import_metadata(
            [skill], category='test_plan_generation',
            description='生成测试方案', display_name='e投票方案生成',
        )

        skill.refresh_from_db()
        self.assertEqual(skill.display_name, 'e投票方案生成')
        # 逻辑名不动：它是归并键，也是版本包认身份的依据。
        self.assertEqual(skill.name, 'sse-evote-test-plan')

    def test_multi_skill_batch_ignores_the_display_name(self):
        skills = [self._skill('skill-a'), self._skill('skill-b')]

        _apply_import_metadata(
            skills, category='platform_base', description='批量导入', display_name='统一改名',
        )

        for skill in skills:
            skill.refresh_from_db()
            self.assertEqual(skill.display_name, '', f'{skill.name} 不该被套上统一改名')

    def test_blank_display_name_keeps_the_packaged_name(self):
        skill = self._skill('skill-c')

        _apply_import_metadata(
            [skill], category='platform_base', description='x', display_name='   ',
        )

        skill.refresh_from_db()
        self.assertEqual(skill.display_name, '')
        self.assertEqual(skill.name, 'skill-c')

    def test_category_and_description_are_still_applied(self):
        skill = self._skill('skill-d')

        _apply_import_metadata(
            [skill], category='platform_base', description='新简介',
        )

        skill.refresh_from_db()
        self.assertEqual(skill.description, '新简介')
        self.assertEqual(skill.declared_stage, 'platform_base')


class PackageSkillNamesTests(TestCase):
    """``_package_skill_names``：判断"这个包里到底有几个 Skill"。"""

    def test_reads_frontmatter_name_from_each_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('skill-a', 'skill-b'):
                (root / name).mkdir()
                (root / name / 'SKILL.md').write_text(_skill_md(name), encoding='utf-8')

            files = [root / 'skill-a' / 'SKILL.md', root / 'skill-b' / 'SKILL.md']
            self.assertEqual(_package_skill_names(files), ['skill-a', 'skill-b'])

    def test_unparsable_entry_is_skipped_without_killing_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'good').mkdir()
            (root / 'good' / 'SKILL.md').write_text(_skill_md('good-skill'), encoding='utf-8')
            (root / 'bad').mkdir()
            # 没有 frontmatter，parse_skill_md 会抛 ValidationError。
            (root / 'bad' / 'SKILL.md').write_text('# 没有 frontmatter\n', encoding='utf-8')

            files = [root / 'good' / 'SKILL.md', root / 'bad' / 'SKILL.md']
            self.assertEqual(_package_skill_names(files), ['good-skill'])


class CanonicalDisplayNameTests(TestCase):
    """归并成「正本」时的选取顺序。

    同名副本必须落在**不同项目**里：``(project, name)`` 上有唯一约束，
    同项目内本来就不可能出现两条同名 Skill。
    """

    def setUp(self):
        self.user = User.objects.create_user(username='canonical-tester', password='secret')
        self.project_a = Project.objects.create(
            name='canonical-project-a', description='test project', creator=self.user,
        )
        self.project_b = Project.objects.create(
            name='canonical-project-b', description='test project', creator=self.user,
        )

    def _skill(self, project, *, display_name: str = '') -> Skill:
        return Skill.objects.create(
            project=project, creator=self.user, name='same-skill',
            display_name=display_name, description='d',
            skill_content='', skill_path='', is_active=True,
        )

    def test_canonical_prefers_the_copy_with_a_display_name(self):
        # 没有展示名的那条 id 更小，若只看兜底规则它会胜出。
        self._skill(self.project_a)
        named = self._skill(self.project_b, display_name='同名展示名')

        best = pick_canonical(list(Skill.objects.filter(name='same-skill')))

        self.assertEqual(best['same-skill'].pk, named.pk)

    def test_canonical_falls_back_to_smallest_id_when_no_display_name(self):
        first = self._skill(self.project_a)
        self._skill(self.project_b)

        best = pick_canonical(list(Skill.objects.filter(name='same-skill')))

        self.assertEqual(best['same-skill'].pk, first.pk)
