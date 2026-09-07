from unittest.mock import patch
from types import SimpleNamespace
import os
import subprocess
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from .models import AnalysisTask, AnalysisTaskExecutionLog, GitLabConnection, ProjectRepository, TestRequirementDraft, UserGitLabCredential
from .services import DEFAULT_ANNOTATIONS, LOW_VALUE_FILE_PATTERNS, OCR_CONCURRENCY, OCR_RESUME_CONCURRENCY, LocalGitClient, _diff_line_stats, _is_low_value_file, _load_ocr_payload, _managed_gitlab_repository, _ocr_diagnostics, _ocr_needs_resume, _ocr_result_path, _parse_diff, _reuse_cached_result, _risk_findings_for_tests, _sanitize_json_value, _validate_suggested_patch, remove_ocr_repository, run_analysis


class DiffRuleTests(TestCase):
    def test_json_sanitizer_escapes_database_unsupported_control_chars(self):
        value = _sanitize_json_value({"text": "null:\x00 bell:\x07 keep:\t\n"})
        self.assertEqual(value["text"], "null:\\u0000 bell:\\u0007 keep:\t\n")
        self.assertNotIn("\x00", value["text"])

    def test_ocr_uses_six_workers(self):
        self.assertEqual(OCR_CONCURRENCY, 6)

    def test_low_value_files_are_filtered(self):
        self.assertTrue(_is_low_value_file("frontend/package-lock.json"))
        self.assertTrue(_is_low_value_file("web/dist/app.min.js"))
        self.assertFalse(_is_low_value_file("src/orders/service.py"))
        self.assertIn("package-lock.json", LOW_VALUE_FILE_PATTERNS)

    def test_ocr_diagnostics_classifies_http_200_subtask_failure(self):
        payload = {
            "summary": {"elapsed": "2m", "total_tokens": 10},
            "groups": [{"label": "配置", "files": ["a.json", "b.py"]}],
            "manifest": {"execution": {"configured_concurrency": 6}, "coverage": {
                "selected": [{"path": "a.json"}, {"path": "b.py"}],
                "completed": [{"path": "b.py"}], "reused": [],
                "failed": [{"path": "a.json", "classification": "provider", "reason": "provider or subtask request failed"}],
            }},
            "retry_report": {"total_requests": 2, "failed_requests": 1, "requests": [{
                "file_path": "a.json", "request_no": 1, "outcome": "failed",
                "attempts": [{"outcome": "success", "status_code": 200}],
            }]},
        }
        diagnostics = _ocr_diagnostics(payload)
        self.assertEqual(diagnostics["failure_type_counts"], {"agent_subtask": 1})
        self.assertEqual(diagnostics["groups"][0]["coverage"], 50.0)

    def test_partial_ocr_with_failed_items_uses_low_concurrency_resume(self):
        payload = {
            "session_id": "session-1",
            "manifest": {"coverage": {
                "selected": [{"path": "a.py"}, {"path": "b.py"}],
                "completed": [{"path": "a.py"}], "reused": [],
                "failed": [{"path": "b.py"}],
            }},
        }
        self.assertTrue(_ocr_needs_resume(payload))
        self.assertEqual(OCR_RESUME_CONCURRENCY, 2)
        self.assertEqual(_ocr_diagnostics(payload)["coverage"], 50.0)
        payload["manifest"]["coverage"]["failed"] = []
        self.assertFalse(_ocr_needs_resume(payload))

    def test_same_commit_rerun_reuses_report_and_rebuilds_drafts(self):
        from .services import _cache_report_complete
        user = User.objects.create_user("cache-user")
        project = Project.objects.create(name="缓存测试项目", creator=user)
        repository = ProjectRepository.objects.create(
            project=project, source_type="local_git", local_path="repo", name="repo",
            path_with_namespace="repo",
        )
        point = {"title": "验证登录", "objective": "提交凭据", "expected_result": "登录成功", "priority": "high", "test_type": "迭代验证", "source_finding_key": "iteration-test:0", "status": "draft"}
        task = AnalysisTask.objects.create(
            project=project, repository=repository, creator=user, source_type="commits",
            base_sha="a" * 40, head_sha="b" * 40, status="fetching", raw_diff="diff -- app.py\n+ok",
            change_report={"summary": {"risk_count": 1}, "findings": [], "ocr_status": {"status": "completed", "coverage": 100}},
            test_report={"summary": {"test_point_count": 1}, "iteration_summary": {"title": "登录变更", "change_groups": [{"name": "登录"}]}, "test_requirements": [point]},
            machine_coverage=100, ai_coverage=100,
        )
        self.assertTrue(_reuse_cached_result(task, task.base_sha, task.head_sha))
        task.refresh_from_db()
        self.assertEqual(task.current_step, "已复用相同 Commit 的分析缓存")
        self.assertTrue(task.change_report["cache"]["reused"])
        self.assertEqual(task.test_requirement_drafts.count(), 1)
        task.change_report["ocr_status"] = {"status": "partial", "coverage": 55.6}
        self.assertFalse(_cache_report_complete(task))
        task.change_report["ocr_status"] = {"status": "completed", "coverage": 100}
        task.test_report["iteration_summary"] = {}
        self.assertFalse(_cache_report_complete(task))
        task.test_report["iteration_summary"] = {"title": "登录", "change_groups": [{"name": "登录"}]}
        task.test_report["test_requirements"][0]["test_type"] = "风险排查"
        self.assertFalse(_cache_report_complete(task))

    def test_suggested_patch_is_checked_in_isolated_target_copy(self):
        task = SimpleNamespace(
            head_sha="head",
            repository=SimpleNamespace(source_type="local_git", local_path="."),
        )
        patch_text = "\n".join([
            "diff --git a/app.py b/app.py", "--- a/app.py", "+++ b/app.py",
            "@@ -1 +1 @@", "-old", "+new", "",
        ])
        with patch("code_analysis.services._target_file_content", return_value="old\n"):
            applicable, message = _validate_suggested_patch(task, patch_text)
        self.assertTrue(applicable, message)

    def test_ocr_result_is_written_outside_read_only_source_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "ocr-workspace"
            source_repository = Path(directory) / "source"
            source_repository.mkdir()
            task = SimpleNamespace(pk="task-123")
            with patch("code_analysis.services.OCR_WORKSPACE_ROOT", workspace):
                result_path = _ocr_result_path(task)
                result_path.write_text("{}", encoding="utf-8")
            self.assertEqual(result_path.read_text(encoding="utf-8"), "{}")
            self.assertFalse(result_path.is_relative_to(source_repository))

    def test_deleted_excel_annotation_is_deterministic_risk(self):
        findings = _parse_diff("@@ -1,2 +1 @@\n-    @Excel(name = \"证券代码\")\n     private String code;", "Quote.java", DEFAULT_ANNOTATIONS)
        self.assertEqual(findings[0]["change"], "删除 @Excel 注解")
        self.assertEqual(findings[0]["severity"], "high")

    def test_context_line_is_not_treated_as_deleted(self):
        findings = _parse_diff("@@ -1,2 +1,2 @@\n @Excel(name = \"证券代码\")\n-private String code;\n+private String stockCode;", "Quote.java", DEFAULT_ANNOTATIONS)
        self.assertFalse(any("Excel" in item["change"] for item in findings))

    def test_diff_line_stats_excludes_file_headers(self):
        self.assertEqual(_diff_line_stats("--- a/a.py\n+++ b/a.py\n-old\n+new\n+more"), (2, 1))

    def test_python_permission_decorator_removal_is_high_risk(self):
        findings = _parse_diff("-@login_required\n def view(): pass\n", "app/views.py", set())
        self.assertEqual(findings[0]["severity"], "high")

    def test_deleted_security_config_is_high_risk(self):
        findings = _parse_diff("-security.verify-ssl: true\n", "application.yml", set())
        self.assertEqual(findings[0]["change"], "删除关键配置：security.verify-ssl")
        self.assertEqual(findings[0]["severity"], "high")

    def test_same_evidence_is_not_counted_twice(self):
        from .services import _deduplicate_findings
        duplicated = [{"file": "a.java", "evidence": "@Excel", "source": "machine_rule"}, {"file": "a.java", "evidence": "@Excel", "source": "ai_analysis"}]
        self.assertEqual(len(_deduplicate_findings(duplicated)), 1)

    def test_unverified_ai_syntax_claim_is_filtered(self):
        from .services import _is_unverified_syntax_claim
        self.assertTrue(_is_unverified_syntax_claim({"change": "闭合标签写错", "impact": "Vue 模板编译错误"}))
        self.assertFalse(_is_unverified_syntax_claim({"change": "权限校验缺失", "impact": "未授权访问"}))

    def test_ocr_payload_ignores_non_json_prefix(self):
        payload = _load_ocr_payload("OCR started\\n{\"status\": \"completed\", \"comments\": []}")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["comments"], [])

    def test_only_high_and_medium_report_risks_generate_risk_tests(self):
        findings = [
            {"key": "high", "severity": "high", "verified": True},
            {"key": "medium-ai", "severity": "medium", "verified": False},
            {"key": "low", "severity": "low", "verified": True},
        ]
        self.assertEqual([item["key"] for item in _risk_findings_for_tests(findings)], ["high", "medium-ai"])


