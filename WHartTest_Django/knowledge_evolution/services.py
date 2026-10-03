import hashlib
import json
import logging
from typing import Any

from django.db import transaction

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace

logger = logging.getLogger(__name__)


def _record_standard_spans(*, trace, output, task_type, channels=None, timings=None,
                           prompt_version="", model_version="", token_usage=0):
    """把八类业务入口的通用执行阶段投影为节点轨迹。

    这是观测增强，任何写入失败都不得回滚业务产出。更细粒度的工具调用可在
    同一 trace 下继续补充子 Span。
    """
    try:
        from .attribution import SpanRecorder
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = protocol.get("workflow_id", "")
        timings = timings or {}
        channels = channels or {}
        total_ms = max(0, int(timings.get("total_ms") or 0))
        sequence = 0
        parent = SpanRecorder.record(
            trace=trace, stage=task_type, step_type="intent", sequence=sequence,
            workflow_id=workflow_id, status="completed",
            input_hash=hashlib.sha256((trace.query or "").encode()).hexdigest(),
            metadata={"granularity": "business_entry", "schema_version": protocol.get("schema_version", "")},
        )
        sequence += 1
        retrieval_channel = channels.get("knowledge") or channels.get("graph")
        if retrieval_channel and retrieval_channel.get("enabled", retrieval_channel.get("status") not in {"skipped", None}):
            SpanRecorder.record(
                trace=trace, stage=task_type,
                step_type="graph_retrieval" if channels.get("graph") else "retrieval",
                sequence=sequence, workflow_id=workflow_id, parent_span=parent,
                status="completed", evidence=_json_safe(trace.citations or []),
                knowledge_version_ids=_json_safe(retrieval_channel.get("knowledge_base_ids") or []),
                metadata={"granularity": "aggregate", "channel": _json_safe(retrieval_channel)},
            )
            sequence += 1
        if model_version or (channels.get("ai") or {}).get("enabled") or (channels.get("agent") or {}).get("enabled"):
            SpanRecorder.record(
                trace=trace, stage=task_type, step_type="model", sequence=sequence,
                workflow_id=workflow_id, parent_span=parent, status="completed",
                prompt_version=prompt_version, model_version=model_version,
                output_hash=output.output_hash, latency_ms=total_ms,
                token_usage=max(0, int(token_usage or 0)),
                metadata={"granularity": "aggregate"},
            )
            sequence += 1
        if (channels.get("agent") or {}).get("enabled"):
            SpanRecorder.record(
                trace=trace, stage=task_type, step_type="tool", sequence=sequence,
                workflow_id=workflow_id, parent_span=parent, status="completed",
                tool_name="agent-toolchain",
                metadata={"granularity": "aggregate", "step_count": channels["agent"].get("steps", 0)},
            )
            sequence += 1
        SpanRecorder.record(
            trace=trace, stage=task_type, step_type="validation", sequence=sequence,
            workflow_id=workflow_id, parent_span=parent,
            status="completed" if trace.status == "completed" else "failed",
            output_hash=output.output_hash, latency_ms=total_ms,
            metadata={"granularity": "business_output"},
        )
    except Exception:
        logger.exception("记录节点级轨迹失败，不影响业务产出")


