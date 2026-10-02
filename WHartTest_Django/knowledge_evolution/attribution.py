"""节点轨迹记录和规则优先的失败归因。"""
from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .trace_models import (
    CATEGORY_LAYER_MAP,
    LAYER_LABELS,
    LAYER_ORDER,
    ExecutionSpan,
    FailureAttribution,
)


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

    @staticmethod
    def record_human_edit(*, output, actor, before, after, comment=""):
        """只保存可审计的差异摘要与哈希，避免在 Span 中复制敏感正文。"""
        before_text = json.dumps(before, ensure_ascii=False, sort_keys=True, default=str)
        after_text = json.dumps(after, ensure_ascii=False, sort_keys=True, default=str)
        before_keys = set(before) if isinstance(before, dict) else set()
        after_keys = set(after) if isinstance(after, dict) else set()
        protocol = (output.metadata or {}).get("protocol") or {}
        return SpanRecorder.record(
            trace=output.trace, stage=output.task_type, step_type="human_edit",
            status="completed", workflow_id=protocol.get("workflow_id", ""),
            sequence=(output.trace.spans.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1,
            input_hash=hashlib.sha256(before_text.encode()).hexdigest(),
            output_hash=hashlib.sha256(after_text.encode()).hexdigest(),
            evidence=[{
                "added_keys": sorted(after_keys - before_keys),
                "removed_keys": sorted(before_keys - after_keys),
                "changed_keys": sorted(
                    key for key in before_keys & after_keys if before.get(key) != after.get(key)
                ) if isinstance(before, dict) and isinstance(after, dict) else [],
                "comment": str(comment)[:500],
            }],
            metadata={"actor_id": getattr(actor, "pk", None), "diff_size": abs(len(after_text) - len(before_text))},
        )


class CounterevidenceVerifier:
    """在生成归因前主动寻找反证，防止把已恢复节点误判为根因。"""

    @staticmethod
    def verify(*, span, spans, category):
        if span is None:
            return []
        later = [item for item in spans if item.sequence > span.sequence]
        counterevidence = []
        if span.status == "failed" and any(
            item.status == "completed" and item.step_type == span.step_type for item in later
        ):
            counterevidence.append({
                "type": "later_recovery", "span_id": str(span.id),
                "reason": "同类节点在后续重试中已成功",
            })
        if category == "retrieval_error" and span.evidence:
            counterevidence.append({
                "type": "retrieval_has_evidence", "span_id": str(span.id),
                "reason": "检索节点已返回证据，需核对是否为生成阶段使用错误",
            })
        return counterevidence


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
            counterevidence = CounterevidenceVerifier.verify(
                span=span, spans=spans, category=category,
            )
            if counterevidence:
                confidence = min(confidence, 0.6)
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
                    "counterevidence": counterevidence,
                },
            )
            results.append(attribution)
        return results

    @staticmethod
    @transaction.atomic
    def run_reverse_for_output(output):
        """沿统一产出协议的 parent_output_ids 向上游追溯失败节点。"""
        from .models import GenerationOutput
        queue = list(((output.metadata or {}).get("protocol") or {}).get("parent_output_ids") or [])
        visited = set()
        results = []
        while queue:
            parent_id = str(queue.pop(0))
            if parent_id in visited:
                continue
            visited.add(parent_id)
            parent = GenerationOutput.objects.filter(pk=parent_id, project=output.project).select_related("trace").first()
            if not parent:
                continue
            queue.extend(((parent.metadata or {}).get("protocol") or {}).get("parent_output_ids") or [])
            for attribution in AttributionService.run_for_output(parent):
                raw = f"{output.id}:upstream:{attribution.id}:{attribution.category}"
                linked, _ = FailureAttribution.objects.update_or_create(
                    fingerprint=hashlib.sha256(raw.encode()).hexdigest(),
                    defaults={
                        "project": output.project, "output": output, "span": attribution.span,
                        "workflow_id": ((output.metadata or {}).get("protocol") or {}).get("workflow_id", ""),
                        "category": attribution.category, "source": attribution.source,
                        "confidence": max(0.0, attribution.confidence - 0.1),
                        "hypothesis": f"上游 {parent.task_type} 阶段：{attribution.hypothesis}",
                        "evidence": [{"upstream_output_id": str(parent.id), "attribution_id": str(attribution.id)}],
                        "counterevidence": attribution.counterevidence,
                    },
                )
                results.append(linked)
        return results

    @staticmethod
    def decide(*, attribution, actor, accepted: bool, note: str = ""):
        """人工确认或驳回一条归因假设。

        ``note`` 会追加进 ``evidence``：确认是一句"我核过了"的断言，
        但事后需要能回答"当时核的是什么"，所以把结论与说明一起留痕。
        """
        attribution.state = "confirmed" if accepted else "rejected"
        attribution.confirmed_by = actor
        attribution.confirmed_at = timezone.now()
        if note:
            evidence = list(attribution.evidence or [])
            evidence.append({
                "type": "human_review",
                "accepted": bool(accepted),
                "note": str(note)[:1000],
                "reviewer_id": getattr(actor, "pk", None),
            })
            attribution.evidence = evidence
            attribution.save(update_fields=[
                "state", "confirmed_by", "confirmed_at", "evidence", "updated_at",
            ])
        else:
            attribution.save(update_fields=["state", "confirmed_by", "confirmed_at", "updated_at"])
        return attribution

    # ------------------------------------------------------- 上游版本反向追溯

    @staticmethod
    def trace_upstream_versions(output, *, max_depth: int = 8) -> dict:
        """沿 ``parent_output_ids`` 反向定位"产生错误的上游 Skill 版本"（R8）。

        为什么必须走产出链而不是只看当前产出：

        - 报告阶段出问题，根因常在测试用例或方案阶段。当前产出自己没有任何失败迹象，
          只看它会得到"无归因"。
        - 统一协议已经写了 ``parent_output_ids``，产出上也已经固化了
          ``skill_version`` / ``skill_package_sha256``（T08），所以这条链是**可走通的**，
          不需要额外埋点。

        返回 ``{"chain": [...], "responsible": {...} | None}``：

        - ``chain`` 从当前产出逐级向上，含每一级的阶段、版本、包哈希；
        - ``responsible`` 是最靠近"已确认归因"的那一级；若都没有确认归因则为 None，
          此时**不允许**据此生成候选（由 ``assert_no_unconfirmed`` 拦住）。
        """
        from .models import GenerationOutput

        chain: list[dict] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [
            (str(item), 1)
            for item in (((output.metadata or {}).get("protocol") or {}).get("parent_output_ids") or [])
        ]
        current = AttributionService._describe_output(output, depth=0)
        current["confirmed_attributions"] = list(
            output.failure_attributions.filter(state="confirmed").values("category", "layer", "hypothesis")
        )
        chain.append(current)

        while queue and len(chain) < max_depth:
            output_id, depth = queue.pop(0)
            if output_id in visited:
                continue
            visited.add(output_id)
            parent = (
                GenerationOutput.objects.filter(pk=output_id, project=output.project)
                .select_related("skill_version__skill", "capability")
                .first()
            )
            if parent is None:
                continue
            entry = AttributionService._describe_output(parent, depth=depth)
            entry["confirmed_attributions"] = list(
                parent.failure_attributions.filter(state="confirmed").values("category", "layer", "hypothesis")
            )
            chain.append(entry)
            queue.extend(
                (str(item), depth + 1)
                for item in (((parent.metadata or {}).get("protocol") or {}).get("parent_output_ids") or [])
            )

        responsible = next(
            (item for item in chain if item["confirmed_attributions"]), None,
        )
        return {"chain": chain, "responsible": responsible}

    @staticmethod
    def _describe_output(output, *, depth: int) -> dict:
        from .capability_registry import package_sha256_of

        protocol = (output.metadata or {}).get("protocol") or {}
        skill_version = output.skill_version
        return {
            "output_id": str(output.id),
            "depth": depth,
            "stage": protocol.get("stage") or output.task_type,
            "workflow_id": protocol.get("workflow_id", ""),
            "capability_id": str(output.capability_id or ""),
            "skill_id": str(getattr(skill_version, "skill_id", "") or ""),
            "skill_name": getattr(getattr(skill_version, "skill", None), "name", ""),
            "skill_version": getattr(skill_version, "version", ""),
            "skill_version_id": str(output.skill_version_id or ""),
            "package_sha256": package_sha256_of(output),
        }