class LocalGitClientTests(SimpleTestCase):
    def test_local_git_compares_two_branch_names(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True, text=True)
            git("init"); git("config", "user.email", "test@example.com"); git("config", "user.name", "Test")
            file_path = os.path.join(directory, "Demo.java")
            with open(file_path, "w", encoding="utf-8") as handle: handle.write("@Excel\nprivate String name;\n")
            git("add", "."); git("commit", "-m", "base"); git("branch", "base")
            with open(file_path, "w", encoding="utf-8") as handle: handle.write("private String name;\n")
            git("commit", "-am", "remove annotation"); git("branch", "head")
            original_root = LocalGitClient.ROOT
            try:
                LocalGitClient.ROOT = Path(directory).resolve()
                result = LocalGitClient(".").compare("base", "head")
            finally:
                LocalGitClient.ROOT = original_root
            self.assertEqual(len(result["diffs"]), 1)
            self.assertIn("@Excel", result["diffs"][0]["diff"])

    def test_local_git_tolerates_non_utf8_diff_content(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True, text=True)
            git("init"); git("config", "user.email", "test@example.com"); git("config", "user.name", "Test")
            file_path = os.path.join(directory, "legacy.properties")
            with open(file_path, "wb") as handle: handle.write(b"name=\\xcb\\xd4")
            git("add", "."); git("commit", "-m", "base"); git("branch", "base")
            with open(file_path, "wb") as handle: handle.write(b"name=\\xcb\\xd4\\xca\\xd4")
            git("commit", "-am", "legacy encoding"); git("branch", "head")
            original_root = LocalGitClient.ROOT
            try:
                LocalGitClient.ROOT = Path(directory).resolve()
                result = LocalGitClient(".").compare("base", "head")
            finally:
                LocalGitClient.ROOT = original_root
            self.assertEqual(len(result["diffs"]), 1)


