"""任务 13：将重复、可归因的反馈蒸馏为待审知识候选。

该服务只生成 KnowledgeCandidate，不发布知识，不修改 Prompt/Skill/Agent。
原始对话、查询和生成正文不会复制到候选中。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from .knowledge_models import KnowledgeCandidate
from .models import FeedbackEvent


OBJECTIVE_SIGNALS = {
    "defect_confirmed", "false_positive", "missed", "test_passed",
    "test_failed", "merged", "reverted",
}
NEGATIVE_SIGNALS = {"rejected", "false_positive", "missed", "test_failed", "reverted"}
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization|api[-_ ]?key|token|cookie|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/]+=*"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
)


@dataclass(frozen=True)
class DistillationResult:
    created: tuple[KnowledgeCandidate, ...]
    updated: tuple[KnowledgeCandidate, ...]
    skipped_groups: int


class ExperienceDistiller:
    """按任务类型、信号方向和原因聚合同类反馈。"""

    VERSION = "experience-distiller-v1"

    def __init__(self, *, project_id: int, task_type: str,
                 minimum_distinct_tasks: int = 3,
                 minimum_objective_tasks: int = 2):
        self.project_id = project_id
        self.task_type = task_type
        self.minimum_distinct_tasks = max(1, int(minimum_distinct_tasks))
        self.minimum_objective_tasks = max(1, int(minimum_objective_tasks))

    def run(self) -> DistillationResult:
        groups = self._group(self._feedback_queryset())
        created, updated = [], []
        skipped = 0
        for key, events in groups.items():
            if not self._eligible(events):
                skipped += 1
                continue
            candidate, was_created = self._upsert_candidate(key, events)
            (created if was_created else updated).append(candidate)
        return DistillationResult(tuple(created), tuple(updated), skipped)

    def _feedback_queryset(self) -> Iterable[FeedbackEvent]:
        return FeedbackEvent.objects.filter(
            project_id=self.project_id,
        ).filter(
            output__task_type=self.task_type,
        ).select_related("output", "trace", "actor").order_by("created_at")

    @staticmethod
    def _direction(signal: str) -> str:
        return "negative" if signal in NEGATIVE_SIGNALS else "positive"

    def _group(self, events: Iterable[FeedbackEvent]):
        groups = defaultdict(list)
        for event in events:
            reason = (event.reason_code or "unspecified").strip().lower()
            key = (self.task_type, self._direction(event.signal), reason)
            groups[key].append(event)
        return groups

    def _eligible(self, events: list[FeedbackEvent]) -> bool:
        distinct_tasks = {self._task_key(event) for event in events}
        objective_tasks = {
            self._task_key(event) for event in events
            if event.signal in OBJECTIVE_SIGNALS
        }
        return (
            len(distinct_tasks) >= self.minimum_distinct_tasks
            or len(objective_tasks) >= self.minimum_objective_tasks
        )

    @staticmethod
    def _task_key(event: FeedbackEvent) -> str:
        output = event.output
        if output and output.task_id:
            return f"{output.task_type}:{output.task_id}"
        trace = event.trace
        if trace and trace.task_id:
            return f"{trace.task_type}:{trace.task_id}"
        return f"feedback:{event.pk}"

    @staticmethod
    def _sanitize(text: str) -> str:
        value = " ".join((text or "").split())[:500]
        for pattern in SENSITIVE_PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value

    def _upsert_candidate(self, key, events):
        task_type, direction, reason_code = key
        event_ids = sorted(str(event.pk) for event in events)
        task_keys = sorted({self._task_key(event) for event in events})
        objective_count = sum(event.signal in OBJECTIVE_SIGNALS for event in events)
        signal_counts = defaultdict(int)
        for event in events:
            signal_counts[event.signal] += 1
        comments = []
        for event in events:
            comment = self._sanitize(event.comment)
            if comment and comment not in comments:
                comments.append(comment)
            if len(comments) >= 5:
                break
        confidence = min(
            0.95,
            0.5 + 0.07 * len(task_keys) + 0.05 * objective_count,
        )
        identity = f"{self.project_id}:{task_type}:{direction}:{reason_code}"
        dedup_key = "distill:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        payload = {
            "schema_version": "experience-candidate/v1",
            "title": f"{task_type} · {reason_code} · {direction}",
            "task_type": task_type,
            "direction": direction,
            "reason_code": reason_code,
            "experience": {
                "trigger": f"出现 {reason_code} 类型反馈",
                "action": "复核输入证据、检索引用和最终结论，并用相同样本回放验证",
                "expected_result": "降低同类负反馈" if direction == "negative" else "保持可复用的成功模式",
                "counterexample_required": direction == "negative",
            },
            "statistics": {
                "event_count": len(events),
                "distinct_task_count": len(task_keys),
                "objective_signal_count": objective_count,
                "signals": dict(sorted(signal_counts.items())),
            },
            "anonymized_notes": comments,
        }
        evidence = [
            {
                "type": "feedback_event",
                "feedback_id": str(event.pk),
                "task_key": self._task_key(event),
                "signal": event.signal,
                "objective": event.signal in OBJECTIVE_SIGNALS,
            }
            for event in events
        ]
        defaults = {
            "project_id": self.project_id,
            "kind": "experience",
            "origin": "distillation",
            "payload": payload,
            "level": "L3",
            "confidence": confidence,
            "evidence": evidence,
            "feedback_event_ids": event_ids,
            "state": "pending",
            "extracted_by": self.VERSION,
            "prompt_version": "not-applicable",
        }
        with transaction.atomic():
            candidate, created = KnowledgeCandidate.objects.get_or_create(
                dedup_key=dedup_key,
                defaults=defaults,
            )
            if not created and candidate.state in {"pending", "conflicted", "evaluating"}:
                for field in ("payload", "confidence", "evidence", "feedback_event_ids", "extracted_by"):
                    setattr(candidate, field, defaults[field])
                candidate.save(update_fields=[
                    "payload", "confidence", "evidence", "feedback_event_ids",
                    "extracted_by", "updated_at",
                ])
            return candidate, created
