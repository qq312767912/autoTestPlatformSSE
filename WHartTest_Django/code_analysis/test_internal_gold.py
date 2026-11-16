from copy import deepcopy
from django.contrib.auth.models import User, Permission
from django.test import TestCase
from rest_framework.test import APIClient
from projects.models import Project, ProjectMember
from code_analysis.models import AnalysisTask, ProjectRepository
from code_analysis.internal_gold import context, save_snapshot, library
from knowledge_evolution.gold_models import GoldCase, GoldDataset, GoldAnnotation
from rest_framework.exceptions import PermissionDenied, ValidationError


class InternalGoldTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="gold-review-owner", password=None)
        self.member = User.objects.create_user(username="gold-review-member")
        self.project = Project.objects.create(name="内部金标回归", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.member, role="member")
        self.repo = ProjectRepository.objects.create(project=self.project, source_type="local_git", name="示例仓库", local_path=".")
        self.task = AnalysisTask.objects.create(project=self.project, repository=self.repo, creator=self.user, source_type="commits", mode="standard", status="completed", base_sha="a"*40, head_sha="b"*40, title="人工审查快照", raw_diff="diff --git a/a.py b/a.py\n+unsafe()", change_report={"findings":[{"key":"finding-1","change":"未校验权限","file":"a.py","evidence":"unsafe()"}]}, test_report={"test_requirements":[{"title":"验证权限"}]})
        self.payload = {"snapshot_hash":context(self.task)["snapshot_hash"],"dataset_name":"内部代码审查金标集","annotations":[{"index":0,"label":"confirmed","comment":"已核对调用路径"}]}

    def save(self, actor=None, payload=None):
        return save_snapshot(task=self.task, actor=actor or self.user, payload=payload or deepcopy(self.payload))

    def test_snapshot_remains_after_source_changes_and_deletion(self):
        result = self.save();case = GoldCase.objects.get(pk=result['case_id'])
        original = deepcopy(case.input_snapshot)
        self.task.raw_diff="new diff";self.task.change_report={};self.task.save()
        self.task.delete();self.repo.delete()
        case.refresh_from_db()
        self.assertEqual(case.input_snapshot, original)
        self.assertEqual(case.state,"labeling")
        self.assertFalse(case.allow_optimization)
        self.assertEqual(GoldAnnotation.objects.filter(case=case,round="primary").count(),1)
        self.assertEqual(library(actor=self.user,project_id=self.project.pk,case_id=case.pk)['input_snapshot']['raw_diff'],original['raw_diff'])

    def test_same_snapshot_is_idempotent(self):
        first = self.save();second = self.save()
        self.assertEqual(first['case_id'],second['case_id']);self.assertTrue(second['reused'])
        self.assertEqual(GoldCase.objects.count(),1);self.assertEqual(GoldAnnotation.objects.count(),1)

    def test_changed_marks_create_new_version_without_overwriting(self):
        first=self.save();changed=deepcopy(self.payload);changed['annotations'][0].update(label="false_positive",comment="存在上游保护")
        second=self.save(payload=changed)
        self.assertNotEqual(first['version_id'],second['version_id'])
        self.assertEqual(GoldCase.objects.get(pk=first['case_id']).expected_output['confirmed_keys'],['finding-1'])

    def test_stale_snapshot_rejected_without_writes(self):
        self.task.raw_diff="changed";self.task.save()
        with self.assertRaises(ValidationError):self.save()
        self.assertEqual(GoldDataset.objects.count(),0)

    def test_unmarked_duplicate_or_unsupported_labels_are_rejected(self):
        for annotations in [[],[{"index":0,"label":"automatic"}],[{"index":True,"label":"confirmed"}],[{"index":0,"label":"false_positive","comment":""}]]:
            with self.subTest(annotations=annotations):
                payload=deepcopy(self.payload);payload['annotations']=annotations
                with self.assertRaises(ValidationError):self.save(payload=payload)
        self.assertEqual(GoldCase.objects.count(),0)

    def test_model_permission_required_and_new_dataset_requires_lead(self):
        with self.assertRaises(PermissionDenied):self.save(actor=self.member)
        self.member.user_permissions.add(Permission.objects.get(content_type__app_label="knowledge_evolution",codename="add_goldcase"))
        member=User.objects.get(pk=self.member.pk)
        with self.assertRaises(PermissionDenied):self.save(actor=member)
        self.save()
        self.assertTrue(self.save(actor=member)['reused'])

    def test_cross_project_cannot_read_saved_snapshot(self):
        result=self.save()
        outsider=User.objects.create_user(username="gold-outsider")
        outsider.user_permissions.add(Permission.objects.get(content_type__app_label="knowledge_evolution",codename="view_goldcase"))
        with self.assertRaises(PermissionDenied):library(actor=outsider,project_id=self.project.pk,case_id=result['case_id'])

    def test_api_enforces_permission_and_serializes_stored_snapshot(self):
        client=APIClient();client.force_authenticate(self.user)
        url=f'/api/code-analysis/tasks/{self.task.pk}'
        ctx=client.get(url+'/internal-gold-context/')
        self.assertEqual(ctx.status_code,200)
        saved=client.post(url+'/save-internal-gold/',self.payload,format='json')
        self.assertEqual(saved.status_code,200,saved.data)
        listed=client.get('/api/code-analysis/tasks/internal-gold-samples/',{'project':self.project.pk})
        self.assertEqual(listed.status_code,200);self.assertEqual(listed.data['total'],1)
        client.force_authenticate(self.member)
        self.assertEqual(client.post(url+'/save-internal-gold/',self.payload,format='json').status_code,403)

    def test_pending_task_cannot_be_saved(self):
        self.task.status="queued";self.task.save()
        with self.assertRaises(ValidationError):self.save()