class AnalysisLifecycleTests(TransactionTestCase):
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
        delay.assert_called_once_with(str(task.id), force_refresh=True)
        self.assertEqual(list(task.execution_logs.values_list("event", flat=True)), ["queued"])

    @patch("code_analysis.views.generate_suggested_patch")
    def test_suggested_patch_is_generated_on_demand_and_persisted(self, generate):
        finding = {"key": "risk-1", "file": "app.py", "change": "风险", "severity": "high"}
        generated = {**finding, "suggested_patch": "--- a/app.py\n+++ b/app.py\n", "patch_status": "reference", "patch_validation_message": "仅供参考"}
        generate.return_value = (generated, 12, "已生成")
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a" * 40, head_sha="b" * 40,
            status="completed", change_report={"summary": {}, "findings": [finding]},
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(
            f"/api/code-analysis/tasks/{task.id}/suggested-patch/", {"finding_key": "risk-1"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.change_report["findings"][0]["patch_status"], "reference")
        self.assertEqual(task.token_usage, 12)
        generate.assert_called_once()

    @patch("code_analysis.tasks.run_code_analysis.delay")
    def test_running_repository_rejects_duplicate_queue(self, delay):
        current = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a" * 40, head_sha="b" * 40,
            status="pending", celery_task_id="already-queued",
        )
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="c" * 40, head_sha="d" * 40,
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/run/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("正在运行", response.data["detail"])
        delay.assert_not_called()
        self.assertEqual(current.status, "pending")

    def test_execution_logs_are_visible_to_project_member(self):
        task = AnalysisTask.objects.create(project=self.project, repository=self.repository, creator=self.user, source_type="commits", base_sha="a", head_sha="b")
        AnalysisTaskExecutionLog.objects.create(task=task, event="completed", message="分析完成")
        client = APIClient(); client.force_authenticate(self.user)
        response = client.get(f"/api/code-analysis/tasks/{task.id}/execution-logs/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["event"], "completed")

    @patch("code_analysis.views.remove_ocr_repository")
    def test_deleting_analysis_also_deletes_its_ocr_repository(self, remove_repository):
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b",
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.delete(f"/api/code-analysis/tasks/{task.id}/")
        self.assertIn(response.status_code, {200, 204})
        self.assertFalse(AnalysisTask.objects.filter(pk=task.id).exists())
        remove_repository.assert_called_once_with(task.id)

    def test_gitlab_ocr_repository_is_shallow_persisted_until_explicit_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            managed = Path(directory) / "managed"
            source.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", source, *args], check=True, capture_output=True, text=True)
            git("init"); git("config", "user.email", "test@example.com"); git("config", "user.name", "Test")
            (source / "app.py").write_text("value = 1\n", encoding="utf-8")
            git("add", "."); git("commit", "-m", "base")
            base_sha = git("rev-parse", "HEAD").stdout.strip()
            (source / "app.py").write_text("value = 2\n", encoding="utf-8")
            git("commit", "-am", "head")
            head_sha = git("rev-parse", "HEAD").stdout.strip()
            task = AnalysisTask.objects.create(
                project=self.project, repository=self.repository, creator=self.user, executor=self.user,
                source_type="commits", base_sha=base_sha, head_sha=head_sha,
            )
            with patch("code_analysis.services.OCR_WORKSPACE_ROOT", managed), patch(
                "code_analysis.services._gitlab_clone_url", return_value=str(source)
            ):
                with _managed_gitlab_repository(task) as (root, base_ref, head_ref):
                    self.assertTrue((root / ".git").exists())
                    self.assertEqual({base_ref, head_ref}, {"refs/ocr/base", "refs/ocr/head"})
                self.assertTrue(root.exists())
                remove_ocr_repository(task.id)
                self.assertFalse(root.exists())

    def test_platform_admin_can_list_tasks_from_all_projects(self):
        other_owner = User.objects.create_user("other-owner", password="secret")
        other_project = Project.objects.create(name="其他平台", creator=other_owner)
        other_repository = ProjectRepository.objects.create(
            project=other_project,
            connection=self.connection,
            gitlab_project_id="group/other-service",
            name="other-service",
            path_with_namespace="group/other-service",
        )
        AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b",
        )
        AnalysisTask.objects.create(
            project=other_project, repository=other_repository, creator=other_owner,
            source_type="commits", base_sha="c", head_sha="d",
        )
        admin = User.objects.create_user("platform-admin", password="secret", is_staff=True)
        client = APIClient(); client.force_authenticate(admin)
        response = client.get("/api/code-analysis/tasks/")
        self.assertEqual(response.status_code, 200)
        items = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(len(items), 2)
        self.assertEqual({item["project_name"] for item in items}, {"交易平台", "其他平台"})

    def test_project_member_can_read_another_members_reports_diff_and_logs(self):
        member = User.objects.create_user("reviewer", password="secret")
        ProjectMember.objects.create(project=self.project, user=member, role="member")
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", raw_diff="diff -- src/a.py\n+print('ok')",
            change_report={"summary": {}, "findings": []}, test_report={"summary": {}, "test_requirements": []},
        )
        AnalysisTaskExecutionLog.objects.create(task=task, event="completed", message="分析完成")
        client = APIClient(); client.force_authenticate(member)
        self.assertEqual(client.get(f"/api/code-analysis/tasks/?project={self.project.id}").status_code, 200)
        self.assertEqual(client.get(f"/api/code-analysis/tasks/{task.id}/diff/").status_code, 200)
        self.assertEqual(client.get(f"/api/code-analysis/tasks/{task.id}/execution-logs/").status_code, 200)
        self.assertEqual(client.get(f"/api/code-analysis/tasks/{task.id}/download-change-report/").status_code, 200)
        self.assertEqual(client.get(f"/api/code-analysis/tasks/{task.id}/download-test-report/").status_code, 200)

    @patch("code_analysis.tasks.run_code_analysis.delay")
    def test_project_member_can_rerun_task_with_own_executor(self, delay):
        member = User.objects.create_user("reviewer", password="secret")
        ProjectMember.objects.create(project=self.project, user=member, role="member")
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a" * 40, head_sha="b" * 40, status="failed",
        )
        delay.return_value = SimpleNamespace(id="celery-member-run")
        client = APIClient(); client.force_authenticate(member)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/run/")
        self.assertEqual(response.status_code, 202)
        task.refresh_from_db()
        self.assertEqual(task.executor_id, member.id)
        self.assertEqual(task.creator_id, self.user.id)
        log = task.execution_logs.get(event="queued")
        self.assertEqual(log.actor_id, member.id)
        response = client.get(f"/api/code-analysis/tasks/{task.id}/execution-logs/")
        self.assertEqual(response.data[-1]["actor_name"], "reviewer")

    def test_project_member_can_edit_another_members_test_draft(self):
        member = User.objects.create_user("reviewer", password="secret")
        ProjectMember.objects.create(project=self.project, user=member, role="member")
        task = AnalysisTask.objects.create(project=self.project, repository=self.repository, creator=self.user, source_type="commits", base_sha="a", head_sha="b")
        draft = TestRequirementDraft.objects.create(task=task, title="旧标题")
        client = APIClient(); client.force_authenticate(member)
        response = client.patch(f"/api/code-analysis/test-requirements/{draft.id}/", {"title": "成员已编辑", "status": "accepted"}, format="json")
        self.assertEqual(response.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual((draft.title, draft.status), ("成员已编辑", "accepted"))

    def test_project_member_can_accept_ignore_and_convert_draft_to_testcase(self):
        from testcases.models import TestCase, TestCaseModule
        member = User.objects.create_user("reviewer2", password="secret")
        ProjectMember.objects.create(project=self.project, user=member, role="member")
        task = AnalysisTask.objects.create(project=self.project, repository=self.repository, creator=self.user, source_type="commits", base_sha="a", head_sha="b")
        draft = TestRequirementDraft.objects.create(task=task, title="验证导出权限", objective="以无权限账号发起导出", expected_result="接口拒绝且页面无导出入口", priority="high", test_type="安全回归")
        ignored = TestRequirementDraft.objects.create(task=task, title="无需处理")
        module = TestCaseModule.objects.create(project=self.project, name="代码审查回归", level=1, creator=self.user)
        client = APIClient(); client.force_authenticate(member)
        self.assertEqual(client.post(f"/api/code-analysis/test-requirements/{draft.id}/accept/").status_code, 200)
        self.assertEqual(client.post(f"/api/code-analysis/test-requirements/{ignored.id}/ignore/").status_code, 200)
        response = client.post(f"/api/code-analysis/test-requirements/{draft.id}/convert/", {"module_id": module.id}, format="json")
        self.assertEqual(response.status_code, 201)
        draft.refresh_from_db(); ignored.refresh_from_db()
        self.assertEqual((draft.status, ignored.status), ("converted", "ignored"))
        case = TestCase.objects.get(pk=response.data["test_case_id"])
        self.assertEqual((case.project_id, case.module_id, case.level), (self.project.id, module.id, "P0"))
        self.assertIn(str(task.id), case.notes)
        self.assertEqual(case.steps.count(), 1)

    def test_context_enrichment_skips_when_no_document_is_selected(self):
        from .services import _run_context_test_enrichment
        task = AnalysisTask.objects.create(project=self.project, repository=self.repository, creator=self.user, source_type="commits", base_sha="a", head_sha="b")
        points, modules, tokens, note = _run_context_test_enrichment(task, [])
        self.assertEqual((points, modules, tokens), ([], [], 0))
        self.assertIn("未关联", note)

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
