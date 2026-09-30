"""任务 10：标准 Feedback Event 服务。

将业务反馈（采纳/驳回/编辑 diff/测试结果/缺陷确认/合并/回退）统一写入 FeedbackEvent，
并强制关联 GenerationOutput / RetrievalTrace / KnowledgeVersion，实现去重和防刷。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


@dataclass
class FeedbackContext:
    """创建反馈事件所需的最小上下文。"""

    project_id: int
    output: Optional[GenerationOutput] = None
    trace: Optional[RetrievalTrace] = None
    knowledge_version_ids: list[str] = None
    actor: Any = None
    actor_type: str = "user"
    task_type: str = ""
    task_id: str = ""


class FeedbackService:
    """标准反馈事件服务。

    设计 §7：
    - 反馈必须关联任务—输出—轨迹—版本四级。
    - 去重键 = task + output_hash + signal + actor/outcome。
    - 客观信号（测试/缺陷/合并/回退）权重高于主观反馈。
    """

    # 同一输出 + 信号 + actor 的最小间隔（秒），防止连点/脚本刷屏
    SPAM_COOLDOWN_SECONDS = 60
    # 同一任务在 24 小时内同一 actor 的主观反馈上限
    SUBJECTIVE_DAILY_LIMIT = 10

    SIGNAL_WEIGHTS = {
        "defect_confirmed": 1.0,
        "missed": 1.0,
        "test_failed": 0.9,
        "test_passed": 0.9,
        "reverted": 0.9,
        "merged": 0.9,
        "accepted": 0.8,
        "rejected": 0.8,
        "edited": 0.5,
        "false_positive": 0.8,
        "missed": 1.0,
    }

    SUBJECTIVE_SIGNALS = {"accepted", "rejected", "edited"}

    def __init__(self, context: FeedbackContext):
        self.context = context
        self._validate_context()

    def _validate_context(self):
        if not self.context.output and not self.context.trace:
            raise ValueError("反馈必须关联 GenerationOutput 或 RetrievalTrace")

    @property
    def project_id(self):
        return self.context.project_id

    @property
    def output(self):
        return self.context.output

    @property
    def trace(self):
        return self.context.trace

    @staticmethod
    def _build_idempotency_key(
        task_type: str, task_id: str, output_hash: str, signal: str, actor_key: str
    ) -> str:
        payload = f"{task_type}:{task_id}:{output_hash}:{signal}:{actor_key}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _actor_key(self) -> str:
        actor = self.context.actor
        if actor and getattr(actor, "pk", None):
            return f"user:{actor.pk}"
        return f"actor_type:{self.context.actor_type}"

    def _is_spam(self, signal: str) -> tuple[bool, str]:
        """简单防刷：冷却期和单日上限。"""
        actor_key = self._actor_key()
        now = timezone.now()
        # 冷却期
        recent_events = FeedbackEvent.objects.filter(
            project_id=self.project_id,
            signal=signal,
            actor=self.context.actor,
            actor_type=self.context.actor_type,
            created_at__gte=now - timezone.timedelta(seconds=self.SPAM_COOLDOWN_SECONDS),
        )
        if self.output:
            recent_events = recent_events.filter(output=self.output)
        elif self.trace:
            recent_events = recent_events.filter(trace=self.trace)
        recent = recent_events.exists()
        if recent:
            return True, f"同一用户 {self.SPAM_COOLDOWN_SECONDS}s 内已提交过 {signal} 反馈"

        # 主观反馈单日上限
        if signal in self.SUBJECTIVE_SIGNALS:
            count = FeedbackEvent.objects.filter(
                project_id=self.project_id,
                actor=self.context.actor,
                actor_type=self.context.actor_type,
                signal__in=self.SUBJECTIVE_SIGNALS,
                created_at__gte=now - timezone.timedelta(days=1),
            ).count()
            if count >= self.SUBJECTIVE_DAILY_LIMIT:
                return True, f"同一用户 24h 内主观反馈已达 {self.SUBJECTIVE_DAILY_LIMIT} 条上限"
        return False, ""

    def _create(
        self, *, signal: str, value: float, reason_code: str = "",
        comment: str = "", detail: dict | None = None
    ) -> FeedbackEvent:
        output = self.context.output
        output_hash = output.output_hash if output else ""
        task_type = self.context.task_type or (output.task_type if output else "")
        task_id = self.context.task_id or (output.task_id if output else "")
        actor_key = self._actor_key()
        idempotency_key = self._build_idempotency_key(
            task_type, task_id, output_hash, signal, actor_key
        )

        with transaction.atomic():
            try:
                return FeedbackEvent.objects.get(idempotency_key=idempotency_key)
            except FeedbackEvent.DoesNotExist:
                pass

            event = FeedbackEvent.objects.create(
                project_id=self.project_id,
                output=output,
                trace=self.context.trace,
                signal=signal,
                value=value,
                reason_code=reason_code,
                comment=comment,
                actor=self.context.actor,
                actor_type=self.context.actor_type,
                idempotency_key=idempotency_key,
                detail=_json_safe(detail or {}),
            )
            version_ids = self.context.knowledge_version_ids or []
            if version_ids:
                from .knowledge_models import KnowledgeVersion

                versions = KnowledgeVersion.objects.filter(id__in=version_ids)
                event.knowledge_versions.set(versions)
            return event

    def record_accepted(self, reason: str = "") -> FeedbackEvent:
        """人工采纳生成结果。"""
        return self._create(
            signal="accepted",
            value=self.SIGNAL_WEIGHTS["accepted"],
            reason_code="user_accepted",
            comment=reason,
        )

    def record_rejected(self, reason_code: str, comment: str = "") -> FeedbackEvent:
        """人工驳回。reason_code 用于分类：evidence_wrong / hallucination / unsafe / other。"""
        return self._create(
            signal="rejected",
            value=self.SIGNAL_WEIGHTS["rejected"],
            reason_code=reason_code,
            comment=comment,
        )

    def record_edited(self, *, before: str, after: str, reason: str = "") -> FeedbackEvent:
        """记录编辑 diff；不保存完整原文，只保存规范化 diff 摘要。"""
        detail = {
            "edit": {
                "before_hash": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                "after_hash": hashlib.sha256(after.encode("utf-8")).hexdigest(),
                "length_before": len(before),
                "length_after": len(after),
                "diff_summary": self._edit_summary(before, after),
            }
        }
        return self._create(
            signal="edited",
            value=self.SIGNAL_WEIGHTS["edited"],
            reason_code="user_edited",
            comment=reason,
            detail=detail,
        )

    def record_test_result(self, *, passed: bool, details: dict | None = None) -> FeedbackEvent:
        """记录自动化测试结果。"""
        signal = "test_passed" if passed else "test_failed"
        return self._create(
            signal=signal,
            value=self.SIGNAL_WEIGHTS[signal],
            reason_code="automated_test_result",
            detail=details,
        )

    def record_defect_status(self, *, confirmed: bool, reason: str = "") -> FeedbackEvent:
        """缺陷确认/误报。confirmed=True 为 defect_confirmed，False 为 false_positive。"""
        signal = "defect_confirmed" if confirmed else "false_positive"
        return self._create(
            signal=signal,
            value=self.SIGNAL_WEIGHTS[signal],
            reason_code="defect_status_update",
            comment=reason,
        )

    def record_merge(self, commit_sha: str = "") -> FeedbackEvent:
        """代码/用例已合并。"""
        return self._create(
            signal="merged",
            value=self.SIGNAL_WEIGHTS["merged"],
            reason_code="code_merged",
            detail={"commit_sha": commit_sha},
        )

    def record_revert(self, commit_sha: str = "", reason: str = "") -> FeedbackEvent:
        """代码/用例回退。"""
        return self._create(
            signal="reverted",
            value=self.SIGNAL_WEIGHTS["reverted"],
            reason_code="code_reverted",
            comment=reason,
            detail={"commit_sha": commit_sha},
        )

    def record_missed(self, *, expected_summary: str, reason: str = "") -> FeedbackEvent:
        """事后发现漏报。"""
        return self._create(
            signal="missed",
            value=self.SIGNAL_WEIGHTS["missed"],
            reason_code="missed_finding",
            comment=reason,
            detail={"expected_summary": expected_summary},
        )

    @staticmethod
    def _edit_summary(before: str, after: str) -> dict:
        """生成编辑 diff 摘要：按行变化计数，不保存原文。"""
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        return {
            "lines_before": len(before_lines),
            "lines_after": len(after_lines),
            "net_change": len(after_lines) - len(before_lines),
        }

    @classmethod
    def record_for_output(
        cls,
        *,
        output: GenerationOutput,
        signal: str,
        actor: Any = None,
        actor_type: str = "user",
        reason_code: str = "",
        comment: str = "",
        detail: dict | None = None,
        knowledge_version_ids: list[str] | None = None,
    ) -> FeedbackEvent:
        """快捷入口：直接对 GenerationOutput 记录反馈。"""
        ctx = FeedbackContext(
            project_id=output.project_id,
            output=output,
            trace=output.trace,
            knowledge_version_ids=knowledge_version_ids or [],
            actor=actor,
            actor_type=actor_type,
            task_type=output.task_type,
            task_id=output.task_id,
        )
        svc = cls(ctx)
        spam, reason = svc._is_spam(signal)
        if spam:
            raise ValueError(f"反馈被防刷规则拦截：{reason}")
        value = cls.SIGNAL_WEIGHTS.get(signal, 0.5)
        return svc._create(
            signal=signal,
            value=value,
            reason_code=reason_code,
            comment=comment,
            detail=detail,
        )


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