def layering_overview(project_id) -> dict:
    """按八层汇总归因情况，供质量驾驶舱与 Skill Hub 展示"哪一层最常出问题"。

    只统计 ``confirmed``：未确认的假设不能拿来判断"问题集中在哪一层"，
    否则一次误报就会把某层标红。
    """
    from collections import Counter

    rows = (
        FailureAttribution.objects.filter(project_id=project_id)
        .values("layer", "state")
        .annotate(count=Count("id"))
    )
    confirmed = Counter()
    proposed = Counter()
    for row in rows:
        if row["state"] == "confirmed":
            confirmed[row["layer"]] += row["count"]
        elif row["state"] == "proposed":
            proposed[row["layer"]] += row["count"]

    return {
        "project_id": project_id,
        "layer_order": list(LAYER_ORDER),
        "layers": [
            {
                "layer": layer,
                "label": LAYER_LABELS.get(layer, layer),
                "confirmed": confirmed.get(layer, 0),
                "proposed": proposed.get(layer, 0),
                "categories": sorted(
                    category for category, mapped in CATEGORY_LAYER_MAP.items() if mapped == layer
                ),
            }
            for layer in LAYER_ORDER
        ],
        "total_confirmed": sum(confirmed.values()),
        "total_proposed": sum(proposed.values()),
    }


