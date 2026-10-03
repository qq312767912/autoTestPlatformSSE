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
4. **公共目录条目的读写都不按 URL 项目过滤，改由角色把关。** 读（列表、详情、
   内容、版本史）只要求登录；治理（补填阶段、启停、删除、隔离版本）要求
   "超管或任一项目测试负责人"。⚠️ 这条改过一次：初版是"读侧公共、写侧仍锚定
   项目"，结果是列表返回全平台的卡、卡片上的按钮却一律 404 —— 见
   ``PublicCatalogueEntryIsNotProjectBoundTests`` 的类注释。
   创建类动作（upload / preflight / candidates / import-*）仍锚定 URL 项目，
   因为那里的 ``project_pk`` 是"新 Skill 记在哪个项目名下"的上传出处，不是访问边界。

正本规则与"为什么不做数据层合并"见 ``skills.canonical``。
"""
import hashlib

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge_evolution.capability_models import CapabilityRelease
from knowledge_evolution.capability_registry import SKILL_STAGE_OPTIONS
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

    def test_envelope_declares_what_the_caller_may_do(self):
        """`meta.can_manage` 必须与后端的 403 同源 —— 前端靠它决定按钮是否可用。

        没有它，就会出现"按钮在这、点了必然报错"（2026-10-02 的 404 回归就是这么来的）。
        这里刻意用两个极端角色各验一次：零成员关系的普通用户不能管，超管能管。
        """
        meta = self._list().data["meta"]
        self.assertFalse(meta["can_manage"])
        self.assertFalse(meta["can_bind_stage"])
        # 可选值真值 = SKILL_STAGE_OPTIONS（8 类业务能力 + 平台基础能力），不是
        # BUSINESS_CAPABILITY_STAGES —— 后者是八类业务能力口径，不含跨阶段的
        # 「平台基础能力」档。
        self.assertEqual(len(meta["stage_options"]), len(SKILL_STAGE_OPTIONS))
        self.assertEqual(
            [opt["value"] for opt in meta["stage_options"]], list(SKILL_STAGE_OPTIONS)
        )
        self.assertIn("platform_base", [opt["value"] for opt in meta["stage_options"]])
        self.assertNotIn("knowledge_query", [opt["value"] for opt in meta["stage_options"]])

        root = User.objects.create_superuser(username="catalogue-root", password="x")
        self.client.force_authenticate(user=root)
        meta = self._list().data["meta"]
        self.assertTrue(meta["can_manage"])
        self.assertTrue(meta["can_bind_stage"])

    def test_envelope_says_a_plain_project_member_cannot_manage(self):
        """执行人员（= 项目成员）能读、不能治理：这正是 `can_manage` 要区分的。"""
        member = User.objects.create_user(username="public-member", password="x")
        ProjectMember.objects.create(project=self.project_a, user=member, role="member")
        self.client.force_authenticate(user=member)
        meta = self._list().data["meta"]
        self.assertFalse(meta["can_manage"])

    def test_envelope_says_a_test_lead_can_manage(self):
        lead = User.objects.create_user(username="public-lead", password="x")
        ProjectMember.objects.create(project=self.project_a, user=lead, role="admin")
        self.client.force_authenticate(user=lead)
        meta = self._list().data["meta"]
        self.assertTrue(meta["can_manage"])


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

    def test_free_text_is_kept_as_a_namespaced_custom_stage(self):
        """自由文本不再一律报错，而是收进 ``custom:`` 命名空间。

        用户要能自己加一档（如「性能测试」）。但**不能**原样裸存：裸字符串里的拼写
        错误会混进 ``Q(declared_stage=stage)`` 的阶段检索，既不报错、也永远匹配不上，
        页面上和"没填"长得一样。加前缀后它至少是一个可辨识的"自定义阶段"，
        而不是疑似规范阶段的野值。
        """
        response = self._bind("性能测试", self.superuser)
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.declared_stage, "custom:性能测试")

    def test_a_misspelled_identifier_cannot_impersonate_a_canonical_stage(self):
        """``test_execution`` 敲成 ``test_executoin`` 时，它不会变成"另一个规范阶段"。"""
        response = self._bind("test_executoin", self.superuser)
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.declared_stage, "custom:test_executoin")

    def test_canonical_chinese_label_is_normalised_to_its_identifier(self):
        """前端 allow-create 会把中文名当值提交，必须归一化，不能与标识符并存两份。"""
        response = self._bind("测试执行", self.superuser)
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.declared_stage, "test_execution")

    def test_overlong_custom_label_is_rejected(self):
        response = self._bind("测" * 33, self.superuser)
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


class PublicCatalogueEntryIsNotProjectBoundTests(APITestCase):
    """**公共目录的条目不属于任何单一项目**：角色够就能跨项目 URL 操作它。

    这是 2026-10-02 那次回归的钉子测试。当时的做法是"读侧公共化、写侧仍按 URL
    项目过滤"，结果是**列表能返回全平台的卡，但卡片上的启停 / 查看内容 / 版本列表
    一律 404**（实测：URL 项目 1 下 5/20 张死、项目 7 下 15/20、项目 3 下 20/20 全死
    —— 项目 3 名下 6 行前后都是被归并掉的副本，没有一条是正本）。
    列表看得见却点不动，比"看不到"更糟，所以改成读写都按**角色**判定。

    URL 里的 ``project_pk`` 只在创建类动作（upload / preflight / candidates /
    import-*）里仍是锚点：那是"新 Skill 记在哪个项目名下"的上传出处，不是访问边界。

    正本落在别的项目名下是**常态**（同名归并后 7 个名字里就有 1 个是这样），
    所以这里刻意让 Skill 归属 ``project_b``，再用 ``project_a`` 的 URL 去打它。
    """

    def setUp(self):
        self.project_a = Project.objects.create(name="公共目录A")
        self.project_b = Project.objects.create(name="公共目录B")
        self.lead = User.objects.create_user(username="catalogue-lead", password="x")
        self.member = User.objects.create_user(username="catalogue-member", password="x")
        self.outsider = User.objects.create_user(username="catalogue-outsider", password="x")
        # lead 只在 A 项目里是负责人；member 只在 A 项目里是执行人员；outsider 哪个都不是。
        ProjectMember.objects.create(project=self.project_a, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project_a, user=self.member, role="member")
        self.skill = Skill.objects.create(
            project=self.project_b, name="b-canonical", description="正本落在 B 项目",
        )
        self.client.force_authenticate(user=self.lead)

    def _url(self, name, **extra):
        return reverse(
            name, kwargs={"project_pk": self.project_a.id, "pk": self.skill.pk, **extra},
        )

    # ---------------- 负责人：跨项目 URL 也能治理 ----------------

    def test_lead_of_another_project_can_toggle_the_canonical(self):
        """A 项目的负责人在 A 的 URL 下能让 B 项目名下的正本启停。"""
        response = self.client.patch(
            self._url("project-skills-detail"), {"is_active": False}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.skill.refresh_from_db()
        self.assertFalse(self.skill.is_active)

    def test_lead_of_another_project_can_read_content_and_versions(self):
        """这两条正是回归里 404 掉的动作。"""
        self.assertEqual(self.client.get(self._url("project-skills-content")).status_code, 200)
        self.assertEqual(
            self.client.get(self._url("project-skills-list-versions")).status_code, 200,
        )

    def test_lead_of_another_project_can_delete_the_canonical(self):
        response = self.client.delete(self._url("project-skills-detail"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Skill.objects.filter(pk=self.skill.pk).exists())

    # ---------------- 角色门槛：治理需负责人，读只需登录 ----------------

    def test_plain_executor_cannot_govern_the_catalogue(self):
        """启停 / 删除是治理动作，一次动作影响所有项目看到的目录 → 执行人员不行。"""
        self.client.force_authenticate(user=self.member)
        self.assertEqual(
            self.client.patch(
                self._url("project-skills-detail"), {"is_active": False}, format="json",
            ).status_code,
            403,
        )
        self.assertEqual(self.client.delete(self._url("project-skills-detail")).status_code, 403)
        self.assertTrue(Skill.objects.filter(pk=self.skill.pk).exists())

    def test_executor_can_still_read_content_and_versions(self):
        """读目录内容与版本史不要求角色，只要求登录。"""
        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self._url("project-skills-content")).status_code, 200)
        self.assertEqual(
            self.client.get(self._url("project-skills-list-versions")).status_code, 200,
        )

    def test_non_member_without_any_role_cannot_govern(self):
        self.client.force_authenticate(user=self.outsider)
        self.assertEqual(
            self.client.patch(
                self._url("project-skills-detail"), {"is_active": False}, format="json",
            ).status_code,
            403,
        )

    def test_any_authenticated_user_can_read_the_catalogue_entry(self):
        """Skill Hub 对每个项目公开：非成员也能读内容与版本史。"""
        self.client.force_authenticate(user=self.outsider)
        self.assertEqual(self.client.get(self._url("project-skills-content")).status_code, 200)
        self.assertEqual(
            self.client.get(self._url("project-skills-list-versions")).status_code, 200,
        )

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self._url("project-skills-content")).status_code, (401, 403))

    # ---------------- 创建类动作仍锚定 URL 项目 ----------------

    def test_upload_is_still_anchored_to_the_url_project(self):
        """``upload`` 不是"打某条已有 Skill"，而是"新 Skill 记在哪个项目名下" ——
        它不是公共目录条目的动作，角色门槛也仍是"本项目执行人员"。"""
        self.client.force_authenticate(user=self.outsider)
        response = self.client.post(
            reverse("project-skills-preflight", kwargs={"project_pk": self.project_a.id}),
            {},
            format="multipart",
        )
        # 非成员：被 IsProjectScoped 挡下（400 是序列化器先报"没传文件"，这里只关心不是 2xx）。
        self.assertIn(response.status_code, (400, 403))
        self.assertNotEqual(response.status_code, 200)


class CustomStageTests(APITestCase):
    """用户自定义阶段：导入 / 补填时自己敲一档（如「性能测试」），不止固定九项。

    需求方原话："用户在上传 skill 的时候也支持自己添加阶段类型"。

    实现边界（这条是刻意的，不是偷懒）：自定义值一律存 ``custom:<名称>``，与任务类型
    真值 ``SKILL_STAGE_OPTIONS`` **命名空间隔开**。原因有两条——

    * 规范阶段是任务类型：背后有门禁分区、评测模板、``Q(declared_stage=stage)``
      的阶段匹配。真值一旦被用户输入污染，这些都会跟着漂（口径真值只有一处）。
    * 自定义档只是**归属标签**，与 ``platform_base`` 同语义：能让目录分好组、
      能让卡片挂上标签，但**不会**被任何阶段的检索命中。

    归一化只有一处实现（``capability_registry.normalize_stage_input``），下面既钉
    纯函数行为，也钉两个真实入口（导入分类字段、补填接口）与列表信封。
    """

    def setUp(self):
        self.project = Project.objects.create(name="自定义阶段项目")
        self.superuser = User.objects.create_superuser(
            username="custom-stage-admin", password="x", email="custom@example.com",
        )

    # ---------------- 归一化（纯函数） ----------------

    def test_canonical_identifier_passes_through(self):
        from knowledge_evolution.capability_registry import normalize_stage_input

        self.assertEqual(normalize_stage_input("test_execution"), "test_execution")

    def test_canonical_chinese_label_maps_back_to_the_identifier(self):
        from knowledge_evolution.capability_registry import normalize_stage_input

        self.assertEqual(normalize_stage_input("测试执行"), "test_execution")

    def test_platform_utility_stage_is_never_accepted_as_a_custom_one(self):
        """``knowledge_query`` 不是可声明的归属档，也不能"降级"成 ``custom:知识问答``。

        它登记在 ``STAGE_LABELS`` 里（所以看起来像个规范阶段），但属
        ``PLATFORM_UTILITY_STAGES``（平台工具）。两条路都得堵死：既不能当规范阶段
        放行，也不能被当成自定义档收进命名空间——否则会凭空造出一个与平台工具同名的
        假自定义档，排查时比报错更难。
        """
        from knowledge_evolution.capability_registry import normalize_stage_input

        for raw in ("knowledge_query", "知识问答"):
            with self.assertRaises(ValueError):
                normalize_stage_input(raw)

    def test_free_text_lands_in_the_custom_namespace(self):
        from knowledge_evolution.capability_registry import normalize_stage_input

        self.assertEqual(normalize_stage_input("性能测试"), "custom:性能测试")

    def test_normalisation_is_idempotent(self):
        """前端回显的就是库里的值，二次提交不该再套一层前缀。"""
        from knowledge_evolution.capability_registry import normalize_stage_input

        self.assertEqual(normalize_stage_input("custom:性能测试"), "custom:性能测试")

    def test_scattered_whitespace_is_collapsed(self):
        """「性能  测试」与「性能 测试」不该在库里裂成两档。"""
        from knowledge_evolution.capability_registry import normalize_stage_input

        self.assertEqual(normalize_stage_input("  性能  测试 "), "custom:性能 测试")

    def test_blank_is_rejected(self):
        from knowledge_evolution.capability_registry import normalize_stage_input

        with self.assertRaises(ValueError):
            normalize_stage_input("   ")

    def test_overlong_label_is_rejected(self):
        from knowledge_evolution.capability_registry import normalize_stage_input

        with self.assertRaises(ValueError):
            normalize_stage_input("测" * 33)

    def test_display_label_strips_the_namespace(self):
        from knowledge_evolution.capability_registry import stage_display_label

        self.assertEqual(stage_display_label("custom:性能测试"), "性能测试")
        self.assertEqual(stage_display_label("test_execution"), "测试执行")
        self.assertEqual(stage_display_label(""), "")

    # ---------------- 真实入口：导入分类字段 ----------------

    def test_import_category_field_accepts_free_text(self):
        from skills.serializers import SkillUploadSerializer

        self.assertEqual(
            SkillUploadSerializer().validate_category("性能测试"), "custom:性能测试",
        )

    def test_import_category_field_normalises_a_canonical_label(self):
        from skills.serializers import SkillUploadSerializer

        self.assertEqual(
            SkillUploadSerializer().validate_category("测试执行"), "test_execution",
        )

    def test_import_serializers_really_carry_the_category_field(self):
        """导入序列化器必须真的把 ``category`` / ``description`` 收进字段表。

        这条是给一个真实踩过的坑立的桩：``SkillCategoryMixin`` 起初是普通的 ``object``
        混入类，而 DRF 的字段收集**只认自身带 ``_declared_fields`` 的基类**——普通类
        没有这个属性，于是两个字段一个都没进字段表：既不校验、也不进 ``validated_data``，
        视图里 ``validated_data['category']`` 直接 ``KeyError``，上传接口整体 500。

        断言必须落在"字段表里有它"上：光断言 ``validate_category`` 返回什么没用——
        方法写得再对，字段没被收集就永远不会被调用。
        """
        from skills.serializers import (
            SkillGitImportSerializer, SkillUploadSerializer, SkillZipUrlImportSerializer,
        )

        for serializer_cls in (
            SkillUploadSerializer, SkillGitImportSerializer, SkillZipUrlImportSerializer,
        ):
            fields = serializer_cls().fields
            self.assertIn("category", fields, serializer_cls.__name__)
            self.assertIn("description", fields, serializer_cls.__name__)

    # ---------------- 真实入口：列表信封与行 ----------------

    def _list(self):
        self.client.force_authenticate(user=self.superuser)
        return self.client.get(
            reverse("project-skills-list", kwargs={"project_pk": self.project.id}),
        )

    def test_envelope_offers_custom_stages_already_in_use(self):
        """下拉要能列出库里已在用的自定义档，否则复用时只能重新敲、还容易敲出近似重名。"""
        Skill.objects.create(
            project=self.project, name="perf-skill", description="性能压测",
            declared_stage="custom:性能测试",
        )
        options = self._list().data["meta"]["stage_options"]

        # 规范九项在前、顺序不变，且逐项标 custom=False。
        head = options[:len(SKILL_STAGE_OPTIONS)]
        self.assertEqual([opt["value"] for opt in head], list(SKILL_STAGE_OPTIONS))
        self.assertFalse(any(opt["custom"] for opt in head))

        # 自定义项追加在后，带剥了前缀的展示名。
        self.assertEqual(
            [(opt["value"], opt["label"]) for opt in options if opt["custom"]],
            [("custom:性能测试", "性能测试")],
        )

    def test_row_shows_the_custom_name_without_the_namespace(self):
        Skill.objects.create(
            project=self.project, name="perf-skill", description="性能压测",
            declared_stage="custom:性能测试",
        )
        row = next(r for r in self._list().data["data"] if r["name"] == "perf-skill")
        self.assertEqual(row["stage"], "custom:性能测试")
        self.assertEqual(row["stage_label"], "性能测试")
        self.assertEqual(row["stage_source"], "declared")

