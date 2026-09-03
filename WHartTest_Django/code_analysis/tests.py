from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from .models import AnalysisTask, GitLabConnection, ProjectRepository, UserGitLabCredential
from .services import DEFAULT_ANNOTATIONS, _diff_line_stats, _parse_diff, run_analysis


class DiffRuleTests(TestCase):
    def test_deleted_excel_annotation_is_deterministic_risk(self):
        findings = _parse_diff("@@ -1,2 +1 @@\n-    @Excel(name = \"证券代码\")\n     private String code;", "Quote.java", DEFAULT_ANNOTATIONS)
        self.assertEqual(findings[0]["change"], "删除 @Excel 注解")
        self.assertEqual(findings[0]["severity"], "high")

    def test_context_line_is_not_treated_as_deleted(self):
        findings = _parse_diff("@@ -1,2 +1,2 @@\n @Excel(name = \"证券代码\")\n-private String code;\n+private String stockCode;", "Quote.java", DEFAULT_ANNOTATIONS)
        self.assertFalse(any("Excel" in item["change"] for item in findings))

    def test_diff_line_stats_excludes_file_headers(self):
        self.assertEqual(_diff_line_stats("--- a/a.py\n+++ b/a.py\n-old\n+new\n+more"), (2, 1))


class AnalysisLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="secret")
        self.project = Project.objects.create(name="交易平台", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="member")
        self.connection = GitLabConnection.objects.create(name="内网", base_url="https://gitlab.local")
        self.repository = ProjectRepository.objects.create(
            project=self.project,
            connection=self.connection,
            gitlab_project_id="group/service",
            name="service",
            path_with_namespace="group/service",
        )
        credential = UserGitLabCredential(project=self.project, connection=self.connection, user=self.user)
        credential.set_token("read-only-token")
        credential.save()

    @patch("code_analysis.services.GitLabClient.merge_request_changes")
    def test_quick_analysis_generates_two_reports_and_deletes_cascade(self, changes):
        changes.return_value = {
            "title": "fix: export field",
            "diff_refs": {"base_sha": "a" * 40, "head_sha": "b" * 40},
            "changes": [{"old_path": "src/Quote.java", "new_path": "src/Quote.java", "diff": "@@ -1 +0 @@\n-@Excel(name = \"证券代码\")"}],
        }
        task = AnalysisTask.objects.create(
            project=self.project,
            repository=self.repository,
            creator=self.user,
            source_type="merge_request",
            merge_request_iid=12,
            mode="quick",
        )
        run_analysis(task)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.change_report["summary"]["high_risk_count"], 1)
        self.assertEqual(task.test_report["summary"]["test_point_count"], 1)
        self.assertEqual(task.ai_coverage, 0)
        client = APIClient()
        client.force_authenticate(self.user)
        change_download = client.get(f"/api/code-analysis/tasks/{task.id}/download-change-report/")
        test_download = client.get(f"/api/code-analysis/tasks/{task.id}/download-test-report/")
        self.assertEqual(change_download.status_code, 200)
        self.assertIn("代码审查报告", change_download.content.decode())
        self.assertEqual(test_download.status_code, 200)
        self.assertIn("测试分析报告", test_download.content.decode())
        draft_id = task.test_requirement_drafts.get().id
        task.delete()
        self.assertFalse(AnalysisTask.objects.filter(pk=task.id).exists())
        from .models import TestRequirementDraft
        self.assertFalse(TestRequirementDraft.objects.filter(pk=draft_id).exists())

    @patch("code_analysis.tasks.run_code_analysis.delay")
    def test_run_endpoint_queues_background_analysis(self, delay):
        delay.return_value = SimpleNamespace(id="celery-job-1")
        task = AnalysisTask.objects.create(
            project=self.project,
            repository=self.repository,
            creator=self.user,
            source_type="merge_request",
            merge_request_iid=12,
        )
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/run/")
        self.assertEqual(response.status_code, 202)
        task.refresh_from_db()
        self.assertEqual(task.current_step, "等待后台执行")
        self.assertEqual(task.celery_task_id, "celery-job-1")
        delay.assert_called_once_with(str(task.id))

    @patch("code_analysis.views.current_app.control.revoke")
    def test_cancel_marks_task_and_revokes_queued_job(self, revoke):
        task = AnalysisTask.objects.create(
            project=self.project,
            repository=self.repository,
            creator=self.user,
            source_type="merge_request",
            merge_request_iid=12,
            celery_task_id="celery-job-2",
        )
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/cancel/")
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        revoke.assert_called_once_with("celery-job-2", terminate=False)
