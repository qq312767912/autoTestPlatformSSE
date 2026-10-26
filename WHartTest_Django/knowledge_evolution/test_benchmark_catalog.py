"""内置公开测评集：目录、适配器、准备、预检、快照与报告的契约测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

from .benchmark_adapters import AdapterDependencyError, get_adapter
from .benchmark_adapters.base import build_case, dataset_default_language, normalize_issue
from .benchmark_catalog import BENCHMARK_IDS, CATALOG, manifest, normalize_language, version_label
from .benchmark_models import (
    BenchmarkCase,
    BenchmarkDatasetVersion,
    BenchmarkRunBinding,
    ImmutableVersionError,
)
from .benchmark_preflight import preflight
from .benchmark_report import build_benchmark_report
from .benchmark_snapshots import SnapshotRejected, is_allowed_pair, validate_repo, validate_sha
from .evaluation_models import EvaluationResult, EvaluationRun
from .models import EvaluationCase, EvaluationSuite

User = get_user_model()


class CatalogManifestTest(SimpleTestCase):
    def test_three_fixed_benchmarks(self):
        self.assertEqual(BENCHMARK_IDS, ("swe_care", "swr_bench", "aacr_bench"))
        for benchmark_id, entry in CATALOG.items():
            self.assertEqual(entry["benchmark_id"], benchmark_id)
            self.assertTrue(entry["license"]["spdx"])
            self.assertTrue(entry["upstream"]["revision"])
            self.assertTrue(version_label(benchmark_id))

    def test_manifest_files_have_sha256_and_size(self):
        for benchmark_id in BENCHMARK_IDS:
            payload = manifest(benchmark_id)
            self.assertEqual(len(payload["manifest_digest"]), 64)
            for item in payload["files"]:
                self.assertEqual(len(item["sha256"]), 64)
                self.assertGreater(item["size"], 0)

    def test_language_claims_are_verified_numbers(self):
        for benchmark_id in BENCHMARK_IDS:
            verified = CATALOG[benchmark_id]["languages"]["verified"]
            self.assertTrue(verified, benchmark_id)
            for language, count in verified.items():
                self.assertIsNotNone(normalize_language(language), language)
                self.assertGreater(count, 0)
            # 未覆盖的语言必须显式列出，避免「显示 0 条」被误读为可测
            for language in CATALOG[benchmark_id]["languages"]["uncovered"]:
                self.assertNotIn(language, verified)

    def test_swr_negative_examples_are_only_the_clean_split(self):
        entry = CATALOG["swr_bench"]
        self.assertEqual(entry["expected"]["case_count"], 1000)
        self.assertEqual(entry["expected"]["clean_count"], 500)
        self.assertLess(entry["expected"]["clean_count"], entry["expected"]["case_count"])

    def test_aacr_negative_shard_is_excluded_with_reason(self):
        files = {item["name"]: item for item in manifest("aacr_bench")["files"]}
        self.assertTrue(files["negative_samples.json"]["excluded"])
        self.assertFalse(files["positive_samples.json"]["excluded"])


class AdapterContractTest(SimpleTestCase):
    def test_aacr_maps_source_to_base_and_target_to_head(self):
        adapter = get_adapter("aacr_bench")
        case, reason = adapter.adapt_record({
            "githubPrUrl": "https://github.com/FreeCAD/FreeCAD/pull/19411",
            "source_commit": "a" * 40,
            "target_commit": "b" * 40,
            "project_main_language": "C++",
            "comments": [{"path": "src/App.cpp", "note": "空指针未判空", "from_line": 10, "to_line": 12}],
        })
        self.assertEqual(reason, "")
        self.assertEqual(case["repo"], "FreeCAD/FreeCAD")
        self.assertEqual(case["base_sha"], "a" * 40)
        self.assertEqual(case["head_sha"], "b" * 40)
        self.assertEqual(case["language"], "c++")
        self.assertIsNone(case["is_clean"])
        self.assertEqual(case["gold_issues"][0]["file"], "src/App.cpp")
        self.assertEqual(case["gold_issues"][0]["start_line"], 10)

    def test_aacr_rejects_unsupported_language(self):
        adapter = get_adapter("aacr_bench")
        case, reason = adapter.adapt_record({
            "githubPrUrl": "https://github.com/o/r/pull/1",
            "source_commit": "a" * 40, "target_commit": "b" * 40,
            "project_main_language": "Go",
            "comments": [{"path": "main.go", "note": "错误处理缺失", "to_line": 3}],
        })
        self.assertIsNone(case)
        self.assertTrue(reason.startswith("language_not_supported"))

    def test_swr_clean_pr_has_upstream_label(self):
        adapter = get_adapter("swr_bench")
        case, reason = adapter.adapt_record({
            "instance_id": "django__django-1", "repo": "django/django",
            "base_commit": "a" * 40, "change_introduced": False, "changes": [],
            "pr_commits": [{"sha": "b" * 40}],
        })
        self.assertEqual(reason, "")
        self.assertTrue(case["is_clean"])
        self.assertEqual(case["is_clean_source"], "upstream_label")
        self.assertEqual(case["gold_issues"], [])

    def test_swr_defect_without_locatable_gold_is_rejected_not_clean(self):
        adapter = get_adapter("swr_bench")
        case, reason = adapter.adapt_record({
            "instance_id": "django__django-2", "repo": "django/django",
            "base_commit": "a" * 40, "change_introduced": True,
            "changes": [{"change_type": "F.2 Logic", "change_introducing": {"code_snippet": "无路径的片段引用"}}],
            "pr_commits": [{"sha": "b" * 40}],
        })
        self.assertIsNone(case)
        self.assertEqual(reason, "empty_gold_without_clean_label")

    def test_swr_resolves_file_level_gold_from_context_hint(self):
        adapter = get_adapter("swr_bench")
        case, reason = adapter.adapt_record({
            "instance_id": "astropy__astropy-3", "repo": "astropy/astropy",
            "base_commit": "a" * 40, "change_introduced": True,
            "changes": [{
                "change_type": "F.2 Logic",
                "change_introducing": {
                    "code_snippet": "# Relevant context from astropy/modeling/core.py\nclass Model: pass",
                },
            }],
            "pr_commits": [{"sha": "b" * 40}],
        })
        self.assertEqual(reason, "")
        issue = case["gold_issues"][0]
        self.assertEqual(issue["file"], "astropy/modeling/core.py")
        self.assertFalse(issue["located"])

    def test_swe_care_without_pyarrow_fails_loudly(self):
        adapter = get_adapter("swe_care")
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "test.parquet").write_bytes(b"PAR1")
            import builtins

            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name.startswith("pyarrow"):
                    raise ImportError("no pyarrow")
                return real_import(name, *args, **kwargs)

            builtins.__import__ = fake_import
            try:
                with self.assertRaises(AdapterDependencyError):
                    list(adapter.iter_records(temp))
            finally:
                builtins.__import__ = real_import

    def test_swe_care_jsonl_offline_package_does_not_need_pyarrow(self):
        adapter = get_adapter("swe_care")
        record = {
            "instance_id": "django__django-4", "repo": "django/django",
            "base_commit": "a" * 40, "language": "Python",
            "commit_to_review": {"head_commit": "b" * 40},
            "reference_review_comments": [{"path": "django/db.py", "line": 7, "text": "事务边界错误"}],
        }
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "test.jsonl").write_text(json.dumps(record), encoding="utf-8")
            outcome = adapter.convert(temp)
        self.assertEqual(outcome.accepted, 1)
        case = outcome.cases[0]
        self.assertEqual(case["language"], "python")
        self.assertIsNone(case["is_clean"])
        self.assertEqual(case["gold_issues"][0]["line"], 7)

    def test_duplicate_instance_id_is_recorded_not_silently_dropped(self):
        adapter = get_adapter("swe_care")
        record = {
            "instance_id": "django__django-5", "repo": "django/django",
            "base_commit": "a" * 40, "language": "Python",
            "commit_to_review": {"head_commit": "b" * 40},
            "reference_review_comments": [{"path": "django/x.py", "line": 3, "text": "越界"}],
        }
        with tempfile.TemporaryDirectory() as temp:
            lines = "\n".join(json.dumps(record) for _ in range(2))
            Path(temp, "test.jsonl").write_text(lines, encoding="utf-8")
            outcome = adapter.convert(temp)
        self.assertEqual(outcome.accepted, 1)
        self.assertEqual(outcome.rejections[0]["reason"], "duplicate_instance_id")

    def test_identical_commit_pair_rejected(self):
        case, reason = build_case(
            upstream_instance_id="x", repo="a/b", base_sha="a" * 40, head_sha="a" * 40,
            language="python", gold_issues=[], is_clean=False,
        )
        self.assertIsNone(case)
        self.assertEqual(reason, "identical_commit_pair")

    def test_empty_gold_is_not_clean_by_default(self):
        case, reason = build_case(
            upstream_instance_id="x", repo="a/b", base_sha="a" * 40, head_sha="b" * 40,
            language="python", gold_issues=[], is_clean=None,
        )
        self.assertIsNone(case)
        self.assertEqual(reason, "empty_gold_without_clean_label")

    def test_issue_without_line_keeps_file_level_locator(self):
        issue = normalize_issue({"file": "a/b.py", "description": "缺少异常处理"})
        self.assertIsNotNone(issue)
        self.assertEqual(issue["file"], "a/b.py")
        self.assertFalse(issue["located"])

    def test_dataset_default_language_only_for_single_language_sets(self):
        self.assertEqual(dataset_default_language("swr_bench"), "python")
        self.assertIsNone(dataset_default_language("aacr_bench"))


def _make_version(benchmark_id="swr_bench", **kwargs):
    defaults = {
        "benchmark_id": benchmark_id,
        "version": version_label(benchmark_id),
        "upstream_revision": CATALOG[benchmark_id]["upstream"]["revision"],
        "adapter_version": "1.0.0",
        "manifest_digest": manifest(benchmark_id)["manifest_digest"],
        "manifest": manifest(benchmark_id),
        "status": "ready",
        "case_total": 3, "case_valid": 3, "case_rejected": 0,
        "gold_total": 1, "clean_total": 2,
        "language_counts": {"python": 3},
        "published_at": timezone.now(),
    }
    defaults.update(kwargs)
    return BenchmarkDatasetVersion.objects.create(**defaults)


def _make_case(version, instance, *, is_clean, gold=1, language="python", repo="django/django"):
    issues = [{"file": "django/db.py", "line": 10, "start_line": 10, "end_line": 10,
               "located": True, "description": "事务未提交"}] * gold if not is_clean else []
    return BenchmarkCase.objects.create(
        version=version, benchmark_id=version.benchmark_id, upstream_instance_id=instance,
        repo=repo, base_sha="a" * 40, head_sha="b" * 40, language=language,
        gold_issues=issues, gold_count=len(issues), is_clean=is_clean,
        is_clean_source="upstream_label" if is_clean else "unknown",
        source_url=f"https://github.com/{repo}/pull/1", conversion_status="ok",
    )


class VersionImmutabilityTest(TestCase):
    def test_published_version_cannot_be_rewritten(self):
        version = _make_version()
        version.case_valid = 99
        with self.assertRaises(ImmutableVersionError):
            version.save(update_fields=["case_valid"])

    def test_published_version_cannot_be_deleted(self):
        version = _make_version()
        with self.assertRaises(ImmutableVersionError):
            version.delete()

    def test_status_change_is_allowed(self):
        version = _make_version()
        version.status = "deprecated"
        version.deprecated_reason = "上游更新"
        version.save(update_fields=["status", "deprecated_reason"])
        version.refresh_from_db()
        self.assertEqual(version.status, "deprecated")


class PreflightTest(TestCase):
    def test_preflight_reports_zero_clean_and_available_counts(self):
        version = _make_version()
        _make_case(version, "django__django-1", is_clean=False)
        _make_case(version, "django__django-2", is_clean=True)
        result = preflight(version)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["clean_counts"]["clean"], 1)
        self.assertEqual(result["clean_counts"]["defect"], 1)
        self.assertEqual(result["available"], 2)
        self.assertEqual(result["snapshot_pending"], 2)
        self.assertTrue(result["submittable"])

    def test_missing_model_blocks_submission(self):
        version = _make_version()
        _make_case(version, "django__django-3", is_clean=False)
        result = preflight(version, model_ready=False)
        self.assertFalse(result["submittable"])
        self.assertEqual(result["excluded"][0]["reasons"], ["model_not_configured"])

    def test_language_filter_excludes_other_languages(self):
        version = _make_version(benchmark_id="aacr_bench", gold_total=2, clean_total=0,
                                language_counts={"python": 1, "java": 1})
        _make_case(version, "repo__a@aaaaaaa", is_clean=False, language="python")
        _make_case(version, "repo__b@bbbbbbb", is_clean=False, language="java")
        result = preflight(version, languages=["java"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["language_counts"], {"java": 1})


class SnapshotSecurityTest(TestCase):
    def test_repo_and_sha_validation(self):
        self.assertEqual(validate_repo("django/django"), "django/django")
        for bad in ("django", "../../etc/passwd", "http://evil.com/repo", "a/b/c"):
            with self.assertRaises(SnapshotRejected):
                validate_repo(bad)
        self.assertEqual(validate_sha("A" * 40), "a" * 40)
        for bad in ("abc", "z" * 40, ""):
            with self.assertRaises(SnapshotRejected):
                validate_sha(bad)

    def test_pair_must_exist_in_published_cases(self):
        version = _make_version()
        _make_case(version, "django__django-4", is_clean=False)
        self.assertTrue(is_allowed_pair("django/django", "a" * 40, "b" * 40))
        self.assertFalse(is_allowed_pair("evil/repo", "a" * 40, "b" * 40))


class BenchmarkReportTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bench", password="x")
        self.project = Project.objects.create(name="bench-project")
        ProjectMember.objects.create(project=self.project, user=self.user)
        self.suite = EvaluationSuite.objects.create(
            project=self.project, name="内置公开集 · SWR-Bench", suite_type="gold",
            task_type="code_review",
        )
        self.version = _make_version()
        self.defect = _make_case(self.version, "django__django-5", is_clean=False)
        self.clean = _make_case(self.version, "django__django-6", is_clean=True)
        self.run = EvaluationRun.objects.create(
            suite=self.suite, name="试跑", triggered_by=self.user, status="completed",
            config={"benchmark": "code_review", "benchmark_id": "swr_bench"},
        )
        BenchmarkRunBinding.objects.create(
            run=self.run, version=self.version, benchmark_id="swr_bench", mode="pilot",
            selected_case_keys=[self.defect.case_key, self.clean.case_key],
            excluded_cases=[{"case_key": "swr_bench@x:repo__x", "reasons": ["snapshot_failed"]}],
            model_snapshot={"name": "review-model"}, code_version="abc123",
        )

    def _evaluation_case(self, benchmark_case, number):
        return EvaluationCase.objects.create(
            suite=self.suite, case_number=number, task_type="code_review",
            input_payload={"benchmark_case_id": str(benchmark_case.pk)},
            golden_labels={"issues": benchmark_case.gold_issues},
            split="gold",
            metadata={
                "source": "benchmark", "benchmark_id": "swr_bench",
                "benchmark_case_id": str(benchmark_case.pk), "case_key": benchmark_case.case_key,
                "language": benchmark_case.language, "is_clean": benchmark_case.is_clean,
                "repo": benchmark_case.repo, "source_url": benchmark_case.source_url,
            },
        )

    def _task(self, benchmark_case, status, findings, tokens=120):
        from code_analysis.models import AnalysisTask

        return AnalysisTask.objects.create(
            project=self.project, repository=None, benchmark_case=benchmark_case,
            creator=self.user, executor=self.user, source_type="commits",
            base_sha=benchmark_case.base_sha, head_sha=benchmark_case.head_sha,
            title="公开测评", mode="standard", status=status, token_usage=tokens,
            change_report={"findings": findings},
        )

    def test_failed_and_unverified_samples_are_reported_separately(self):
        verified_case = self._evaluation_case(self.defect, 1)
        failed_case = self._evaluation_case(self.clean, 2)
        task = self._task(self.defect, "completed", [{
            "file": "django/db.py", "line": 10, "change": "事务未提交",
            "evidence": "事务未提交", "trigger": "事务未提交", "impact": "事务未提交",
            "disposition": "confirmed", "severity": "high",
        }])
        EvaluationResult.objects.create(
            run=self.run, case=verified_case, status="completed",
            predicted_payload={"analysis_task_id": str(task.id)},
            raw_scores={"code_review_adjudication": {
                "completed": True, "matches": [{"gold_index": 0, "finding_index": 0}],
                "false_findings": [], "valid_findings": [],
            }},
        )
        failed_task = self._task(self.clean, "failed", [])
        EvaluationResult.objects.create(
            run=self.run, case=failed_case, status="failed",
            predicted_payload={"analysis_task_id": str(failed_task.id)},
            error_message="快照缺失",
        )
        report = build_benchmark_report(self.run)
        summary = report["benchmark"]["summary"]
        self.assertEqual(summary["n_selected"], 2)
        self.assertEqual(summary["n_success"], 1)
        self.assertEqual(summary["n_failed"], 1)
        self.assertEqual(summary["n_verified"], 1)
        self.assertEqual(summary["recall"], 1.0)
        self.assertEqual(report["benchmark"]["coverage"]["excluded"], 1)
        self.assertEqual(report["benchmark"]["coverage"]["selected"], 2)
        self.assertFalse(report["benchmark"]["native_metrics"]["implemented"])
        self.assertIsNone(report["benchmark"]["native_metrics"]["value"])
        self.assertEqual(report["benchmark"]["license"]["spdx"], "MIT")
        self.assertEqual(report["benchmark"]["model_snapshot"]["name"], "review-model")

    def test_no_clean_sample_verified_means_no_false_positive_rate(self):
        verified_case = self._evaluation_case(self.defect, 1)
        task = self._task(self.defect, "completed", [])
        EvaluationResult.objects.create(
            run=self.run, case=verified_case, status="completed",
            predicted_payload={"analysis_task_id": str(task.id)},
            raw_scores={"code_review_adjudication": {
                "completed": True, "matches": [], "false_findings": [], "valid_findings": [],
            }},
        )
        summary = build_benchmark_report(self.run)["benchmark"]["summary"]
        self.assertIsNone(summary["clean_pr_false_positive_rate"])
        self.assertIn("无成功的干净样本", summary["note"])

    def test_successful_but_unverified_clean_case_states_why_rate_is_absent(self):
        """干净样本跑成功了但没逐样本核验时，不能说「无干净样本」。"""
        clean_case = self._evaluation_case(self.clean, 1)
        task = self._task(self.clean, "completed", [{
            "file": "django/db.py", "line": 10, "change": "多余的事务提交",
            "evidence": "多余的事务提交", "trigger": "多余的事务提交", "impact": "多余的事务提交",
            "disposition": "confirmed", "severity": "high",
        }])
        EvaluationResult.objects.create(
            run=self.run, case=clean_case, status="completed",
            predicted_payload={"analysis_task_id": str(task.id)},
        )
        summary = build_benchmark_report(self.run)["benchmark"]["summary"]
        self.assertEqual(summary["clean_cases"], 1)
        self.assertEqual(summary["n_verified"], 0)
        self.assertEqual(summary["clean_cases_with_findings"], 1)
        self.assertIsNone(summary["clean_pr_false_positive_rate"])
        self.assertIn("尚未逐样本核验", summary["note"])

    def test_recall_denominator_counts_only_verified_cases(self):
        """未核验样本的金标不得进入召回分母（分母只含成功且已核验样本）。"""
        verified_case = self._evaluation_case(self.defect, 1)
        unverified_case = self._evaluation_case(self.defect, 2)
        task = self._task(self.defect, "completed", [{
            "file": "django/db.py", "line": 10, "change": "事务未提交",
            "evidence": "事务未提交", "trigger": "事务未提交", "impact": "事务未提交",
            "disposition": "confirmed", "severity": "high",
        }])
        EvaluationResult.objects.create(
            run=self.run, case=verified_case, status="completed",
            predicted_payload={"analysis_task_id": str(task.id)},
            raw_scores={"code_review_adjudication": {
                "completed": True, "matches": [{"gold_index": 0, "finding_index": 0}],
                "false_findings": [], "valid_findings": [],
            }},
        )
        EvaluationResult.objects.create(
            run=self.run, case=unverified_case, status="completed",
            predicted_payload={"analysis_task_id": str(task.id)},
        )
        summary = build_benchmark_report(self.run)["benchmark"]["summary"]
        self.assertEqual(summary["gold_issues"], 2)
        self.assertEqual(summary["n_verified"], 1)
        self.assertEqual(summary["recall"], 1.0)

    def test_benchmark_without_clean_split_never_reports_f1(self):
        version = _make_version(benchmark_id="aacr_bench", clean_total=0, gold_total=1)
        case = _make_case(version, "FreeCAD__FreeCAD@abcdef0", is_clean=False, language="c++")
        self.run.benchmark_binding.version = version
        self.run.benchmark_binding.save(update_fields=["version"])
        evaluation_case = EvaluationCase.objects.create(
            suite=self.suite, case_number=9, task_type="code_review",
            input_payload={"benchmark_case_id": str(case.pk)},
            golden_labels={"issues": case.gold_issues}, split="gold",
            metadata={"source": "benchmark", "benchmark_id": "aacr_bench",
                      "benchmark_case_id": str(case.pk), "case_key": case.case_key,
                      "language": "c++", "is_clean": None, "repo": case.repo},
        )
        task = self._task(case, "completed", [{
            "file": "django/db.py", "line": 10, "change": "事务未提交",
            "evidence": "事务未提交", "trigger": "事务未提交", "impact": "事务未提交",
            "disposition": "confirmed", "severity": "high",
        }])
        EvaluationResult.objects.create(
            run=self.run, case=evaluation_case, status="completed",
            predicted_payload={"analysis_task_id": str(task.id)},
            raw_scores={"code_review_adjudication": {
                "completed": True, "matches": [{"gold_index": 0, "finding_index": 0}],
                "false_findings": [], "valid_findings": [],
            }},
        )
        summary = build_benchmark_report(self.run)["benchmark"]["summary"]
        self.assertIsNone(summary["precision"])
        self.assertIsNone(summary["f1"])
        self.assertEqual(summary["recall"], 1.0)


class BenchmarkApiTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="api-user", password="x", is_staff=True)
        self.project = Project.objects.create(name="api-project")
        ProjectMember.objects.create(project=self.project, user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_catalog_lists_three_entries(self):
        response = self.client.get("/api/knowledge-evolution/evaluation-benchmarks/")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        benchmarks = (payload.get("data") or payload)["benchmarks"]
        self.assertEqual([item["benchmark_id"] for item in benchmarks], list(BENCHMARK_IDS))
        for item in benchmarks:
            self.assertIn(item["state"], {"not_prepared", "preparing", "ready", "failed"})
            self.assertTrue(item["license"]["spdx"])

    def test_prepare_requires_staff(self):
        member = User.objects.create_user(username="member", password="x")
        ProjectMember.objects.create(project=self.project, user=member)
        client = APIClient()
        client.force_authenticate(member)
        response = client.post("/api/knowledge-evolution/evaluation-benchmarks/swr_bench/prepare/")
        self.assertEqual(response.status_code, 403)

    def test_manual_public_import_is_rejected(self):
        response = self.client.post(
            "/api/knowledge-evolution/evaluation-suites/import-code-review/",
            {
                "project": self.project.id,
                "source": "public",
                "cases": [{"repository_id": 1, "base_sha": "a" * 40, "head_sha": "b" * 40,
                           "language": "python", "issues": [{"description": "x"}]}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("内置目录", str(response.json()))

    def test_public_run_requires_prepared_version(self):
        response = self.client.post(
            "/api/knowledge-evolution/evaluation-runs/run-code-review-public/",
            {"project": self.project.id, "benchmark_id": "swr_bench", "mode": "pilot"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("version", str(response.json()))

    @patch("knowledge_evolution.benchmark_runs.execute_benchmark_run.delay")
    def test_public_run_creates_project_scoped_run_and_enqueues_execution(self, enqueue):
        from code_analysis.models import CodeAnalysisLLMConfig

        version = _make_version()
        case = _make_case(version, "django__django-api", is_clean=False)
        model = CodeAnalysisLLMConfig(
            name="review-model",
            api_url="https://example.invalid/v1/chat/completions",
            is_active=True,
        )
        model.set_api_key("test-key")
        model.save()

        response = self.client.post(
            "/api/knowledge-evolution/evaluation-runs/run-code-review-public/",
            {
                "project": self.project.id,
                "benchmark_id": "swr_bench",
                "mode": "pilot",
                "case_keys": [case.case_key],
                "allow_partial": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        payload = payload.get("data") or payload
        run = EvaluationRun.objects.get(pk=payload["id"])
        self.assertEqual(run.suite.project_id, self.project.id)
        self.assertEqual(run.benchmark_binding.selected_case_keys, [case.case_key])
        enqueue.assert_called_once_with(str(run.id))

    def test_public_execution_task_is_registered_by_celery_discovery_module(self):
        from . import tasks  # noqa: F401 - exercise Celery autodiscovery module
        from .benchmark_runs import execute_benchmark_run

        self.assertEqual(
            execute_benchmark_run.name,
            "knowledge_evolution.benchmark_runs.execute_benchmark_run",
        )
