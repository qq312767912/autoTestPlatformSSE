"""T01 验收测试：Skill 的业务角色授权与项目边界（**口径已两次修订**）。

覆盖 tasks.md T01 的验收项，外加 2026-10-02 的口径修订：

- 跨项目**读**与跨项目**写**都不再被拒绝 —— Skill Hub 是**平台级公共目录**，
  条目不属于任何单一项目，所以访问边界改按**角色**判定（见下）。
- 同项目授权矩阵：项目成员可读，非成员一律 403（``hub-access`` 仍按项目）；
  测试负责人与测试执行人员的角色判定与 ``knowledge_evolution`` 既有约定一致。

⚠️ **口径修订一（读侧）**：Skill 的内容对每个项目都是公开的，所以列表与详情都
不再按项目过滤——列表返回的是公共目录（同名副本已归并成一条正本）。
原 T01 里"跨项目读取被拒绝""猜 UUID 404"两条读侧验收项**已作废**。

⚠️ **口径修订二（写侧，2026-10-02 同日发现回归后）**：初版只放开读侧、写侧仍锚定
URL 项目，结果**列表能返回全平台的卡、卡片上的按钮却一律 404**（实测 URL 项目 1 下
5/20 张死、项目 7 下 15/20、项目 3 下 **20/20 全死**）。于是写侧也改为按角色判定：
治理动作要"超管或任一项目测试负责人"，读只要登录，创建类动作（上传/预检/候选/导入）
仍锚定 URL 项目（那是"新 Skill 记在哪个项目名下"的上传出处）。
原 T01 里"跨项目写被拒绝""在自有项目路径猜他项目 UUID 404"两条写侧验收项**亦已作废**。
"""
from types import SimpleNamespace

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from projects.models import Project, ProjectMember
from projects.roles import (
    TEST_EXECUTOR_ROLES,
    TEST_LEAD_ROLES,
    IsTestExecutor,
    IsTestLead,
    business_role,
    is_test_executor,
    is_test_lead,
    visible_project_ids,
)
from skills.models import Skill


def grant(user, *codenames):
    """给用户授予指定模型权限，并丢弃 Django 的权限缓存。"""
    permissions = Permission.objects.filter(codename__in=codenames)
    user.user_permissions.add(*permissions)
    return User.objects.get(pk=user.pk)


class RoleMappingTests(TestCase):
    """业务角色映射真值表。"""

    def setUp(self):
        self.project = Project.objects.create(name="角色项目")
        self.other_project = Project.objects.create(name="另一个项目")
        self.owner = User.objects.create_user(username="owner")
        self.admin = User.objects.create_user(username="admin")
        self.member = User.objects.create_user(username="member")
        self.outsider = User.objects.create_user(username="outsider")
        self.superuser = User.objects.create_superuser(username="root", password="x")
        ProjectMember.objects.create(project=self.project, user=self.owner, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.admin, role="admin")
        ProjectMember.objects.create(project=self.project, user=self.member, role="member")
        ProjectMember.objects.create(project=self.other_project, user=self.outsider, role="member")

    def test_role_constants_are_consistent(self):
        # 测试执行人员集合必须包含测试负责人集合，否则负责人会被挡在执行动作外。
        self.assertTrue(set(TEST_LEAD_ROLES).issubset(set(TEST_EXECUTOR_ROLES)))

    def test_lead_and_executor_matrix(self):
        self.assertTrue(is_test_lead(self.owner, self.project.id))
        self.assertTrue(is_test_lead(self.admin, self.project.id))
        self.assertFalse(is_test_lead(self.member, self.project.id))
        self.assertFalse(is_test_lead(self.outsider, self.project.id))

        self.assertTrue(is_test_executor(self.member, self.project.id))
        self.assertFalse(is_test_executor(self.outsider, self.project.id))

    def test_superuser_is_lead_everywhere(self):
        self.assertTrue(is_test_lead(self.superuser, self.other_project.id))
        self.assertTrue(is_test_executor(self.superuser, self.other_project.id))
        # 超级用户不受项目枚举限制。
        self.assertIsNone(visible_project_ids(self.superuser))

    def test_business_role_labels(self):
        self.assertEqual(business_role(self.owner, self.project.id), "test_lead")
        self.assertEqual(business_role(self.member, self.project.id), "test_executor")
        self.assertIsNone(business_role(self.outsider, self.project.id))

    def test_permission_classes_follow_role_mapping(self):
        def view_for(user, project_id):
            return SimpleNamespace(kwargs={"project_pk": project_id})

        request_owner = SimpleNamespace(user=self.owner)
        request_member = SimpleNamespace(user=self.member)
        request_outsider = SimpleNamespace(user=self.outsider)

        self.assertTrue(IsTestLead().has_permission(request_owner, view_for(self.owner, self.project.id)))
        self.assertFalse(IsTestLead().has_permission(request_member, view_for(self.member, self.project.id)))
        self.assertFalse(IsTestLead().has_permission(request_outsider, view_for(self.outsider, self.project.id)))
        self.assertFalse(IsTestExecutor().has_permission(request_outsider, view_for(self.outsider, self.project.id)))


