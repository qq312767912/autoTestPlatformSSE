from unittest.mock import patch
from types import SimpleNamespace
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from django.contrib.auth.models import Permission, User
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from langgraph_integration.models import LLMConfig
from .models import AnalysisTask, AnalysisTaskExecutionLog, CodeAnalysisLLMConfig, GitLabConnection, ProjectRepository, TestRequirementDraft, UserGitLabCredential
from .serializers import GitLabConnectionSerializer, ProjectRepositorySerializer
from .services import AI_REVIEW_LANGUAGE_RULE, AnalysisCancelled, DEFAULT_ANNOTATIONS, LOW_VALUE_FILE_PATTERNS, OCR_CONCURRENCY, OCR_RESUME_CONCURRENCY, GitLabClient, LocalGitClient, _diff_line_stats, _ensure_not_cancelled, _invalid_ocr_result_reason, _is_low_value_file, _load_ocr_payload, _managed_gitlab_repository, _ocr_diagnostics, _ocr_needs_resume, _ocr_result_path, _ocr_timeout_budget, _parse_diff, _reuse_cached_result, _review_payload_needs_chinese_retry, _risk_findings_for_tests, _sanitize_json_value, _validate_suggested_patch, remove_ocr_repositories_for_repository, remove_ocr_repository, retry_ocr_analysis, run_analysis


class CodeReviewPromptLanguageTests(SimpleTestCase):
    def test_ai_review_prompt_requires_chinese_for_report_fields(self):
        self.assertIn("所有面向用户展示的自然语言内容必须使用简体中文", AI_REVIEW_LANGUAGE_RULE)
        self.assertIn("即使 Diff、源码注释或业务上下文是英文，也不得输出英文审查结论", AI_REVIEW_LANGUAGE_RULE)
        for field in ("change", "evidence", "impact", "regression_scope"):
            self.assertIn(field, AI_REVIEW_LANGUAGE_RULE)

    def test_english_review_payload_requires_retry(self):
        self.assertTrue(_review_payload_needs_chinese_retry({
            "risks": [{"change": "Added a new API field", "impact": "May break clients"}],
        }))

    def test_chinese_review_payload_does_not_require_retry(self):
        self.assertFalse(_review_payload_needs_chinese_retry({
            "risks": [{"change": "新增 usci 字段", "impact": "可能影响现有客户端兼容性"}],
        }))


