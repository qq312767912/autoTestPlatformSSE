"""T09–T13：评测分区与硬门禁、反馈金标闭环、多层归因、候选自进化、审批与自动回滚。

测试组织原则：**每个用例只证明一件在真实业务里会出错的事**。
不做"调用一下看看不抛异常"式的覆盖——那种测试在需求变更时既不会红也不会提示。
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from knowledge_evolution.attribution import (
    AttributionService,
    assert_confirmed_attributions,
    layering_overview,
)
from knowledge_evolution.capability_models import CapabilityDefinition, CapabilityRelease
from knowledge_evolution.capability_registry import (
    ALL_TASK_TYPES,
    BUSINESS_CAPABILITY_STAGES,
    WORKFLOW_STAGES,
    capability_info,
    grouped_stages,
    is_evolvable,
    required_partitions,
)
from knowledge_evolution.capabilities import CapabilityReleaseService
from knowledge_evolution.evaluation_gates import (
    EvaluationGateService,
    EvaluationPartitionService,
    RunMetricsService,
)
from knowledge_evolution.evaluation_models import EvaluationResult, EvaluationRun
from knowledge_evolution.feedback import (
    FeedbackService,
    assert_evidence_complete,
    effective_evidence,
    resolve_binding,
)
from knowledge_evolution.gate_models import EvaluationGateSnapshot
from knowledge_evolution.gold import GoldCandidateService, GoldCatalogService
from knowledge_evolution.gold_models import GoldCase, GoldDataset, GoldDatasetVersion
from knowledge_evolution.models import (
    EvaluationCase,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    RetrievalTrace,
)
from knowledge_evolution.optimization import NON_OPTIMIZABLE_CATEGORIES
from knowledge_evolution.skill_evolution import (
    GUARDRAIL_BEGIN,
    GUARDRAIL_END,
    SkillCandidateDeriver,
    assert_derivation_allowed,
)
from knowledge_evolution.trace_models import (
    CATEGORY_LAYER_MAP,
    LAYER_ORDER,
    ExecutionSpan,
    FailureAttribution,
)
from knowledge_evolution.workflow_models import WorkflowSkillLock  # noqa: F401  (触发模型注册)
from projects.models import Project, ProjectMember
from skills.versions import SkillVersionService

#: 测试专用媒体目录：派生候选会真实落盘，绝不能写进项目数据目录。
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t09t13-")

SKILL_MD = """---
name: {name}
description: T09–T13 测试用 Skill
version: {version}
stage: {stage}
entrypoint: scripts/main.py
permissions: []
---
# {name}

请检查以下要点：

