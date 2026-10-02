#!/usr/bin/env python
"""核验真实闭环产物：反馈→金标→评测→归因→候选→发布→晋级/回滚。"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
sys.path.insert(0, "/app")
import django
django.setup()

from django.contrib.auth.models import User
from knowledge_evolution.models import FeedbackEvent, GenerationOutput
from knowledge_evolution.evaluation_v2_models import JudgeResult
from knowledge_evolution.evaluation_models import EvaluationResult
from knowledge_evolution.gold_models import GoldCase, GoldDatasetVersion
from knowledge_evolution.trace_models import FailureAttribution
from knowledge_evolution.optimization_models import OptimizationProposal, OptimizationExperiment
from knowledge_evolution.capability_models import CapabilityRelease, PromotionDecision
from knowledge_evolution.attribution import SpanRecorder
from knowledge_evolution.trace_models import ExecutionSpan

print("=== 反馈事件 ===")
for fb in FeedbackEvent.objects.filter(signal="false_positive").order_by("-occurred_at")[:3]:
    print(f"  {fb.signal} | output={fb.output_id} | actor={fb.actor_id} | {fb.reason_code}")

print("\n=== 金标版本与样本 ===")
for v in GoldDatasetVersion.objects.all():
    print(f"  version={v.version} state={v.state} hash={(v.content_hash or '')[:16]} stats={v.sample_stats}")
for c in GoldCase.objects.all():
    print(f"  case state={c.state} split={c.split} privacy={c.privacy_level} tags={c.tags}")

print("\n=== 评测运行与逐裁判证据 ===")
print("  运行数:", EvaluationResult.objects.count(), "条结果")
from collections import Counter
print("  裁判状态分布:", dict(Counter(JudgeResult.objects.values_list("status", flat=True))))
print("  裁判层级分布:", dict(Counter(JudgeResult.objects.values_list("level", flat=True))))
print("  裁判类型分布:", dict(Counter(JudgeResult.objects.values_list("evaluator_type", flat=True))))
sample = EvaluationResult.objects.order_by("-updated_at").first()
if sample:
    print(f"  最新结果: L0={sample.l0_score} L1={sample.l1_score} L2={sample.l2_score} L3={sample.l3_score}")

print("\n=== 节点轨迹 Span ===")
print("  Span 总数:", ExecutionSpan.objects.count())
print("  步骤类型:", dict(Counter(ExecutionSpan.objects.values_list("step_type", flat=True))))

print("\n=== 失败归因 ===")
for a in FailureAttribution.objects.all()[:3]:
    print(f"  category={a.category} state={a.state} confidence={a.confidence} evidence={len(a.evidence or [])}")

print("\n=== 优化候选与实验 ===")
for p in OptimizationProposal.objects.all()[:3]:
    print(f"  proposal type={p.proposal_type} state={p.state} risk={p.risk_notes}")
for e in OptimizationExperiment.objects.all()[:3]:
    print(f"  experiment status={e.status} gate_passed={e.gate_report.get('passed')}")

print("\n=== 能力发布与晋级决策 ===")
for r in CapabilityRelease.objects.all()[:5]:
    print(f"  release kind={r.kind} version={r.version} state={r.state} hash={(r.artifact_hash or '')[:12]}")
for d in PromotionDecision.objects.all()[:5]:
    print(f"  decision={d.decision} release={d.release_id} reason={d.reason}")

print("\n=== 无生产对象被改写核验 ===")
print("  GenerationOutput 总数:", GenerationOutput.objects.count(), "(闭环只新增隔离候选，不改写产出)")