def assert_confirmed_attributions(attributions) -> list:
    """生成候选前的硬校验：未确认的归因不得派生可发布版本（R6）。"""
    confirmed = [item for item in attributions if item.state == "confirmed"]
    unconfirmed = [item for item in attributions if item.state != "confirmed"]
    if not confirmed:
        raise ValidationError(
            "没有任何已确认的归因，不能据此生成候选版本（需人工确认后才能派生）"
        )
    if unconfirmed:
        raise ValidationError(
            f"提交的 {len(unconfirmed)} 条归因尚未人工确认，不能与已确认归因混用生成候选"
        )
    return confirmed



class LLMAssistedAttributionService:
    """LLM 只生成待确认假设，不能覆盖确定性归因或自动进入优化。"""

    VALID_CATEGORIES = {value for value, _label in FailureAttribution.CATEGORY_CHOICES}

    def __init__(self, llm_factory=None):
        self.llm_factory = llm_factory or self._default_factory

    @staticmethod
    def _default_factory(config):
        from langgraph_integration.views import create_llm_instance
        return create_llm_instance(config, temperature=0)

    @transaction.atomic
    def run(self, output):
        from langgraph_integration.models import LLMConfig
        from .llm_judges import _json_object

        config = LLMConfig.objects.filter(is_active=True).first()
        if not config:
            raise ValueError("未配置激活的LLM，无法执行辅助归因")
        spans = list(output.trace.spans.order_by("sequence", "created_at"))
        feedback = list(output.feedback_events.values("signal", "reason_code", "comment"))
        deterministic = list(output.failure_attributions.filter(source="rule").values(
            "category", "hypothesis", "confidence", "counterevidence",
        ))
        trace_payload = [{
            "id": str(span.id), "sequence": span.sequence, "step_type": span.step_type,
            "status": span.status, "error_type": span.error_type,
            "error_message": span.error_message[:500], "has_evidence": bool(span.evidence),
        } for span in spans]
        prompt = (
            "你是质量失败归因审查员。先寻找反证，再提出最多3条可验证假设。"
            "不得覆盖确定性结果，不得把相关性当因果。"
            "仅返回JSON：{\"hypotheses\":[{\"category\":\"...\",\"confidence\":0到1,"
            "\"hypothesis\":\"...\",\"evidence\":[],\"counterevidence\":[]}]}\n"
            f"节点轨迹：{json.dumps(trace_payload, ensure_ascii=False)}\n"
            f"真实反馈：{json.dumps(feedback, ensure_ascii=False, default=str)}\n"
            f"确定性归因：{json.dumps(deterministic, ensure_ascii=False, default=str)}"
        )
        payload = _json_object(self.llm_factory(config).invoke(prompt))
        results = []
        span_by_id = {str(item.id): item for item in spans}
        for item in list(payload.get("hypotheses") or [])[:3]:
            category = str(item.get("category") or "")
            hypothesis = str(item.get("hypothesis") or "").strip()
            if category not in self.VALID_CATEGORIES or not hypothesis:
                continue
            span = span_by_id.get(str(item.get("span_id") or ""))
            raw = f"{output.id}:llm:{category}:{hypothesis}"
            attribution, _ = FailureAttribution.objects.update_or_create(
                fingerprint=hashlib.sha256(raw.encode()).hexdigest(),
                defaults={
                    "project": output.project, "output": output, "span": span,
                    "workflow_id": ((output.metadata or {}).get("protocol") or {}).get("workflow_id", ""),
                    "category": category, "source": "llm",
                    "confidence": max(0.0, min(0.8, float(item.get("confidence") or 0.5))),
                    "hypothesis": hypothesis,
                    "evidence": item.get("evidence") or [],
                    "counterevidence": item.get("counterevidence") or [],
                    "state": "proposed",
                },
            )
            results.append(attribution)
        return results
