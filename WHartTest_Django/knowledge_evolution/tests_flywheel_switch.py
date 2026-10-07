"""灰度开关的**写入侧**：谁能改、取值怎么解析、改完闸门是否真的跟着变。

``tests_t14_launch.RolloutSwitchTests`` 钉的是**关闭时**四个入口一律拒绝；
本文件补的是**打开与关闭这个动作本身**。少了这一环，"先灰度 e 投票"就只能
靠改数据库完成，而"只能靠改数据库完成的上线动作"等于没有上线流程。

单独立文件而不是塞进 T14：开关是上线当天每个待灰度项目都要用一次的动作，
它的权限矩阵与取值解析值得有自己的断言，而不是挂在长链路用例的末尾。
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from projects.models import ProjectMember

from .history_models import ProjectFlywheelSetting
from .rollout import LINKAGE_DISABLED_MESSAGE, linkage_enabled
from .tests_t09_t13 import TEST_MEDIA_ROOT, SkillHubBaseTests

BASE = "/api/knowledge-evolution/"
OPS = "/api/knowledge-evolution/operations/"
SET_URL = f"{BASE}flywheel-settings/set/"
STATE_URL = f"{BASE}flywheel-settings/state/"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LinkageSwitchWriteTests(SkillHubBaseTests):
    """打开/关闭开关这个动作本身。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _set(self, *, actor, project=None, **payload):
        self.client.force_authenticate(actor)
        body = {"project": (project or self.project).pk}
        body.update(payload)
        return self.client.post(SET_URL, body, format="json")

    def test_owner_opens_then_closes_the_gate(self):
        opened = self._set(actor=self.lead, enabled=True, rollout_note="先灰度方案阶段")
        self.assertEqual(opened.status_code, 200, opened.content)
        self.assertTrue(linkage_enabled(self.project))

        closed = self._set(actor=self.lead, enabled=False)
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertFalse(linkage_enabled(self.project))

    def test_project_admin_role_can_open_the_gate(self):
        """项目角色 admin 与 owner 同为测试负责人（``projects/roles.py`` 的唯一真值）。"""
        deputy = User.objects.create_user(username="t14-deputy", password="pass")
        ProjectMember.objects.create(project=self.project, user=deputy, role="admin")

        response = self._set(actor=deputy, enabled=True)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(linkage_enabled(self.project))

    def test_superuser_can_open_a_project_they_do_not_belong_to(self):
        """平台管理员对任意项目有效——"管理员最高权限"的边界，钉住它。

        没有这条，管理员会被自己的灰度开关挡在门外，而打开它的唯一办法是改库。
        """
        root = User.objects.create_superuser(username="t14-root", password="pass")

        response = self._set(actor=root, project=self.other_project, enabled=True)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(linkage_enabled(self.other_project))

    def test_executor_cannot_open_the_gate_and_nothing_is_written(self):
        response = self._set(actor=self.executor, enabled=True)

        self.assertEqual(response.status_code, 403)
        # 被拒就不能留下半成品：留下 setting 会让后续合法读取把"未开启"读成"已开启"。
        self.assertFalse(
            ProjectFlywheelSetting.objects.filter(project=self.project).exists()
        )

    def test_outsider_cannot_open_the_gate(self):
        outsider = User.objects.create_user(username="t14-outsider-2", password="pass")

        response = self._set(actor=outsider, enabled=True)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectFlywheelSetting.objects.filter(project=self.project).exists()
        )

    def test_reopening_is_idempotent(self):
        """OneToOne：重复开必须落在同一行，不能因为"第二次"就报错或另起一行。"""
        self._set(actor=self.lead, enabled=True)
        again = self._set(actor=self.lead, enabled=True)

        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(
            ProjectFlywheelSetting.objects.filter(project=self.project).count(), 1,
        )

    def test_switch_records_who_flipped_it(self):
        self._set(actor=self.lead, enabled=True, rollout_note="留档")

        row = ProjectFlywheelSetting.objects.get(project=self.project)
        self.assertEqual(row.updated_by_id, self.lead.pk)
        self.assertEqual(row.rollout_note, "留档")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LinkageSwitchValueTests(SkillHubBaseTests):
    """开关取值解析：含糊输入宁可拒绝，也不能被猜成"开启"。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)

    def _set(self, **payload):
        body = {"project": self.project.pk}
        body.update(payload)
        return self.client.post(SET_URL, body, format="json")

    def test_string_false_does_not_open_the_gate(self):
        """``bool("false")`` 是 ``True``——漏了 JSON 类型就会把"关闭"写成"开启"。

        这是本类里最重要的一条：误开等于闸门失效，而它在界面上显示"操作成功"。
        """
        response = self._set(enabled="false")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(linkage_enabled(self.project))
        self.assertFalse(response.json()["data"]["enabled"])

    def test_string_true_opens_the_gate(self):
        response = self._set(enabled="true")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(linkage_enabled(self.project))

    def test_numeric_flags_are_accepted(self):
        self._set(enabled=1)
        self.assertTrue(linkage_enabled(self.project))

        self._set(enabled=0)
        self.assertFalse(linkage_enabled(self.project))

    def test_missing_value_is_a_client_error_not_a_silent_open(self):
        response = self._set()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            ProjectFlywheelSetting.objects.filter(project=self.project).exists()
        )

    def test_ambiguous_value_is_rejected(self):
        response = self._set(enabled="maybe")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            ProjectFlywheelSetting.objects.filter(project=self.project).exists()
        )

    def test_missing_project_is_rejected(self):
        response = self.client.post(SET_URL, {"enabled": True}, format="json")

        self.assertEqual(response.status_code, 400)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LinkageSwitchStateShapeTests(SkillHubBaseTests):
    """前端按 ``can_manage`` 决定开关按钮显隐，按 state 的形状重绘。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_state_reports_can_manage_per_role(self):
        self.client.force_authenticate(self.lead)
        lead_state = self.client.get(
            STATE_URL, {"project": self.project.pk},
        ).json()["data"]
        self.assertTrue(lead_state["can_manage"])

        self.client.force_authenticate(self.executor)
        executor_state = self.client.get(
            STATE_URL, {"project": self.project.pk},
        ).json()["data"]
        self.assertFalse(executor_state["can_manage"])

    def test_executor_can_read_state_to_explain_a_grey_button(self):
        """读侧不设限：按钮为什么是灰的，得有人能解释给执行人员听。

        这里钉的是"读得到"与"改不了"同时成立——只做前者会让人以为读完就能点。
        """
        self.client.force_authenticate(self.executor)

        response = self.client.get(STATE_URL, {"project": self.project.pk})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["data"]["can_manage"])

    def test_set_returns_the_same_shape_as_state(self):
        """同构是为了让前端开关一次就重绘，不必"写完再查一次"。"""
        self.client.force_authenticate(self.lead)
        written = self.client.post(SET_URL, {
            "project": self.project.pk, "enabled": True, "rollout_note": "同构",
        }, format="json").json()["data"]
        read = self.client.get(
            STATE_URL, {"project": self.project.pk},
        ).json()["data"]

        self.assertEqual(set(written), set(read))
        for key in ("enabled", "configured", "rollout_note", "can_manage", "pilot_stage"):
            self.assertEqual(written[key], read[key], key)
        self.assertTrue(written["configured"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LinkageSwitchGateTests(SkillHubBaseTests):
    """开关不是装饰：开与关必须真的改变入口的放行结果。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)

    def _start(self, workflow_id):
        return self.client.post(f"{OPS}start-workflow/", {
            "project": self.project.pk, "workflow_id": workflow_id,
        }, format="json")

    def _flip(self, enabled):
        return self.client.post(SET_URL, {
            "project": self.project.pk, "enabled": enabled,
        }, format="json")

    def test_flipping_on_turns_the_rejection_into_an_accepted_launch(self):
        rejected = self._start("wf-before")
        self.assertEqual(rejected.status_code, 403)
        self.assertIn(LINKAGE_DISABLED_MESSAGE, rejected.content.decode("utf-8"))

        self._flip(True)
        accepted = self._start("wf-after")

        self.assertEqual(accepted.status_code, 201, accepted.content)

    def test_flipping_back_off_rejects_again(self):
        """开关必须能双向改：灰度出问题时，第一动作就是把它关回去。"""
        self._flip(True)
        self.assertEqual(self._start("wf-open").status_code, 201)

        self._flip(False)
        rejected = self._start("wf-closed")

        self.assertEqual(rejected.status_code, 403)
        self.assertIn(LINKAGE_DISABLED_MESSAGE, rejected.content.decode("utf-8"))
