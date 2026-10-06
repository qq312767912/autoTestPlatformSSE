"""节点轨迹记录和规则优先的失败归因。"""
from __future__ import annotations

import hashlib
import json
import re

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


# ---------------------------------------------------------------------------
# 执行链路（T08 / §5、§12）
# ---------------------------------------------------------------------------

#: 业务摘要分组 → 对应的 Span 步骤类型。
#:
#: 飞轮阶段卡展示的是"这一轮 Agent 干了什么"，用业务词而不是 ``step_type``：
#: ``graph_retrieval`` 对使用者没有意义，"检索"才有。分组只做展示映射，
#: 不参与任何判定——判定一律读 ``status`` 与 ``step_type`` 原文。
BUSINESS_STEP_GROUPS: dict[str, tuple[str, ...]] = {
    "input": ("intent", "planning", "prompt"),
    "retrieval": ("retrieval", "graph_retrieval"),
    "tool": ("tool",),
    "file": ("handoff", "downstream"),
    "validation": ("validation",),
    "human_edit": ("human_edit",),
}

GROUP_LABELS: dict[str, str] = {
    "input": "输入理解",
    "retrieval": "知识检索",
    "tool": "工具调用",
    "file": "文件与下游",
    "validation": "结果校验",
    "human_edit": "人工修改",
    "failure": "失败",
}

#: 敏感键名（小写包含匹配）。命中的值一律替换成占位符，不落库、不外显。
#:
#: 用**包含匹配**而不是精确匹配：真实的凭据字段叫 ``api_key`` / ``apiKey`` /
#: ``X-Api-Key`` / ``access_token`` / ``Authorization`` 的都有，写白名单式精确
#: 匹配必然漏。宁可多遮一个无害字段，也不能把凭据透出去。
#:
#: ⚠️ ``api_key`` / ``api-key`` / ``apikey`` 三种写法**都要列**：HTTP 头里是
#: ``X-Api-Key``、Python 里常是 ``api_key``、前端配置里可能是 ``apiKey``。
#: 只列一种，另外两种就会原样透出去，而它们泄露的是同一个凭据。
SENSITIVE_KEY_MARKERS: frozenset[str] = frozenset({
    "token", "password", "passwd", "secret", "authorization", "auth",
    "api_key", "apikey", "api-key", "credential", "cookie", "session_key",
    "private_key", "access_key", "signature",
})

#: 敏感文本模式：``Bearer ...`` / ``sk-...`` / ``key=...`` 这类出现在自由文本里的凭据。
SENSITIVE_TEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***"),
    (r"(?i)(authorization\"?\s*[:=]\s*)\"[^\"]*\"", r"\1\"***\""),
    (r"sk-[A-Za-z0-9]{8,}", "sk-***"),
    (r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;\"'}]+", r"\1***"),
)

#: 错误摘要的最大长度。与 attempt 上的受限摘要同一口径：页面够看，又不至于
#: 把一段堆栈当成"结论"展示给人。
MAX_ERROR_SUMMARY = 300

REDACTED_PLACEHOLDER = "***"


