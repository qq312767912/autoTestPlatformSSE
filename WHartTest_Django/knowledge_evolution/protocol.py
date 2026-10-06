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
    REPORT_GENERATION = "report_generation"


#: 需要 ``workflow_id`` 的阶段集合 = 新链路四阶段 ∪ 历史链路四阶段。
#:
#: 为什么是并集而不是只放新四阶段：存量流程的产出仍会带着
#: ``test_plan_generation`` / ``report_generation`` 进来，只认新四阶段会让它们
#: 在发布时因为"没带 workflow_id"被拒——而它们本来是有 workflow_id 的，
#: 被拒的原因只是口径变了。反过来，``risk_identification`` / ``issue_tracking``
#: 已升为链路阶段，从此要求带 ``workflow_id`` 才是正确行为。
WORKFLOW_STAGES = {
    OutputStage.RISK_IDENTIFICATION, OutputStage.TESTCASE_GENERATION,
    OutputStage.TEST_EXECUTION, OutputStage.ISSUE_TRACKING,
    OutputStage.TEST_PLAN_GENERATION, OutputStage.REPORT_GENERATION,
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
    #: 本次产出所依赖的 Skill 版本（T08 的任务锁解析结果）。
    #: 放在协议层而不是让各业务自己往 metadata 里塞，是因为下游的反馈绑定、金标候选、
    #: 归因与派生全都要读它——一旦有业务漏填，闭环就会在那一环断掉却查不出原因。
    skill_version: Any = None
    #: ``stage-result/v1`` 信封是否随本次产出提交（T05）。**可选**：存量 Skill 不提交
    #: 时保持 ``False``，平台按 L0/L1 处理，照样发布——把"有没有信封"当发布前提
    #: 会让这次升级一次性卡死所有存量 Skill。
    #:
    #: 这里只记"有没有"、不记正文：信封正文是 Skill 的产物文件，需要时按主产物名
    #: 去读文件重新校验；把正文复制进协议元数据会让每条产出都多带一份内容副本
    #: （与 ``output_descriptor`` 只存哈希同一条理由）。
    stage_result: bool = False
    #: 该产出实测到的兼容等级（L0–L3）。由 ``stage_outputs.classify_level`` 算出，
    #: 不接受调用方直接声明——声明值放在 Skill 版本的 manifest 里，是另一回事。
    compatibility_level: str = "L0"
    #: 校验结论摘要（错误路径清单、声明与实测的差距等）。只存摘要，不存整份 payload，
    #: 避免把产出内容复制进协议元数据、扩大访问面。
    stage_result_validation: dict[str, Any] | None = None
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
        # 引用 ID 一律归一成字符串。调用方很自然会直接传 ``output.pk``（UUID 对象），
        # 而 UUID 不是 JSON 可序列化类型——`metadata` 落库前会整体退化成 ``str(dict)``，
        # 后果不是"丢一个字段"，而是下游读 ``metadata["protocol"]`` 时直接抛
        # ``AttributeError``，门禁登记当场失败、产出也拿不到版本溯源。
        # 在协议入口收口，比要求每个业务方都记得 ``str()`` 可靠。
        self.parent_output_ids = [
            str(item) for item in (self.parent_output_ids or []) if str(item or "")
        ]
        self.supersedes_output_id = str(self.supersedes_output_id or "")
        self.workflow_id = str(self.workflow_id or "")
        return self

    @property
    def capability_id(self) -> str:
        if self.capability is None:
            return ""
        return str(self.capability.pk if hasattr(self.capability, "pk") else self.capability)

    @property
    def skill_descriptor(self) -> dict[str, Any]:
        """产出自带的 Skill 版本指纹，写进协议元数据供事后核对。

        刻意只写标识与哈希、不写包内容：协议元数据会被多处读取与回显，
        把包内容塞进去等于把 Skill 全文复制到每条产出上。
        """
        version = self.skill_version
        if version is None:
            return {"skill_id": "", "skill_version_id": "", "version": "", "package_sha256": ""}
        return {
            "skill_id": str(getattr(version, "skill_id", "") or ""),
            "skill_version_id": str(getattr(version, "pk", "") or ""),
            "version": str(getattr(version, "version", "") or ""),
            "package_sha256": str(getattr(version, "package_sha256", "") or ""),
        }

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
            "skill": self.skill_descriptor,
            "compatibility_level": self.compatibility_level,
            "stage_result_submitted": self.stage_result,
            "stage_result_validation": self.stage_result_validation or {},
            "extensions": self.extensions,
        }

    def attach_stage_result(self, validation: Any) -> "OutputEnvelope":
        """挂上 ``stage-result/v1`` 的校验结论。

        参数收的是任何有 ``as_dict()`` 的对象（实际类型是
        ``stage_outputs.StageResultValidation``），协议层刻意不 import 校验器：
        协议要能被只想发一条产出的调用方单独使用，不该被 schema 库拖进来。
        """
        if validation is None:
            return self
        summary = validation.as_dict() if hasattr(validation, "as_dict") else dict(validation)
        self.stage_result_validation = summary
        self.compatibility_level = str(summary.get("effective_level") or "L0")
        self.stage_result = True
        return self

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
class ReportGenerationAdapter(OutputAdapter): stage = OutputStage.REPORT_GENERATION