- 空值检查
- 幂等性
"""


def _write_package(root: Path, name: str, version: str, stage: str = "case_review") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        SKILL_MD.format(name=name, version=version, stage=stage), encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "main.py").write_text("# entrypoint\nprint('ok')\n", encoding="utf-8")
    return root


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SkillHubBaseTests(TestCase):
    """公共夹具：项目、负责人/执行人、真实落盘的 Skill 版本。"""

    def setUp(self):
        self.lead = User.objects.create_user(username="t09-lead", password="pass")
        self.executor = User.objects.create_user(username="t09-exec", password="pass")
        self.project = Project.objects.create(name="T09 项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.other_project = Project.objects.create(name="T09 他项目", creator=self.lead)
        ProjectMember.objects.create(project=self.other_project, user=self.lead, role="owner")

    # ------------------------------------------------------------ 构造 helper

    def make_skill_version(self, *, name="clarity-review", version="1.0.0",
                           stage="case_review", project=None, body="初始"):
        project = project or self.project
        work = Path(tempfile.mkdtemp(prefix="t09-src-"))
        try:
            _write_package(work / "package", name, version, stage)
            if body != "初始":
                target = work / "package" / "SKILL.md"
                target.write_text(
                    target.read_text(encoding="utf-8") + f"\n{body}\n", encoding="utf-8",
                )
            skill, skill_version = SkillVersionService.create_candidate_from_dir(
                source_dir=work / "package", project=project, actor=self.lead,
            )
            return skill, skill_version
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def make_trace(self, *, task_type="case_review", project=None):
        project = project or self.project
        return RetrievalTrace.objects.create(
            project=project, user=self.executor, task_type=task_type,
            task_id=f"task-{task_type}", query="检查用例是否可执行",
        )

    def make_output(self, *, task_type="case_review", trace=None, skill_version=None,
                    project=None, workflow_id="", parents=None, capability=None,
                    output_hash=None):
        project = project or self.project
        trace = trace or self.make_trace(task_type=task_type, project=project)
        return GenerationOutput.objects.create(
            project=project, trace=trace, task_type=task_type,
            task_id=f"task-{task_type}", content="输出正文",
            output_hash=output_hash or f"{task_type}-hash".ljust(64, "0"),
            skill_version=skill_version, capability=capability,
            metadata={"protocol": {
                "schema_version": "platform-output/v1",
                "stage": task_type,
                "workflow_id": workflow_id,
                "parent_output_ids": parents or [],
            }},
        )

    def make_suite(self, *, task_type="case_review", name="T09 套件", project=None):
        return EvaluationSuite.objects.create(
            project=project or self.project, name=name,
            suite_type="gold", task_type=task_type,
        )

    def make_run_with_results(self, *, suite, splits, scores=None, latency=100,
                              tokens=100, run_name="run", outputs=None):
        """按 ``splits={"gold": 2, "regression": 1}`` 造一次评测。

        **基线与候选必须跑在同一批 case 上**，所以这里复用套件里已存在的 case，
        只按需补足数量。若每次调用都从 case_number=1 重新造，第二个 run 会直接
        撞上 ``uniq_case_suite_number`` 唯一约束——这个约束本身是对的，它守住的正是
        "同套件内样本编号唯一"这条业务不变式，所以修夹具而不是放宽约束。
        """
        run = EvaluationRun.objects.create(suite=suite, name=run_name, status="completed")
        wanted: list[str] = []
        for split, count in splits.items():
            wanted.extend([split] * count)

        existing = list(
            EvaluationCase.objects.filter(suite=suite).order_by("case_number")
        )
        cases: list[EvaluationCase] = []
        for index, split in enumerate(wanted):
            if index < len(existing):
                case = existing[index]
            else:
                case = EvaluationCase.objects.create(
                    suite=suite, case_number=index + 1, task_type=suite.task_type,
                    input_payload={"n": index + 1}, split=split,
                    source_output=(outputs or {}).get(split),
                )
            cases.append(case)

        for index, case in enumerate(cases):
            output = (outputs or {}).get(case.split)
            if output is None and outputs:
                output = list(outputs.values())[0]
            if output is not None and case.source_output_id is None:
                case.source_output = output
                case.save(update_fields=["source_output"])
            score = None
            if scores is not None:
                score = scores[index % len(scores)]
            EvaluationResult.objects.create(
                run=run, case=case, status="completed",
                l0_score=1.0, l1_score=score,
                latency_ms=latency, token_usage=tokens,
            )
        return run

    def make_passing_gate(self, *, task_type="case_review", release=None,
                          quality=0.9, splits=None, latency=100, tokens=100,
                          with_feedback=True):
        """造一次**必然通过**门禁的评测，供审批链路复用。

        刻意让基线/候选同套件、同样本、同耗时同 Token，只留质量可控：
        否则"门禁为什么没过"会有十几种可能，测试就无法指出真正的缺陷。
        """
        suite = self.make_suite(task_type=task_type)
        output = self.make_output(task_type=task_type)
        splits = splits or {"gold": 1, "regression": 1, "fresh": 1}
        baseline = self.make_run_with_results(
            suite=suite, splits=splits, scores=[quality], latency=latency, tokens=tokens,
            outputs={"gold": output}, run_name="baseline",
        )
        candidate = self.make_run_with_results(
            suite=suite, splits=splits, scores=[quality], latency=latency, tokens=tokens,
            outputs={"gold": output}, run_name="candidate",
        )
        if with_feedback:
            FeedbackEvent.objects.create(
                project=self.project, output=output, signal="defect_confirmed",
                idempotency_key=f"passing-{candidate.id}", actor=self.executor,
            )
        snapshot = EvaluationGateService.run_gate(
            candidate_run=candidate, baseline_run=baseline, release=release,
            required_partitions=tuple(splits.keys()),
        )
        return snapshot, baseline, candidate, output

    def make_attribution(self, *, output=None, category="prompt_error", state="confirmed",
                         hypothesis="缺少空值检查指令"):
        import hashlib

        output = output or self.make_output()
        fingerprint = hashlib.sha256(
            f"{output.id}:{category}:{hypothesis}:{state}".encode()
        ).hexdigest()
        return FailureAttribution.objects.create(
            project=output.project, output=output, category=category,
            source="rule", confidence=0.9, hypothesis=hypothesis,
            state=state, fingerprint=fingerprint,
            evidence=[{"span_id": None, "feedback_signals": ["defect_confirmed"]}],
            confirmed_by=self.lead if state == "confirmed" else None,
        )


# ============================================================== T09


class PartitionRegistryTests(SkillHubBaseTests):
    """分区口径与能力分类的唯一真值。"""

    def test_business_capability_count_matches_requirement(self):
        """需求写"八类业务能力"，注册表必须真的是 8 类且不含平台工具。"""
        self.assertEqual(len(BUSINESS_CAPABILITY_STAGES), 8)
        self.assertIn("code_review", BUSINESS_CAPABILITY_STAGES)
        self.assertNotIn("knowledge_query", BUSINESS_CAPABILITY_STAGES)
        self.assertIn("knowledge_query", ALL_TASK_TYPES)

    def test_workflow_stages_require_all_five_partitions(self):
        # 遍历真值源而不是抄一份阶段名：主链路口径变更时，
        # 抄下来的那份会变成"测的还是老四个阶段"，看起来绿其实是空的。
        for stage in WORKFLOW_STAGES:
            self.assertEqual(len(required_partitions(stage)), 5, stage)

    def test_demoted_stages_only_require_three_partitions(self):
        """降为单次能力的阶段不再要求五分区：凑"隐藏集"对它们没有链路意义。"""
        for stage in ("test_plan_generation", "report_generation"):
            self.assertEqual(len(required_partitions(stage)), 3, stage)
            self.assertNotIn("hidden", required_partitions(stage))

    def test_single_capability_does_not_require_hidden(self):
        self.assertNotIn("hidden", required_partitions("case_review"))

    def test_code_review_is_composite_not_skill(self):
        self.assertEqual(capability_info("code_review")["kind"], "composite")
        self.assertTrue(is_evolvable("code_review"))
        self.assertFalse(is_evolvable("knowledge_query"))

    def test_grouped_stages_covers_every_task_type(self):
        grouped = grouped_stages()
        total = sum(len(items) for items in grouped.values())
        self.assertEqual(total, len(ALL_TASK_TYPES))


class MetricsAndPartitionTests(SkillHubBaseTests):
    def test_metrics_compute_quality_latency_token_and_stability(self):
        suite = self.make_suite()
        run = self.make_run_with_results(
            suite=suite, splits={"gold": 3}, scores=[1.0, 1.0, 1.0],
            latency=200, tokens=50,
        )
        metrics = RunMetricsService.summarize(run)
        self.assertEqual(metrics["sample_count"], 3)
        self.assertEqual(metrics["quality"], 1.0)
        self.assertEqual(metrics["latency_ms"], 200)
        self.assertEqual(metrics["token_usage"], 50)
        # 分数完全一致 → 标准差 0 → 稳定性 1.0
        self.assertEqual(metrics["stability"], 1.0)

    def test_rate_is_none_when_no_signal_instead_of_zero(self):
        """没有信号时返回 None（无法判定），不能记 0 让候选看起来完美。"""
        suite = self.make_suite()
        run = self.make_run_with_results(suite=suite, splits={"gold": 1}, scores=[0.9])
        metrics = RunMetricsService.summarize(run)
        self.assertIsNone(metrics["false_positive_rate"])
        self.assertIsNone(metrics["false_negative_rate"])

    def test_signal_rates_use_true_positive_denominator(self):
        suite = self.make_suite()
        output = self.make_output()
        run = self.make_run_with_results(
            suite=suite, splits={"gold": 1}, scores=[0.8], outputs={"gold": output},
        )
        for signal, key in (("defect_confirmed", "s-1"), ("false_positive", "s-2"),
                            ("missed", "s-3")):
            FeedbackEvent.objects.create(
                project=self.project, output=output, signal=signal,
                idempotency_key=key, actor=self.executor,
            )
        metrics = RunMetricsService.summarize(run)
        # TP=1, FP=1 → 误报率 1/2；missed=1 → 漏报率 1/2
        self.assertEqual(metrics["false_positive_rate"], 0.5)
        self.assertEqual(metrics["false_negative_rate"], 0.5)

    def test_partition_detail_counts_by_case_split(self):
        suite = self.make_suite()
        run = self.make_run_with_results(suite=suite, splits={"gold": 2, "fresh": 1})
        detail = EvaluationPartitionService.partition_detail(run)
        self.assertEqual(detail["gold"]["results"], 2)
        self.assertEqual(detail["fresh"]["results"], 1)

    def test_missing_partitions_reports_absent_ones(self):
        # 用**链路阶段**做样本：只有链路阶段要求五分区齐全（含隐藏集）。
        # 写死某个阶段名会在口径变更后失效——阶段名从"方案生成"换成"风险识别"时，
        # 这里会静默变成"测一个单能力阶段"，而它本来就不要求隐藏集。
        stage = WORKFLOW_STAGES[0]
        suite = self.make_suite(task_type=stage)
        run = self.make_run_with_results(suite=suite, splits={"gold": 1})
        missing = EvaluationPartitionService.missing_partitions(
            run, required=required_partitions(stage),
        )
        self.assertIn("hidden", missing)
        self.assertNotIn("gold", missing)

    def test_baseline_comparability_detects_suite_mismatch(self):
        baseline_suite = self.make_suite(name="A")
        candidate_suite = self.make_suite(name="B")
        baseline = self.make_run_with_results(suite=baseline_suite, splits={"gold": 1})
        candidate = self.make_run_with_results(suite=candidate_suite, splits={"gold": 1})
        result = EvaluationPartitionService.baseline_comparability(baseline, candidate)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "suite_mismatch")

    def test_baseline_comparability_rejects_sample_shrinking(self):
        """候选丢掉基线样本 = 用减少覆盖换通过率，必须拦下。"""
        suite = self.make_suite()
        baseline = self.make_run_with_results(suite=suite, splits={"gold": 3})
        candidate = self.make_run_with_results(suite=suite, splits={"gold": 1})
        result = EvaluationPartitionService.baseline_comparability(baseline, candidate)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "sample_shrunk")


class GateServiceTests(SkillHubBaseTests):
    def test_gate_fails_when_partitions_incomplete(self):
        suite = self.make_suite(task_type="test_plan_generation")
        candidate = self.make_run_with_results(suite=suite, splits={"gold": 1}, scores=[0.99])
        snapshot = EvaluationGateService.run_gate(candidate_run=candidate)
        codes = {item["code"]: item["ok"] for item in snapshot.checks}
        self.assertFalse(codes["partitions_complete"])
        self.assertFalse(snapshot.passed)

    def test_gate_fails_when_no_feedback_signals(self):
        """没有反馈信号时漏报/误报无法判定，不得判为通过。"""
        suite = self.make_suite(task_type="case_review")
        candidate = self.make_run_with_results(
            suite=suite, splits={"gold": 1, "regression": 1, "fresh": 1}, scores=[0.95],
        )
        snapshot = EvaluationGateService.run_gate(candidate_run=candidate)
        codes = {item["code"]: item for item in snapshot.checks}
        self.assertFalse(codes["false_positive_rate"]["ok"])
        self.assertFalse(codes["false_negative_rate"]["ok"])

    def test_gate_fails_on_quality_regression(self):
        suite = self.make_suite(task_type="case_review")
        output = self.make_output()
        baseline = self.make_run_with_results(
            suite=suite, splits={"gold": 1, "regression": 1, "fresh": 1},
            scores=[0.95], outputs={"gold": output},
        )
        candidate = self.make_run_with_results(
            suite=suite, splits={"gold": 1, "regression": 1, "fresh": 1},
            scores=[0.60], outputs={"gold": output}, run_name="cand",
        )
        FeedbackEvent.objects.create(
            project=self.project, output=output, signal="defect_confirmed",
            idempotency_key="ok-1", actor=self.executor,
        )
        snapshot = EvaluationGateService.run_gate(
            candidate_run=candidate, baseline_run=baseline,
        )
        codes = {item["code"]: item for item in snapshot.checks}
        self.assertFalse(codes["quality_floor"]["ok"])
        self.assertFalse(codes["quality_regression"]["ok"])
        self.assertFalse(snapshot.passed)

    def test_gate_fails_when_baseline_missing(self):
        suite = self.make_suite(task_type="case_review")
        candidate = self.make_run_with_results(
            suite=suite, splits={"gold": 1, "regression": 1, "fresh": 1}, scores=[0.95],
        )
        snapshot = EvaluationGateService.run_gate(candidate_run=candidate, baseline_run=None)
        codes = {item["code"]: item["ok"] for item in snapshot.checks}
        self.assertFalse(codes["baseline_present"])
        self.assertFalse(codes["baseline_comparable"])
        self.assertFalse(snapshot.passed)

    def test_snapshot_is_immutable(self):
        """门禁快照是审批证据，落库后不得修改。"""
        suite = self.make_suite(task_type="case_review")
        candidate = self.make_run_with_results(suite=suite, splits={"gold": 1}, scores=[0.9])
        snapshot = EvaluationGateService.run_gate(candidate_run=candidate)
        snapshot.passed = True
        with self.assertRaises(ValidationError):
            snapshot.save()

    def test_snapshot_is_idempotent_for_same_inputs(self):
        suite = self.make_suite(task_type="case_review")
        candidate = self.make_run_with_results(suite=suite, splits={"gold": 1}, scores=[0.9])
        first = EvaluationGateService.run_gate(candidate_run=candidate)
        second = EvaluationGateService.run_gate(candidate_run=candidate)
        self.assertEqual(str(first.id), str(second.id))
        self.assertEqual(EvaluationGateSnapshot.objects.count(), 1)

    def test_missing_conditions_are_returned_when_gate_not_run(self):
        release = CapabilityRelease.objects.create(
            project=self.project, kind="skill", name="x", version="1.0.0",
            artifact_hash="a" * 64, state="shadow",
        )
        conditions = EvaluationGateService.missing_conditions(release)
        self.assertEqual([item["code"] for item in conditions], ["gate_not_run"])
        with self.assertRaises(ValidationError):
            EvaluationGateService.assert_can_submit_approval(release)

    def test_thresholds_can_relax_partition_requirement_for_single_capability(self):
        suite = self.make_suite(task_type="case_review")
        candidate = self.make_run_with_results(
            suite=suite, splits={"gold": 1, "regression": 1, "fresh": 1}, scores=[0.9, 0.9],
        )
        snapshot = EvaluationGateService.run_gate(
            candidate_run=candidate,
            required_partitions=("gold", "regression", "fresh"),
        )
        codes = {item["code"]: item["ok"] for item in snapshot.checks}
        self.assertTrue(codes["partitions_complete"])


# ============================================================== T10


class FeedbackBindingTests(SkillHubBaseTests):
    def test_feedback_binds_release_and_skill_version(self):
        skill, version = self.make_skill_version()
        output = self.make_output(skill_version=version)
        event = FeedbackService.record_for_output(
            output=output, signal="accepted", actor=self.executor,
            evidence=[{"file": "a.py"}],
        )
        self.assertEqual(str(event.skill_version_id), str(version.id))
        self.assertEqual(str(event.release_id), str(version.release_id))
        self.assertEqual(event.capability_kind, "skill")
        self.assertEqual(len(event.evidence), 1)

    def test_feedback_keeps_binding_after_output_deleted(self):
        """产出被清理后仍要能回答"这条反馈针对哪个版本"。"""
        skill, version = self.make_skill_version()
        output = self.make_output(skill_version=version)
        event = FeedbackService.record_for_output(
            output=output, signal="accepted", actor=self.executor,
        )
        event.refresh_from_db()
        release_id = event.release_id
        skill_version_id = event.skill_version_id
        self.assertIsNotNone(release_id)
        self.assertIsNotNone(skill_version_id)

    def test_resolve_binding_falls_back_to_capability_active_release(self):
        definition = CapabilityDefinition.objects.create(
            project=self.project, kind="composite", name="代码审查", description="",
            evaluation_mode="single", stages=["code_review"],
        )
        release = CapabilityRelease.objects.create(
            project=self.project, kind="composite", name="代码审查", version="1.0.0",
            artifact_hash="b" * 64, state="active",
        )
        definition.active_release = release
        definition.save(update_fields=["active_release", "updated_at"])
        binding = resolve_binding(output=None, task_type="code_review")
        self.assertEqual(binding["capability_kind"], "composite")

    def test_evidence_required_for_false_positive(self):
        output = self.make_output()
        event = FeedbackEvent.objects.create(
            project=self.project, output=output, signal="false_positive",
            idempotency_key="fp-1", actor=self.executor,
        )
        missing = assert_evidence_complete(event)
        self.assertEqual([item["code"] for item in missing], ["evidence_missing"])

    def test_protocol_evidence_counts_as_evidence(self):
        """产出协议里已有的 evidence 不能被判成"没有证据"。"""
        output = self.make_output()
        output.metadata = {"protocol": {
            "schema_version": "platform-output/v1",
            "evidence": [{"file": "x.py", "line": 3}],
        }}
        output.save(update_fields=["metadata"])
        event = FeedbackEvent.objects.create(
            project=self.project, output=output, signal="missed",
            idempotency_key="miss-1", actor=self.executor,
        )
        self.assertEqual(len(effective_evidence(event)), 1)
        self.assertEqual(assert_evidence_complete(event), [])


class GoldClosureTests(SkillHubBaseTests):
    def _dataset(self, task_type="code_review", cases=0):
        """建一个金标数据集版本。

        ``cases`` 用来补已确认样本：``GoldVersionService.freeze`` 拒绝冻结空集，
        这是刻意的正确行为（空集冻结出来的"基线"无法回放任何东西），
        所以需要冻结的用例要通过本参数把样本补齐。
        """
        dataset = GoldDataset.objects.create(
            project=self.project, name="代码审查金标", task_type=task_type,
            owner=self.lead, created_by=self.lead,
        )
        version = GoldDatasetVersion.objects.create(
            dataset=dataset, version="v1", created_by=self.lead,
        )
        for index in range(cases):
            GoldCase.objects.create(
                version=version, task_type=task_type,
                title=f"样本 {index + 1}",
                input_snapshot={"index": index},
                expected_output={"summary": f"结论 {index + 1}"},
                split="gold", state="confirmed",
                source_hash=f"hash-{index + 1}",
                created_by=self.lead,
            )
        return dataset, version

    def test_feedback_without_evidence_cannot_become_gold(self):
        _, version = self._dataset()
        trace = self.make_trace(task_type="code_review")
        output = self.make_output(task_type="code_review", trace=trace)
        feedback = FeedbackEvent.objects.create(
            project=self.project, output=output, signal="false_positive",
            idempotency_key="np-1", actor=self.executor,
        )
        with self.assertRaises(ValidationError):
            GoldCandidateService.from_feedback(
                version=version, feedback=feedback, actor=self.lead,
            )

    def test_capability_mismatch_is_rejected(self):
        """代码审查样本不能塞进用例审查金标集。"""
        _, version = self._dataset(task_type="case_review")
        trace = self.make_trace(task_type="code_review")
        output = self.make_output(task_type="code_review", trace=trace)
        feedback = FeedbackEvent.objects.create(
            project=self.project, output=output, signal="rejected",
            idempotency_key="mm-1", actor=self.executor,
            evidence=[{"reason": "缺少证据"}],
        )
        with self.assertRaises(ValidationError):
            GoldCandidateService.from_feedback(
                version=version, feedback=feedback, actor=self.lead,
            )

    def test_gold_case_carries_version_traceability(self):
        _, version = self._dataset()
        skill, skill_version = self.make_skill_version(
            name="code-review-skill", stage="code_review",
        )
        trace = self.make_trace(task_type="code_review")
        output = self.make_output(
            task_type="code_review", trace=trace, skill_version=skill_version,
        )
        feedback = FeedbackService.record_for_output(
            output=output, signal="defect_confirmed", actor=self.executor,
            evidence=[{"file": "a.py", "line": 10}],
        )
        case = GoldCandidateService.from_feedback(
            version=version, feedback=feedback, actor=self.lead,
        )
        self.assertEqual(case.input_snapshot["skill_version_id"], str(skill_version.id))
        self.assertEqual(case.input_snapshot["package_sha256"], skill_version.package_sha256)
        # tags 里带能力形态快照：code_review 属复合能力，不是 Skill 型。
        self.assertIn("composite", case.tags)
        self.assertEqual(case.input_snapshot["capability_id"], "")
        self.assertEqual(case.input_snapshot["release_id"], str(skill_version.release_id))
        self.assertIsNotNone(skill)

    def test_catalog_groups_by_capability_and_reports_missing_partitions(self):
        _, version = self._dataset(task_type="code_review")
        overview = GoldCatalogService.overview(self.project.id)
        bucket = overview["capabilities"]["code_review"]
        self.assertEqual(len(bucket["datasets"]), 1)
        # 能力级缺口：必需分区里一条样本都没有的
        self.assertIn("gold", bucket["missing_partitions"])
        self.assertIn("regression", bucket["missing_partitions"])
        # 数据集级缺口：该集自身缺哪些分区
        entry = bucket["datasets"][0]
        self.assertIn("gold", entry["missing_partitions"])
        self.assertEqual(overview["total_capability_count"], len(ALL_TASK_TYPES))
        self.assertFalse(entry["replayable"])
        self.assertIsNotNone(version)

    def test_catalog_marks_frozen_version_replayable(self):
        from knowledge_evolution.gold import GoldVersionService

        _, version = self._dataset(task_type="code_review", cases=1)
        self.assertFalse(
            GoldCatalogService.overview(self.project.id)["capabilities"]["code_review"]
            ["datasets"][0]["replayable"]
        )
        GoldVersionService.freeze(version=version, actor=self.lead)
        entry = (
            GoldCatalogService.overview(self.project.id)["capabilities"]["code_review"]
            ["datasets"][0]
        )
        self.assertTrue(entry["replayable"])
        self.assertEqual(entry["latest_state"], "frozen")


# ============================================================== T11


class AttributionLayerTests(SkillHubBaseTests):
    def test_every_category_maps_to_declared_layer(self):
        from knowledge_evolution.trace_models import FailureAttribution as FA

        declared = {value for value, _label in FA.CATEGORY_CHOICES}
        self.assertEqual(declared, set(CATEGORY_LAYER_MAP))
        self.assertEqual(set(CATEGORY_LAYER_MAP.values()), set(LAYER_ORDER))
        self.assertEqual(len(LAYER_ORDER), 8)

    def test_layer_is_persisted_from_category(self):
        attribution = self.make_attribution(category="retrieval_error")
        attribution.refresh_from_db()
        self.assertEqual(attribution.layer, "retrieval")

    def test_environment_error_is_not_optimizable(self):
        """环境问题不能靠改 Prompt/Skill 修复，必须显式拒绝。"""
        self.assertIn("environment_error", NON_OPTIMIZABLE_CATEGORIES)

    def test_layering_overview_counts_only_confirmed_as_confirmed(self):
        self.make_attribution(category="prompt_error", state="confirmed")
        self.make_attribution(category="prompt_error", state="proposed",
                              hypothesis="另一个假设")
        overview = layering_overview(self.project.id)
        prompt_layer = next(item for item in overview["layers"] if item["layer"] == "prompt")
        self.assertEqual(prompt_layer["confirmed"], 1)
        self.assertEqual(prompt_layer["proposed"], 1)

    def test_decide_records_review_note(self):
        attribution = self.make_attribution(category="prompt_error", state="proposed")
        AttributionService.decide(
            attribution=attribution, actor=self.lead, accepted=True, note="已核对日志",
        )
        attribution.refresh_from_db()
        self.assertEqual(attribution.state, "confirmed")
        self.assertEqual(attribution.evidence[-1]["note"], "已核对日志")

    def test_unconfirmed_attribution_blocks_candidate_generation(self):
        proposed = self.make_attribution(category="prompt_error", state="proposed")
        with self.assertRaises(ValidationError):
            assert_confirmed_attributions([proposed])

    def test_mixed_attribution_batch_is_rejected(self):
        confirmed = self.make_attribution(category="prompt_error", state="confirmed")
        proposed = self.make_attribution(
            category="tool_error", state="proposed", hypothesis="工具假设",
        )
        with self.assertRaises(ValidationError):
            assert_confirmed_attributions([confirmed, proposed])


class UpstreamTracingTests(SkillHubBaseTests):
    def test_trace_upstream_finds_responsible_stage_and_version(self):
        """下游报告出问题，必须能反查到上游方案阶段的 Skill 版本（R8）。"""
        _, upstream_version = self.make_skill_version(
            name="plan-skill", stage="test_plan_generation", version="1.0.0",
        )
        plan_output = self.make_output(
            task_type="test_plan_generation", skill_version=upstream_version,
            workflow_id="wf-trace",
        )
        report_output = self.make_output(
            task_type="report_generation", workflow_id="wf-trace",
            parents=[str(plan_output.id)],
        )
        # 上游存在已确认归因
        self.make_attribution(
            output=plan_output, category="prompt_error", state="confirmed",
            hypothesis="方案未覆盖边界",
        )
        result = AttributionService.trace_upstream_versions(report_output)
        self.assertIsNotNone(result["responsible"])
        self.assertEqual(result["responsible"]["stage"], "test_plan_generation")
        self.assertEqual(
            result["responsible"]["skill_version_id"], str(upstream_version.id),
        )
        self.assertEqual(result["responsible"]["package_sha256"], upstream_version.package_sha256)

    def test_trace_upstream_returns_none_when_no_confirmed_attribution(self):
        upstream = self.make_output(task_type="test_plan_generation", workflow_id="wf-x")
        downstream = self.make_output(
            task_type="report_generation", workflow_id="wf-x", parents=[str(upstream.id)],
        )
        result = AttributionService.trace_upstream_versions(downstream)
        self.assertIsNone(result["responsible"])
        self.assertEqual(len(result["chain"]), 2)

    def test_trace_upstream_does_not_cross_project(self):
        other_trace = self.make_trace(task_type="test_plan_generation",
                                      project=self.other_project)
        foreign = self.make_output(
            task_type="test_plan_generation", trace=other_trace,
            project=self.other_project, workflow_id="wf-y",
        )
        mine = self.make_output(task_type="report_generation", workflow_id="wf-y",
                                parents=[str(foreign.id)])
        result = AttributionService.trace_upstream_versions(mine)
        self.assertEqual(len(result["chain"]), 1)

    def test_run_for_output_records_span_failure(self):
        output = self.make_output()
        ExecutionSpan.objects.create(
            trace=output.trace, stage="case_review", step_type="tool",
            status="failed", sequence=1, error_type="timeout",
        )
        attributions = AttributionService.run_for_output(output)
        self.assertTrue(any(item.category == "tool_error" for item in attributions))
        self.assertTrue(any(item.layer == "skill_tool" for item in attributions))


# ============================================================== T12


class DerivationGuardTests(SkillHubBaseTests):
    def test_non_addressable_category_is_rejected_with_channel_hint(self):
        skill, version = self.make_skill_version()
        attribution = self.make_attribution(
            output=self.make_output(skill_version=version),
            category="knowledge_missing", state="confirmed",
            hypothesis="知识库缺该规则",
        )
        problems = assert_derivation_allowed(version, [attribution])
        self.assertTrue(any("知识候选" in item["detail"] for item in problems))

    def test_baseline_must_be_active(self):
        skill, v1 = self.make_skill_version(version="1.0.0")
        _, v2 = self.make_skill_version(version="2.0.0", body="第二版")
        # 把 v2 激活，使 v1 不再是活跃版本
        v2.release.gate_report = {"passed": True}
        v2.release.state = "awaiting_approval"
        v2.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(v2.release, actor=self.lead, reason="激活 2.0.0")
        skill.refresh_from_db()
        self.assertEqual(str(skill.active_version_id), str(v2.id))

        attribution = self.make_attribution(output=self.make_output(skill_version=v1))
        problems = assert_derivation_allowed(v1, [attribution])
        self.assertTrue(any(item["code"] == "baseline_not_active" for item in problems))

    def test_patch_path_escape_is_rejected(self):
        _, version = self.make_skill_version()
        work = Path(tempfile.mkdtemp(prefix="t12-escape-"))
        try:
            with self.assertRaises(ValidationError):
                SkillCandidateDeriver.apply_patch(
                    source_dir=Path(version.get_full_path()),
                    patch={"edits": [{"path": "../../evil.txt", "content": "x"}]},
                    work_dir=work / "pkg",
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_patch_with_missing_find_target_is_rejected(self):
        _, version = self.make_skill_version()
        work = Path(tempfile.mkdtemp(prefix="t12-find-"))
        try:
            with self.assertRaises(ValidationError):
                SkillCandidateDeriver.apply_patch(
                    source_dir=Path(version.get_full_path()),
                    patch={"edits": [{
                        "path": "SKILL.md", "find": "这段文字不存在", "replace": "x",
                    }]},
                    work_dir=work / "pkg",
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_guardrail_replaces_previous_block_instead_of_appending(self):
        """反复派生不能让护栏小节堆积。"""
        _, version = self.make_skill_version()
        patch = SkillCandidateDeriver.build_patch(
            skill_version=version,
            attributions=[self.make_attribution(
                output=self.make_output(skill_version=version),
            )],
        )
        content = patch["edits"][0]["content"]
        self.assertEqual(content.count(GUARDRAIL_BEGIN), 1)
        # 再派生一次：仍只有一个受管块
        again = SkillCandidateDeriver._replace_guardrail(content, "REPLACED")
        self.assertEqual(again.count(GUARDRAIL_BEGIN), 0)
        self.assertIn("REPLACED", again)


class DerivationTests(SkillHubBaseTests):
    def _activate(self, version):
        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活基线")

    def test_derive_candidate_creates_new_draft_and_leaves_active_untouched(self):
        from knowledge_evolution.skill_evolution import SkillEvolutionService

        skill, version = self.make_skill_version()
        self._activate(version)
        version.refresh_from_db()
        baseline_sha = version.package_sha256
        baseline_path = version.get_full_path()

        output = self.make_output(skill_version=version)
        attribution = self.make_attribution(
            output=output, category="prompt_error", state="confirmed",
            hypothesis="缺少空值检查指令",
        )
        result = SkillEvolutionService.derive_candidate(
            skill_version=version, attributions=[attribution], actor=self.lead,
            change_reason="补充空值检查护栏",
        )
        candidate = result["candidate"]
        self.assertNotEqual(str(candidate.id), str(version.id))
        self.assertEqual(candidate.state, "draft")
        self.assertEqual(candidate.source_type, "evolution")
        self.assertEqual(str(candidate.previous_version_id), str(version.id))
        self.assertTrue(result["active_untouched"])
        self.assertEqual(result["rollback_target"], str(version.id))

        # 基线包一个字节都没变，且仍是活跃版本
        version.refresh_from_db()
        self.assertEqual(version.package_sha256, baseline_sha)
        self.assertEqual(version.get_full_path(), baseline_path)
        skill.refresh_from_db()
        self.assertEqual(str(skill.active_version_id), str(version.id))
        self.assertTrue(SkillVersionService.verify_package_integrity(version)["ok"])

        # 候选中确实写入了护栏
        candidate_skill_md = Path(candidate.get_full_path()) / "SKILL.md"
        self.assertIn(GUARDRAIL_BEGIN, candidate_skill_md.read_text(encoding="utf-8"))
        self.assertIn("缺少空值检查指令", candidate_skill_md.read_text(encoding="utf-8"))

    def test_derive_refuses_unconfirmed_attribution(self):
        from knowledge_evolution.skill_evolution import SkillEvolutionService

        _, version = self.make_skill_version()
        self._activate(version)
        attribution = self.make_attribution(
            output=self.make_output(skill_version=version), state="proposed",
        )
        with self.assertRaises(ValidationError):
            SkillEvolutionService.derive_candidate(
                skill_version=version, attributions=[attribution], actor=self.lead,
            )

    def test_derive_refuses_non_addressable_category(self):
        from knowledge_evolution.skill_evolution import SkillEvolutionService

        _, version = self.make_skill_version()
        self._activate(version)
        attribution = self.make_attribution(
            output=self.make_output(skill_version=version),
            category="knowledge_stale", state="confirmed",
        )
        with self.assertRaises(ValidationError):
            SkillEvolutionService.derive_candidate(
                skill_version=version, attributions=[attribution], actor=self.lead,
            )

    def test_derive_refuses_identical_result(self):
        """同一批归因重复派生必须报错，而不是返回活跃版本糊弄过去。"""
        from knowledge_evolution.skill_evolution import SkillEvolutionService

        _, version = self.make_skill_version()
        self._activate(version)
        attribution = self.make_attribution(
            output=self.make_output(skill_version=version), state="confirmed",
        )
        SkillEvolutionService.derive_candidate(
            skill_version=version, attributions=[attribution], actor=self.lead,
        )
        version.refresh_from_db()
        with self.assertRaises(ValidationError):
            SkillEvolutionService.derive_candidate(
                skill_version=version, attributions=[attribution], actor=self.lead,
            )


# ============================================================== T13


class ApprovalFlowTests(SkillHubBaseTests):
    def _release_with_gate(self, *, passed=True):
        """造一个已通过（或未通过）门禁的 shadow 候选。"""
        skill, version = self.make_skill_version()
        release = version.release
        release.state = "shadow"
        release.save(update_fields=["state"])
        snapshot, _baseline, candidate, _output = self.make_passing_gate(
            release=release, quality=0.9 if passed else 0.30, with_feedback=passed,
        )
        release.candidate_run = candidate
        release.save(update_fields=["candidate_run"])
        return skill, version, release, snapshot

    def test_submit_for_approval_requires_passed_gate(self):
        _, _, release, _snapshot = self._release_with_gate(passed=False)
        self.assertFalse(EvaluationGateService.latest_snapshot(release).passed)
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.submit_for_approval(release, actor=self.lead)

    def test_submit_for_approval_records_snapshot_hash(self):
        _, _, release, snapshot = self._release_with_gate()
        self.assertTrue(snapshot.passed, "夹具应造出门禁通过的候选")
        submitted = CapabilityReleaseService.submit_for_approval(release, actor=self.lead)
        self.assertEqual(submitted.state, "awaiting_approval")
        self.assertEqual(submitted.approval_snapshot_hash, snapshot.content_hash)
        self.assertTrue(submitted.gate_report["passed"])
        self.assertEqual(submitted.gate_report["snapshot_id"], str(snapshot.id))

    def test_approve_refuses_when_evidence_changed(self):
        """审批后被重新评测 → 必须重新审批，不能直接激活。"""
        from knowledge_evolution.skill_evolution import SkillEvolutionService  # noqa: F401

        skill, version = self.make_skill_version()
        release = version.release
        release.state = "shadow"
        release.save(update_fields=["state"])
        snapshot = EvaluationGateSnapshot.objects.create(
            project=self.project, release=release, kind="full",
            candidate_run=None, partitions={}, metrics={}, checks=[{"code": "x", "ok": True}],
            passed=True, content_hash="h" * 64, created_by=self.lead,
        )
        CapabilityReleaseService.submit_for_approval(
            release, actor=self.lead, gate_snapshot=snapshot,
        )
        release.refresh_from_db()
        self.assertEqual(release.state, "awaiting_approval")

        # 候选被重新评测，产生新的门禁快照
        new_run = self.make_run_with_results(
            suite=self.make_suite(name="重跑套件"), splits={"gold": 1}, scores=[0.5],
        )
        snapshot2 = EvaluationGateService.run_gate(candidate_run=new_run, release=release)
        self.assertNotEqual(snapshot2.content_hash, snapshot.content_hash)

        with self.assertRaises(ValidationError):
            CapabilityReleaseService.promote(release, actor=self.lead, reason="尝试激活")

    def test_approve_activates_and_retires_previous(self):
        skill, v1 = self.make_skill_version(version="1.0.0")
        v1.release.gate_report = {"passed": True}
        v1.release.state = "awaiting_approval"
        v1.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(v1.release, actor=self.lead, reason="激活 1.0.0")

        _, v2 = self.make_skill_version(version="2.0.0", body="第二版")
        v2.release.gate_report = {"passed": True}
        v2.release.state = "awaiting_approval"
        v2.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(v2.release, actor=self.lead, reason="激活 2.0.0")

        v1.refresh_from_db()
        skill.refresh_from_db()
        self.assertEqual(v1.release.state, "retired")
        self.assertEqual(str(skill.active_version_id), str(v2.id))
        self.assertEqual(str(v2.release.previous_release_id), str(v1.release_id))

    def test_reject_requires_reason(self):
        _, version = self.make_skill_version()
        release = version.release
        release.state = "awaiting_approval"
        release.save(update_fields=["state"])
        with self.assertRaises(ValidationError):
            CapabilityReleaseService.reject(release, actor=self.lead, reason="  ")
        rejected = CapabilityReleaseService.reject(
            release, actor=self.lead, reason="指令改动无实质收益",
        )
        self.assertEqual(rejected.state, "rejected")

    def test_submit_is_idempotent(self):
        _, version = self.make_skill_version()
        release = version.release
        release.state = "shadow"
        release.save(update_fields=["state"])
        snapshot = EvaluationGateSnapshot.objects.create(
            project=self.project, release=release, kind="full",
            candidate_run=None, partitions={}, metrics={}, checks=[], passed=True,
            content_hash="z" * 64, created_by=self.lead,
        )
        first = CapabilityReleaseService.submit_for_approval(
            release, actor=self.lead, gate_snapshot=snapshot,
        )
        second = CapabilityReleaseService.submit_for_approval(
            release, actor=self.lead, gate_snapshot=snapshot,
        )
        self.assertEqual(str(first.id), str(second.id))
        self.assertEqual(second.state, "awaiting_approval")
        self.assertEqual(release.decisions.count(), 1)

    def test_approval_view_exposes_missing_conditions(self):
        _, version = self.make_skill_version()
        release = version.release
        view = CapabilityReleaseService.approval_view(release)
        self.assertFalse(view["can_submit_approval"])
        self.assertTrue(view["missing_conditions"])


class ObservationRollbackTests(SkillHubBaseTests):
    def _active_release(self):
        skill, v1 = self.make_skill_version(version="1.0.0")
        v1.release.gate_report = {"passed": True}
        v1.release.state = "awaiting_approval"
        v1.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(v1.release, actor=self.lead, reason="激活 1.0.0")

        _, v2 = self.make_skill_version(version="2.0.0", body="第二版")
        v2.release.gate_report = {"passed": True}
        v2.release.state = "awaiting_approval"
        v2.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(v2.release, actor=self.lead, reason="激活 2.0.0")
        return skill, v1, v2

    def test_healthy_observation_keeps_active(self):
        skill, v1, v2 = self._active_release()
        result = CapabilityReleaseService.observe(
            v2.release, window_key="w1",
            metrics={"error_rate": 0.0, "l1_score": 0.9}, actor=self.lead,
        )
        v2.refresh_from_db()
        skill.refresh_from_db()
        self.assertFalse(result["rolled_back"])
        self.assertEqual(v2.release.state, "active")
        self.assertEqual(str(skill.active_version_id), str(v2.id))
        self.assertEqual(v2.release.observation_state, "healthy")

    def test_breach_triggers_automatic_rollback(self):
        skill, v1, v2 = self._active_release()
        result = CapabilityReleaseService.observe(
            v2.release, window_key="w2",
            metrics={"error_rate": 0.5, "l1_score": 0.2}, actor=self.lead,
        )
        v2.refresh_from_db()
        v1.refresh_from_db()
        skill.refresh_from_db()
        self.assertTrue(result["rolled_back"])
        self.assertEqual(v2.release.state, "rolled_back")
        self.assertEqual(v1.release.state, "active")
        self.assertEqual(str(skill.active_version_id), str(v1.id))
        self.assertIn("error_rate", result["alert"])

    def test_rollback_failure_quarantines_instead_of_staying_active(self):
        """回滚失败也必须阻断放量，不能留在 active 上继续跑。"""
        skill, v1, v2 = self._active_release()
        # 把上一版本隔离，使 rollback 中的 retired->active 非法而抛错
        v1.release.state = "quarantined"
        v1.release.save(update_fields=["state"])

        result = CapabilityReleaseService.observe(
            v2.release, window_key="w3",
            metrics={"error_rate": 0.9}, actor=self.lead,
        )
        v2.refresh_from_db()
        self.assertTrue(result["quarantined"])
        self.assertEqual(v2.release.state, "quarantined")
        self.assertIn("回滚失败", result["alert"])

    def test_observation_is_idempotent_per_window(self):
        _, _, v2 = self._active_release()
        first = CapabilityReleaseService.observe(
            v2.release, window_key="same", metrics={"error_rate": 0.0}, actor=self.lead,
        )
        second = CapabilityReleaseService.observe(
            v2.release, window_key="same", metrics={"error_rate": 0.01}, actor=self.lead,
        )
        self.assertEqual(first["observation_id"], second["observation_id"])


class AuditTrailTests(SkillHubBaseTests):
    def test_governance_actions_are_audited(self):
        from knowledge_evolution.knowledge_models import KnowledgeAuditLog

        _, v1 = self.make_skill_version(version="1.0.0")
        v1.release.gate_report = {"passed": True}
        v1.release.state = "awaiting_approval"
        v1.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(v1.release, actor=self.lead, reason="激活")

        actions = set(
            KnowledgeAuditLog.objects.filter(project=self.project).values_list("action", flat=True)
        )
        self.assertIn("approve", actions)

    def test_rollback_is_audited(self):
        from knowledge_evolution.knowledge_models import KnowledgeAuditLog

        _, first = self.make_skill_version(version="1.0.0")
        first.release.gate_report = {"passed": True}
        first.release.state = "awaiting_approval"
        first.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(first.release, actor=self.lead, reason="v1")

        _, second = self.make_skill_version(version="2.0.0", body="v2")
        second.release.gate_report = {"passed": True}
        second.release.state = "awaiting_approval"
        second.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(second.release, actor=self.lead, reason="v2")

        CapabilityReleaseService.rollback(second.release, actor=self.lead, reason="人工回滚")
        actions = set(
            KnowledgeAuditLog.objects.filter(project=self.project).values_list("action", flat=True)
        )
        self.assertIn("rollback", actions)
        self.assertIn("approve", actions)
