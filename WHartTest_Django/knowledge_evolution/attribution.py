"""节点轨迹记录和规则优先的失败归因。"""
from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone

from .trace_models import ExecutionSpan, FailureAttribution


class SpanRecorder:
    @staticmethod
    def record(*, trace, stage, step_type, status="completed", parent_span=None,
               workflow_id="", sequence=0, **kwargs):
        span = ExecutionSpan(
            trace=trace, stage=stage, step_type=step_type, status=status,
            parent_span=parent_span, workflow_id=workflow_id, sequence=sequence,
            **kwargs,
        )
        span.full_clean()
        span.save()
        return span


class AttributionService:
    """确定性信号优先；LLM辅助归因将在后续任务中作为独立提供者接入。"""

    @staticmethod
    @transaction.atomic
    def run_for_output(output):
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = protocol.get("workflow_id", "")
        spans = list(output.trace.spans.order_by("sequence", "created_at"))
        signals = set(output.feedback_events.values_list("signal", flat=True))
        proposals = []

        for span in spans:
            if span.status != "failed":
                continue
            if span.step_type == "tool":
                proposals.append(("tool_error", span, 1.0, "工具调用节点明确失败"))
            elif span.step_type in {"retrieval", "graph_retrieval"}:
                proposals.append(("retrieval_error", span, 1.0, "检索节点明确失败"))
            elif span.step_type == "intent":
                proposals.append(("intent_error", span, 0.95, "意图理解节点失败"))
            elif span.step_type == "planning":
                proposals.append(("planning_error", span, 0.95, "任务规划节点失败"))
            elif span.step_type == "prompt":
                proposals.append(("prompt_error", span, 0.9, "Prompt组装节点失败"))
            elif span.step_type == "downstream":
                proposals.append(("downstream_execution_error", span, 1.0, "下游执行节点失败"))
            else:
                proposals.append(("generation_error", span, 0.8, "生成或校验节点失败"))

        if not proposals and signals & {"missed", "false_positive", "rejected"}:
            retrieval = next((span for span in spans if span.step_type in {"retrieval", "graph_retrieval"}), None)
            if retrieval and not retrieval.evidence:
                proposals.append(("retrieval_error", retrieval, 0.8, "负反馈且检索节点没有证据"))
            else:
                proposals.append(("generation_error", None, 0.65, "存在负反馈但未发现确定性基础设施错误"))
        if not proposals and "test_failed" in signals:
            proposals.append(("downstream_execution_error", None, 0.8, "下游测试结果失败"))

        results = []
        for category, span, confidence, hypothesis in proposals:
            raw = f"{output.id}:{span.id if span else ''}:{category}:{hypothesis}"
            fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            attribution, _ = FailureAttribution.objects.update_or_create(
                fingerprint=fingerprint,
                defaults={
                    "project": output.project, "output": output, "span": span,
                    "workflow_id": workflow_id, "category": category, "source": "rule",
                    "confidence": confidence, "hypothesis": hypothesis,
                    "evidence": [{
                        "span_id": str(span.id) if span else None,
                        "error_type": span.error_type if span else "",
                        "feedback_signals": sorted(signals),
                    }],
                },
            )
            results.append(attribution)
        return results

    @staticmethod
    def decide(*, attribution, actor, accepted: bool):
        attribution.state = "confirmed" if accepted else "rejected"
        attribution.confirmed_by = actor
        attribution.confirmed_at = timezone.now()
        attribution.save(update_fields=["state", "confirmed_by", "confirmed_at", "updated_at"])
        return attribution