class AttemptTraceService:
    """attempt 维度的实时执行链路（T08）。

    三条口径贯穿全模块，它们各自都对应一种"看起来正常、实际误导"的失败：

    1. **不等待最终产出**。Span 在 ``running`` 状态下就要能写、能查——
       否则"失败任务没有轨迹"永远修不好，因为失败任务恰恰不会有产出。
    2. **观测面不改执行面**。登记/查询全程只读，不碰 attempt 状态机，
       也不写 ``GenerationOutput``；出问题只影响展示。
    3. **不显示思维链、凭据与敏感参数**。对外一律走白名单字段 + 脱敏，
       而不是"把 metadata 整个吐出去、前端自己挑要显示什么"。
    """

    #: 对外可展示的 Span 字段白名单。
    SAFE_SPAN_FIELDS: tuple[str, ...] = (
        "id", "step_type", "status", "agent_name", "tool_name", "tool_version",
        "latency_ms", "token_usage", "sequence", "input_hash", "output_hash",
        "error_type", "started_at", "finished_at",
    )

    # ------------------------------------------------------------------ 写入

    @staticmethod
    def trace_for_attempt(attempt):
        """取（或建）该 attempt 的检索轨迹。

        attempt 与 ``RetrievalTrace`` 之间没有外键：轨迹表的语义是"一次检索"，
        而 attempt 是"一次运行"。硬加外键会让轨迹表承担两件事。这里用
        ``task_id = attempt:<uuid>`` 建立**确定性对应**——同一个 attempt 永远
        映射到同一条轨迹，失败重试（新 attempt）自然得到新的一条。
        """
        from .models import RetrievalTrace

        return RetrievalTrace.objects.get_or_create(
            project_id=attempt.project_id,
            task_type=attempt.stage,
            task_id=f"attempt:{attempt.pk}",
            defaults={
                "user": attempt.requested_by,
                "query": f"{attempt.stage} 阶段执行",
            },
        )[0]

    @classmethod
    def record_span(
        cls, *, attempt, step_type, status="running", sequence=None,
        evidence=None, metadata=None, **fields,
    ):
        """增量写入一个 Span。**不要求产出存在**。

        ``sequence`` 缺省时自动接在当前最大值之后：调用方（Skill 运行时/工具包装）
        通常不知道前面写了几条，而乱序会让"链路时序"这件事直接失真。
        """
        trace = cls.trace_for_attempt(attempt)
        if sequence is None:
            last = (
                trace.spans.order_by("-sequence").values_list("sequence", flat=True).first()
            )
            sequence = (last or 0) + 1
        else:
            try:
                sequence = int(sequence)
            except (TypeError, ValueError) as exc:
                raise ValueError("sequence 必须是整数") from exc

        payload = {
            key: value for key, value in fields.items()
            if value is not None and key in cls._span_model_fields()
        }
        span = ExecutionSpan.objects.create(
            trace=trace,
            stage=attempt.stage,
            step_type=step_type,
            status=status,
            workflow_id=attempt.workflow_id,
            sequence=sequence,
            evidence=evidence if isinstance(evidence, list) else [],
            # 元数据入库前先脱敏：脱敏放在**写**这一侧，是因为读侧漏一处就等于没脱敏，
            # 而写侧只有这一个入口。
            metadata=cls.redact_mapping(metadata or {}),
            **payload,
        )
        return span

    @staticmethod
    def _span_model_fields() -> frozenset[str]:
        return frozenset(
            field.name for field in ExecutionSpan._meta.get_fields()
            if getattr(field, "concrete", False)
        )

    # ------------------------------------------------------------------ 脱敏

    @staticmethod
    def redact_mapping(payload):
        """递归脱敏：敏感键名整值替换，其余字符串再过一遍文本模式。"""
        if isinstance(payload, dict):
            result = {}
            for key, value in payload.items():
                lowered = str(key).lower()
                if any(marker in lowered for marker in SENSITIVE_KEY_MARKERS):
                    result[key] = REDACTED_PLACEHOLDER
                else:
                    result[key] = AttemptTraceService.redact_mapping(value)
            return result
        if isinstance(payload, (list, tuple)):
            return [AttemptTraceService.redact_mapping(item) for item in payload]
        if isinstance(payload, str):
            return AttemptTraceService.redact_text(payload)
        return payload

    @staticmethod
    def redact_text(text: str) -> str:
        value = str(text or "")
        for pattern, replacement in SENSITIVE_TEXT_PATTERNS:
            value = re.sub(pattern, replacement, value)
        return value

    @classmethod
    def limit_error(cls, text: str) -> str:
        """错误摘要：脱敏 + 截断到首行附近。"""
        value = cls.redact_text(text or "").strip()
        if len(value) <= MAX_ERROR_SUMMARY:
            return value
        return value[:MAX_ERROR_SUMMARY] + "…"

    # ------------------------------------------------------------------ 读取

    @staticmethod
    def spans_for(attempt):
        return list(
            ExecutionSpan.objects
            .filter(trace__task_id=f"attempt:{attempt.pk}", stage=attempt.stage)
            .order_by("sequence", "created_at")
        )

    @classmethod
    def summary(cls, attempt, *, user=None) -> dict:
        """飞轮阶段卡需要的业务摘要（T08 实施内容第 3 条）。"""
        spans = cls.spans_for(attempt)

        groups: dict[str, dict] = {}
        for name, step_types in BUSINESS_STEP_GROUPS.items():
            matched = [span for span in spans if span.step_type in step_types]
            entry = {"count": len(matched), "failed": sum(1 for s in matched if s.status == "failed")}
            if name == "tool":
                entry["names"] = sorted({s.tool_name for s in matched if s.tool_name})
                entry["latency_ms"] = sum(int(s.latency_ms or 0) for s in matched)
            if name == "retrieval":
                entry["evidence_count"] = sum(len(s.evidence or []) for s in matched)
            groups[name] = entry

        failures = [span for span in spans if span.status == "failed"]
        running = [span for span in spans if span.status == "running"]
        last_failure = failures[-1] if failures else None

        return {
            "attempt_id": str(attempt.pk),
            "workflow_id": attempt.workflow_id,
            "stage": attempt.stage,
            "status": attempt.status,
            "channel": (attempt.detail or {}).get("channel", ""),
            "output_id": str(attempt.output_id or ""),
            "session_id": attempt.session_id,
            "started_at": _iso(attempt.started_at),
            "finished_at": _iso(attempt.finished_at),
            "duration_ms": _duration_ms(attempt.started_at, attempt.finished_at),
            "steps": {"total": len(spans), "running": len(running), "failed": len(failures)},
            "groups": groups,
            "labels": GROUP_LABELS,
            "failure": {
                "count": len(failures),
                # ``error_code`` 只放**真实的错误码**。Span 没报错码时留空，
                # 而不是拿 ``step_type`` 冒充——"校验步骤失败"与"错误码是 validation"
                # 是两件事，混在一起会让页面显示一个不存在的错误码。
                "error_code": attempt.error_code
                or (last_failure.error_type if last_failure else ""),
                "step_type": last_failure.step_type if last_failure else "",
                "group": _group_of(last_failure.step_type) if last_failure else "",
                "error_summary": cls.limit_error(
                    attempt.error_summary
                    or (last_failure.error_message if last_failure else "")
                ),
                "at": _iso(last_failure.finished_at or last_failure.created_at) if last_failure else "",
            },
            "attempt_error_summary": cls.limit_error(attempt.error_summary),
            "in_flight": attempt.status in {"dispatched", "running", "publishing"},
            "can_see_quotes": cls.can_see_quotes(user, attempt.project_id),
        }

    @classmethod
    def events(cls, attempt, *, user=None) -> list[dict]:
        """按时间顺序的事件流：Attempt 状态变化 + 每个 Span 的起止。"""
        events: list[dict] = []
        if attempt.created_at:
            events.append({
                "at": _iso(attempt.created_at), "kind": "attempt_created",
                "label": "派发执行尝试", "status": attempt.status,
            })
        for span in cls.spans_for(attempt):
            events.append({
                "at": _iso(span.started_at or span.created_at), "kind": "span",
                "span_id": str(span.id), "label": GROUP_LABELS.get(
                    _group_of(span.step_type), span.step_type
                ),
                "step_type": span.step_type, "status": span.status,
                "tool_name": span.tool_name, "latency_ms": int(span.latency_ms or 0),
                "error_summary": cls.limit_error(span.error_message) if span.status == "failed" else "",
            })
        if attempt.output_published_at:
            events.append({
                "at": _iso(attempt.output_published_at), "kind": "output_published",
                "label": "正式产出发布", "output_id": str(attempt.output_id or ""),
            })
        if attempt.finished_at:
            events.append({
                "at": _iso(attempt.finished_at), "kind": "attempt_finished",
                "label": "执行结束", "status": attempt.status,
                "error_summary": cls.limit_error(attempt.error_summary),
            })
        return sorted(events, key=lambda item: item["at"] or "")

    @classmethod
    def spans(cls, attempt, *, user=None) -> list[dict]:
        """下钻明细：工具名、状态、耗时、哈希、受限错误摘要（可脱敏）。"""
        rows: list[dict] = []
        for span in cls.spans_for(attempt):
            row = {field: _jsonable(getattr(span, field, None)) for field in cls.SAFE_SPAN_FIELDS}
            row["error_summary"] = cls.limit_error(span.error_message)
            row["metadata"] = cls.redact_mapping(span.metadata or {})
            row["evidence"] = cls.evidence_for_display(
                span.evidence or [], user=user, attempt_project_id=attempt.project_id,
            )
            row["group"] = _group_of(span.step_type)
            rows.append(row)
        return rows

    # -------------------------------------------------------- 原文二次鉴权

    @staticmethod
    def _is_project_member(user, project_id) -> bool:
        from projects.models import ProjectMember

        if not project_id:
            return False
        return ProjectMember.objects.filter(user=user, project_id=project_id).exists()

    @classmethod
    def can_see_quotes(cls, user, project_id) -> bool:
        """能否看到**本阶段所属项目**知识库的原句正文（页面据此给提示）。

        这只是粗判：真正的判定按每条引用**原文所在知识库的项目**逐个做，
        见 ``_can_read_document``。之所以还要有这个方法，是因为页面需要先知道
        "这次展示会不会全是打码的"，否则用户只会看到一片空白却不知道为什么。
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        return cls._is_project_member(user, project_id)

    @classmethod
    def _can_read_document(cls, user, document_id: str, attempt_project_id) -> bool:
        """按**原文所在项目**判定；解析不到文档时拒绝展示正文。

        解析不到就拒绝，而不是放行：原文权限的默认姿态是"不给"。放行会让
        "文档已被删除 / id 拼错 / 跨项目引用"这三种情况统统变成泄露通道。
        """
        from knowledge.models import Document

        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if not document_id:
            # 没有文档 id 的引用只有一段自由文本，无从判定归属 → 只在本项目内展示。
            return cls._is_project_member(user, attempt_project_id)
        project_id = (
            Document.objects.filter(pk=document_id)
            .values_list("knowledge_base__project_id", flat=True)
            .first()
        )
        if project_id is None:
            # 有文档 id 但解析不到：**拒绝**。放行会让"文档已删除 / id 拼错 /
            # 跨项目引用"三种情况统统变成泄露通道，而它们恰好是最常见的三种。
            return False
        return cls._is_project_member(user, project_id)

    @classmethod
    def evidence_for_display(cls, entries, *, user, attempt_project_id) -> list[dict]:
        """证据条目对外形态：**无原文权限时只保留定位信息，丢掉原句正文**。

        保留 document_id/chunk_id/offset/hash 而不是整条抹掉：没有原句，人仍然
        能判断"这条引用指向哪份文档的哪一段"，而把整条隐藏会让人以为"没有证据"。
        """
        result: list[dict] = []
        for entry in entries or []:
            if isinstance(entry, str):
                result.append({
                    "id": entry, "document_id": "", "document_version": "",
                    "chunk_id": "", "start_offset": None, "end_offset": None,
                    "content_hash": "", "masked": True, "quote": "",
                    "mask_reason": "no_document_reference",
                })
                continue
            if not isinstance(entry, dict):
                continue
            document_id = str(entry.get("document_id") or "")
            allowed = cls._can_read_document(user, document_id, attempt_project_id)
            result.append({
                "id": str(entry.get("id") or ""),
                "document_id": document_id,
                "document_version": str(entry.get("document_version") or ""),
                "chunk_id": str(entry.get("chunk_id") or ""),
                "start_offset": entry.get("start_offset"),
                "end_offset": entry.get("end_offset"),
                "content_hash": str(entry.get("content_hash") or ""),
                "masked": not allowed,
                "quote": str(entry.get("quote") or "") if allowed else "",
                "mask_reason": "" if allowed else "no_document_permission",
            })
        return result


def _group_of(step_type: str) -> str:
    for name, step_types in BUSINESS_STEP_GROUPS.items():
        if step_type in step_types:
            return name
    return ""


def _iso(moment) -> str:
    return moment.isoformat() if moment else ""


def _duration_ms(start, end) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


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

    @staticmethod
    def rewrite(attribution, *, actor, category: str = "", hypothesis: str = "", note: str = ""):
        """人工改写归因（T11：确认 / 改写 / 驳回三选一里的"改写"）。

        改写的语义是"我同意这里有问题，但不是它说的那个原因"。所以：

        - ``category`` 变了就**必须**重算 ``layer``：层是候选补丁改哪个文件的依据，
          留旧值会让"改成知识缺失"仍然去改 ``SKILL.md``；
        - ``source`` 置为 ``human``：之后能回答"这条结论是机器提的还是人写的"，
          而这是评估归因质量时唯一可靠的标签；
        - 状态**不在这里改**。改写是修正假设，确认是采纳假设，两件事混在一个
          动作里会让"我改完了但还没想好要不要用它"这个中间态无处安放。
        """
        category = str(category or "").strip()
        if category:
            valid = {value for value, _label in FailureAttribution.CATEGORY_CHOICES}
            if category not in valid:
                raise ValidationError(f"未知的归因类别：{category}")
            attribution.category = category
            attribution.layer = CATEGORY_LAYER_MAP.get(category, "skill_tool")
        if hypothesis:
            attribution.hypothesis = str(hypothesis)[:2000]

        evidence = list(attribution.evidence or [])
        evidence.append({
            "type": "human_rewrite",
            "category": category,
            "note": str(note or "")[:1000],
            "rewriter_id": getattr(actor, "pk", None),
        })
        attribution.evidence = evidence
        attribution.source = "human"
        attribution.save(update_fields=[
            "category", "layer", "hypothesis", "evidence", "source", "updated_at",
        ])
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


def assert_usable_for_content_patch(attributions) -> list:
    """环境问题不得用来生成 Skill 内容补丁（T11 / R6）。

    为什么这条要单独一道闸：环境类失败（超时、配额、网络、依赖缺失）看起来
    和"模型写得不好"一样都表现为产出不对，但改 Skill 一点用都没有——把环境
    故障写成 skill_content 补丁，既修不好问题，又会在下一轮评测里表现为
    "改了也没用"，把真正的环境问题掩盖掉。

    已确认但属于环境层的归因会被**剔除**而不是报错：一次运行里同时有环境
    故障与真实内容问题的情形很常见，为后者把整个派生停掉是过度反应。
    """
    usable: list = []
    for attribution in attributions:
        layer = getattr(attribution, "layer", "") or CATEGORY_LAYER_MAP.get(
            getattr(attribution, "category", ""), ""
        )
        if layer == "environment":
            continue
        usable.append(attribution)
    return usable


#: 人工「修改类型」→（归因类别, 基础置信度）。
#:
#: 这是**人工判断到责任层**的映射真值。写在这里而不是散在服务里，是因为它
#: 决定了候选补丁改哪个文件：改错一条，下一轮评测的对照就失去意义。
EDIT_CATEGORY_RULES: dict[str, tuple[str, float]] = {
    "内容错误": ("generation_error", 0.8),
    "粒度不当": ("generation_error", 0.7),
    "覆盖不足": ("generation_error", 0.75),
    "证据错误": ("retrieval_error", 0.8),
    "优先级不当": ("planning_error", 0.7),
    "重复或无效": ("planning_error", 0.7),
    "其他": ("generation_error", 0.5),
}

#: 环境类失败的识别标记（小写匹配 ``error_type`` 与 ``error_message``）。
#
#: 宁可多收一些：把环境故障误判成内容问题会污染 Skill 补丁，反过来只是让
#: 一条归因多带一个"疑似环境"的提示，人还能驳回。
ENVIRONMENT_ERROR_MARKERS: tuple[str, ...] = (
    "timeout", "timed out", "connection", "connect", "dns", "resolve",
    "quota", "rate limit", "429", "502", "503", "504", "proxy", "ssl",
    "no space", "disk", "memory", "unavailable", "refused",
)

#: 证据状态 → 归因类别。从图谱状态直接派生，不靠人填的那个词。
EVIDENCE_STATUS_RULES: dict[str, tuple[str, str]] = {
    "invalid": ("retrieval_error", "引用的原文无法定位"),
    "contradicted": ("knowledge_stale", "引用存在反证，依据本身已不成立"),
}


class HumanEditAttributionService:
    """把人工差异转成**待确认**归因（T11 / §8.2）。

    三条刻意为之的约束：

    1. **只创建 ``proposed``**。人改过不等于 Skill 有错（可能只是偏好），
       直接落成 confirmed 会跳过唯一一次能拦住错误补丁的机会。
    2. **反证先于结论**。功能层面"需求与知识都在"与"知识缺失"在产出上长得一样，
       区别只在图谱里那条"召回了但没被引用"的状态。归因必须先读它。
    3. **环境归因单独成条**。不与环境信号混合成一条"综合原因"，
       否则 ``assert_usable_for_content_patch`` 无法把它们分开剔除。
    """

    @classmethod
    def environmental_spans(cls, trace) -> list:
        """找出轨迹里疑似环境故障的失败节点。"""
        if trace is None:
            return []
        found = []
        for span in trace.spans.all():
            if getattr(span, "status", "") != "failed":
                continue
            haystack = f"{span.error_type or ''} {span.error_message or ''}".lower()
            if any(marker in haystack for marker in ENVIRONMENT_ERROR_MARKERS):
                found.append(span)
        return found

    @staticmethod
    def graph_signals(graph: dict | None) -> dict:
        """从图谱里取出"依据侧"的事实：有没有知识、召回了用没用上。"""
        status = {
            str(key): str((value or {}).get("status") or "")
            for key, value in ((graph or {}).get("evidence_status") or {}).items()
        }
        return {
            "total": len(status),
            "invalid": [key for key, value in status.items() if value == "invalid"],
            "contradicted": [key for key, value in status.items() if value == "contradicted"],
            # ``retrieved`` 的语义是"检索到了但没有任何业务项引用它"——
            # 这正是"知识在、没被用上"这一情形在图谱里的唯一痕迹。
            "retrieved_unused": [key for key, value in status.items() if value == "retrieved"],
            "verified": [key for key, value in status.items() if value == "verified"],
        }

    @classmethod
    def hypothesize(cls, item: dict, *, graph: dict | None) -> dict:
        """给一条人工差异定类别、层、置信度、反证与理由。

        返回 ``{"category", "confidence", "hypothesis", "evidence", "counterevidence"}``。
        """
        signals = cls.graph_signals(graph)
        category = ""
        confidence = 0.0
        reason = ""
        counterevidence: list[dict] = []

        category_text = str(item.get("edit_category") or "")
        rules = EDIT_CATEGORY_RULES.get(category_text)
        if rules:
            category, confidence = rules
            reason = f"人工将「{category_text}」归为待确认问题"

        # 证据类差异优先按**图谱状态**纠正人工填的那个词：
        # "证据错误"是人的印象，invalid / contradicted / retrieved 才是事实。
        if "evidence_error" in (item.get("kinds") or []):
            evidence_ids = list(item.get("evidence_ids") or [])
            hit = next(
                (code for code in ("invalid", "contradicted") if any(
                    evidence_id in signals[code] for evidence_id in evidence_ids
                )),
                "",
            )
            if hit:
                category, why = EVIDENCE_STATUS_RULES[hit]
                confidence = max(confidence, 0.85)
                reason = why
            elif evidence_ids and all(
                evidence_id in signals["verified"] for evidence_id in evidence_ids
            ):
                # 引用本身定位得到，人却判"证据错误" → 记一条反证，压低置信度。
                counterevidence.append({
                    "type": "evidence_locatable",
                    "evidence_ids": evidence_ids,
                    "reason": "引用可定位到原文，需确认是依据不当还是使用不当",
                })
                confidence = min(confidence or 0.6, 0.6)

        # 缺口类：区分"知识缺失"与"知识在但没被用上"。
        wants_coverage = (
            "requirement_gap" in (item.get("kinds") or [])
            or category_text == "覆盖不足"
        )
        if wants_coverage and item.get("requirement_ids"):
            if signals["retrieved_unused"] or signals["verified"]:
                category = "prompt_error"
                confidence = max(confidence, 0.7)
                reason = (
                    "需求与相关原文都已召回，但未被本方案项采用"
                    "（知识缺口已被排除）"
                )
                counterevidence.append({
                    "type": "knowledge_available",
                    "evidence_ids": signals["retrieved_unused"] or signals["verified"],
                    "reason": "相关原文已进入上下文，问题不在知识缺失",
                })
            else:
                category = "knowledge_missing"
                confidence = max(confidence, 0.8)
                reason = "该需求关联的知识库中没有召回到任何可用原文"

        if not category:
            if "removed" in (item.get("kinds") or []):
                category, confidence = "generation_error", 0.6
                reason = "整条方案项被人删除，按生成质量归因"
            elif "modified" in (item.get("kinds") or []):
                category, confidence = "generation_error", 0.6
                reason = "方案项被人工修改，未声明修改类型"
            else:
                category, confidence = "generation_error", 0.4
                reason = "存在人工改动，证据不足以进一步定位"

        hypothesis = (
            f"方案项 {item.get('item_id')}「{item.get('title') or ''}」：{reason}"
        )
        if item.get("field_diffs"):
            changed = "、".join(diff["column"] for diff in item["field_diffs"])
            hypothesis += f"；涉及字段：{changed}"
        if item.get("edit_content"):
            hypothesis += f"；人工说明：{str(item['edit_content'])[:200]}"

        return {
            "category": category,
            "confidence": round(float(confidence), 4),
            "hypothesis": hypothesis,
            "reason": reason,
            "evidence": [{
                "kind": "human_edit",
                "item_id": item.get("item_id"),
                "verdict": item.get("verdict"),
                "edit_category": item.get("edit_category"),
                "field_diffs": item.get("field_diffs") or [],
                "evidence_ids": item.get("evidence_ids") or [],
                "requirement_ids": item.get("requirement_ids") or [],
                "graph_signals": {
                    key: value for key, value in signals.items() if value
                },
            }],
            "counterevidence": counterevidence,
        }

    @classmethod
    @transaction.atomic
    def run_for_output(cls, output, *, stage_result, rows, graph=None, attachments=()) -> list:
        """对一份产出跑人工差异归因，返回新建/更新的（``proposed``）归因。

        幂等：指纹由 ``产出 + 方案项 + 类别`` 派生，同一份报告重复解析只会
        更新同一条，不会每上传一次就多一批待确认项。
        """
        from .stage_diff import build_stage_diff

        diff = build_stage_diff(stage_result, rows, attachments=attachments)
        workflow_id = ((output.metadata or {}).get("protocol") or {}).get("workflow_id", "")
        results: list = []

        for item in diff["items"]:
            if not item["kinds"]:
                continue
            proposal = cls.hypothesize(item, graph=graph)
            attribution = cls._upsert(
                output=output, workflow_id=workflow_id,
                key=f"{item['item_id']}:{proposal['category']}",
                proposal=proposal,
            )
            results.append(attribution)

        for span in cls.environmental_spans(getattr(output, "trace", None)):
            # 只记了 error_type、没记 error_message 的 span 很常见（工具直接在
            # error_type 里写 "timeout"）。取首行前必须判空，否则一次"环境失败"
            # 会把归因整条链路崩掉——而这恰恰是它最该被记下来的时刻。
            detail_lines = (span.error_message or "").strip().splitlines()
            proposal = {
                "category": "environment_error",
                "confidence": 1.0,
                "hypothesis": (
                    f"执行环境失败（{span.error_type or '未知'}）："
                    f"{detail_lines[0][:200] if detail_lines else '无错误详情'}"
                ),
                "evidence": [{
                    "kind": "execution_span",
                    "span_id": str(span.id),
                    "step_type": span.step_type,
                    "error_type": span.error_type,
                }],
                "counterevidence": [],
            }
            attribution = cls._upsert(
                output=output, workflow_id=workflow_id,
                key=f"span:{span.id}:{proposal['category']}",
                proposal=proposal,
            )
            results.append(attribution)
        return results

    @classmethod
    def _upsert(cls, *, output, workflow_id: str, key: str, proposal: dict):
        raw = f"human-edit:{output.id}:{key}"
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        # ``layer`` 显式写入而不是留给 ``save()`` 兜底：``save(update_fields=...)``
        # 不会把 ``save()`` 里补的值落库，留空会让改写路径与创建路径的层不一致。
        defaults = {
            "project": output.project, "output": output,
            "workflow_id": workflow_id,
            "category": proposal["category"],
            "layer": CATEGORY_LAYER_MAP.get(proposal["category"], "skill_tool"),
            "source": "rule",
            "confidence": proposal["confidence"],
            "hypothesis": proposal["hypothesis"],
            "evidence": proposal["evidence"],
            "counterevidence": proposal["counterevidence"],
        }
        attribution, created = FailureAttribution.objects.get_or_create(
            fingerprint=fingerprint,
            # 只创建待确认：刷新一次报告就自动变成"已确认"的话，
            # 人工确认这道闸门等于不存在。
            defaults={**defaults, "state": "proposed"},
        )
        if not created:
            # 已存在则**只刷新内容，不动 state**：人工已经确认或驳回的结论，
            # 不能被一次"重新归因"悄悄重置回待确认 —— 那会让确认这道闸门
            # 变成"随时可撤销"的摆设，也会让驳回过的假设反复回到候选池。
            for field, value in defaults.items():
                setattr(attribution, field, value)
            attribution.save(update_fields=[*defaults, "updated_at"])
        return attribution



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
