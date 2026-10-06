"""历史包预检、确认导入和结构化回放。"""
from __future__ import annotations

import hashlib
import json

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from file_management.models import FileAsset

from .gold import AssetCandidateService
from .history_models import (
    HistoryImportBatch, HistoryImportItem, HistoryReplay, HistoryReplayDifference,
)
from .workflow_models import FlywheelRun, WorkflowSkillLock


def _hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HistoryIngestionService:
    REQUIRED_ROLES = {"requirement", "plan", "case"}
    VALID_ROLES = REQUIRED_ROLES | {"execution", "report"}
    SALT = "knowledge-evolution.history-import.v1"

    @classmethod
    def preflight(cls, *, project, manifest: dict) -> dict:
        """只读预检：不创建批次、不创建候选、不读取正文。"""
        files = list(manifest.get("files") or [])
        normalized, errors, warnings, seen = [], [], [], set()
        roles = {str(item.get("role") or "") for item in files}
        missing = sorted(cls.REQUIRED_ROLES - roles)
        if missing:
            errors.append({"code": "missing_required_roles", "roles": missing})
        for index, item in enumerate(files):
            role, file_id = str(item.get("role") or ""), item.get("file_id")
            if role not in cls.VALID_ROLES:
                errors.append({"code": "invalid_role", "index": index, "role": role})
                continue
            asset = FileAsset.objects.filter(
                pk=file_id, project=project, is_deleted=False, status=FileAsset.STATUS_AVAILABLE,
            ).first()
            if asset is None:
                errors.append({"code": "file_unavailable", "index": index, "file_id": str(file_id)})
                continue
            key = (role, asset.pk)
            if key in seen:
                warnings.append({"code": "duplicate_mapping", "role": role, "file_id": asset.pk})
                continue
            seen.add(key)
            normalized.append({
                "role": role, "file_id": asset.pk, "sha256": asset.sha256,
                "name": asset.original_name, "size": asset.size,
            })
            if not asset.sha256:
                errors.append({"code": "missing_hash", "file_id": asset.pk})
        canonical = {
            "name": str(manifest.get("name") or "历史资料包"),
            "task_type": str(manifest.get("task_type") or "testcase_generation"),
            "files": sorted(normalized, key=lambda value: (value["role"], value["file_id"])),
        }
        token_payload = {"project": project.pk, "manifest": canonical, "hash": _hash(canonical)}
        return {
            "ok": not errors, "errors": errors, "warnings": warnings,
            "manifest": canonical, "manifest_hash": token_payload["hash"],
            "estimated_candidates": len([x for x in normalized if x["role"] in {"plan", "case"}]),
            "confirmation_token": signing.dumps(token_payload, salt=cls.SALT, compress=True) if not errors else "",
        }

    @classmethod
    @transaction.atomic
    def confirm(cls, *, project, token: str, actor):
        try:
            payload = signing.loads(token, salt=cls.SALT, max_age=3600)
        except signing.BadSignature as exc:
            raise ValidationError("预检凭证无效或已过期，请重新预检") from exc
        if int(payload.get("project") or 0) != project.pk:
            raise ValidationError("预检凭证不属于当前项目")
        checked = cls.preflight(project=project, manifest=payload["manifest"])
        if not checked["ok"] or checked["manifest_hash"] != payload.get("hash"):
            raise ValidationError("文件状态或哈希已变化，请重新预检")
        batch, created = HistoryImportBatch.objects.get_or_create(
            project=project, manifest_hash=checked["manifest_hash"],
            defaults={
                "name": checked["manifest"]["name"], "manifest": checked["manifest"],
                "preflight": {key: checked[key] for key in ("errors", "warnings", "estimated_candidates")},
                "status": "confirmed", "created_by": actor, "confirmed_by": actor,
                "confirmed_at": timezone.now(), "candidate_count": checked["estimated_candidates"],
            },
        )
        if created:
            assets = {item.pk: item for item in FileAsset.objects.filter(
                project=project, pk__in=[entry["file_id"] for entry in checked["manifest"]["files"]],
            )}
            for entry in checked["manifest"]["files"]:
                HistoryImportItem.objects.create(
                    batch=batch, role=entry["role"], file=assets[entry["file_id"]],
                    file_hash=entry["sha256"], metadata={"name": entry["name"], "size": entry["size"]},
                )
                if entry["role"] in {"plan", "case"}:
                    AssetCandidateService.enqueue_history_candidate(
                        project=project, task_type=checked["manifest"]["task_type"],
                        source_id=f"{batch.pk}:{entry['role']}:{entry['file_id']}",
                        payload={"batch_id": str(batch.pk), "file_id": entry["file_id"],
                                 "file_hash": entry["sha256"], "role": entry["role"]}, actor=actor,
                    )
            batch.status = "completed"
            batch.save(update_fields=["status", "updated_at"])
        return batch, created


