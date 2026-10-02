#!/usr/bin/env python
"""真实用例审查 Badcase 端到端闭环演练。

链路：真实产出 -> 用户反馈 -> 金标候选 -> 双人标注 -> 冻结
      -> 金标回放评测(L0-L3) -> 失败归因 -> 人工确认
      -> 优化候选 -> 物化隔离发布 -> 赛马门禁 -> 人工晋级 -> 回滚
每步失败只记录、不中断，便于定位断点。
"""
import os
import sys
import traceback

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
sys.path.insert(0, "/app")
import django
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone

from knowledge_evolution.models import (
    CapabilityDefinition,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
)
from knowledge_evolution.evaluation_models import EvaluationResult, EvaluationRun
from knowledge_evolution.evaluation_v2_models import EvaluationRubric, JudgeResult
from knowledge_evolution.feedback import FeedbackService
from knowledge_evolution.gold import GoldAnnotationService, GoldCandidateService, GoldVersionService
from knowledge_evolution.gold_models import GoldCase, GoldDataset, GoldDatasetVersion
from knowledge_evolution.evaluators import LayeredEvaluationService
from knowledge_evolution.attribution import AttributionService
from knowledge_evolution.trace_models import FailureAttribution
from knowledge_evolution.optimization import OptimizationMaterializationService, OptimizationProposalService
from knowledge_evolution.optimization_models import OptimizationExperiment, OptimizationProposal
from knowledge_evolution.experiments import GoldReplayService, OptimizationExperimentService
from knowledge_evolution.capabilities import CapabilityReleaseService
from knowledge_evolution.capability_models import CapabilityRelease, PromotionDecision

STEPS = []


def step(name, fn):
    print(f"\n=== {name} ===")
    try:
        result = fn()
        print("OK:", result)
        STEPS.append((name, "OK", str(result)[:200]))
        return result
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", type(exc).__name__, exc)
        traceback.print_exc()
        STEPS.append((name, "FAIL", f"{type(exc).__name__}: {exc}"[:200]))
        return None


class StubPromptOptimizer:
    """演练用桩：不调用真实 LLM，仅返回保留原 Prompt 的增量候选。"""

    def generate(self, proposal):
        return {
            "prompt": "【演练】在原 Prompt 上补充：输出前执行证据一致性与反证校验",
            "diff": {"mode": "append_only", "changes": ["补充证据一致性校验", "补充反证校验"]},
            "risks": ["演练桩件，未调用真实 LLM"],
            "generator_model": "stub-prompt-optimizer",
        }


def build_executor(output, suffix=""):
    content = (output.content or "")[:20000] + suffix

    def executor(input_payload):
        return {
            "output": content,
            "citations": [f"case-review:{output.task_id}"],
            "token_usage": 1200,
        }

    return executor


