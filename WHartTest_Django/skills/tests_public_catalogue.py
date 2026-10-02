"""Skill Hub 是**公共目录**：列表不按项目过滤，同名副本归并成一条正本；
「阶段未声明」的 Skill 由平台管理员或项目测试负责人补填阶段。

这里钉住四类容易被无声改坏的行为：

1. **列表不再按项目过滤。** 这是用户明确的口径（"Skill 的内容对每个项目都是公开的"）。
   一旦有人把 ``filter(project_id=...)`` 加回来，页面上不会报错，只会"每个项目看到的
   内容又不一样了"——必须由测试挡住。
2. **同名副本归并成一条正本，且副本数要显式给出。** 库里实测 34 条 Skill 只有 20 个
   名字（``browser-use`` 等 7 个名字各在 3 个项目里各有一条）。归并规则若漂移，
   表现是"某个 Skill 突然换了版本来源"，同样不报错。
3. **补填阶段必须真的改变展示与匹配。** 阶段写在 ``Skill.declared_stage``（版本包
   不可改写），取用时版本 manifest 声明**优先**、它作回落。
4. **写侧仍然锚定项目。** 读侧公共了，但"在 A 项目的 URL 下改到 B 项目的 Skill"
   必须仍然 404 —— 这是不能因为公共化而丢掉的边界。

正本规则与"为什么不做数据层合并"见 ``skills.canonical``。
"""
import hashlib

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge_evolution.capability_models import CapabilityRelease
from projects.models import Project, ProjectMember
from skills.canonical import canonical_skills, pick_canonical
from skills.models import Skill, SkillVersion


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def make_version(skill, version, *, stage="", source_type="upload",
                 package_path="pkg", state=None):
    """建一条版本；``stage`` 非空时写进 manifest（模拟"包自己声明了阶段"）。"""
    release = None
    if state is not None:
        release = CapabilityRelease.objects.create(
            project=skill.project, kind="skill", name=skill.name,
            version=version, artifact_hash=_sha(f"{skill.name}:{version}"),
            state=state, gate_report={},
        )
    return SkillVersion.objects.create(
        skill=skill, version=version,
        package_path=package_path,
        package_sha256=_sha(f"{skill.pk}:{version}"),
        manifest={"stage": stage} if stage else {},
        source_type=source_type,
        release=release,
    )


class CanonicalSkillTests(TestCase):
    """正本归并的选取规则（纯逻辑）。"""

    def setUp(self):
        self.project_a = Project.objects.create(name="正本A")
        self.project_b = Project.objects.create(name="正本B")

    def _skill(self, project, name):
        return Skill.objects.create(project=project, name=name, description="x")

    def test_same_name_collapses_into_one_entry(self):
        a = self._skill(self.project_a, "browser-use")
        self._skill(self.project_b, "browser-use")
        skills, copies = canonical_skills(Skill.objects.all())
        self.assertEqual([s.pk for s in skills], [a.pk])
        self.assertEqual(copies["browser-use"], 2)

    def test_copy_with_an_active_version_wins(self):
        """有活跃版本的那份是"正在生产用"的，不能被版本更多的副本顶掉。"""
        plain = self._skill(self.project_a, "url-reader")
        pinned = self._skill(self.project_b, "url-reader")
        make_version(plain, "1.0.0")
        make_version(plain, "1.1.0")
        pinned.active_version = make_version(pinned, "1.0.0")
        pinned.save(update_fields=["active_version"])

        skills, _ = canonical_skills(Skill.objects.all())
        self.assertEqual([s.pk for s in skills], [pinned.pk])

    def test_more_versions_wins_when_none_is_active(self):
        few = self._skill(self.project_a, "drawio")
        many = self._skill(self.project_b, "drawio")
        make_version(few, "1.0.0")
        for index in range(4):
            make_version(many, f"1.0.{index}")

        skills, _ = canonical_skills(Skill.objects.all())
        self.assertEqual([s.pk for s in skills], [many.pk])

    def test_lowest_id_breaks_ties_deterministically(self):
        """同优先级下结果必须稳定，不能随查询顺序漂移。"""
        first = self._skill(self.project_a, "whart-test")
        second = self._skill(self.project_b, "whart-test")
        make_version(first, "1.0.0")
        make_version(second, "1.0.0")

        chosen = pick_canonical(list(Skill.objects.all()))["whart-test"].pk
        self.assertEqual(chosen, min(first.pk, second.pk))

    def test_ordering_is_by_name_not_insertion(self):
        self._skill(self.project_a, "zeta")
        self._skill(self.project_a, "alpha")
        skills, _ = canonical_skills(Skill.objects.all())
        self.assertEqual([s.name for s in skills], ["alpha", "zeta"])


