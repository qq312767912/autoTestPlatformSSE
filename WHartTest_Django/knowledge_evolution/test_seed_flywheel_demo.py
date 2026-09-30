"""`seed_flywheel_demo` 命令的回归测试。

关注三件事（缺一不可）：
1. 首次执行确实把四个阶段的演示数据都造出来；
2. 重复执行幂等——行数不变（此前踩过 KnowledgeEvidence.save() 覆盖 location_hash
   导致 get_or_create 永远匹配不到、二次执行直接 IntegrityError 的坑）；
3. --reset 只清演示数据，不误删既有真实数据；
4. 界面失败样本判据与 EvaluationReviewBridge 一致，否则「失败样本数」
   与点「生成知识候选」实际产出的候选数会对不上。
"""

from django.core.management import call_command
from django.test import TestCase

from knowledge_evolution.eval_review_bridge import EvaluationReviewBridge
from knowledge_evolution.evaluation_models import EvaluationResult, EvaluationRun
from knowledge_evolution.knowledge_models import (
    KnowledgeAsset,
    KnowledgeCandidate,
    SourceSnapshot,
)
from knowledge_evolution.models import (
    EvaluationCase,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    RetrievalTrace,
)
from projects.models import Project

DEMO_SUITES = ["Phase0 种子集", "回归基准集 v1.2", "挑战样本集 · 边界与对抗", "新样本流 · 用例生成"]


class SeedFlywheelDemoTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="seed-demo-test")

    def _run(self, *extra):
        call_command("seed_flywheel_demo", "--project-id", str(self.project.pk), *extra, verbosity=0)

    def _counts(self):
        return {
            "suites": EvaluationSuite.objects.filter(project=self.project).count(),
            "cases": EvaluationCase.objects.filter(suite__project=self.project).count(),
            "runs": EvaluationRun.objects.filter(suite__project=self.project).count(),
            "results": EvaluationResult.objects.filter(run__suite__project=self.project).count(),
            "traces": RetrievalTrace.objects.filter(project=self.project).count(),
            "outputs": GenerationOutput.objects.filter(project=self.project).count(),
            "feedback": FeedbackEvent.objects.filter(project=self.project).count(),
            "candidates": KnowledgeCandidate.objects.filter(project=self.project).count(),
        }

    def test_first_run_creates_all_four_stages(self):
        self._run()
        counts = self._counts()
        self.assertEqual(counts["suites"], 4)
        self.assertEqual(
            sorted(EvaluationSuite.objects.filter(project=self.project).values_list("name", flat=True)),
            sorted(DEMO_SUITES),
        )
        self.assertEqual(counts["cases"], 50 + 24 + 12 + 8)
        self.assertEqual(counts["runs"], 5)
        self.assertEqual(counts["results"], 50 * 3)      # 3 次运行的完整结果
        self.assertEqual(counts["traces"], 14)
        self.assertEqual(counts["outputs"], 12)          # 2 条非 completed 轨迹无输出
        self.assertEqual(counts["feedback"], 16)
        self.assertEqual(counts["candidates"], 12)

    def test_second_run_is_idempotent(self):
        self._run()
        before = self._counts()
        self._run()   # 不 --reset 再跑一次，不得新增任何行
        self.assertEqual(self._counts(), before)

    def test_dry_run_writes_nothing(self):
        self._run("--dry-run")
        counts = self._counts()
        self.assertEqual(counts["suites"], 0)
        self.assertEqual(counts["traces"], 0)
        self.assertEqual(counts["candidates"], 0)

    def test_reset_keeps_non_demo_data(self):
        keep_snapshot = SourceSnapshot.objects.create(
            project=self.project, source_type="document", source_id="keep-1",
            content_hash="a" * 64, authority="internal", status="parsed",
        )
        keep_asset = KnowledgeAsset.objects.create(
            project=self.project, asset_type="rule", key="keep/rule",
            title="真实资产", level="L2", status="active",
        )
        keep_suite = EvaluationSuite.objects.create(
            project=self.project, name="真实评测集", suite_type="regression",
            task_type="code_review",
        )

        self._run()
        self._run("--reset")

        self.assertTrue(SourceSnapshot.objects.filter(pk=keep_snapshot.pk).exists())
        self.assertTrue(KnowledgeAsset.objects.filter(pk=keep_asset.pk).exists())
        self.assertTrue(EvaluationSuite.objects.filter(pk=keep_suite.pk).exists())
        # 演示数据被重建，总量保持稳定（多一个真实评测集）
        counts = self._counts()
        self.assertEqual(counts["suites"], 5)
        self.assertEqual(counts["traces"], 14)
        self.assertEqual(counts["candidates"], 12)

    def test_failure_verdict_matches_review_bridge(self):
        """界面失败样本判据（score<0.5 或 status=failed）必须等于桥接器判定的失败数。"""
        from knowledge_evolution.management.commands.seed_flywheel_demo import FAILURE_THRESHOLDS

        self._run()
        checked = 0
        for run in EvaluationRun.objects.filter(
            suite__project=self.project, status="completed"
        ):
            total = run.results.count()
            if not total:
                continue
            bridge = len(EvaluationReviewBridge(run).find_failures())
            ui_like = sum(
                1
                for result in run.results.all()
                if result.status == "failed"
                or any(
                    getattr(result, f"{level}_score") is not None
                    and getattr(result, f"{level}_score") < threshold
                    for level, threshold in FAILURE_THRESHOLDS.items()
                )
            )
            self.assertEqual(bridge, ui_like, f"{run.name} 判据不一致：桥接器 {bridge} vs 界面 {ui_like}")
            self.assertGreater(bridge, 0)
            checked += 1
        self.assertEqual(checked, 3)

    def test_newest_run_has_results_so_workbench_is_not_empty(self):
        """工作台默认选中最新运行，它必须带完整结果，否则后两个阶段是空的。"""
        self._run()
        suite = EvaluationSuite.objects.get(project=self.project, name="Phase0 种子集")
        newest = EvaluationRun.objects.filter(suite=suite).order_by("-created_at").first()
        self.assertEqual(newest.status, "completed")
        self.assertEqual(newest.results.count(), 50)
        self.assertTrue(newest.metrics_summary.get("l3_score") is not None)
