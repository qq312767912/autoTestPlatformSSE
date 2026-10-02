"""Skill 列表接口的展示元数据：来源 / 声明阶段 / 版本号。

这三个字段只服务 Skill Hub 的筛选器与卡片标签，**不是可用性判据** ——
可用性判据只有 ``SkillVersion.is_runnable`` 一处（见 ``tests_runtime.py``）。

有两条容易被无声改坏的行为，必须在这里钉住：

1. **视图必须把"展示版本"批量查好后注入序列化器 context。** 漏注入不会抛错，
   只会让三个字段静默变成空串 —— 表现是"筛选器一个选项都没有"。
   所以这里走**真实接口**断言，而不只是单测序列化器。
2. **展示版本刻意包含不可运行的版本。** 存量迁移那批 ``package_path`` 为空、
   确实跑不起来，恰恰是用户最需要"一眼看出它没包"的对象；若按可用性过滤，
   它们会显示成"无来源 / 无版本"，反而把问题藏起来。
"""
import hashlib

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge_evolution.capability_models import CapabilityRelease
from projects.models import Project, ProjectMember
from skills.models import Skill, SkillVersion
from skills.serializers import SkillListSerializer
from skills.views import SkillViewSet


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def make_version(skill, version, *, stage="", source_type="upload",
                 package_path="pkg", state=None):
    """建一条版本；给了 ``state`` 就同时建一条同状态的发布单元。

    ``package_path=""`` 用来复刻"只有内联 SKILL.md、包目录从未落盘"的存量记录 ——
    模型没有 ``blank=True``，但真实迁移就是这么写的，``objects.create()`` 不跑
    ``full_clean()``，所以这条路与线上一致。
    """
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


class SkillListMetadataTests(TestCase):
    """序列化器：展示版本 → 来源 / 阶段 / 版本号。"""

    def setUp(self):
        self.project = Project.objects.create(name="列表元数据项目")
        self.skill = Skill.objects.create(
            project=self.project, name="case-review", description="审查用例清晰度",
        )

    def _serialize(self, skills=None):
        skills = list(skills if skills is not None else [self.skill])
        data = SkillListSerializer(
            skills, many=True,
            context={"versions": SkillViewSet._display_versions(skills)},
        ).data
        return data[0]

    def test_version_metadata_is_exposed_with_labels(self):
        make_version(
            self.skill, "1.0.0", stage="case_review",
            source_type="upload",
            package_path=f"skills/1/{self.skill.id}/versions/x/1.0.0__abc",
        )
        row = self._serialize()
        self.assertEqual(row["source_type"], "upload")
        self.assertEqual(row["source_type_label"], "本地上传")
        self.assertEqual(row["stage"], "case_review")
        # 阶段中文名取自 capability_registry，不在前端另抄一份。
        self.assertEqual(row["stage_label"], "用例审查")
        self.assertEqual(row["version"], "1.0.0")

    def test_unknown_stage_falls_back_to_the_raw_identifier(self):
        # 未登记的自定义阶段原样返回，不编造中文名——裸标识符更好排查。
        make_version(self.skill, "1.0.0", stage="my_custom_stage")
        row = self._serialize()
        self.assertEqual(row["stage"], "my_custom_stage")
        self.assertEqual(row["stage_label"], "my_custom_stage")

    def test_skill_without_any_version_returns_blank_metadata(self):
        row = self._serialize()
        self.assertEqual(
            (row["source_type"], row["source_type_label"], row["stage"], row["version"]),
            ("", "", "", ""),
        )
        # 基本信息仍要正常返回，不能因为缺版本把整条记录弄丢。
        self.assertEqual(row["name"], "case-review")

    def test_active_version_wins_over_a_newer_version(self):
        """活跃指针优先：最新一版存在也不顶掉被钉住的生产版本。"""
        self.skill.active_version = make_version(
            self.skill, "1.0.0", stage="case_review", source_type="upload",
        )
        self.skill.save(update_fields=["active_version"])
        make_version(
            self.skill, "1.0.1", stage="case_review",
            source_type="evolution", state="draft",
        )
        row = self._serialize()
        self.assertEqual(row["version"], "1.0.0")
        self.assertEqual(row["source_type"], "upload")

    def test_display_version_includes_non_runnable_versions(self):
        """被隔离的版本照样要显示出来源——"跑不起来"是用户要看的信息，不是要藏的。"""
        version = make_version(
            self.skill, "1.0.0", stage="case_review",
            source_type="upload", state="quarantined",
        )
        self.assertFalse(version.is_runnable)
        row = self._serialize()
        self.assertEqual(row["version"], "1.0.0")
        self.assertEqual(row["source_type"], "upload")
        self.assertEqual(row["stage"], "case_review")

    def test_inline_only_legacy_version_still_reports_its_source(self):
        """存量迁移那批 ``package_path`` 为空：不可运行，但仍要有来源与版本号。"""
        version = make_version(
            self.skill, "0.0.0-migrated", source_type="migration", package_path="",
        )
        self.assertFalse(version.is_runnable)
        row = self._serialize()
        self.assertEqual(row["source_type"], "migration")
        self.assertEqual(row["version"], "0.0.0-migrated")

    def test_display_versions_costs_exactly_one_query(self):
        """批量查版本：条数增加不许导致查询数增加（列表页最忌 N+1）。"""
        for index in range(5):
            skill = Skill.objects.create(
                project=self.project, name=f"bulk-{index}", description="批量",
            )
            make_version(skill, "1.0.0", source_type="store", state="active")
        skills = list(Skill.objects.filter(project=self.project))
        with self.assertNumQueries(1):
            SkillViewSet._display_versions(skills)

    def test_display_versions_uses_no_query_without_skills(self):
        with self.assertNumQueries(0):
            self.assertEqual(SkillViewSet._display_versions([]), {})


class SkillListEndpointMetadataTests(APITestCase):
    """接口层：三个字段真的出现在响应里（防止视图漏注入 context）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="list-meta", password="x")
        self.project = Project.objects.create(name="列表接口项目")
        ProjectMember.objects.create(project=self.project, user=self.user, role="owner")
        self.skill = Skill.objects.create(
            project=self.project, name="webtest-plan-generator", description="生成测试方案",
        )
        make_version(
            self.skill, "1.0.0", stage="test_plan_generation", source_type="upload",
            state="active",
        )
        self.client.force_authenticate(user=self.user)

    def test_list_response_carries_filter_metadata(self):
        response = self.client.get(
            reverse("project-skills-list", kwargs={"project_pk": self.project.id}),
        )
        self.assertEqual(response.status_code, 200)
        row = response.data["data"][0]
        self.assertEqual(row["source_type"], "upload")
        self.assertEqual(row["source_type_label"], "本地上传")
        self.assertEqual(row["stage"], "test_plan_generation")
        self.assertEqual(row["stage_label"], "测试方案生成")
        self.assertEqual(row["version"], "1.0.0")