class PublicSkillListTests(APITestCase):
    """接口层：列表是公共目录。"""

    def setUp(self):
        self.project_a = Project.objects.create(name="公共A")
        self.project_b = Project.objects.create(name="公共B")
        self.user = User.objects.create_user(username="public-reader", password="x")
        # 刻意**不**给 self.user 任何项目成员关系：公共目录对全体登录用户可读。
        Skill.objects.create(project=self.project_b, name="b-only", description="B 项目独有")
        self.client.force_authenticate(user=self.user)

    def _list(self, project=None):
        return self.client.get(
            reverse("project-skills-list", kwargs={"project_pk": (project or self.project_a).id}),
        )

    def test_list_is_not_filtered_by_the_url_project(self):
        response = self._list()
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.data["data"]]
        # 在 A 项目的 URL 下，必须看到 B 项目的 Skill。
        self.assertIn("b-only", names)

    def test_list_is_readable_without_any_project_membership(self):
        self.assertEqual(self._list().status_code, 200)

    def test_list_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self._list().status_code, 401)

    def test_duplicate_names_are_collapsed_and_the_copy_count_is_reported(self):
        keeper = Skill.objects.create(project=self.project_b, name="url-reader", description="B")
        make_version(keeper, "1.0.0")
        # 同名但只有内联内容、跑不起来的那份不该被选成正本。
        Skill.objects.create(project=self.project_a, name="url-reader", description="A")

        rows = [row for row in self._list().data["data"] if row["name"] == "url-reader"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], keeper.pk)
        self.assertEqual(rows[0]["copies"], 2)

    def test_single_copy_reports_one(self):
        rows = [row for row in self._list().data["data"] if row["name"] == "b-only"]
        self.assertEqual(rows[0]["copies"], 1)


