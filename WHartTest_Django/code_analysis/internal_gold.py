"""人工标记后的代码审查快照，复用正式金标资产的持久化与复核体系。"""
import hashlib
import json
from copy import deepcopy

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from knowledge_evolution.gold import GoldAnnotationService
from knowledge_evolution.gold_models import GoldCase, GoldDataset, GoldDatasetVersion
from knowledge_evolution.models import GenerationOutput
from projects.roles import ensure_test_lead, is_project_member

SCOPE = "internal-code-review"
LABELS = {"confirmed", "false_positive", "uncertain"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def task_snapshot(task):
    outputs = GenerationOutput.objects.filter(project_id=task.project_id, task_type="code_review", task_id=str(task.pk)).order_by("created_at", "pk")
    repository = task.repository
    return {
        "schema": "internal-code-review-v1",
        "project_id": task.project_id,
        "task_id": str(task.pk),
        "title": task.title,
        "mode": task.mode,
        "status": task.status,
        "base_sha": task.base_sha,
        "head_sha": task.head_sha,
        "source_type": task.source_type,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "coverage": {"machine": float(task.machine_coverage), "ai": float(task.ai_coverage)},
        "repository": {"id": repository.pk, "name": repository.name, "source_type": repository.source_type, "path": repository.path_with_namespace} if repository else None,
        "raw_diff": task.raw_diff,
        "requirement_context": task.requirement_context,
        "api_context": task.api_context,
        "change_report": deepcopy(task.change_report),
        "test_report": deepcopy(task.test_report),
        "provenance": list(outputs.values("id", "output_hash", "model_version", "prompt_version", "skill_version_id", "skill_package_sha256")),
        "replay_scope": "diff_and_report_context",
    }


def findings_for(snapshot):
    # 使用原始顺序编号，不能以筛选后的行号对齐人工标记。
    return [{"index": i, "key": str(item.get("key") or f"finding-{i}"), "title": item.get("change") or "未命名风险", "file": item.get("file") or "", "severity": item.get("severity") or "", "evidence": str(item.get("evidence") or "")} for i, item in enumerate((snapshot.get("change_report") or {}).get("findings") or [])]


def context(task):
    snapshot = task_snapshot(task)
    return {
        "snapshot_hash": digest(snapshot), "findings": findings_for(snapshot),
        "dataset_names": list(GoldDataset.objects.filter(project_id=task.project_id, task_type="code_review", scope_key=SCOPE, status="active").values_list("name", flat=True)),
    }


@transaction.atomic
def save_snapshot(*, task, actor, payload):
    from .models import AnalysisTask
    if not is_project_member(actor, task.project_id):
        raise PermissionDenied("请先加入该项目")
    if not actor.has_perm("knowledge_evolution.add_goldcase"):
        raise PermissionDenied("需要“添加金标用例”权限")
    # 不锁带可空关联的外连接；单独锁任务以拒绝重跑/并发更新造成的标记错位。
    task = AnalysisTask.objects.select_for_update().get(pk=task.pk)
    if task.status not in {"completed", "degraded", "partial"}:
        raise ValidationError("仅可保存已完成或部分完成的审查结果")
    snapshot = task_snapshot(task)
    if payload.get("snapshot_hash") != digest(snapshot):
        raise ValidationError("审查数据已变化，请重新打开标记窗口后保存")
    annotations = payload.get("annotations")
    findings = findings_for(snapshot)
    if not findings:
        raise ValidationError("当前审查没有风险项，不能生成逐项标记金标")
    if not isinstance(annotations, list) or len(annotations) != len(findings):
        raise ValidationError("请逐项完成人工标记，不得遗漏风险项")
    indexed = {}
    for item in annotations:
        if not isinstance(item, dict):
            raise ValidationError("人工标记格式无效")
        index = item.get("index")
        if type(index) is not int or index < 0 or index >= len(findings) or index in indexed:
            raise ValidationError("人工标记序号无效或重复")
        if item.get("label") not in LABELS:
            raise ValidationError("人工结论仅支持确认问题、误报、待复核")
        comment = item.get("comment", "")
        if not isinstance(comment, str) or len(comment) > 4000:
            raise ValidationError("标记说明最多4000字")
        if item["label"] == "false_positive" and not comment.strip():
            raise ValidationError("误报项需填写判断依据")
        indexed[index] = {"index": index, "key": findings[index]["key"], "label": item["label"], "comment": comment.strip()}
    labels = [indexed[i] for i in range(len(findings))]
    name = payload.get("dataset_name", "内部代码审查金标集")
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise ValidationError("数据集名称须为1至255字")
    fingerprint = digest({"snapshot": snapshot, "annotations": labels})
    snapshot = json.loads(json.dumps(snapshot, default=str))
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > 16 * 1024 * 1024:
        raise ValidationError("当前快照超过16MB，请拆分审查任务后保存；平台不会截断证据")
    dataset = GoldDataset.objects.filter(project_id=task.project_id, name=name.strip(), task_type="code_review", scope_key=SCOPE).first()
    if dataset is None:
        ensure_test_lead(actor, task.project_id)
        dataset, _ = GoldDataset.objects.get_or_create(
            project_id=task.project_id, name=name.strip(), task_type="code_review", scope_key=SCOPE,
            defaults={"scope_type": "domain", "description": "人工标记的内部审查快照，初标后须复核。", "governance": {"internal_only": True, "allow_optimization": False}, "created_by": actor, "owner": actor},
        )
    dataset = GoldDataset.objects.select_for_update().get(pk=dataset.pk)
    if dataset.status != "active":
        raise ValidationError("数据集已归档，请使用启用中的内部金标集")
    version, _ = GoldDatasetVersion.objects.get_or_create(dataset=dataset, version=f"manual-{fingerprint[:24]}", defaults={"created_by": actor})
    existing = version.cases.filter(source_hash=fingerprint).first()
    if existing:
        return result(existing, reused=True)
    expected = {"annotations": labels, "confirmed_keys": [x["key"] for x in labels if x["label"] == "confirmed"], "false_positive_keys": [x["key"] for x in labels if x["label"] == "false_positive"], "uncertain_keys": [x["key"] for x in labels if x["label"] == "uncertain"]}
    evidence = [{"type": "manual_code_review", "task_id": str(task.pk), "input_hash": digest(snapshot), "source_hash": fingerprint}]
    case = GoldCase.objects.create(
        version=version, task_type="code_review", title=(task.title or "内部代码审查样本")[:255],
        input_snapshot=snapshot, expected_output=expected, evidence=evidence,
        tags=["内部代码审查", "人工初标"], split="regression", state="candidate", privacy_level="internal", allow_optimization=False,
        source_hash=fingerprint, dedup_fingerprint=fingerprint, candidate_origin="manual", created_by=actor,
        review_checklist={"manual_marked": True, "needs_independent_review": True, "saved_at": timezone.now().isoformat()},
    )
    GoldAnnotationService.submit(case=case, round_name="primary", actor=actor, answer=expected, rubric_scores={}, evidence=evidence, conclusion="needs_changes" if expected["uncertain_keys"] else "accepted", comment="代码审查页面逐项人工标记后保存", split="regression")
    version.state = "labeling"
    version.save(update_fields=["state", "updated_at"])
    return result(case, reused=False)


def result(case, *, reused):
    return {"case_id": str(case.pk), "dataset_id": str(case.version.dataset_id), "version_id": str(case.version_id), "state": case.state, "source_hash": case.source_hash, "reused": reused, "message": "内部金标样本已保存，人工初标已记录，待独立复核"}


def library(*, actor, project_id, case_id=None, page=1):
    if not actor.has_perm("knowledge_evolution.view_goldcase"):
        raise PermissionDenied("需要“查看金标用例”权限")
    if not is_project_member(actor, project_id):
        raise PermissionDenied("请先加入该项目")
    cases = GoldCase.objects.filter(version__dataset__project_id=project_id, version__dataset__scope_key=SCOPE, task_type="code_review").select_related("version__dataset").order_by("-created_at")
    if case_id:
        from django.shortcuts import get_object_or_404
        case = get_object_or_404(cases, pk=case_id)
        return {**summary(case), "input_snapshot": case.input_snapshot, "expected_output": case.expected_output, "evidence": case.evidence, "privacy_level": case.privacy_level, "allow_optimization": case.allow_optimization}
    return {"items": [summary(case) for case in cases[(page - 1) * 20:page * 20]], "total": cases.count(), "page": page}


def summary(case):
    return {"id": str(case.pk), "title": case.title, "dataset_name": case.version.dataset.name, "version": case.version.version, "state": case.state, "created_at": case.created_at.isoformat(), "source_hash": case.source_hash, "source_task_id": case.input_snapshot.get("task_id", "")}
