"""全平台统一产出协议与八阶段薄适配器。"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvaluationMode(str, Enum):
    SINGLE = "single"
    WORKFLOW = "workflow"


class OutputStage(str, Enum):
    CASE_REVIEW = "case_review"
    CODE_REVIEW = "code_review"
    KNOWLEDGE_QUERY = "knowledge_query"
    RISK_IDENTIFICATION = "risk_identification"
    TEST_PLAN_GENERATION = "test_plan_generation"
    TESTCASE_GENERATION = "testcase_generation"
    TEST_EXECUTION = "test_execution"
    ISSUE_TRACKING = "issue_tracking"


WORKFLOW_STAGES = {
    OutputStage.RISK_IDENTIFICATION, OutputStage.TEST_PLAN_GENERATION,
    OutputStage.TESTCASE_GENERATION, OutputStage.TEST_EXECUTION, OutputStage.ISSUE_TRACKING,
}


@dataclass
class OutputEnvelope:
    project: Any
    user: Any
    stage: OutputStage
    source_id: str
    input_summary: str
    output: dict[str, Any]
    status: str = "completed"
    workflow_id: str = ""
    parent_output_ids: list[str] = field(default_factory=list)
    supersedes_output_id: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    channels: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    producer: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    capability: Any = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = "platform-output/v1"

    @property
    def evaluation_mode(self) -> EvaluationMode:
        return EvaluationMode.WORKFLOW if self.stage in WORKFLOW_STAGES else EvaluationMode.SINGLE

    def validate(self):
        if not self.source_id:
            raise ValueError("source_id 不能为空")
        if self.evaluation_mode == EvaluationMode.WORKFLOW and not self.workflow_id:
            raise ValueError(f"链路型阶段 {self.stage.value} 必须提供 workflow_id")
        if not isinstance(self.output, dict):
            raise ValueError("output 必须是对象")
        return self

    @property
    def capability_id(self) -> str:
        if self.capability is None:
            return ""
        return str(self.capability.pk if hasattr(self.capability, "pk") else self.capability)

    def canonical(self) -> dict[str, Any]:
        self.validate()
        output_json = json.dumps(self.output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "evaluation_mode": self.evaluation_mode.value,
            "stage": self.stage.value,
            "source_id": self.source_id,
            "workflow_id": self.workflow_id,
            "parent_output_ids": self.parent_output_ids,
            "supersedes_output_id": self.supersedes_output_id,
            "capability_id": self.capability_id,
            # 正文仅保存在 GenerationOutput.content；协议元数据只保留可核验描述，
            # 避免敏感内容被重复写入 JSON 元数据并扩大访问面。
            "output_descriptor": {
                "content_hash": hashlib.sha256(output_json.encode()).hexdigest(),
                "keys": sorted(self.output.keys()),
                "size_bytes": len(output_json.encode()),
            },
            "evidence": self.evidence,
            "findings": self.findings,
            "channels": self.channels,
            "metrics": self.metrics,
            "producer": self.producer,
            "extensions": self.extensions,
        }

    @property
    def idempotency_key(self) -> str:
        raw = f"{self.project.pk}:{self.stage.value}:{self.source_id}:{self.workflow_id}:{self.supersedes_output_id}:{self.capability_id}"
        return hashlib.sha256(raw.encode()).hexdigest()


class OutputAdapter:
    stage: OutputStage

    def build(self, **kwargs) -> OutputEnvelope:
        return OutputEnvelope(stage=self.stage, **kwargs)


class CaseReviewAdapter(OutputAdapter): stage = OutputStage.CASE_REVIEW
class CodeReviewAdapter(OutputAdapter): stage = OutputStage.CODE_REVIEW
class KnowledgeQueryAdapter(OutputAdapter): stage = OutputStage.KNOWLEDGE_QUERY
class RiskIdentificationAdapter(OutputAdapter): stage = OutputStage.RISK_IDENTIFICATION
class TestPlanGenerationAdapter(OutputAdapter): stage = OutputStage.TEST_PLAN_GENERATION
class TestcaseGenerationAdapter(OutputAdapter): stage = OutputStage.TESTCASE_GENERATION
class TestExecutionAdapter(OutputAdapter): stage = OutputStage.TEST_EXECUTION
class IssueTrackingAdapter(OutputAdapter): stage = OutputStage.ISSUE_TRACKING


ADAPTERS = {adapter.stage.value: adapter() for adapter in (
    CaseReviewAdapter, CodeReviewAdapter, KnowledgeQueryAdapter, RiskIdentificationAdapter,
    TestPlanGenerationAdapter, TestcaseGenerationAdapter, TestExecutionAdapter, IssueTrackingAdapter,
)}


def publish_output(envelope: OutputEnvelope):
    from .services import record_task_output
    payload = envelope.canonical()
    content = json.dumps(envelope.output, ensure_ascii=False, sort_keys=True)
    return record_task_output(
        project=envelope.project, user=envelope.user, task_type=envelope.stage.value,
        task_id=envelope.source_id, query=envelope.input_summary, content=content,
        channels=envelope.channels, candidates=envelope.findings, citations=envelope.evidence,
        timings={"total_ms": envelope.metrics.get("latency_ms", 0)},
        token_usage=envelope.metrics.get("token_usage", 0),
        metadata={"protocol": payload, "idempotency_key": envelope.idempotency_key},
        policy_version=envelope.producer.get("policy_version", "platform-output/v1"),
        prompt_version=envelope.producer.get("prompt_version", "platform-output/v1"),
        model_version=envelope.producer.get("model_version", ""), status=envelope.status,
        capability=envelope.capability,
    )