class DiffRuleTests(TestCase):
    def test_gitlab_connections_always_disable_ssl_verification(self):
        serializer = GitLabConnectionSerializer(data={
            "name": "内网 GitLab", "base_url": "https://10.10.11.59", "verify_ssl": True,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        connection = serializer.save()
        self.assertFalse(connection.verify_ssl)
        self.assertFalse(GitLabClient(connection, "token").verify)

    @patch("code_analysis.services.requests.get")
    def test_gitlab_api_requests_never_verify_ssl(self, request_get):
        request_get.return_value.json.return_value = {"version": "test"}
        connection = SimpleNamespace(base_url="https://10.10.11.59", verify_ssl=True)
        GitLabClient(connection, "token").get("/version")
        self.assertFalse(request_get.call_args.kwargs["verify"])

    def test_local_repository_does_not_require_gitlab_project_id(self):
        user = User.objects.create_user("local-repository-user")
        project = Project.objects.create(name="本地仓库项目", creator=user)
        serializer = ProjectRepositorySerializer(data={
            "project": project.id, "source_type": "local_git", "name": "本地仓库",
            "path_with_namespace": "demo_repositories/repo",
            "local_path": "demo_repositories/repo", "default_branch": "dev",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        repository = serializer.save()
        self.assertEqual(repository.gitlab_project_id, "")
        self.assertIsNone(repository.connection)

    def test_json_sanitizer_escapes_database_unsupported_control_chars(self):
        value = _sanitize_json_value({"text": "null:\x00 bell:\x07 keep:\t\n"})
        self.assertEqual(value["text"], "null:\\u0000 bell:\\u0007 keep:\t\n")
        self.assertNotIn("\x00", value["text"])

    def test_ocr_uses_single_worker_for_slow_internal_models(self):
        self.assertEqual(OCR_CONCURRENCY, 1)
        self.assertEqual(OCR_RESUME_CONCURRENCY, 1)

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

    def test_partial_ocr_with_failed_items_uses_bounded_resume_concurrency(self):
        payload = {
            "session_id": "session-1",
            "manifest": {"coverage": {
                "selected": [{"path": "a.py"}, {"path": "b.py"}],
                "completed": [{"path": "a.py"}], "reused": [],
                "failed": [{"path": "b.py"}],
            }},
        }
        self.assertTrue(_ocr_needs_resume(payload))
        self.assertEqual(OCR_RESUME_CONCURRENCY, 1)
        self.assertEqual(_ocr_diagnostics(payload)["coverage"], 50.0)
        payload["manifest"]["coverage"]["failed"] = []
        self.assertFalse(_ocr_needs_resume(payload))

    def test_invalid_ocr_result_preserves_timeout_reason(self):
        result = SimpleNamespace(returncode=-15, stderr="upstream did not finish")
        reason = _invalid_ocr_result_reason({}, result, True, 20, Path("result.json"))
        self.assertIn("超过 20 分钟", reason)
        self.assertIn("未生成有效 JSON", reason)
        self.assertIn("退出码 -15", reason)
        self.assertIn("upstream did not finish", reason)

    def test_ocr_timeout_budget_scales_with_monthly_change_size(self):
        self.assertEqual(_ocr_timeout_budget(800), (60, 30))
        self.assertEqual(_ocr_timeout_budget(1219), (70, 35))
        self.assertEqual(_ocr_timeout_budget(5000), (80, 40))
        self.assertEqual(_ocr_timeout_budget(9000), (100, 50))

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
            base_sha="a" * 40, head_sha="b" * 40, mode="deep", status="fetching", raw_diff="diff -- app.py\n+ok",
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
        task.mode = "standard"
        self.assertTrue(_cache_report_complete(task))
        task.mode = "deep"
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

    def test_repository_cleanup_only_removes_matching_marked_ocr_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matching, unrelated = root / "task-1", root / "task-2"
            matching.mkdir(); unrelated.mkdir()
            (matching / ".repository-id").write_text("12", encoding="utf-8")
            (unrelated / ".repository-id").write_text("13", encoding="utf-8")
            with patch("code_analysis.services.OCR_WORKSPACE_ROOT", root):
                self.assertEqual(remove_ocr_repositories_for_repository(12), 1)
            self.assertFalse(matching.exists())
            self.assertTrue(unrelated.exists())

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


class LocalGitMergeBaseTests(SimpleTestCase):
    """回归：基准分支独有的改动（如主干已修好的鉴权）不得渲染成目标分支的删除。"""

    def _diverged_repository(self, directory, git):
        """构造真正的分叉历史：基准分支补了鉴权，目标分支自分叉点拉出后只新增自己的文件。

        必须真分叉（基准提交不是目标的祖先）：否则共同祖先即基准自身，两点比较与
        共同祖先比较结果相同，用例会退化为恒过，守不住本缺陷。
        """
        git("init"); git("config", "user.email", "test@example.com"); git("config", "user.name", "Test")
        file_path = os.path.join(directory, "Demo.java")
        with open(file_path, "w", encoding="utf-8") as handle: handle.write("private String name;\n")
        git("add", "."); git("commit", "-m", "base")
        git("branch", "-M", "trunk")
        # 目标分支从分叉点拉出，稍后只新增自己的文件
        git("checkout", "-q", "-b", "feature")
        # 基准分支继续前进：补上鉴权注解，该提交不在目标分支上
        git("checkout", "-q", "trunk")
        with open(file_path, "w", encoding="utf-8") as handle: handle.write("@PreAuthorize\nprivate String name;\n")
        git("commit", "-am", "trunk adds annotation")
        git("checkout", "-q", "feature")
        with open(os.path.join(directory, "Added.java"), "w", encoding="utf-8") as handle: handle.write("class Added {}\n")
        git("add", "."); git("commit", "-m", "feature adds file")
        return file_path

    def test_local_git_compares_from_common_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True, text=True)
            self._diverged_repository(directory, git)
            expected_base = git("merge-base", "trunk", "feature").stdout.strip()
            # 前提自检：两条分支必须真的分叉，否则本用例无法区分两种比较语义
            self.assertNotEqual(expected_base, git("rev-parse", "trunk").stdout.strip())
            original_root = LocalGitClient.ROOT
            try:
                LocalGitClient.ROOT = Path(directory).resolve()
                result = LocalGitClient(".").compare("trunk", "feature")
            finally:
                LocalGitClient.ROOT = original_root
            self.assertEqual([item["new_path"] for item in result["diffs"]], ["Added.java"])
            self.assertNotIn("@PreAuthorize", "".join(item["diff"] for item in result["diffs"]))
            self.assertEqual(result["base_sha"], expected_base)

    def test_local_git_keeps_ancestor_range_unchanged(self):
        """基准是目标祖先（常见相邻 Commit 对比）时，比较范围与原先一致。"""
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
            self.assertEqual(result["base_sha"], git("rev-parse", "base").strip())

    def test_local_git_rejects_reversed_commit_range(self):
        """目标提交是基准提交的祖先时，说明选反了，必须拦下而不是反向报风险。"""
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True, text=True)
            git("init"); git("config", "user.email", "test@example.com"); git("config", "user.name", "Test")
            file_path = os.path.join(directory, "Demo.java")
            with open(file_path, "w", encoding="utf-8") as handle: handle.write("private String name;\n")
            git("add", "."); git("commit", "-m", "old"); git("branch", "old")
            with open(file_path, "w", encoding="utf-8") as handle: handle.write("@PreAuthorize\nprivate String name;\n")
            git("commit", "-am", "fix"); git("branch", "new")
            original_root = LocalGitClient.ROOT
            try:
                LocalGitClient.ROOT = Path(directory).resolve()
                with self.assertRaisesMessage(ValueError, "颠倒"):
                    LocalGitClient(".").compare("new", "old")
            finally:
                LocalGitClient.ROOT = original_root


class AnalysisLifecycleTests(TransactionTestCase):
    @staticmethod
    def grant_code_analysis_permissions(user, *codenames):
        permissions = Permission.objects.filter(
            content_type__app_label="code_analysis",
            codename__in=codenames,
        )
        user.user_permissions.add(*permissions)

    def setUp(self):
        self.user = User.objects.create_user("tester", password="secret")
        self.user.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="code_analysis")
        )
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
        self.llm_config = CodeAnalysisLLMConfig(
            config_name="代码审查专用", name="internal-review-model",
            api_url="http://llm.internal/v1", request_timeout=600, max_retries=2,
        )
        self.llm_config.set_api_key("review-secret")
        self.llm_config.save()

    def test_code_analysis_llm_key_is_encrypted_and_never_returned(self):
        self.assertNotEqual(self.llm_config.encrypted_api_key, "review-secret")
        self.assertEqual(self.llm_config.get_api_key(), "review-secret")
        client = APIClient(); client.force_authenticate(self.user)
        response = client.get("/api/code-analysis/llm-config/")
        self.assertEqual(response.status_code, 200)
        item = (response.data.get("results") or response.data)[0] if isinstance(response.data, dict) else response.data[0]
        self.assertNotIn("api_key", item)
        self.assertTrue(item["has_api_key"])

    def test_copy_existing_llm_config_without_exposing_key(self):
        source = LLMConfig.objects.create(
            config_name="内网推理服务", provider="openai_compatible",
            name="deepseek-v4-pro", api_url="http://model.internal/v1",
            api_key="platform-secret", request_timeout=900, max_retries=3,
        )
        client = APIClient(); client.force_authenticate(self.user)

        options = client.get("/api/code-analysis/llm-config/platform-configs/")
        self.assertEqual(options.status_code, 200)
        self.assertNotIn("api_key", options.data[0])
        self.assertTrue(options.data[0]["has_api_key"])

        response = client.post(
            "/api/code-analysis/llm-config/copy-from-platform/",
            {"source_config_id": source.pk}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.data)
        self.llm_config.refresh_from_db()
        self.assertEqual(self.llm_config.name, "deepseek-v4-pro")
        self.assertEqual(self.llm_config.request_timeout, 900)
        self.assertEqual(self.llm_config.get_api_key(), "platform-secret")

    @patch("code_analysis.tasks.run_code_analysis.delay")
    def test_non_quick_analysis_requires_dedicated_llm(self, delay):
        self.llm_config.delete()
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", mode="standard",
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/run/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("代码审查专用 LLM", response.data["detail"])
        delay.assert_not_called()

    def test_deleted_task_is_treated_as_cancelled(self):
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b",
        )
        task.delete()
        with self.assertRaises(AnalysisCancelled):
            _ensure_not_cancelled(task)

    def test_global_slot_allows_only_one_running_analysis(self):
        from .tasks import _claim_global_slot
        active = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", status="ai_analyzing",
        )
        queued = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="c", head_sha="d", status="queued",
        )
        self.assertEqual(_claim_global_slot(queued.id, "准备执行"), "queued")
        queued.refresh_from_db()
        self.assertEqual((queued.status, queued.current_step), ("queued", "排队中"))
        active.status = "completed"
        active.save(update_fields=["status"])
        self.assertEqual(_claim_global_slot(queued.id, "准备执行"), "claimed")
        queued.refresh_from_db()
        self.assertEqual((queued.status, queued.current_step), ("fetching", "准备执行"))

    @patch("code_analysis.services._analysis_stage")
    def test_failed_standalone_ocr_retry_keeps_degraded_report(self, analysis_stage):
        analysis_stage.return_value = (
            [], 0, "OCR 超时", False, 0,
            {"failure_type_counts": {"timeout": 1}, "failure_details": [{"type": "timeout"}]},
        )
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", mode="deep", status="degraded",
            change_report={
                "summary": {"risk_count": 1, "high_risk_count": 0},
                "findings": [{"key": "machine:1", "severity": "medium", "source": "machine_rule", "impact": "影响"}],
                "ocr_status": {"status": "failed"},
            },
        )
        retry_ocr_analysis(task)
        task.refresh_from_db()
        self.assertEqual(task.status, "degraded")
        self.assertEqual(task.change_report["ocr_status"]["message"], "OCR 超时")
        self.assertEqual(task.change_report["summary"]["risk_count"], 1)

    @patch("code_analysis.services.GitLabClient.commits")
    def test_repository_lists_latest_forty_commits_from_configured_branch(self, commits):
        self.repository.default_branch = "develop"
        self.repository.save(update_fields=["default_branch"])
        commits.return_value = [{
            "id": "a" * 40, "short_id": "a" * 8, "title": "latest change",
            "author_name": "tester", "authored_date": "2026-09-09T10:00:00+08:00",
        }]
        client = APIClient(); client.force_authenticate(self.user)
        response = client.get(f"/api/code-analysis/repositories/{self.repository.id}/commits/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["title"], "latest change")
        commits.assert_called_once_with(self.repository.gitlab_project_id, "develop", limit=40)
        self.repository.refresh_from_db()
        self.assertEqual(self.repository.default_branch, "develop")

    @patch("code_analysis.services.GitLabClient.merge_base")
    @patch("code_analysis.services.GitLabClient.commit")
    def test_repository_validates_and_resolves_both_commit_refs(self, commit, merge_base):
        commit.side_effect = [{"id": "a" * 40}, {"id": "b" * 40}]
        merge_base.return_value = "c" * 40
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(
            f"/api/code-analysis/repositories/{self.repository.id}/validate-refs/",
            {"base_sha": "feature~1", "head_sha": "feature"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["valid"], True)
        self.assertEqual(response.data["base_sha"], "a" * 40)
        self.assertEqual(response.data["head_sha"], "b" * 40)
        # 基准不是目标的祖先时必须提示按共同祖先比较，避免两点比较报出已修复的问题。
        self.assertEqual(response.data["compare_base_sha"], "c" * 40)
        self.assertIn("共同祖先", response.data["notice"])
        self.assertEqual(commit.call_count, 2)
        merge_base.assert_called_once_with(self.repository.gitlab_project_id, "a" * 40, "b" * 40)

    @patch("code_analysis.services.GitLabClient.merge_base")
    @patch("code_analysis.services.GitLabClient.commit")
    def test_repository_rejects_reversed_commit_refs(self, commit, merge_base):
        # 目标提交是基准提交的祖先，说明基准/目标选反：必须拦下，否则会把刚修好的改动报成删除。
        commit.side_effect = [{"id": "b" * 40}, {"id": "a" * 40}]
        merge_base.return_value = "a" * 40
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(
            f"/api/code-analysis/repositories/{self.repository.id}/validate-refs/",
            {"base_sha": "a" * 40, "head_sha": "b" * 40}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("颠倒", response.data["detail"])

    def test_repository_rejects_empty_commit_refs_before_task_creation(self):
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(
            f"/api/code-analysis/repositories/{self.repository.id}/validate-refs/",
            {"base_sha": "", "head_sha": ""}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Commit", response.data["detail"])

    @patch("code_analysis.services.GitLabClient.merge_requests")
    def test_merge_request_error_is_returned_as_json(self, merge_requests):
        merge_requests.side_effect = RuntimeError("404 Project Not Found")
        client = APIClient(); client.force_authenticate(self.user)
        response = client.get(f"/api/code-analysis/repositories/{self.repository.id}/merge-requests/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("404 Project Not Found", response.data["detail"])

    @patch("code_analysis.services.GitLabClient.commit")
    @patch("code_analysis.services.GitLabClient.project")
    def test_repository_access_validation_preserves_and_checks_configured_branch(self, project, commit):
        project.return_value = {
            "name": "Autotest API",
            "path_with_namespace": "source-sse_projects/Autotest_API",
            "default_branch": "master",
        }
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(
            f"/api/code-analysis/repositories/{self.repository.id}/validate-access/",
            {"gitlab_project_id": self.repository.gitlab_project_id, "default_branch": "release/2026"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.repository.refresh_from_db()
        self.assertEqual(self.repository.path_with_namespace, "source-sse_projects/Autotest_API")
        self.assertEqual(self.repository.default_branch, "release/2026")
        self.assertEqual(response.data["repository"]["name"], "Autotest API")
        commit.assert_called_once_with(self.repository.gitlab_project_id, "release/2026")

    @patch("code_analysis.services.GitLabClient.commit")
    @patch("code_analysis.services.GitLabClient.project")
    def test_repository_access_validation_rejects_missing_configured_branch(self, project, commit):
        project.return_value = {"name": "service", "path_with_namespace": "group/service", "default_branch": "master"}
        commit.side_effect = RuntimeError("404 Commit Not Found")
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(
            f"/api/code-analysis/repositories/{self.repository.id}/validate-access/",
            {"gitlab_project_id": self.repository.gitlab_project_id, "default_branch": "missing-branch"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("404 Commit Not Found", response.data["detail"])
        self.repository.refresh_from_db()
        self.assertEqual(self.repository.default_branch, "master")

    @patch("code_analysis.views.remove_ocr_repositories_for_repository")
    def test_repository_without_analysis_records_can_be_deleted(self, cleanup):
        repository_id = self.repository.id
        client = APIClient(); client.force_authenticate(self.user)
        response = client.delete(f"/api/code-analysis/repositories/{repository_id}/")
        self.assertIn(response.status_code, {200, 204})
        self.assertFalse(ProjectRepository.objects.filter(pk=repository_id).exists())
        cleanup.assert_called_once_with(repository_id)

    @patch("code_analysis.views.remove_ocr_repositories_for_repository")
    def test_repository_with_analysis_records_deletes_platform_records(self, cleanup):
        repository_id = self.repository.id
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b",
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.delete(f"/api/code-analysis/repositories/{self.repository.id}/")
        self.assertIn(response.status_code, {200, 204})
        self.assertFalse(ProjectRepository.objects.filter(pk=repository_id).exists())
        self.assertFalse(AnalysisTask.objects.filter(pk=task.id).exists())
        cleanup.assert_called_once_with(repository_id)

    def test_gitlab_connection_with_repository_cannot_be_deleted(self):
        client = APIClient(); client.force_authenticate(User.objects.create_superuser("admin", password="secret"))
        response = client.delete(f"/api/code-analysis/connections/{self.connection.id}/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["repository_count"], 1)
        self.assertTrue(GitLabConnection.objects.filter(pk=self.connection.id).exists())

    def test_code_review_api_rejects_member_without_model_permission(self):
        member = User.objects.create_user("member-without-code-review-permission")
        ProjectMember.objects.create(project=self.project, user=member, role="member")
        client = APIClient(); client.force_authenticate(member)
        self.assertEqual(client.get("/api/code-analysis/tasks/").status_code, 403)
        self.assertEqual(client.get("/api/code-analysis/repositories/").status_code, 403)

    def test_code_review_view_permission_still_respects_project_membership(self):
        member = User.objects.create_user("member-with-code-review-permission")
        self.grant_code_analysis_permissions(member, "view_analysistask")
        client = APIClient(); client.force_authenticate(member)
        response = client.get("/api/code-analysis/tasks/")
        self.assertEqual(response.status_code, 200)
        items = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(items, [])

    def test_unused_gitlab_connection_can_be_deleted(self):
        unused = GitLabConnection.objects.create(name="unused", base_url="https://unused.local")
        client = APIClient(); client.force_authenticate(User.objects.create_superuser("admin", password="secret"))
        response = client.delete(f"/api/code-analysis/connections/{unused.id}/")
        self.assertIn(response.status_code, {200, 204})
        self.assertFalse(GitLabConnection.objects.filter(pk=unused.id).exists())

    def test_staff_with_delete_permission_can_delete_unused_connection(self):
        unused = GitLabConnection.objects.create(name="staff-unused", base_url="https://staff-unused.local")
        staff = User.objects.create_user("platform-admin", password="secret", is_staff=True)
        self.grant_code_analysis_permissions(staff, "delete_gitlabconnection")
        client = APIClient(); client.force_authenticate(staff)
        response = client.delete(f"/api/code-analysis/connections/{unused.id}/")
        self.assertIn(response.status_code, {200, 204})
        self.assertFalse(GitLabConnection.objects.filter(pk=unused.id).exists())

    def test_connection_list_returns_global_repository_count(self):
        client = APIClient(); client.force_authenticate(self.user)
        response = client.get("/api/code-analysis/connections/")
        self.assertEqual(response.status_code, 200)
        items = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        connection = next(item for item in items if item["id"] == self.connection.id)
        self.assertEqual(connection["repository_count"], 1)

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
        self.assertEqual(change_download["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn(quote("代码审查报告_交易平台.md"), change_download["Content-Disposition"])
        self.assertIn("代码审查报告", change_download.content.decode())
        self.assertEqual(test_download.status_code, 200)
        self.assertEqual(test_download["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn(quote("测试分析报告_交易平台.md"), test_download["Content-Disposition"])
        self.assertIn("测试分析报告", test_download.content.decode())
        draft_id = task.test_requirement_drafts.get().id
        task.delete()
        self.assertFalse(AnalysisTask.objects.filter(pk=task.id).exists())
        from .models import TestRequirementDraft
        self.assertFalse(TestRequirementDraft.objects.filter(pk=draft_id).exists())

    def test_markdown_reports_sort_risks_and_test_points_by_priority(self):
        task = AnalysisTask.objects.create(
            project=self.project,
            repository=self.repository,
            creator=self.user,
            source_type="commits",
            base_sha="a",
            head_sha="b",
            status="completed",
            change_report={
                "summary": {},
                "findings": [
                    {"severity": "low", "change": "低风险项", "file": "low.py"},
                    {"severity": "high", "change": "高风险项", "file": "high.py"},
                    {"severity": "medium", "change": "中风险项", "file": "medium.py"},
                ],
            },
            test_report={
                "summary": {},
                "test_requirements": [
                    {"title": "低优先级需求", "priority": "low", "change_group": "迭代验证"},
                    {"title": "高优先级需求", "priority": "high", "change_group": "迭代验证"},
                    {"title": "中优先级需求", "priority": "medium", "change_group": "迭代验证"},
                    {"title": "低优先级风险", "priority": "low", "change_group": "风险排查"},
                    {"title": "高优先级风险", "priority": "high", "change_group": "风险排查"},
                    {"title": "中优先级风险", "priority": "medium", "change_group": "风险排查"},
                ],
            },
        )
        client = APIClient()
        client.force_authenticate(self.user)

        change_content = client.get(
            f"/api/code-analysis/tasks/{task.id}/download-change-report/"
        ).content.decode()
        self.assertLess(change_content.index("高风险项"), change_content.index("中风险项"))
        self.assertLess(change_content.index("中风险项"), change_content.index("低风险项"))

        test_content = client.get(
            f"/api/code-analysis/tasks/{task.id}/download-test-report/"
        ).content.decode()
        self.assertLess(test_content.index("高优先级需求"), test_content.index("中优先级需求"))
        self.assertLess(test_content.index("中优先级需求"), test_content.index("低优先级需求"))
        self.assertLess(test_content.index("高优先级风险"), test_content.index("中优先级风险"))
        self.assertLess(test_content.index("中优先级风险"), test_content.index("低优先级风险"))

    def test_analysis_modes_use_the_expected_ai_and_ocr_stages(self):
        diff_payload = {"diffs": [{
            "old_path": "src/service.py", "new_path": "src/service.py",
            "diff": "@@ -1 +1 @@\n-old_value = 1\n+new_value = 2",
        }]}
        iteration_summary = {
            "title": "服务逻辑调整", "description": "变更服务参数",
            "change_groups": [{"name": "服务逻辑", "description": "参数调整", "files": ["src/service.py"]}],
        }
        iteration_points = [{
            "title": "验证服务参数", "objective": "执行变更后服务",
            "expected_result": "服务使用新参数", "priority": "medium",
        }]
        with (
            patch("code_analysis.services.GitLabClient.compare", return_value=diff_payload),
            patch("code_analysis.services.GitLabClient.merge_base", return_value=""),
            patch("code_analysis.review_mcp.CodeReviewMCP.run_static_checks", return_value=([], [])),
            patch("code_analysis.services._analysis_stage", side_effect=lambda _task_id, _name, function, *args: function(*args)),
            patch("code_analysis.services._run_ai_batches", return_value=([], [], [], 5, 100, "AI分析完成")) as ai_batches,
            patch("code_analysis.services._run_open_code_review", return_value=([], 7, "OCR完成", True, 100, {"selected": 1, "completed": 1})) as ocr,
            patch("code_analysis.services._normalize_ocr_findings", return_value=([], 0, "")),
            patch("code_analysis.services._run_iteration_test_design", return_value=(iteration_summary, iteration_points, 3, "迭代分析完成")) as iteration,
            patch("code_analysis.services._run_context_test_enrichment", return_value=([], [], 0, "无文档补充")) as context,
            patch("code_analysis.services._run_risk_test_design", return_value=({}, 0, "无风险测试点")) as risk_design,
        ):
            expected = {
                "quick": {"ai": 0, "ocr": 0, "iteration": 0, "context": 0, "risk": 0},
                "standard": {"ai": 1, "ocr": 0, "iteration": 1, "context": 1, "risk": 1},
                "deep": {"ai": 1, "ocr": 1, "iteration": 1, "context": 1, "risk": 1},
            }
            for index, (mode, calls) in enumerate(expected.items()):
                ai_batches.reset_mock(); ocr.reset_mock(); iteration.reset_mock(); context.reset_mock(); risk_design.reset_mock()
                task = AnalysisTask.objects.create(
                    project=self.project, repository=self.repository, creator=self.user,
                    source_type="commits", base_sha=f"base-{index}", head_sha=f"head-{index}", mode=mode,
                )
                run_analysis(task, force_refresh=True)
                task.refresh_from_db()
                self.assertEqual(task.status, "completed", mode)
                self.assertEqual(ai_batches.call_count, calls["ai"], mode)
                self.assertEqual(ocr.call_count, calls["ocr"], mode)
                self.assertEqual(iteration.call_count, calls["iteration"], mode)
                self.assertEqual(context.call_count, calls["context"], mode)
                self.assertEqual(risk_design.call_count, calls["risk"], mode)
                self.assertEqual(task.change_report["ocr_status"]["status"], "completed" if mode == "deep" else "skipped")

    @patch("code_analysis.tasks.retry_code_analysis_ocr.delay")
    def test_standard_analysis_cannot_retry_ocr(self, delay):
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", mode="standard", status="degraded",
            change_report={"ocr_status": {"status": "failed", "message": "legacy failure"}},
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/retry-ocr/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("深度模式", response.data["detail"])
        delay.assert_not_called()

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
        self.assertEqual((task.status, task.current_step), ("queued", "排队中"))
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
    def test_second_analysis_is_accepted_as_queued(self, delay):
        delay.return_value = SimpleNamespace(id="queued-job")
        current = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a" * 40, head_sha="b" * 40,
            status="ai_analyzing", celery_task_id="already-running",
        )
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="c" * 40, head_sha="d" * 40,
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/run/")
        self.assertEqual(response.status_code, 202)
        task.refresh_from_db()
        self.assertEqual((task.status, task.current_step), ("queued", "排队中"))
        delay.assert_called_once_with(str(task.id), force_refresh=True)
        self.assertEqual(current.status, "ai_analyzing")

    @patch("code_analysis.tasks.retry_code_analysis_ocr.delay")
    def test_failed_ocr_can_be_retried_without_full_analysis(self, delay):
        delay.return_value = SimpleNamespace(id="ocr-retry-job")
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", mode="deep", status="degraded",
            change_report={"ocr_status": {"status": "failed", "message": "timeout"}},
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.post(f"/api/code-analysis/tasks/{task.id}/retry-ocr/")
        self.assertEqual(response.status_code, 202)
        task.refresh_from_db()
        self.assertEqual((task.status, task.current_step), ("queued", "OCR 重试排队中"))
        self.assertEqual(task.celery_task_id, "ocr-retry-job")
        delay.assert_called_once_with(str(task.id))

    def test_execution_logs_are_visible_to_project_member(self):
        task = AnalysisTask.objects.create(project=self.project, repository=self.repository, creator=self.user, source_type="commits", base_sha="a", head_sha="b")
        AnalysisTaskExecutionLog.objects.create(task=task, event="completed", message="分析完成")
        client = APIClient(); client.force_authenticate(self.user)
        response = client.get(f"/api/code-analysis/tasks/{task.id}/execution-logs/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["event"], "completed")

    @patch("code_analysis.views.current_app.control.revoke")
    @patch("code_analysis.views.terminate_ocr_processes")
    @patch("code_analysis.views.remove_ocr_repository")
    def test_deleting_analysis_stops_worker_and_deletes_its_ocr_repository(
        self, remove_repository, terminate_ocr, revoke,
    ):
        task = AnalysisTask.objects.create(
            project=self.project, repository=self.repository, creator=self.user,
            source_type="commits", base_sha="a", head_sha="b", celery_task_id="celery-delete-1",
        )
        client = APIClient(); client.force_authenticate(self.user)
        response = client.delete(f"/api/code-analysis/tasks/{task.id}/")
        self.assertIn(response.status_code, {200, 204})
        self.assertFalse(AnalysisTask.objects.filter(pk=task.id).exists())
        terminate_ocr.assert_called_once_with(task.id)
        revoke.assert_called_once_with("celery-delete-1", terminate=True, signal="SIGTERM")
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
                    # OCR 必须与平台机器规则看到同一段差异：从共同祖先开始比较。
                    # 线性历史下共同祖先即基准提交本身。
                    self.assertEqual(base_ref, base_sha)
                    self.assertEqual(head_ref, "refs/ocr/head")
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
        self.grant_code_analysis_permissions(admin, "view_analysistask")
        client = APIClient(); client.force_authenticate(admin)
        response = client.get("/api/code-analysis/tasks/")
        self.assertEqual(response.status_code, 200)
        items = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(len(items), 2)
        self.assertEqual({item["project_name"] for item in items}, {"交易平台", "其他平台"})

    def test_project_member_can_read_another_members_reports_diff_and_logs(self):
        member = User.objects.create_user("reviewer", password="secret")
        self.grant_code_analysis_permissions(member, "view_analysistask")
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
        self.grant_code_analysis_permissions(member, "view_analysistask", "change_analysistask")
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
        self.grant_code_analysis_permissions(member, "change_testrequirementdraft")
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
        self.grant_code_analysis_permissions(member, "change_testrequirementdraft")
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
    @patch("code_analysis.views.terminate_ocr_processes")
    def test_cancel_marks_task_and_terminates_running_job(self, terminate_ocr, revoke):
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
        terminate_ocr.assert_called_once_with(task.id)
        revoke.assert_called_once_with("celery-job-2", terminate=True, signal="SIGTERM")
