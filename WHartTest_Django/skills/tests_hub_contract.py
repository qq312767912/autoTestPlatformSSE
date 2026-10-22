import io
import json
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project
from skills.models import Skill, SkillVersion
from skills.versions import SkillVersionService
from skills.exporter import SkillPackageExporter
from skills.views import SkillViewSet
from skills.import_preview import preview_directory


class HubUploadContractTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(MEDIA_ROOT=self.temp.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_superuser('contract-admin', 'a@example.test', 'contract-password')
        self.project = Project.objects.create(name='Contract A', creator=self.user)
        self.other = Project.objects.create(name='Contract B', creator=self.user)
        self.root = Path(self.temp.name) / 'input'
        self.root.mkdir()
        self.write('1.0.0')

    def write(self, version, body='核查用例。'):
        line = 'version: ' + version + '\n' if version else ''
        (self.root/'SKILL.md').write_text('---\nname: contract-review\ndescription: 用例核查\n' + line + '---\n' + body)

    def create(self, project=None, **kwargs):
        return SkillVersionService.create_candidate_from_dir(source_dir=self.root, project=project or self.project, actor=self.user, **kwargs)

    def call(self, skill, action, version, data=None, user=None):
        request = APIRequestFactory().post('/test/', data or {}, format='json')
        force_authenticate(request, user=user or self.user)
        return SkillViewSet.as_view({'post': action})(request, project_pk=self.project.pk, pk=skill.pk, version_id=str(version.id))

    def test_cross_project_upload_appends_and_rewrites_only_private_copy(self):
        skill, original = self.create()
        original_bytes = Path(original.get_full_path(), 'SKILL.md').read_bytes()
        self.write('1.0.0', '修订的规则。')
        preview = preview_directory(self.root)[0]
        self.assertTrue(preview['conflict'])
        self.assertEqual(preview['target_skill_id'], skill.pk)
        with self.assertRaises(ValidationError): self.create(self.other)
        same, updated = self.create(self.other, version_override='1.0.1')
        self.assertEqual(skill.pk, same.pk)
        self.assertEqual(Skill.objects.filter(name='contract-review').count(), 1)
        self.assertEqual(updated.manifest['version'], '1.0.1')
        self.assertIn('version: 1.0.0', (self.root/'SKILL.md').read_text())
        self.assertEqual(Path(original.get_full_path(), 'SKILL.md').read_bytes(), original_bytes)
        exported = SkillPackageExporter.export_bytes(updated, actor=self.user)
        with zipfile.ZipFile(io.BytesIO(exported.payload)) as archive:
            self.assertIn('version: 1.0.1', archive.read('SKILL.md').decode())
        self.assertTrue(SkillVersionService.verify_package_integrity(updated)['ok'])
        _, duplicate = self.create(self.other, version_override='1.0.1')
        self.assertEqual(duplicate.pk, updated.pk)

    def test_missing_version_is_written_and_duplicate_upload_is_idempotent(self):
        self.write('')
        skill, version = self.create()
        self.assertIn('version: 1.0.0', Path(version.get_full_path(), 'SKILL.md').read_text())
        _, again = self.create(self.other)
        self.assertEqual(version.pk, again.pk)
        self.assertEqual(preview_directory(self.root)[0]['duplicate_version'], '1.0.0')
        self.assertEqual(skill.versions.count(), 1)

    def test_archive_restore_and_permissions_preserve_package(self):
        skill, old = self.create()
        self.write('1.0.1')
        _, newest = self.create()
        user = get_user_model().objects.create_user('unprivileged')
        self.assertEqual(self.call(skill, 'archive_version', old, user=user).status_code, 403)
        response = self.call(skill, 'archive_version', old)
        self.assertEqual(response.status_code, 200, response.data)
        old.refresh_from_db()
        self.assertTrue(Path(old.get_full_path()).is_dir())
        self.assertIn('hub_deleted_at', old.source_metadata)
        self.assertEqual(self.call(skill, 'archive_version', newest).status_code, 409)
        self.assertEqual(self.call(skill, 'restore_version', old).status_code, 200)
        skill.active_version = old; skill.save(update_fields=['active_version'])
        self.assertEqual(self.call(skill, 'archive_version', old).status_code, 409)

    def test_multipart_upload_reports_actual_version(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive: archive.writestr('SKILL.md', (self.root/'SKILL.md').read_text())
        req = APIRequestFactory().post('/test/', {'file': SimpleUploadedFile('review.zip', buffer.getvalue()), 'category': 'case_review', 'description': '用例评审', 'version_overrides': json.dumps({'contract-review': '2.0.0'})}, format='multipart')
        force_authenticate(req, user=self.user)
        response = SkillViewSet.as_view({'post': 'upload'})(req, project_pk=self.project.pk)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['data'][0]['uploaded_version']['version'], '2.0.0')

    def test_explicit_cross_project_lock_survives_archive_but_new_lock_is_refused(self):
        from skills.runtime import SkillRuntimeResolver, SkillRuntimeUnavailable
        skill, first = self.create()
        self.write('1.0.1')
        _, newer = self.create()
        lock = SkillRuntimeResolver.lock_for_task(project=self.other, workflow_id='contract-locked', skill=skill, skill_version=first)
        self.assertEqual(lock.skill_version_id, first.pk)
        self.assertEqual(self.call(skill, 'archive_version', first).status_code, 200)
        self.assertEqual(SkillRuntimeResolver.resolve_locked(lock).pk, first.pk)
        with self.assertRaises(SkillRuntimeUnavailable):
            SkillRuntimeResolver.lock_for_task(project=self.other, workflow_id='contract-new', skill=skill, skill_version=first)
        self.assertEqual(SkillRuntimeResolver.resolve_version(project=self.other, skill=skill).pk, newer.pk)

    def test_same_declared_override_preserves_bytes_and_preflight_retry_token(self):
        from skills.validation import SkillPackageValidationService
        from django.core.files.uploadedfile import SimpleUploadedFile
        skill, first = self.create()
        _, same = self.create(version_override='1.0.0')
        self.assertEqual(same.pk, first.pk)
        self.write('1.0.0', '改写后的规则。')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('SKILL.md', (self.root/'SKILL.md').read_text())
        report = SkillPackageValidationService.preflight(zip_file=SimpleUploadedFile('test.zip', buffer.getvalue()))
        self.assertTrue(report.report.ok)
        with self.assertRaises(ValidationError):
            SkillVersionService.create_candidate_from_preflight(token=report.token, project=self.other, actor=self.user)
        _, changed = SkillVersionService.create_candidate_from_preflight(token=report.token, project=self.other, actor=self.user, version_override='1.0.1')
        self.assertEqual(changed.version, '1.0.1')

    def test_equivalent_old_copies_are_listed_once_and_archived_together(self):
        skill, first = self.create()
        self.write('1.0.1')
        self.create()
        # Simulate a legacy duplicate created before canonical uploads were fixed.
        duplicate_skill = Skill.objects.create(project=self.other, name=skill.name, creator=self.user)
        duplicate = SkillVersion.objects.create(skill=duplicate_skill, version=first.version, package_path=first.package_path, package_sha256=first.package_sha256, manifest=first.manifest)
        req = APIRequestFactory().get('/test/')
        force_authenticate(req, user=self.user)
        response = SkillViewSet.as_view({'get':'list_versions'})(req, project_pk=self.project.pk, pk=skill.pk)
        self.assertEqual(len(response.data['data']), 2)
        self.assertEqual(self.call(skill, 'archive_version', first).status_code, 200)
        duplicate.refresh_from_db()
        self.assertIn('hub_deleted_at', duplicate.source_metadata)
        self.assertEqual(self.call(skill, 'restore_version', first).status_code, 200)
        duplicate.refresh_from_db()
        self.assertNotIn('hub_deleted_at', duplicate.source_metadata)

    def test_review_snapshot_reads_the_exact_locked_version(self):
        from types import SimpleNamespace
        from testcases.review_service import _lock_review_skill
        self.write('1.0.0', '第一版规则')
        skill, first = self.create()
        self.write('1.0.1', '第二版规则')
        self.create()
        review = SimpleNamespace(pk=91365, project=self.other, creator=self.user, selected_skill=skill,
            summary={'selected_skill_version':str(first.pk)}, skill_snapshot='旧的内联内容', save=lambda **kwargs: None)
        binding = _lock_review_skill(review)
        self.assertEqual(binding['skill_version'].pk, first.pk)
        self.assertIn('第一版规则', review.skill_snapshot)
        self.assertNotIn('第二版规则', review.skill_snapshot)

    def test_edit_card_metadata_synchronizes_duplicates_without_changing_package(self):
        from skills.serializers import SkillListSerializer
        skill, version = self.create()
        version.manifest = {**version.manifest, 'stage': 'code_review'}
        version.save(update_fields=['manifest'])
        duplicate = Skill.objects.create(project=self.other, name=skill.name, creator=self.user, declared_stage='')
        text = '第一句介绍。第二句保留完整内容。' * 12
        req = APIRequestFactory().patch('/test/', {'display_name':'人工展示名称', 'stage':'用例审查', 'description':text}, format='json')
        force_authenticate(req, user=self.user)
        response = SkillViewSet.as_view({'patch':'edit_metadata'})(req, project_pk=self.other.pk, pk=skill.pk)
        self.assertEqual(response.status_code, 200, response.data)
        skill.refresh_from_db(); duplicate.refresh_from_db()
        self.assertEqual(skill.name,'contract-review')
        self.assertEqual(duplicate.display_name,'人工展示名称')
        self.assertEqual(duplicate.declared_stage,'case_review')
        row = SkillListSerializer(skill,context={'versions':{skill.pk:version}}).data
        self.assertEqual(row['stage'],'case_review')
        self.assertEqual(row['stage_source'],'declared')
        self.assertEqual(row['description'],text)
        self.assertTrue(SkillVersionService.verify_package_integrity(version)['ok'])

    def test_card_metadata_permission_and_invalid_identity_change(self):
        skill, version = self.create()
        user = get_user_model().objects.create_user('metadata-readonly')
        def request(data, actor):
            req=APIRequestFactory().patch('/test/',data,format='json')
            force_authenticate(req,user=actor)
            return SkillViewSet.as_view({'patch':'edit_metadata'})(req,project_pk=self.other.pk,pk=skill.pk)
        self.assertEqual(request({'display_name':'拒绝'},user).status_code,403)
        self.assertEqual(request({'name':'changed-identity'},self.user).status_code,400)
        self.assertEqual(request({'stage':'test_executoin'},self.user).status_code,200)
        skill.refresh_from_db()
        self.assertEqual(skill.declared_stage, 'custom:test_executoin')
        self.assertEqual(request({'stage':'x'*100},self.user).status_code,400)
        self.assertEqual(request({'stage':'','display_name':'','description':''},self.user).status_code,200)
        skill.refresh_from_db()
        self.assertEqual(skill.name,'contract-review')
        self.assertEqual(skill.display_name,'')

    def test_bundled_sync_preserves_edited_card_description(self):
        from django.core.management import call_command
        skill, version = self.create()
        skill.description='测试人员维护的简介。'
        skill.save(update_fields=['description'])
        bundled=Path(self.temp.name)/'bundled'
        package=bundled/'contract-review'; package.mkdir(parents=True)
        (package/'SKILL.md').write_text((self.root/'SKILL.md').read_text())
        call_command('init_skills',skills_dir=str(bundled),stdout=io.StringIO())
        skill.refresh_from_db()
        self.assertEqual(skill.description,'测试人员维护的简介。')
