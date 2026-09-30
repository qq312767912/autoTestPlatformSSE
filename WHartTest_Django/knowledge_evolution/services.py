import hashlib
import json
import logging
from typing import Any

from django.db import transaction

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace

logger = logging.getLogger(__name__)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _candidate(source: dict[str, Any], rank: int) -> dict[str, Any]:
    metadata = source.get("metadata") or {}
    fusion_detail = source.get("fusion_detail") or {}
    if not isinstance(fusion_detail, dict):
        fusion_detail = {"raw": _json_safe(fusion_detail)}
    return {
        "rank": rank,
        "score": float(source.get("similarity_score") or 0),
        "document_id": str(metadata.get("document_id") or ""),
        "chunk_index": metadata.get("chunk_index"),
        "vector_id": str(metadata.get("vector_id") or ""),
        "source": str(metadata.get("source") or ""),
        "fusion_detail": _json_safe(fusion_detail),
    }


def record_knowledge_query(*, knowledge_base, user, query: str, answer: str,
                           sources: list[dict[str, Any]], retrieval_time: float,
                           generation_time: float, total_time: float) -> tuple[str, str] | None:
    """Best-effort telemetry. A failure must never break the knowledge query."""
    try:
        candidates = [_candidate(source, index + 1) for index, source in enumerate(sources)]
        citations = [
            {
                "citation_id": f"kb:{knowledge_base.id}:{item['document_id']}:{item['chunk_index']}",
                "document_id": item["document_id"],
                "chunk_index": item["chunk_index"],
                "rank": item["rank"],
            }
            for item in candidates if item["document_id"]
        ]
        with transaction.atomic():
            trace = RetrievalTrace.objects.create(
                project=knowledge_base.project,
                knowledge_base=knowledge_base,
                user=user if getattr(user, "is_authenticated", False) else None,
                task_type="knowledge_query",
                query=query,
                channels={
                    "dense": {"enabled": True},
                    "sparse": {"enabled": any(
                        "sparse" in (item.get("fusion_detail") or {}).get("sources", [])
                        for item in candidates
                    )},
                    "graph": {"enabled": False, "reason": "phase_0_observability"},
                },
                candidates=candidates,
                citations=citations,
                timings={
                    "retrieval_ms": round(retrieval_time * 1000),
                    "generation_ms": round(generation_time * 1000),
                    "total_ms": round(total_time * 1000),
                },
            )
            output = GenerationOutput.objects.create(
                project=knowledge_base.project,
                trace=trace,
                task_type="knowledge_query",
                content=answer,
                output_hash=hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                metadata={"source_count": len(sources), "citation_count": len(citations)},
            )
        return str(trace.id), str(output.id)
    except Exception:
        logger.exception("记录检索轨迹失败，本次输出不进入数据飞轮")
        return None


def record_task_output(*, project, user, task_type: str, task_id: str,
                       query: str, content: str, channels: dict[str, Any] | None = None,
                       candidates: list[dict[str, Any]] | None = None,
                       citations: list[dict[str, Any]] | None = None,
                       timings: dict[str, Any] | None = None, token_usage: int = 0,
                       metadata: dict[str, Any] | None = None,
                       policy_version: str = "platform-v1",
                       prompt_version: str = "platform-v1",
                       model_version: str = "", status: str = "completed",
                       capability=None) -> tuple[str, str] | None:
    """Record a platform task using the same trace contract as knowledge retrieval."""
    try:
        output_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        capability_id = None
        if capability is not None:
            capability_id = capability.pk if hasattr(capability, "pk") else capability
        with transaction.atomic():
            existing = GenerationOutput.objects.filter(
                project=project, task_type=task_type, task_id=str(task_id),
                output_hash=output_hash,
            ).select_related("trace").first()
            if existing:
                if capability_id and existing.capability_id != capability_id:
                    existing.capability_id = capability_id
                    existing.save(update_fields=["capability", "updated_at"])
                return str(existing.trace_id), str(existing.id)
            trace = RetrievalTrace.objects.create(
                project=project,
                user=user if getattr(user, "is_authenticated", False) else None,
                task_type=task_type,
                task_id=str(task_id),
                query=query,
                policy_version=policy_version,
                status=status,
                channels=_json_safe(channels or {}),
                candidates=_json_safe(candidates or []),
                citations=_json_safe(citations or []),
                timings=_json_safe(timings or {}),
                token_usage=max(0, int(token_usage or 0)),
            )
            output = GenerationOutput.objects.create(
                project=project,
                trace=trace,
                capability_id=capability_id,
                task_type=task_type,
                task_id=str(task_id),
                model_version=model_version,
                prompt_version=prompt_version,
                content=content,
                output_hash=output_hash,
                metadata=_json_safe(metadata or {}),
            )
        return str(trace.id), str(output.id)
    except Exception:
        logger.exception("记录任务输出失败，本次输出不进入数据飞轮")
        return None


