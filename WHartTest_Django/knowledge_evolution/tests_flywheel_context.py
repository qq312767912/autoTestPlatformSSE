from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.flywheel_context import FlywheelContextService
from knowledge_evolution.operations import WorkflowGateService
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.workflow_models import FlywheelRun


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class FlywheelContextServiceTests(SkillHubBaseTests):
    def test_same_workflow_id_is_isolated_by_project(self):
        first = FlywheelContextService.create(
            project=self.project, workflow_id="wf-shared", entry_type="requirement",
            actor=self.lead,
        )
        second = FlywheelContextService.create(
            project=self.other_project, workflow_id="wf-shared", entry_type="chat",
            actor=self.lead,
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(FlywheelRun.objects.filter(workflow_id="wf-shared").count(), 2)

    def test_resolve_stage_returns_the_projects_explicit_version_lock(self):
        stage = "testcase_generation"
        _skill, version = self.make_skill_version(
            name="public-testcase", version="2.1.0", stage=stage,
            project=self.other_project,
        )
        run = FlywheelContextService.create(
            project=self.project, workflow_id="wf-version", entry_type="flywheel",
            actor=self.lead,
        )
        WorkflowGateService.start_workflow(
            project=self.project, workflow_id=run.workflow_id, actor=self.lead,
            stages=[stage], pins={stage: str(version.pk)},
        )

        context = FlywheelContextService.resolve_stage(run=run, stage=stage)

        self.assertTrue(context["managed"])
        self.assertEqual(context["skill_version_id"], str(version.pk))

    def test_cross_project_parent_output_is_rejected(self):
        run = FlywheelContextService.create(
            project=self.project, workflow_id="wf-parent", entry_type="chat", actor=self.lead,
        )
        foreign = self.make_output(
            project=self.other_project, task_type="risk_identification",
            workflow_id=run.workflow_id,
        )

        with self.assertRaisesMessage(ValidationError, "上游产出不属于当前项目"):
            FlywheelContextService.resolve_stage(
                run=run, stage="testcase_generation", parent_output_ids=[foreign.pk],
            )

    def test_parent_output_from_another_workflow_is_rejected(self):
        run = FlywheelContextService.create(
            project=self.project, workflow_id="wf-one", entry_type="chat", actor=self.lead,
        )
        output = self.make_output(
            project=self.project, task_type="risk_identification", workflow_id="wf-two",
        )

        with self.assertRaisesMessage(ValidationError, "上游产出不属于当前流程"):
            FlywheelContextService.resolve_stage(
                run=run, stage="testcase_generation", parent_output_ids=[output.pk],
            )


class FlywheelRunAPITests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from projects.models import Project, ProjectMember

        self.user = User.objects.create_user(username="flywheel-member", password="pass")
        self.outsider = User.objects.create_user(username="flywheel-outsider", password="pass")
        self.project = Project.objects.create(name="飞轮上下文项目", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="member")
        self.client = APIClient()
        self.url = "/api/knowledge-evolution/flywheel-runs/"
        # T14：`flywheel-runs` 的创建与 `open` 都受项目级灰度开关管辖。
        from .history_models import ProjectFlywheelSetting
        ProjectFlywheelSetting.objects.create(project=self.project, enabled=True)

    def test_member_can_create_and_list_a_run(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(self.url, {
            "project": self.project.pk,
            "workflow_id": "wf-api-context",
            "entry_type": "requirement",
            "intent": "production",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        run_id = response.json()["data"]["id"]

        listing = self.client.get(self.url, {"project": self.project.pk})
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()["data"][0]["id"], run_id)

    def test_non_member_cannot_create_or_see_a_run(self):
        FlywheelRun.objects.create(
            project=self.project, workflow_id="wf-private", entry_type="flywheel",
        )
        self.client.force_authenticate(self.outsider)
        created = self.client.post(self.url, {
            "project": self.project.pk,
            "workflow_id": "wf-denied",
            "entry_type": "chat",
        }, format="json")
        self.assertEqual(created.status_code, 403)
        listing = self.client.get(self.url, {"project": self.project.pk})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"], [])