class DeclaredStageTests(APITestCase):
    """阶段补填：写在 Skill 上，manifest 声明优先。"""

    def setUp(self):
        self.project = Project.objects.create(name="阶段项目")
        self.other_project = Project.objects.create(name="阶段别的项目")
        self.superuser = User.objects.create_superuser(
            username="stage-admin", password="x", email="a@example.com",
        )
        self.lead = User.objects.create_user(username="stage-lead", password="x")
        ProjectMember.objects.create(project=self.other_project, user=self.lead, role="owner")
        self.member = User.objects.create_user(username="stage-member", password="x")
        ProjectMember.objects.create(project=self.project, user=self.member, role="member")

        # 刻意让目标 Skill 属于 self.project，而 lead 只在 other_project 里是负责人——
        # 公共目录下这种"正本落在别的项目名下"才是常态。
        self.skill = Skill.objects.create(
            project=self.project, name="legacy-no-stage", description="存量包没声明阶段",
        )
        make_version(self.skill, "0.0.0-migrated", source_type="migration", package_path="")

    def _bind(self, stage, user):
        self.client.force_authenticate(user=user)
        return self.client.post(
            reverse("project-skills-bind-stage", kwargs={
                "project_pk": self.project.id, "pk": self.skill.pk,
            }),
            {"stage": stage}, format="json",
        )

    def _row(self):
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(
            reverse("project-skills-list", kwargs={"project_pk": self.project.id}),
        )
        return next(r for r in response.data["data"] if r["name"] == "legacy-no-stage")

    def test_superuser_can_declare_a_stage(self):
        response = self._bind("testcase_generation", self.superuser)
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.declared_stage, "testcase_generation")

    def test_test_lead_of_any_project_can_declare_a_stage(self):
        """lead 只在自己那个项目里是负责人，仍能改公共池里的这条。"""
        response = self._bind("testcase_generation", self.lead)
        self.assertEqual(response.status_code, 200)

    def test_plain_project_member_is_refused(self):
        response = self._bind("testcase_generation", self.member)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("project-skills-bind-stage", kwargs={
                "project_pk": self.project.id, "pk": self.skill.pk,
            }),
            {"stage": "testcase_generation"}, format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_unknown_stage_is_rejected(self):
        """拼错的阶段不会报错、只会永远匹配不上，所以必须在入口拦掉。"""
        response = self._bind("not_a_stage", self.superuser)
        self.assertEqual(response.status_code, 400)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.declared_stage, "")

    def test_platform_utility_stage_is_rejected(self):
        """knowledge_query 属平台工具，不参与业务能力口径，不该被绑成阶段。"""
        response = self._bind("knowledge_query", self.superuser)
        self.assertEqual(response.status_code, 400)

    def test_blank_revokes_the_declaration(self):
        self._bind("testcase_generation", self.superuser)
        response = self._bind("", self.superuser)
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.declared_stage, "")

    def test_declared_stage_shows_up_in_the_list_with_its_source(self):
        self._bind("testcase_generation", self.superuser)
        row = self._row()
        self.assertEqual(row["stage"], "testcase_generation")
        self.assertEqual(row["stage_label"], "测试用例生成")
        # 前端靠 source 区分"包自己声明的"和"管理员补的"。
        self.assertEqual(row["stage_source"], "declared")

    def test_manifest_declaration_wins_over_the_manual_one(self):
        skill = Skill.objects.create(
            project=self.project, name="declared-both", description="两边都有",
        )
        make_version(skill, "1.0.0", stage="test_execution")
        skill.declared_stage = "testcase_generation"
        skill.save(update_fields=["declared_stage"])

        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(
            reverse("project-skills-list", kwargs={"project_pk": self.project.id}),
        )
        row = next(r for r in response.data["data"] if r["name"] == "declared-both")
        self.assertEqual(row["stage"], "test_execution")
        self.assertEqual(row["stage_source"], "manifest")

    def test_undeclared_skill_reports_no_stage_and_no_source(self):
        row = self._row()
        self.assertEqual((row["stage"], row["stage_label"], row["stage_source"]), ("", "", ""))


class WriteSideStaysProjectAnchoredTests(APITestCase):
    """读侧公共了，但跨项目**写**仍然必须被挡住。"""

    def setUp(self):
        self.project_a = Project.objects.create(name="写A")
        self.project_b = Project.objects.create(name="写B")
        self.lead = User.objects.create_user(username="write-lead", password="x")
        ProjectMember.objects.create(project=self.project_a, user=self.lead, role="owner")
        self.foreign_skill = Skill.objects.create(
            project=self.project_b, name="b-private", description="B 项目的 Skill",
        )
        self.client.force_authenticate(user=self.lead)

    def _url(self, name):
        return reverse(name, kwargs={"project_pk": self.project_a.id, "pk": self.foreign_skill.pk})

    def test_cannot_delete_another_projects_skill_through_own_project_url(self):
        response = self.client.delete(self._url("project-skills-detail"))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Skill.objects.filter(pk=self.foreign_skill.pk).exists())

    def test_cannot_patch_another_projects_skill_through_own_project_url(self):
        response = self.client.patch(
            self._url("project-skills-detail"), {"is_active": False}, format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.foreign_skill.refresh_from_db()
        self.assertTrue(self.foreign_skill.is_active)