def _record_execution_spans(*, trace, output, task_type, execution_spans=None):
    """记录每次真实工具调用的子 Span。

    输入只允许传入哈希、状态和受限摘要，不在观测表中复制原始业务数据。
    采用幂等查找，避免业务重试时重复写入。
    """
    if not execution_spans:
        return
    try:
        from .attribution import SpanRecorder
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = protocol.get("workflow_id", "")
        parent = trace.spans.filter(step_type="intent").order_by("sequence", "created_at").first()
        sequence = (trace.spans.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1
        for item in execution_spans:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "unknown")[:128]
            input_hash = str(item.get("input_hash") or "")[:64]
            output_hash = str(item.get("output_hash") or "")[:64]
            if trace.spans.filter(
                step_type="tool", tool_name=tool_name,
                input_hash=input_hash, output_hash=output_hash,
            ).exists():
                continue
            status = str(item.get("status") or "completed")
            if status not in {"running", "completed", "failed", "skipped"}:
                status = "failed"
            SpanRecorder.record(
                trace=trace, stage=task_type, step_type="tool", sequence=sequence,
                workflow_id=workflow_id, parent_span=parent, status=status,
                tool_name=tool_name,
                tool_version=str(item.get("tool_version") or "")[:128],
                input_hash=input_hash, output_hash=output_hash,
                latency_ms=max(0, int(item.get("latency_ms") or 0)),
                error_type=str(item.get("error_type") or "")[:100],
                error_message=str(item.get("error_message") or "")[:500],
                metadata={
                    "granularity": "real_tool_call",
                    "call_id": str(item.get("call_id") or "")[:128],
                    "step": max(0, int(item.get("step") or 0)),
                },
            )
            sequence += 1
    except Exception:
        logger.exception("记录真实工具调用子 Span 失败，不影响业务产出")


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
                    # 图谱通道按**实际是否命中图谱来源**记，不再写死 False。
                    # 写死会让"图谱确实参与了、但渠道记录说没有"永久成立，
                    # 于是溯源断链永远查不出来 —— 页面上只表现为"引不到东西"。
                    # 这里是**观测值**（跑完看到什么），不是配置声明。
                    "graph": {
                        "enabled": any(
                            "graph" in (item.get("fusion_detail") or {}).get("sources", [])
                            for item in candidates
                        ),
                        "observed": True,
                    },
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
        _record_standard_spans(
            trace=trace, output=output, task_type="knowledge_query",
            channels={"knowledge": {"enabled": True, "knowledge_base_ids": [str(knowledge_base.id)]}},
            timings=trace.timings,
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
                       capability=None, execution_spans=None,
                       skill_version=None) -> tuple[str, str] | None:
    """Record a platform task using the same trace contract as knowledge retrieval.

    ``skill_version`` 是产出生成时锁定的 Skill 版本（T08）。写进产出后，这条结果
    就永远能回答"它当时基于哪份包"——即使之后该版本已被取代或回滚。
    """
    try:
        output_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        capability_id = None
        if capability is not None:
            capability_id = capability.pk if hasattr(capability, "pk") else capability

        # 统一产出协议里的版本标识：即使外键被 SET_NULL 清掉，这四个值仍在 metadata
        # 里留下完整溯源链（skill_id / skill_version_id / capability_id / package_sha256）。
        version_id = None
        package_sha256 = ""
        skill_id = None
        if skill_version is not None:
            version_id = getattr(skill_version, "pk", skill_version)
            package_sha256 = getattr(skill_version, "package_sha256", "") or ""
            skill_id = getattr(skill_version, "skill_id", None)
        protocol = {
            "skill_id": str(skill_id) if skill_id else "",
            "skill_version_id": str(version_id) if version_id else "",
            "capability_id": str(capability_id) if capability_id else "",
            "package_sha256": package_sha256,
        }

        with transaction.atomic():
            existing = GenerationOutput.objects.filter(
                project=project, task_type=task_type, task_id=str(task_id),
                output_hash=output_hash,
            ).select_related("trace").first()
            if existing:
                updates = []
                if capability_id and existing.capability_id != capability_id:
                    existing.capability_id = capability_id
                    updates.append("capability")
                # 同一份产出重复上报时补齐版本溯源，但不允许被后来的版本覆盖：
                # 产出属于"第一次生成它的那个版本"，重报不该改写历史。
                if version_id and not existing.skill_version_id:
                    existing.skill_version_id = version_id
                    existing.skill_package_sha256 = package_sha256
                    updates.extend(["skill_version", "skill_package_sha256"])
                if updates:
                    # GenerationOutput 只有 created_at，没有 updated_at；
                    # 带 "updated_at" 会让 update_fields 报 FieldDoesNotExist。
                    existing.save(update_fields=updates)
                _record_execution_spans(
                    trace=existing.trace, output=existing, task_type=task_type,
                    execution_spans=execution_spans,
                )
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
            combined_metadata = dict(metadata or {})
            # 协议键覆盖调用方传入的同名键：版本溯源必须是服务端解析出来的那一个，
            # 不能让上游随手写的 metadata 把它顶掉。
            for key, value in protocol.items():
                if value:
                    combined_metadata[key] = value

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
                skill_version_id=version_id,
                skill_package_sha256=package_sha256,
                metadata=_json_safe(combined_metadata),
            )
        _record_standard_spans(
            trace=trace, output=output, task_type=task_type, channels=channels,
            timings=timings, prompt_version=prompt_version, model_version=model_version,
            token_usage=token_usage,
        )
        _record_execution_spans(
            trace=trace, output=output, task_type=task_type,
            execution_spans=execution_spans,
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
    graph_context = report.get("graph_context") or {}
    execution_spans = [{
        "tool_name": "machine-rules",
        "call_id": f"code-review:{task.pk}:machine",
        "status": "completed" if task.machine_coverage else "skipped",
        "output_hash": hashlib.sha256(str(task.machine_coverage).encode()).hexdigest(),
    }]
    if graph_context:
        graph_status = str(graph_context.get("status") or "completed")
        execution_spans.append({
            "tool_name": "crg",
            "call_id": f"code-review:{task.pk}:crg",
            "status": "completed" if graph_status in {"completed", "ready", "success"} else graph_status,
            "output_hash": hashlib.sha256(json.dumps(graph_context, sort_keys=True, default=str).encode()).hexdigest(),
            "error_type": "" if graph_status in {"completed", "ready", "success"} else graph_status,
        })
    if task.mode != "quick":
        execution_spans.append({
            "tool_name": "counterevidence-verifier",
            "call_id": f"code-review:{task.pk}:counterevidence",
            "status": "completed",
            "output_hash": hashlib.sha256(str(len(rejected)).encode()).hexdigest(),
        })
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
        execution_spans=execution_spans,
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
        if workflow_id:
            from .operations import WorkflowGateService
            WorkflowGateService.register_output(output)
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