def record_code_review_task(task, rejected_findings=None, capability=None) -> tuple[str, str] | None:
    """Persist code-review classifications and counter-evidence without raw diff text."""
    if capability is None:
        from .capability_models import CapabilityDefinition
        capability, _ = CapabilityDefinition.objects.get_or_create(
            project=task.project,
            name="代码审查",
            defaults={
                "kind": "skill",
                "evaluation_mode": "single",
                "stages": ["code_review"],
                "gate_rules": {"min_mean_diff": 0.05, "min_pass_rate": 0.80},
            },
        )
    report = dict(task.change_report or {})
    report.pop("evolution", None)
    findings = report.get("findings") or []
    rejected = rejected_findings or []

    def compact(item, disposition=None):
        return {
            "key": str(item.get("key") or ""),
            "file": str(item.get("file") or ""),
            "line_start": item.get("line_start"),
            "severity": str(item.get("severity") or "medium"),
            "disposition": disposition or str(item.get("disposition") or "needs_confirmation"),
            "verification_status": str(item.get("verification_status") or ""),
            "reason": str(item.get("verification_reason") or item.get("reason") or "")[:1000],
            "counter_evidence": str(item.get("counter_evidence") or "")[:1000],
        }

    candidates = [compact(item) for item in findings]
    candidates.extend(compact(item, "counter_evidence_rejected") for item in rejected)
    citations = [
        {"finding_key": item["key"], "file": item["file"], "line_start": item["line_start"]}
        for item in candidates if item["file"]
    ]
    summary = report.get("summary") or {}
    content = json.dumps(
        {"change_report": report, "test_report": task.test_report or {}},
        ensure_ascii=False, sort_keys=True,
    )
    duration_ms = 0
    if task.completed_at and task.created_at:
        duration_ms = max(0, round((task.completed_at - task.created_at).total_seconds() * 1000))
    schema_version = report.get("schema_version") or "unknown"
    return record_task_output(
        project=task.project,
        user=task.executor or task.creator,
        task_type="code_review",
        task_id=str(task.pk),
        query=f"{task.repository.name}: {task.base_sha}..{task.head_sha}",
        content=content,
        channels={
            "machine": {"enabled": True, "coverage": task.machine_coverage},
            "ai": {"enabled": task.mode != "quick", "coverage": task.ai_coverage},
            "graph": (report.get("graph_context") or {"status": "skipped"}),
            "counter_evidence": {"enabled": task.mode != "quick", "rejected_count": len(rejected)},
        },
        candidates=candidates,
        citations=citations,
        timings={"total_ms": duration_ms},
        token_usage=task.token_usage,
        metadata={
            "mode": task.mode,
            "status": task.status,
            "confirmed_count": summary.get("confirmed_count", 0),
            "needs_confirmation_count": summary.get("needs_confirmation_count", 0),
            "advisory_count": summary.get("advisory_count", 0),
            "counter_evidence_rejected_count": len(rejected),
            "changed_files": summary.get("changed_files", 0),
        },
        policy_version=f"code-review-schema-v{schema_version}",
        prompt_version="code-review-current",
        status="completed" if task.status in {"completed", "degraded", "partial"} else "failed",
        capability=capability,
    )


def record_test_execution(execution) -> tuple[str, str] | None:
    """Persist an objective test signal and link it to the generated trace/output."""
    summary = {
        "execution_id": execution.pk,
        "suite_id": execution.suite_id,
        "suite_name": execution.suite.name,
        "status": execution.status,
        "total": execution.total_count,
        "passed": execution.passed_count,
        "failed": execution.failed_count,
        "skipped": execution.skipped_count,
        "error": execution.error_count,
        "pass_rate": execution.pass_rate,
    }
    content = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    workflow_id = getattr(execution, "workflow_id", "") or ""
    capability = getattr(execution, "capability", None)
    source_output = getattr(execution, "source_output", None)
    protocol = {"workflow_id": workflow_id, "capability_id": str(capability.pk) if capability else ""}
    if source_output:
        protocol["parent_output_id"] = str(source_output.pk)
    ids = record_task_output(
        project=execution.suite.project,
        user=execution.executor,
        task_type="test_execution",
        task_id=str(execution.pk),
        query=f"执行测试套件：{execution.suite.name}",
        content=content,
        channels={"test_runner": {"enabled": True}},
        timings={"total_ms": round((execution.duration or 0) * 1000)},
        metadata={"protocol": protocol, **summary},
        policy_version="test-execution-v1",
        prompt_version="not-applicable",
        status="completed" if execution.status == "completed" else "failed",
        capability=capability,
    )
    if not ids or execution.status not in {"completed", "failed"}:
        return ids
    try:
        trace = RetrievalTrace.objects.get(pk=ids[0])
        output = GenerationOutput.objects.get(pk=ids[1])
        signal = (
            "test_passed"
            if execution.status == "completed" and not execution.failed_count and not execution.error_count
            else "test_failed"
        )
        FeedbackEvent.objects.get_or_create(
            idempotency_key=f"test-execution:{execution.pk}:{execution.status}",
            defaults={
                "project": execution.suite.project,
                "trace": trace,
                "output": output,
                "signal": signal,
                "value": 1.0,
                "reason_code": "automated_test_result",
                "actor": execution.executor,
                "actor_type": "system",
            },
        )
    except Exception:
        logger.exception("记录测试执行反馈失败，不影响测试主流程")
    return ids
