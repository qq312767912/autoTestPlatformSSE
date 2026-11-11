from io import BytesIO
from zipfile import ZipFile
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient
from projects.models import Project, ProjectMember
from .models import RequirementComparison
from .test_document_comparison import document


class ComparisonApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('comparison-user')
        self.other = User.objects.create_user('other-user')
        self.project = Project.objects.create(name='Comparison project', creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role='owner')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def upload(self, project=None):
        return self.client.post('/comparisons/', {
            'project': str(project or self.project.pk),
            'old_file': SimpleUploadedFile('旧版.docx', document('超时30秒')),
            'new_file': SimpleUploadedFile('新版.docx', document('超时60秒')),
        }, format='multipart')

    def test_confirmed_download_and_audit(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        record_id = response.data['id']
        self.assertEqual(response.data['result']['added'], 1)
        download = self.client.post(f'/comparisons/{record_id}/confirm-extract/',
                                    {'title': '迭代一', 'hunk_ids': ['h0']}, format='json')
        self.assertEqual(download.status_code, 200)
        with ZipFile(BytesIO(download.content)) as archive:
            self.assertIn('迭代需求.docx', archive.namelist())
        record = RequirementComparison.objects.get(pk=record_id)
        self.assertEqual(record.confirmed_hunks, ['h0'])
        self.assertIsNotNone(record.confirmed_at)

    def test_invalid_selection_does_not_confirm(self):
        record_id = self.upload().data['id']
        for ids in [[], ['invalid'], 'h0', [2]]:
            response = self.client.post(f'/comparisons/{record_id}/confirm-extract/',
                                        {'title': '迭代', 'hunk_ids': ids}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNone(RequirementComparison.objects.get(pk=record_id).confirmed_at)

    def test_another_member_cannot_read_private_session(self):
        record_id = self.upload().data['id']
        ProjectMember.objects.create(project=self.project, user=self.other, role='member')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/comparisons/{record_id}/').status_code, 404)
        self.assertEqual(self.client.post(f'/comparisons/{record_id}/confirm-extract/',
                                         {'title': '越权', 'hunk_ids': ['h0']}, format='json').status_code, 404)

    def test_nonmember_and_removed_membership(self):
        record_id = self.upload().data['id']
        ProjectMember.objects.filter(project=self.project, user=self.user).delete()
        self.assertEqual(self.client.get(f'/comparisons/{record_id}/').status_code, 404)
        self.assertEqual(self.upload().status_code, 404)

    def test_invalid_files_and_missing_pair(self):
        response = self.client.post('/comparisons/', {'project': self.project.pk,
            'old_file': SimpleUploadedFile('bad.docx', b'bad'),
            'new_file': SimpleUploadedFile('new.docx', document('新'))}, format='multipart')
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/comparisons/', {'project': self.project.pk}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RequirementComparison.objects.count(), 0)

    def test_anonymous_upload_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.upload().status_code, (401, 403))
