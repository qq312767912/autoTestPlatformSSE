from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from knowledge_evolution.gold_models import GoldDataset, TestAssetTaxonomy


class TestAssetTaxonomyAPITests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from projects.models import Project, ProjectMember

        self.lead = User.objects.create_user(username="taxonomy-lead", password="pass")
        self.member = User.objects.create_user(username="taxonomy-member", password="pass")
        self.project = Project.objects.create(name="上证 e 投票", creator=self.lead)
        self.other_project = Project.objects.create(name="其他项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.member, role="member")
        ProjectMember.objects.create(project=self.other_project, user=self.lead, role="owner")
        self.client = APIClient()
        self.url = "/api/knowledge-evolution/test-asset-taxonomies/"

    def _create(self):
        self.client.force_authenticate(self.lead)
        response = self.client.post(self.url, {
            "project": self.project.pk,
            "scope_key": "sse_evote",
            "version": "1.0.0",
            "categories": [{"key": "meeting", "name": "股东大会"}],
            "critical_scenarios": [{"key": "vote_submit", "name": "投票提交"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def test_test_lead_can_publish_an_immutable_taxonomy(self):
        created = self._create()
        submitted = self.client.post(f"{self.url}{created['id']}/submit/", {}, format="json")
        self.assertEqual(submitted.status_code, 200, submitted.content)
        published = self.client.post(f"{self.url}{created['id']}/publish/", {}, format="json")
        self.assertEqual(published.status_code, 200, published.content)
        payload = published.json()["data"]
        self.assertEqual(payload["state"], "published")
        self.assertEqual(len(payload["content_hash"]), 64)
        self.assertEqual(payload["approved_by"], self.lead.pk)

        taxonomy = TestAssetTaxonomy.objects.get(pk=created["id"])
        taxonomy.categories = [{"key": "changed"}]
        with self.assertRaisesMessage(ValidationError, "不可修改"):
            taxonomy.save()

    def test_regular_member_cannot_create_taxonomy(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.url, {
            "project": self.project.pk,
            "scope_key": "sse_evote",
            "version": "1.0.0",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_dataset_rejects_foreign_or_unpublished_taxonomy(self):
        draft = TestAssetTaxonomy.objects.create(
            project=self.project, scope_key="sse_evote", version="draft",
            maintained_by=self.lead,
        )
        with self.assertRaisesMessage(ValidationError, "已发布"):
            GoldDataset.objects.create(
                project=self.project, name="专用集", task_type="testcase_generation",
                scope_type="domain", scope_key="sse_evote", taxonomy_version=draft,
            )

        foreign = TestAssetTaxonomy.objects.create(
            project=self.other_project, scope_key="sse_evote", version="published",
            state="published", maintained_by=self.lead,
        )
        with self.assertRaisesMessage(ValidationError, "不属于当前项目"):
            GoldDataset.objects.create(
                project=self.project, name="专用集", task_type="testcase_generation",
                scope_type="domain", scope_key="sse_evote", taxonomy_version=foreign,
            )