class HistoryReplayService:
    CATEGORIES = {"missing", "extra", "conflict", "equivalent", "uncertain"}

    @staticmethod
    def compare(expected, actual) -> str:
        if expected == actual:
            return "equivalent"
        if expected in (None, {}, [], "") and actual not in (None, {}, [], ""):
            return "extra"
        if actual in (None, {}, [], "") and expected not in (None, {}, [], ""):
            return "missing"
        if isinstance(expected, dict) and isinstance(actual, dict):
            overlap = set(expected) & set(actual)
            if overlap and any(expected[key] != actual[key] for key in overlap):
                return "conflict"
        return "uncertain"

    @classmethod
    @transaction.atomic
    def create(cls, *, project, batch, actor, workflow_id, gold_version=None, config=None):
        if batch.project_id != project.pk or batch.status != "completed":
            raise ValidationError("只能回放当前项目已完成的历史包")
        if gold_version and (gold_version.dataset.project_id != project.pk or gold_version.state != "frozen"):
            raise ValidationError("回放金标必须是当前项目的冻结版本")
        run, _ = FlywheelRun.objects.get_or_create(
            project=project, workflow_id=workflow_id,
            defaults={"entry_type": "history_replay", "intent": "history_replay",
                      "status": "running", "created_by": actor,
                      "metadata": {"history_batch_id": str(batch.pk), "isolated": True}},
        )
        if run.intent != "history_replay":
            raise ValidationError("该 workflow_id 已用于生产链路，回放不得覆盖")
        locks = list(WorkflowSkillLock.objects.filter(
            project=project, workflow_id=workflow_id,
        ).values("stage", "skill_version_id", "package_sha256"))
        execution_lock = {"skills": locks, "config": config or {}, "isolated": True}
        return HistoryReplay.objects.create(
            project=project, batch=batch, flywheel_run=run, gold_version=gold_version,
            status="running", execution_lock=execution_lock, config_hash=_hash(execution_lock),
            created_by=actor,
        )

    @classmethod
    @transaction.atomic
    def record(cls, *, replay, rows: list[dict]):
        if replay.status not in {"running", "needs_review"}:
            raise ValidationError("该回放已结束")
        replay.differences.all().delete()
        counts = {key: 0 for key in cls.CATEGORIES}
        critical_regression = False
        for index, row in enumerate(rows):
            category = row.get("category") or cls.compare(row.get("expected"), row.get("actual"))
            if category not in cls.CATEGORIES:
                raise ValidationError(f"未知比对类型: {category}")
            critical = bool(row.get("critical"))
            critical_regression = critical_regression or (critical and category in {"missing", "conflict"})
            HistoryReplayDifference.objects.create(
                replay=replay, stage=str(row.get("stage") or "testcase_generation"),
                case_key=str(row.get("case_key") or index + 1), category=category,
                expected=row.get("expected") or {}, actual=row.get("actual") or {},
                evidence=row.get("evidence") or [], critical=critical,
            )
            counts[category] += 1
        unresolved = counts["uncertain"]
        replay.summary = {"counts": counts, "unresolved": unresolved, "total": len(rows)}
        replay.gate_report = {
            "passed": not unresolved and not critical_regression,
            "critical_regression": critical_regression,
            "human_decisions_complete": not unresolved,
        }
        replay.status = "needs_review" if unresolved else ("blocked" if critical_regression else "passed")
        replay.save(update_fields=["summary", "gate_report", "status", "updated_at"])
        return replay

    @staticmethod
    @transaction.atomic
    def decide(*, difference, actor, decision, note=""):
        if difference.category != "uncertain":
            raise ValidationError("只有不确定项需要人工判定")
        if decision not in dict(HistoryReplayDifference.DECISION_CHOICES):
            raise ValidationError("无效的人工判定")
        difference.human_decision, difference.decision_note = decision, note
        difference.decided_by, difference.decided_at = actor, timezone.now()
        difference.save(update_fields=["human_decision", "decision_note", "decided_by", "decided_at"])
        replay = difference.replay
        unresolved = replay.differences.filter(category="uncertain", human_decision="").count()
        critical_regression = replay.differences.filter(critical=True, category__in=["missing", "conflict"]).exists() or replay.differences.filter(critical=True, human_decision="regression").exists()
        replay.summary = {**(replay.summary or {}), "unresolved": unresolved}
        replay.gate_report = {**(replay.gate_report or {}), "passed": not unresolved and not critical_regression,
                              "critical_regression": critical_regression,
                              "human_decisions_complete": not unresolved}
        replay.status = "needs_review" if unresolved else ("blocked" if critical_regression else "passed")
        replay.save(update_fields=["summary", "gate_report", "status", "updated_at"])
        return difference


def flywheel_enabled(project) -> bool:
    """项目是否已灰度开启质量飞轮联动。

    真值已上移到 ``rollout.linkage_enabled``（T14），这里保留同名包装只是为了
    不破坏既有调用方。"未配置视为关闭"的语义与开关默认值一致，两处不会再分叉。
    """
    from .rollout import linkage_enabled

    return linkage_enabled(project)