def main():
    target_id = os.environ.get("WHARTTEST_OUTPUT_ID", "").strip()
    if target_id:
        output = GenerationOutput.objects.filter(pk=target_id).first()
    else:
        output = GenerationOutput.objects.filter(task_type="case_review").order_by("-created_at").first()
    if not output:
        print("没有 case_review 产出，终止")
        return
    print("目标产出:", output.id, "| task_id(review):", output.task_id)
    actor = User.objects.filter(is_staff=True).first() or User.objects.first()
    reviewer = User.objects.filter(username="closed_loop_reviewer").first()
    if not reviewer:
        reviewer, _ = User.objects.get_or_create(
            username="closed_loop_reviewer",
            defaults={"email": "reviewer@example.com", "is_staff": True},
        )
        reviewer.set_password("closed-loop-reviewer")
        reviewer.save()
    print("产出:", output.id, "| 项目:", output.project_id)
    print("初标人:", actor.username, "| 复核人:", reviewer.username)

    def _feedback():
        existing = FeedbackEvent.objects.filter(
            output=output, signal="false_positive", actor=actor,
        ).order_by("-occurred_at").first()
        if existing:
            return f"复用已有反馈 {existing.id}（防刷/幂等生效）"
        return FeedbackService.record_for_output(
            output=output, signal="false_positive", actor=actor, actor_type="user",
            reason_code="finding_not_applicable",
            comment="真实业务反馈：该问题在当前版本已不适用，属于误报",
            detail={"sheet": "Sheet1", "row": 3, "case_id": "TC-001", "reason": "业务规则已变更"},
        )

    fb = step("1. 写入真实用户反馈 false_positive", _feedback)
    if not fb:
        return
    if isinstance(fb, str):
        fb = FeedbackEvent.objects.filter(
            output=output, signal="false_positive", actor=actor,
        ).order_by("-occurred_at").first()

    def _dataset():
        dataset, _ = GoldDataset.objects.get_or_create(
            project=output.project, name="真实用例审查金标集", task_type="case_review",
            defaults={"description": "从真实用例审查反馈生成的金标资产", "owner": actor, "created_by": actor},
        )
        version, _ = GoldDatasetVersion.objects.get_or_create(
            dataset=dataset, version="v1.0-real", defaults={"state": "draft", "created_by": actor},
        )
        return f"dataset={dataset.id} version={version.id} state={version.state}"

    step("2. 创建金标数据集与版本", _dataset)
    version = GoldDatasetVersion.objects.filter(dataset__name="真实用例审查金标集").first()

    case = step(
        "3. 从反馈生成金标候选",
        lambda: GoldCandidateService.from_feedback(version=version, feedback=fb, actor=actor, split="fresh"),
    )

    def _submit_round(round_name, who, comment):
        existing = case.annotations.filter(round=round_name).first()
        if existing:
            return f"复用已有{round_name}标注 {existing.id}"
        return GoldAnnotationService.submit(
            case=case, round_name=round_name, actor=who,
            answer={"conclusion": "false_positive", "severity": "low"},
            rubric_scores={"accuracy": 0.2, "completeness": 0.5},
            evidence=[{"type": "feedback", "id": str(fb.id)}],
            conclusion="accepted", comment=comment,
        )

    step("4a. 初标（测试执行人员）", lambda: _submit_round("primary", actor, "初标：误报成立"))
    step("4b. 复核（另一人）", lambda: _submit_round("review", reviewer, "复核：同意误报"))

    frozen = step(
        "5. 冻结金标版本（生成内容哈希，禁止静默修改）",
        lambda: GoldVersionService.freeze(version=version, actor=actor),
    )
    if frozen:
        version.refresh_from_db()
        print("版本快照:", version.sample_stats, "| hash:", version.content_hash[:16])

    cap = step("6a. 创建能力定义", lambda: CapabilityDefinition.objects.get_or_create(
        project=output.project, name="用例审查能力",
        defaults={"kind": "skill", "evaluation_mode": "single", "stages": ["case_review"], "created_by": actor},
    )[0])
    rubric = step("6b. 创建评测量表", lambda: EvaluationRubric.objects.get_or_create(
        project=output.project, name="用例审查误报检测",
        defaults={
            "task_type": "case_review",
            "dimensions": [{"key": "accuracy", "weight": 0.6}, {"key": "completeness", "weight": 0.4}],
            "required_items": ["问题说明", "修改建议"],
            "forbidden_items": ["臆造需求"],
            "created_by": actor,
        },
    )[0])

    baseline_run = step(
        "7a. 金标回放（基线运行）",
        lambda: GoldReplayService.run(
            version=version, executor=build_executor(output), actor=actor, name="真实闭环-基线",
        ),
    )

    def _layered():
        result = EvaluationResult.objects.filter(run=baseline_run).order_by("case__case_number").first()
        LayeredEvaluationService.evaluate(evaluation_result=result, output=output, rubric=rubric)
        result.refresh_from_db()
        return (
            f"L0={result.l0_score} L1={result.l1_score} L2={result.l2_score} L3={result.l3_score} "
            f"判定证据={JudgeResult.objects.filter(evaluation_result=result).count()} 条"
        )

    step("7b. L0-L3 分层评测并持久化逐裁判证据", _layered)

    attr_list = step("8a. 确定性失败归因", lambda: list(AttributionService().run_for_output(output)))
    if not attr_list:
        print("没有产生归因，终止")
        return
    for item in attr_list:
        print("归因:", item.category, "| 置信度:", item.confidence, "| 状态:", item.state)

    def _confirm():
        for item in attr_list:
            AttributionService.decide(attribution=item, actor=actor, accepted=True)
        return f"已确认 {len(attr_list)} 条归因"

    step("8b. 人工确认归因", _confirm)

    def _generate():
        from knowledge_evolution.optimization import TYPE_BY_CATEGORY
        usable = [item for item in attr_list if item.category in TYPE_BY_CATEGORY]
        if not usable:
            raise ValueError(f"没有可映射到优化类型的归因，类别={[i.category for i in attr_list]}")
        return OptimizationProposalService.generate(attributions=usable, actor=actor, capability=cap)

    proposals = step("9a. 生成优化候选", _generate)
    proposal = proposals[0] if proposals else None
    if proposal:
        print("候选类型:", proposal.proposal_type, "| 标题:", proposal.title)

    release = step(
        "9b. 物化为隔离候选发布（不修改生产对象）",
        lambda: OptimizationMaterializationService.materialize(
            proposal=proposal, actor=actor, prompt_optimizer=StubPromptOptimizer(),
        ) if proposal else None,
    )
    if release:
        print("发布单元:", release.id, "| kind:", release.kind, "| state:", release.state)

    experiment = step(
        "10. 赛马门禁（基线 vs 候选，同一冻结快照）",
        lambda: OptimizationExperimentService.run(
            proposal=proposal, gold_version=version,
            baseline_executor=build_executor(output),
            candidate_executor=build_executor(output, "\n[候选：过滤误报，补充证据引用]"),
            candidate_release=release, actor=actor,
        ) if (proposal and release) else None,
    )
    if experiment:
        print("实验状态:", experiment.status, "| 门禁通过:", experiment.gate_report.get("passed"))
        print("硬门禁:", experiment.gate_report.get("hard_gates"))

    step("11a. 人工晋级（测试负责人审批）", lambda: CapabilityReleaseService.promote(
        release, actor=actor, reason="测试负责人审批通过（真实闭环演练）",
    ) if release else None)
    step("11b. 回滚演练（恢复上一生产版本）", lambda: CapabilityReleaseService.rollback(
        release, actor=actor, reason="演练回滚，验证恢复能力",
    ) if release else None)

    print("\n=== 闭环产物统计 ===")
    print("FeedbackEvent:", FeedbackEvent.objects.filter(output=output).count())
    print("GoldDataset:", GoldDataset.objects.count(), "| GoldCase:", GoldCase.objects.count())
    print("EvaluationRun:", EvaluationRun.objects.count(), "| EvaluationResult:", EvaluationResult.objects.count())
    print("JudgeResult:", JudgeResult.objects.count())
    print("FailureAttribution:", FailureAttribution.objects.count())
    print("OptimizationProposal:", OptimizationProposal.objects.count(), "| Experiment:", OptimizationExperiment.objects.count())
    print("CapabilityRelease:", CapabilityRelease.objects.count(), "| PromotionDecision:", PromotionDecision.objects.count())

    print("\n=== 步骤结果 ===")
    ok = sum(1 for _, s, _ in STEPS if s == "OK")
    for name, status, detail in STEPS:
        print(f"[{status}] {name} :: {detail}")
    print(f"\n成功 {ok}/{len(STEPS)} 步")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