ADAPTERS = {adapter.stage.value: adapter() for adapter in (
    CaseReviewAdapter, CodeReviewAdapter, KnowledgeQueryAdapter, RiskIdentificationAdapter,
    TestPlanGenerationAdapter, TestcaseGenerationAdapter, TestExecutionAdapter, IssueTrackingAdapter,
    ReportGenerationAdapter,
)}


def publish_output(envelope: OutputEnvelope):
    from .services import record_task_output
    from .operations import WorkflowGateService
    if envelope.evaluation_mode == EvaluationMode.WORKFLOW and envelope.extensions.get("enforce_quality_gate"):
        WorkflowGateService.assert_can_enter(
            envelope.project.pk, envelope.workflow_id, envelope.stage.value
        )
    payload = envelope.canonical()
    content = json.dumps(envelope.output, ensure_ascii=False, sort_keys=True)
    result = record_task_output(
        project=envelope.project, user=envelope.user, task_type=envelope.stage.value,
        task_id=envelope.source_id, query=envelope.input_summary, content=content,
        channels=envelope.channels, candidates=envelope.findings, citations=envelope.evidence,
        timings={"total_ms": envelope.metrics.get("latency_ms", 0)},
        token_usage=envelope.metrics.get("token_usage", 0),
        metadata={
            "protocol": payload,
            "idempotency_key": envelope.idempotency_key,
            # 与业务产物同存一条记录：飞轮要能直接回答"这条产出当时的信封合不合规"，
            # 而不是事后重算——重算需要重建当时的 Skill 版本与文件，多半已经不在。
            "stage_result_submitted": envelope.stage_result,
            "stage_result_validation": envelope.stage_result_validation or {},
            "compatibility_level": envelope.compatibility_level,
        },
        policy_version=envelope.producer.get("policy_version", "platform-output/v1"),
        prompt_version=envelope.producer.get("prompt_version", "platform-output/v1"),
        model_version=envelope.producer.get("model_version", ""), status=envelope.status,
        capability=envelope.capability,
        execution_spans=envelope.extensions.get("execution_spans") or [],
        skill_version=envelope.skill_version,
    )
    if envelope.evaluation_mode == EvaluationMode.WORKFLOW:
        # 走带补偿的登记入口（T14 / §13）：登记失败**不得**反向炸掉业务产出。
        # 业务产出此时已落库且可用，飞轮侧的问题应落成一条可重试、可告警的
        # 补偿记录，而不是让业务页面看到一个 500，也不是只写一行日志。
        from .models import GenerationOutput
        from .registration import FlywheelRegistrationService

        output = GenerationOutput.objects.get(pk=result[1])
        FlywheelRegistrationService.register(
            output,
            actor=envelope.user,
            create_gate=envelope.extensions.get("register_workflow_gate", True),
        )
    return result
