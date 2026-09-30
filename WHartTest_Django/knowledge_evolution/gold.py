"""正式金标资产服务。所有状态变化集中在这里，避免 API 绕过业务约束。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .gold_models import (
    AnnotationConflict,
    GoldAnnotation,
    GoldCase,
    GoldDatasetVersion,
)


def _canonical_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GoldCandidateService:
    ELIGIBLE_SIGNALS = {
        "defect_confirmed", "false_positive", "missed", "edited",
        "test_failed", "reverted", "rejected",
    }

    @staticmethod
    @transaction.atomic
    def from_feedback(*, version: GoldDatasetVersion, feedback, actor, split="fresh") -> GoldCase:
        if version.state == "frozen":
            raise ValidationError("冻结版本不能新增金标候选")
        if feedback.signal not in GoldCandidateService.ELIGIBLE_SIGNALS:
            raise ValidationError("该反馈信号不足以生成金标候选")
        output = feedback.output
        if not output:
            raise ValidationError("金标候选必须关联具体业务产出")
        if output.project_id != version.dataset.project_id:
            raise ValidationError("反馈与金标数据集不属于同一项目")

        protocol = (output.metadata or {}).get("protocol") or {}
        privacy = protocol.get("privacy") or {}
        privacy_level = privacy.get("level", "internal")
        prohibited = bool(privacy.get("prohibit_optimization")) or privacy_level == "prohibited"
        source_hash = _canonical_hash({
            "project": output.project_id,
            "output": str(output.id),
            "feedback": str(feedback.id),
            "output_hash": output.output_hash,
            "signal": feedback.signal,
        })
        evidence = protocol.get("evidence") or []
        case, _ = GoldCase.objects.get_or_create(
            version=version,
            source_hash=source_hash,
            defaults={
                "source_output": output,
                "source_feedback": feedback,
                "task_type": output.task_type,
                "title": f"{output.task_type} · {feedback.get_signal_display()}",
                # 只保存引用和哈希，避免把原始查询、Diff或敏感正文复制到金标元数据。
                "input_snapshot": {
                    "trace_id": str(output.trace_id),
                    "output_id": str(output.id),
                    "query_hash": _canonical_hash(output.trace.query),
                    "output_hash": output.output_hash,
                    "protocol_version": protocol.get("schema_version", ""),
                },
                "expected_output": (feedback.detail or {}).get("expected_output") or {},
                "evidence": evidence,
                "tags": [feedback.signal, feedback.reason_code] if feedback.reason_code else [feedback.signal],
                "split": split,
                "state": "candidate",
                "privacy_level": "prohibited" if prohibited else privacy_level,
                "allow_optimization": not prohibited,
                "created_by": actor,
            },
        )
        if version.state == "draft":
            version.state = "labeling"
            version.save(update_fields=["state", "updated_at"])
        return case


class GoldAnnotationService:
    @staticmethod
    @transaction.atomic
    def submit(*, case: GoldCase, round_name: str, actor, answer, rubric_scores,
               evidence, conclusion: str, comment="") -> GoldAnnotation:
        if case.version.state == "frozen":
            raise ValidationError("冻结版本不能继续标注")
        if round_name not in {"primary", "review"}:
            raise ValidationError("普通标注只支持初标或复核")
        primary = case.annotations.filter(round="primary").first()
        if round_name == "review":
            if not primary:
                raise ValidationError("必须先完成初标")
            if primary.annotator_id == actor.id:
                raise ValidationError("复核人不能与初标人相同")
        annotation = GoldAnnotation.objects.create(
            case=case, round=round_name, answer=answer or {},
            rubric_scores=rubric_scores or {}, evidence=evidence or [],
            conclusion=conclusion, comment=comment, annotator=actor,
        )
        case.state = "labeling"
        case.save(update_fields=["state", "updated_at"])
        if round_name == "review":
            GoldAnnotationService._compare(case, primary, annotation)
        return annotation

    @staticmethod
    def _compare(case, primary, review):
        differing = []
        for field in ("answer", "rubric_scores", "evidence", "conclusion"):
            if getattr(primary, field) != getattr(review, field):
                differing.append(field)
        if differing:
            AnnotationConflict.objects.update_or_create(
                case=case,
                defaults={
                    "primary_annotation": primary,
                    "review_annotation": review,
                    "differing_fields": differing,
                    "state": "open",
                },
            )
            case.state = "conflict"
            case.save(update_fields=["state", "updated_at"])
            return
        GoldAnnotationService._apply_final(case, review)

    @staticmethod
    def _apply_final(case, annotation):
        case.state = "confirmed" if annotation.conclusion == "accepted" else "rejected"
        case.expected_output = annotation.answer
        case.rubric = annotation.rubric_scores
        case.evidence = annotation.evidence
        case.save(update_fields=["state", "expected_output", "rubric", "evidence", "updated_at"])

    @staticmethod
    @transaction.atomic
    def resolve(*, conflict: AnnotationConflict, actor, answer, rubric_scores,
                evidence, conclusion: str, comment="") -> GoldAnnotation:
        if conflict.state != "open":
            raise ValidationError("该冲突已经处理")
        annotation = GoldAnnotation.objects.create(
            case=conflict.case, round="arbitration", answer=answer or {},
            rubric_scores=rubric_scores or {}, evidence=evidence or [],
            conclusion=conclusion, comment=comment, annotator=actor,
        )
        conflict.state = "resolved"
        conflict.resolution = {
            "annotation_id": str(annotation.id),
            "conclusion": conclusion,
        }
        conflict.resolved_by = actor
        conflict.resolved_at = timezone.now()
        conflict.save(update_fields=["state", "resolution", "resolved_by", "resolved_at"])
        GoldAnnotationService._apply_final(conflict.case, annotation)
        return annotation


class GoldVersionService:
    @staticmethod
    @transaction.atomic
    def freeze(*, version: GoldDatasetVersion, actor) -> GoldDatasetVersion:
        version = GoldDatasetVersion.objects.select_for_update().get(pk=version.pk)
        if version.state == "frozen":
            return version
        cases = list(version.cases.order_by("id"))
        if not cases:
            raise ValidationError("空数据集版本不能冻结")
        invalid = [case for case in cases if case.state != "confirmed"]
        if invalid:
            raise ValidationError(f"仍有 {len(invalid)} 条样本未确认")
        if any(case.privacy_level == "prohibited" and case.allow_optimization for case in cases):
            raise ValidationError("存在隐私策略不一致的样本")

        snapshot = [
            {
                "id": str(case.id), "source_hash": case.source_hash,
                "task_type": case.task_type, "split": case.split,
                "expected_hash": _canonical_hash(case.expected_output),
                "rubric_hash": _canonical_hash(case.rubric),
                "evidence_hash": _canonical_hash(case.evidence),
                "privacy_level": case.privacy_level,
                "allow_optimization": case.allow_optimization,
            }
            for case in cases
        ]
        split_counts = Counter(case.split for case in cases)
        task_counts = Counter(case.task_type for case in cases)
        version.content_hash = _canonical_hash(snapshot)
        version.sample_stats = {
            "total": len(cases),
            "splits": dict(sorted(split_counts.items())),
            "task_types": dict(sorted(task_counts.items())),
        }
        version.state = "frozen"
        version.frozen_by = actor
        version.frozen_at = timezone.now()
        # 首次冻结时模型保护逻辑允许从非 frozen 状态进入 frozen。
        version.save(update_fields=[
            "content_hash", "sample_stats", "state", "frozen_by", "frozen_at", "updated_at",
        ])
        return version