class SkillProjectIsolationTests(APITestCase):
    """读侧是公共目录；写侧按**角色**判定（都不是按 URL 项目）。

    2026-10-02 用户明确口径：Skill 的内容对每个项目都是公开的 —— Skill Hub 是
    平台公共资源，不是"某个项目名下的私产"。所以列表与详情都不按项目过滤，
    任何项目进来看到的是同一份内容。

    写侧的边界见 ``SkillWritePermissionTests`` 与
    ``tests_public_catalogue.PublicCatalogueEntryIsNotProjectBoundTests``：
    URL 里的 ``project_pk`` 不再是访问边界，只在创建类动作里当"上传出处"。
    """

    def setUp(self):
        self.project = Project.objects.create(name="隔离项目 A")
        self.other_project = Project.objects.create(name="隔离项目 B")
        self.user = grant(User.objects.create_user(username="reader"), "view_skill")
        ProjectMember.objects.create(project=self.project, user=self.user, role="member")
        self.skill = Skill.objects.create(
            project=self.project, name="case-review", description="本项目的 Skill"
        )
        self.foreign_skill = Skill.objects.create(
            project=self.other_project, name="foreign-skill", description="别的项目的 Skill"
        )
        self.client.force_authenticate(user=self.user)

    def _url(self, project, skill_id=None):
        if skill_id is None:
            return reverse("project-skills-list", kwargs={"project_pk": project.id})
        return reverse("project-skills-detail", kwargs={"project_pk": project.id, "pk": skill_id})

    def test_list_returns_the_shared_catalogue(self):
        """列表是公共目录：本项目的与别的项目的 Skill 都要出现（按名字排序）。"""
        response = self.client.get(self._url(self.project))
        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.data["data"]]
        self.assertEqual(names, ["case-review", "foreign-skill"])

    def test_read_does_not_require_membership_of_the_url_project(self):
        """用户不是 B 项目的成员，照样能读 B 项目路径下的公共目录。"""
        self.assertFalse(
            ProjectMember.objects.filter(project=self.other_project, user=self.user).exists()
        )
        response = self.client.get(self._url(self.other_project))
        self.assertEqual(response.status_code, 200)
        self.assertIn("foreign-skill", [item["name"] for item in response.data["data"]])

    def test_foreign_skill_detail_is_readable(self):
        """详情也是公共的：换一个 URL 项目也读得到。"""
        response = self.client.get(self._url(self.project, self.foreign_skill.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["name"], "foreign-skill")

    def test_own_skill_detail_is_visible(self):
        response = self.client.get(self._url(self.project, self.skill.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["name"], "case-review")

    def test_anonymous_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self._url(self.project))
        self.assertIn(response.status_code, (401, 403))

    def test_superuser_can_read_any_project(self):
        root = User.objects.create_superuser(username="root2", password="x")
        self.client.force_authenticate(user=root)
        response = self.client.get(self._url(self.other_project))
        self.assertEqual(response.status_code, 200)
        # 按名字找，不按下标取 —— 列表已按名字排序，用下标断言会把"排序"和
        # "内容"两件事混在一起（上一版就是这么写的）。
        names = [item["name"] for item in response.data["data"]]
        self.assertIn("foreign-skill", names)


class SkillWritePermissionTests(APITestCase):
    """写操作的角色门槛（**不再按 URL 项目判定**，改按"在某个项目里是什么角色"）。

    ⚠️ 口径改过一次。T27 初版是"读侧公共、写侧仍锚定 URL 项目"，于是列表返回
    全平台的卡、卡片上的启停 / 内容 / 版本列表却按 URL 项目过滤 → 归属项目 ≠ 当前
    项目的卡一律 404（实测项目 3 的 URL 下 20/20 张全死）。改成读写都按角色判定后，
    这里的旧断言（"跨项目写 403/404"）语义已经不成立。

    现在的边界是**角色**，不是项目：
    - 治理动作（启停、删除、补填阶段、隔离版本）→ 超管或任一项目测试负责人；
    - 读（详情、内容、版本史）→ 任何登录用户；
    - 创建类动作（上传、预检、候选、导入）→ 仍是 URL 项目的执行人员（那是"新 Skill
      记在哪个项目名下"的上传出处）。
    """

    def setUp(self):
        self.project = Project.objects.create(name="写权限项目")
        self.other_project = Project.objects.create(name="写权限项目 B")
        self.lead = User.objects.create_user(username="write-lead2")
        self.executor = User.objects.create_user(username="executor2")
        # lead 只在 self.project 里是负责人；executor 只在 self.project 里是成员。
        ProjectMember.objects.create(project=self.project, user=self.lead, role="admin")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.skill = Skill.objects.create(
            project=self.project, name="case-review", description="本项目的 Skill"
        )
        # 归属别的项目的 Skill：在公共目录里它就是"普通的一条目录项"。
        self.foreign_skill = Skill.objects.create(
            project=self.other_project, name="foreign-skill", description="别的项目"
        )
        self.client.force_authenticate(user=self.lead)

    def _detail_url(self, skill, project=None):
        return reverse(
            "project-skills-detail",
            kwargs={"project_pk": (project or self.project).id, "pk": skill.pk},
        )

    def test_lead_can_toggle_a_skill_through_any_project_url(self):
        """公共目录条目不属于任何项目：换一个 URL 项目照样能启停。"""
        response = self.client.patch(
            self._detail_url(self.foreign_skill, self.other_project),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.foreign_skill.refresh_from_db()
        self.assertFalse(self.foreign_skill.is_active)

    def test_lead_can_toggle_own_skill(self):
        response = self.client.patch(
            self._detail_url(self.skill), {"is_active": False}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertFalse(self.skill.is_active)

    def test_executor_cannot_toggle(self):
        """启停是治理动作：执行人员（哪怕是本项目成员）也不行。"""
        self.client.force_authenticate(user=self.executor)
        response = self.client.patch(
            self._detail_url(self.skill), {"is_active": False}, format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.skill.refresh_from_db()
        self.assertTrue(self.skill.is_active)

    def test_executor_cannot_delete(self):
        self.client.force_authenticate(user=self.executor)
        response = self.client.delete(self._detail_url(self.skill))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Skill.objects.filter(pk=self.skill.pk).exists())

    def test_lead_can_delete_and_the_row_is_gone(self):
        response = self.client.delete(self._detail_url(self.foreign_skill))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Skill.objects.filter(pk=self.foreign_skill.pk).exists())



class SkillHubAccessTests(APITestCase):
    """T18 控制台的自服务角色端点：成员读到自己的角色，非成员一律 403。

    这个端点是为了替代前端读 ``/projects/{id}/members/`` 反查角色而存在的——
    后者要求 ``projects.view_projectmember`` 权限位，会把零模型权限的项目成员
    误判成"非本项目成员"，直接违背 T18 验收里的"只显示测试负责人和测试执行人员"。
    因此这里刻意**不给任何账号授予模型权限**，用测试锁住"项目成员即可读取自己的
    业务角色"这条约束；哪天后端又在权限链上叠回模型权限位，这个用例会失败。
    """

    def setUp(self):
        self.project = Project.objects.create(name="控制台项目")
        self.other_project = Project.objects.create(name="控制台项目 B")
        self.lead = User.objects.create_user(username="hub-lead")
        self.executor = User.objects.create_user(username="hub-executor")
        self.outsider = User.objects.create_user(username="hub-outsider")
        ProjectMember.objects.create(project=self.project, user=self.lead, role="admin")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        ProjectMember.objects.create(
            project=self.other_project, user=self.outsider, role="member"
        )

    def _url(self, project):
        return reverse("project-skills-hub-access", kwargs={"project_pk": project.id})

    def test_executor_without_model_permissions_gets_own_role(self):
        """零模型权限的项目成员仍应读到自己的业务角色（T18 的执行人员路径）。"""
        self.assertEqual(self.executor.get_all_permissions(), set())
        self.client.force_authenticate(user=self.executor)
        response = self.client.get(self._url(self.project))
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["business_role"], "test_executor")
        self.assertFalse(data["is_test_lead"])
        self.assertTrue(data["is_test_executor"])

    def test_lead_gets_lead_role(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self._url(self.project))
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["business_role"], "test_lead")
        self.assertTrue(data["is_test_lead"])
        self.assertTrue(data["is_test_executor"])

    def test_non_member_is_forbidden(self):
        self.client.force_authenticate(user=self.outsider)
        response = self.client.get(self._url(self.project))
        self.assertEqual(response.status_code, 403)

    def test_payload_answers_only_who_am_i(self):
        """端点只回答"我是谁"，不得把成员列表顺带返回。"""
        self.client.force_authenticate(user=self.executor)
        response = self.client.get(self._url(self.project))
        self.assertEqual(
            set(response.data["data"].keys()),
            {"project_id", "business_role", "is_test_lead", "is_test_executor"},
        )

    def test_anonymous_is_rejected(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self._url(self.project)).status_code, (401, 403))

    def test_superuser_on_missing_project_is_not_found(self):
        root = User.objects.create_superuser(username="hub-root", password="x")
        self.client.force_authenticate(user=root)
        response = self.client.get(
            reverse("project-skills-hub-access", kwargs={"project_pk": 999999})
        )
        self.assertEqual(response.status_code, 404)
