"""Critic Agent 的可定位证据包。

该模块只暴露可审计的输入/输出摘要、工具调用、检索记录和报告行，
不返回模型隐藏思维链。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def _hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _summary(value, limit: int = 500) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text.replace("\x00", "")[:limit]


@dataclass(frozen=True)
class CriticEvidence:
    evidence_id: str
    type: str
    source_id: str
    source_hash: str
    summary: str
    locatable: bool = True
    payload_ref: str = ""

    def as_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "type": self.type,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "summary": self.summary,
            "locatable": self.locatable,
            "payload_ref": self.payload_ref,
        }


class CriticEvidencePackage:
    """将报告与运行轨迹转换为可被 LLM 引用、服务端反查的证据索引。"""

    @classmethod
    def build(cls, *, output, scan) -> "CriticEvidencePackage":
        entries: list[CriticEvidence] = []
        entries.extend(cls._report_entries(scan))
        entries.extend(cls._trace_entries(output))
        return cls(entries)

    def __init__(self, entries):
        self.entries = tuple(entries)
        self._by_id = {entry.evidence_id: entry for entry in self.entries}

    def resolve(self, evidence_ids) -> tuple[list[dict], list[str]]:
        resolved, missing = [], []
        for evidence_id in dict.fromkeys(str(item) for item in (evidence_ids or []) if str(item)):
            entry = self._by_id.get(evidence_id)
            if entry is None or not entry.locatable:
                missing.append(evidence_id)
            else:
                resolved.append(entry.as_dict())
        return resolved, missing

    def as_prompt_view(self) -> list[dict]:
        return [entry.as_dict() for entry in self.entries]

    @staticmethod
    def _entry(*, type: str, source_id: str, payload, summary: str, payload_ref=""):
        source_hash = _hash(payload)
        return CriticEvidence(
            evidence_id=f"{type}:{source_hash[:20]}",
            type=type,
            source_id=source_id,
            source_hash=source_hash,
            summary=_summary(summary),
            locatable=bool(source_id),
            payload_ref=payload_ref,
        )

    @classmethod
    def _report_entries(cls, scan) -> list[CriticEvidence]:
        entries = []
        for defect in scan.defects:
            for index, sample in enumerate(defect.samples):
                source_id = ":".join(filter(None, (
                    "report", str(sample.get("sheet") or "问题明细"),
                    str(sample.get("row") or sample.get("case_id") or index + 1),
                )))
                entries.append(cls._entry(
                    type="report_row", source_id=source_id,
                    payload={"category": defect.category, "issue_type": defect.issue_type, **sample},
                    summary=(
                        f"{defect.issue_type}；原始信号 {defect.category}；"
                        f"{sample.get('reason') or '人工已确认'}"
                    ),
                ))
        return entries

    @classmethod
    def _trace_entries(cls, output) -> list[CriticEvidence]:
        entries = []
        trace = getattr(output, "trace", None)
        metadata = getattr(output, "metadata", None) or {}
        input_payload = metadata.get("input_snapshot") or metadata.get("input")
        if input_payload:
            entries.append(cls._entry(
                type="input_snapshot", source_id=f"output:{output.pk}", payload=input_payload,
                summary=_summary(input_payload), payload_ref=f"generation-output:{output.pk}",
            ))
        actual_output = getattr(output, "content", None) or metadata.get("output")
        if actual_output:
            entries.append(cls._entry(
                type="output_comparison", source_id=f"output:{output.pk}", payload=actual_output,
                summary=_summary(actual_output), payload_ref=f"generation-output:{output.pk}",
            ))
        if trace is None:
            return entries
        for span in trace.spans.order_by("sequence", "created_at"):
            if span.step_type == "tool":
                evidence_type = "tool_call"
            elif span.step_type in {"retrieval", "graph_retrieval"}:
                evidence_type = "retrieval_hit"
            else:
                evidence_type = "decision_summary"
            payload = {
                "step_type": span.step_type, "tool_name": span.tool_name,
                "status": span.status, "error_type": span.error_type,
                "error_message": span.error_message, "evidence": span.evidence,
            }
            summary = (
                f"{span.get_step_type_display()}；状态 {span.status}"
                + (f"；工具 {span.tool_name}" if span.tool_name else "")
                + (f"；错误 {span.error_message}" if span.error_message else "")
            )
            entries.append(cls._entry(
                type=evidence_type, source_id=f"span:{span.pk}", payload=payload,
                summary=summary, payload_ref=f"execution-span:{span.pk}",
            ))
        return entries
