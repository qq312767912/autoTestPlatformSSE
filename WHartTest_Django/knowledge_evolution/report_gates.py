"""T16：报告契约校验、阶段/端到端评测与下游责任定位。

为什么报告阶段需要一道**独立于评测分数**的硬校验：

报告是全链路测试的终态，正文里必须引用上游三个阶段（方案 / 用例 / 执行）的产出。
如果引用缺失或正文不合法，"这份报告通过了"就无从谈起——它压根没有可核验的依据。
分层评测（l0–l3）衡量的是"文本像不像一份好报告"，它**看不出引用是不是真的指向了
本项目、本链路的产出**：一份把别人的产出 ID 抄进来、或者引用了根本不存在的 ID 的
报告，在文案层面同样可以写得很像样。所以这里补一道确定性契约校验，并把结论写进门禁。

契约由两部分组成，**任一不满足即报告门禁失败**：

1. **上游引用完整性**（``upstream``）——引用必须覆盖
   ``test_plan_generation`` / ``testcase_generation`` / ``test_execution``，
   且每个被引用的产出必须真实存在、属于同一项目、同一 ``workflow_id``；
2. **正文契约**（``schema``）——正文必须是合法 JSON 对象，且显式给出
   覆盖率、通过率、失败分布、未闭环问题四项。**未闭环问题即使为空也必须显式写出来**，
   否则"确实没有未闭环问题"和"忘了写这一项"在报告里长得一模一样，
   而后者恰恰是最需要被发现的情况。

引用来源取两份的并集：统一协议里的 ``parent_output_ids``（各适配器已经写了）
以及正文里显式列出的引用字段。取并集而不是二选一，是为了让"协议带引用"和
"正文内联引用"两种产出风格都能通过，同时只要两处都没写就必然失败。

本模块只做只读校验 + 评测计算；唯一写动作是 ``WorkflowEvaluationService.evaluate_and_record``
把评测结论落到门禁的 ``detail`` 上（供页面读取），不改变门禁状态机。
"""
from __future__ import annotations

import json
import logging
from statistics import mean
from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone

from .capability_registry import STAGE_LABELS, WORKFLOW_STAGES
from .evaluation_models import EvaluationResult
from .models import GenerationOutput

logger = logging.getLogger(__name__)

REPORT_STAGE = "report_generation"

#: 报告必须引用的上游阶段（顺序即链路顺序）。
UPSTREAM_STAGES: tuple[str, ...] = tuple(
    stage for stage in WORKFLOW_STAGES if stage != REPORT_STAGE
)

#: 报告正文契约版本。写进校验结果，便于将来放宽/收紧时区分历史结论。
REPORT_SCHEMA_VERSION = "workflow-report/v1"

#: 引用字段别名：阶段 -> 正文中可能出现的引用字段名。
#: 规范来源始终是协议 ``parent_output_ids``；这里的别名只是为了兼容
#: "把引用内联进正文"的产出风格，不是第二套规范。
REF_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "test_plan_generation": ("plan_output_ids", "plan_refs", "test_plan_output_ids"),
    "testcase_generation": ("case_output_ids", "case_refs", "testcase_output_ids"),
    "test_execution": ("execution_output_ids", "execution_refs", "test_execution_output_ids"),
}

#: 四项必需统计：规范字段名 -> (类型, 中文名, 别名)。
#: 类型取值：``number`` / ``mapping`` / ``sequence``。
REQUIRED_STATS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "coverage": ("number", "覆盖率", ("coverage_rate",)),
    "pass_rate": ("number", "通过率", ("passing_rate",)),
    "failure_distribution": ("mapping", "失败分布", ("failures",)),
    "unclosed_issues": ("sequence", "未闭环问题", ("open_issues",)),
}

#: 取值范围必须在 [0, 1] 的字段。
RATE_FIELDS = ("coverage", "pass_rate")


class ReportContractError(ValidationError):
    """报告契约不满足。继承 Django ``ValidationError``，视图层可直接转 DRF 版本。"""


def _is_number(value: Any) -> bool:
    # ``bool`` 是 ``int`` 的子类，不排掉的话 ``True`` 会被当成 1.0 的覆盖率。
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pick(body: dict, aliases: tuple[str, ...]) -> tuple[str, Any]:
    """按 (规范名, 别名...) 顺序取第一个存在的键。返回 (命中的键名, 值)。"""
    for key in aliases:
        if key in body:
            return key, body[key]
    return "", None


def _matched_type(value: Any, expected: str) -> bool:
    if expected == "number":
        return _is_number(value)
    if expected == "mapping":
        # 允许 dict（按失败原因聚合）或 list（按失败条目罗列）。
        return isinstance(value, (dict, list))
    if expected == "sequence":
        return isinstance(value, list)
    return False


class ReportGateService:
    """报告契约校验；结论同时供门禁、页面和端到端评测读取。"""

    @staticmethod
    def body(output) -> tuple[dict, bool]:
        """解析报告正文。返回 ``(body, parsable)``；不可解析时 body 为空字典。"""
        raw = (getattr(output, "content", "") or "").strip()
        if not raw:
            return {}, False
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}, False
        if not isinstance(parsed, dict):
            return {}, False
        return parsed, True

    @staticmethod
    def _collect_references(output, body: dict) -> dict:
        """汇总引用来源：协议 parent_output_ids ∪ 正文内联引用字段。"""
        protocol = ((output.metadata or {}).get("protocol") or {})
        protocol_ids = [
            str(item) for item in (protocol.get("parent_output_ids") or []) if str(item or "")
        ]
        by_stage: dict[str, list[str]] = {stage: [] for stage in UPSTREAM_STAGES}
        inline_ids: list[str] = []
        for stage, aliases in REF_FIELD_ALIASES.items():
            key, value = _pick(body, aliases)
            if not key or not isinstance(value, list):
                continue
            ids = [str(item) for item in value if str(item or "")]
            inline_ids.extend(ids)
            by_stage[stage].extend(ids)
        return {
            "protocol_ids": protocol_ids,
            "inline_ids": inline_ids,
            "all_ids": list(dict.fromkeys(protocol_ids + inline_ids)),
            "by_stage": by_stage,
        }

    @classmethod
    def validate(cls, output) -> dict:
        """校验一份报告产出；**不抛异常**，把结论作为数据返回。

        返回结构固定，页面与门禁读同一份：
        ``ok`` / ``upstream`` / ``schema`` / ``errors``。
        """
        output = cls._as_output(output)
        protocol = ((output.metadata or {}).get("protocol") or {})
        workflow_id = str(protocol.get("workflow_id") or "")
        body, parsable = cls.body(output)
        refs = cls._collect_references(output, body)
        errors: list[str] = []

        # ---------------- 上游引用完整性 ----------------
        known_ids = set()
        resolved: dict[str, Any] = {}
        if refs["all_ids"]:
            for item in GenerationOutput.objects.filter(
                project_id=output.project_id, pk__in=refs["all_ids"]
            ).select_related("skill_version"):
                resolved[str(item.pk)] = item
                known_ids.add(str(item.pk))

        unresolved = [item for item in refs["all_ids"] if item not in known_ids]
        foreign: list[str] = []
        covered: set[str] = set()
        for item_id, item in resolved.items():
            item_protocol = ((item.metadata or {}).get("protocol") or {})
            item_stage = str(item_protocol.get("stage") or item.task_type or "")
            item_workflow = str(item_protocol.get("workflow_id") or "")
            if workflow_id and item_workflow and item_workflow != workflow_id:
                # 同项目但另一条流水线：引用它等于把别条链路的结论挪过来用。
                foreign.append(item_id)
                continue
            if item_stage in UPSTREAM_STAGES:
                covered.add(item_stage)

        missing_stages = [stage for stage in UPSTREAM_STAGES if stage not in covered]
        upstream_ok = not missing_stages and not unresolved and not foreign
        if missing_stages:
            errors.append(
                "报告未引用上游阶段产出："
                + "、".join(STAGE_LABELS.get(stage, stage) for stage in missing_stages)
            )
        if unresolved:
            errors.append(f"报告引用了不存在的产出 ID：{'、'.join(unresolved)}")
        if foreign:
            errors.append(f"报告引用了不属于本链路（{workflow_id or '未指定工作流'}）的产出：{'、'.join(foreign)}")

        # ---------------- 正文契约 ----------------
        missing_fields: list[str] = []
        type_errors: list[str] = []
        range_errors: list[str] = []
        stats: dict[str, Any] = {}
        if not parsable:
            errors.append("报告正文不是合法的 JSON 对象，无法校验覆盖率/通过率/失败分布/未闭环问题")
        else:
            for canonical, (expected, label, aliases) in REQUIRED_STATS.items():
                key, value = _pick(body, (canonical,) + aliases)
                if not key:
                    missing_fields.append(canonical)
                    errors.append(f"报告缺少「{label}」（字段名 {canonical}）")
                    continue
                if not _matched_type(value, expected):
                    type_errors.append(f"{canonical}:{type(value).__name__}")
                    errors.append(f"报告「{label}」字段类型不合法：{key}")
                    continue
                if canonical in RATE_FIELDS:
                    if not 0.0 <= float(value) <= 1.0:
                        range_errors.append(f"{canonical}={value}")
                        errors.append(f"报告「{label}」超出 0~1 取值区间：{value}")
                        continue
                    stats[canonical] = round(float(value), 6)
                elif canonical == "failure_distribution":
                    entries = len(value)
                    stats[canonical] = value
                    stats["failure_entry_count"] = entries
                else:
                    stats[canonical] = value
                    stats["unclosed_issue_count"] = len(value)
        schema_ok = (
            parsable and not missing_fields and not type_errors and not range_errors
        )

        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "ok": bool(upstream_ok and schema_ok),
            "checked_at": timezone.now().isoformat(),
            "output_id": str(output.pk),
            "workflow_id": workflow_id,
            "upstream": {
                "ok": upstream_ok,
                "required_stages": list(UPSTREAM_STAGES),
                "covered_stages": sorted(covered),
                "missing_stages": missing_stages,
                "missing_stage_labels": [
                    STAGE_LABELS.get(stage, stage) for stage in missing_stages
                ],
                "referenced_output_ids": refs["all_ids"],
                "from_protocol": refs["protocol_ids"],
                "from_body": refs["inline_ids"],
                "unresolved_output_ids": unresolved,
                "foreign_output_ids": foreign,
            },
            "schema": {
                "ok": schema_ok,
                "parsable": parsable,
                "required_fields": list(REQUIRED_STATS),
                "missing_fields": missing_fields,
                "type_errors": type_errors,
                "range_errors": range_errors,
                "stats": stats,
            },
            "errors": errors,
        }

    @classmethod
    def assert_report_ready(cls, output) -> dict:
        """校验不通过则抛 ``ReportContractError``；通过则返回校验结果。"""
        result = cls.validate(output)
        if not result["ok"]:
            raise ReportContractError(result["errors"] or ["报告契约校验未通过"])
        return result

    @classmethod
    def is_report_output(cls, output) -> bool:
        if output is None:
            return False
        protocol = ((getattr(output, "metadata", None) or {}).get("protocol") or {})
        stage = str(protocol.get("stage") or getattr(output, "task_type", "") or "")
        return stage == REPORT_STAGE

    @staticmethod
    def _as_output(output):
        if isinstance(output, GenerationOutput):
            return output
        found = GenerationOutput.objects.filter(pk=output).first()
        if found is None:
            raise ValidationError(f"产出不存在：{output}")
        return found


class WorkflowEvaluationService:
    """同一份产出同时给出**阶段独立评测**与**四阶段端到端评测**（R8）。

    为什么两者都要，不能只取其一：

    - 只看阶段评测，无法回答"这一阶段变好了，但整条链路是不是还通"——一份改进了
      文案的方案可能导致下游用例阶段读不懂；
    - 只看端到端，无法回答"这次变化到底改善了哪一段"——端到端掉分时不知道是谁的锅，
      也就无法把候选退回给正确的子单元。

    两者都是**确定性**计算：阶段评测取该产出已完成的分层评测结果（没有结果就如实
    记 ``judged=False``，不伪造分数）；端到端评测取四阶段覆盖、门禁状态、版本锁
    与报告契约。不调用模型，因此可以在门禁里反复执行。
    """

    SCOPE_STAGE = "stage"
    SCOPE_END_TO_END = "end_to_end"
    DEFAULT_THRESHOLD = 0.7

    @classmethod
    def stage_scores(cls, output, *, threshold: float | None = None) -> dict:
        """取某产出已完成的分层评测均分（无结果时返回空字典）。"""
        results = list(
            EvaluationResult.objects.filter(
                case__source_output_id=output.pk, status="completed",
            )
        )
        scores = {}
        for level in range(4):
            values = [
                float(getattr(item, f"l{level}_score"))
                for item in results
                if getattr(item, f"l{level}_score") is not None
            ]
            if values:
                scores[f"l{level}"] = round(mean(values), 4)
        return {
            "scores": scores,
            "sample_count": len(results),
            "threshold": cls.DEFAULT_THRESHOLD if threshold is None else threshold,
        }

    @classmethod
    def evaluate_stage(cls, *, output, threshold: float | None = None, actor=None) -> dict:
        """阶段独立评测：只看这一阶段自己的产出与分层评测结果。"""
        output = ReportGateService._as_output(output)
        protocol = ((output.metadata or {}).get("protocol") or {})
        stage = str(protocol.get("stage") or output.task_type or "")
        threshold = cls.DEFAULT_THRESHOLD if threshold is None else threshold
        stats = cls.stage_scores(output, threshold=threshold)
        scores = stats["scores"]

        contract = ReportGateService.validate(output) if stage == REPORT_STAGE else None
        scored_ok = bool(scores) and all(value >= threshold for value in scores.values())
        contract_ok = contract is None or contract["ok"]
        judged = bool(scores) or contract is not None
        passed = (scored_ok and contract_ok) if judged else None

        if not scores:
            reason = "该阶段尚无已完成的分层评测结果，无法给出分数"
        elif not scored_ok:
            reason = f"分层评测未达阈值 {threshold}"
        elif not contract_ok:
            reason = "报告契约校验未通过"
        else:
            reason = "阶段评测通过"

        return {
            "scope": cls.SCOPE_STAGE,
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "output_id": str(output.pk),
            "workflow_id": str(protocol.get("workflow_id") or ""),
            "skill_version_id": str(output.skill_version_id or ""),
            "package_sha256": output.skill_package_sha256 or "",
            "scores": scores,
            "sample_count": stats["sample_count"],
            "threshold": threshold,
            "judged": judged,
            "passed": passed,
            "report_contract": contract,
            "reason": reason,
            "checked_at": timezone.now().isoformat(),
        }

    @classmethod
    def evaluate_end_to_end(cls, *, project, workflow_id: str, threshold: float | None = None,
                            actor=None) -> dict:
        """四阶段端到端评测：链路是否真的完整、每段是否放行、版本是否锁定一致。"""
        from .workflow_models import WorkflowSkillLock, WorkflowStageGate

        workflow_id = str(workflow_id or "").strip()
        threshold = cls.DEFAULT_THRESHOLD if threshold is None else threshold
        outputs: dict[str, Any] = {}
        for output in GenerationOutput.objects.filter(project_id=project.pk):
            protocol = ((output.metadata or {}).get("protocol") or {})
            if str(protocol.get("workflow_id") or "") != workflow_id:
                continue
            outputs[str(protocol.get("stage") or output.task_type or "")] = output
        gates = {
            gate.stage: gate
            for gate in WorkflowStageGate.objects.filter(
                project_id=project.pk, workflow_id=workflow_id
            )
        }
        locks = {
            lock.stage: lock
            for lock in WorkflowSkillLock.objects.filter(
                project_id=project.pk, workflow_id=workflow_id
            ).select_related("skill", "skill_version")
            if lock.stage
        }

        stages = []
        missing_stages: list[str] = []
        open_gates: list[str] = []
        version_pins: list[dict] = []
        for stage in WORKFLOW_STAGES:
            output = outputs.get(stage)
            gate = gates.get(stage)
            lock = locks.get(stage)
            if output is None:
                missing_stages.append(stage)
            gate_status = gate.status if gate is not None else ("pending" if output else "missing")
            gate_passed = gate_status in {"passed", "overridden"}
            if output is not None and not gate_passed:
                open_gates.append(stage)
            pin = {
                "stage": stage,
                "label": STAGE_LABELS.get(stage, stage),
                "version_locked": lock is not None,
                "skill_id": str(lock.skill_id) if lock and lock.skill_id else "",
                "skill_name": (lock.skill.name if lock and lock.skill_id else ""),
                "skill_version_id": str(lock.skill_version_id) if lock and lock.skill_version_id else "",
                "version": (lock.skill_version.version if lock and lock.skill_version_id else ""),
                "package_sha256": (lock.package_sha256 if lock else ""),
            }
            if not pin["skill_version_id"] and output is not None and output.skill_version_id:
                # 没有锁但有产出自身携带的版本：如实标注来源为产出，而不是假装锁上了。
                pin.update({
                    "source": "output",
                    "skill_version_id": str(output.skill_version_id),
                    "version": (output.skill_version.version if output.skill_version_id else ""),
                    "package_sha256": output.skill_package_sha256 or "",
                })
            else:
                pin["source"] = "lock" if lock is not None else ""
            version_pins.append(pin)
            stages.append({
                "stage": stage,
                "label": STAGE_LABELS.get(stage, stage),
                "has_output": output is not None,
                "output_id": str(output.pk) if output is not None else "",
                "gate_status": gate_status,
                "gate_passed": gate_passed,
                "gate_reason": gate.reason if gate is not None else "",
                "overridden": gate_status == "overridden",
            })

        coverage = round(len(WORKFLOW_STAGES) - len(missing_stages), 6) / len(WORKFLOW_STAGES)
        report_output = outputs.get(REPORT_STAGE)
        report = ReportGateService.validate(report_output) if report_output is not None else None
        report_ok = bool(report and report["ok"])

        failures: list[str] = []
        if missing_stages:
            failures.append(
                "链路缺少阶段产出：" + "、".join(STAGE_LABELS.get(s, s) for s in missing_stages)
            )
        if open_gates:
            failures.append(
                "阶段门禁未放行：" + "、".join(STAGE_LABELS.get(s, s) for s in open_gates)
            )
        if report_output is None:
            failures.append("尚无报告阶段产出，无法判定链路终态")
        elif not report_ok:
            failures.append("报告契约校验未通过")

        return {
            "scope": cls.SCOPE_END_TO_END,
            "workflow_id": workflow_id,
            "project_id": project.pk,
            "stage_order": list(WORKFLOW_STAGES),
            "stages": stages,
            "coverage": coverage,
            "covered_count": len(WORKFLOW_STAGES) - len(missing_stages),
            "stage_count": len(WORKFLOW_STAGES),
            "missing_stages": missing_stages,
            "open_gates": open_gates,
            "version_pins": version_pins,
            "version_locked_count": sum(1 for pin in version_pins if pin["version_locked"]),
            "report_contract": report,
            "passed": not missing_stages and not open_gates and report_ok,
            "threshold": threshold,
            "failures": failures,
            "checked_at": timezone.now().isoformat(),
        }

    @classmethod
    def evaluate_and_record(cls, *, project, workflow_id: str, stage: str = REPORT_STAGE,
                            actor=None) -> dict:
        """同时生成两份评测，并把结论落到该阶段门禁的 ``detail``（供页面读取）。

        刻意不改变门禁状态：状态机只由 ``WorkflowGateService.evaluate`` 推进。
        评测是**输入**，不是放行决定本身——否则"跑一次评测"就等同于"批准进入下一步"。
        """
        from .workflow_models import WorkflowStageGate

        workflow_id = str(workflow_id or "").strip()
        stage = str(stage or REPORT_STAGE)
        output = None
        for candidate in GenerationOutput.objects.filter(project_id=project.pk):
            protocol = ((candidate.metadata or {}).get("protocol") or {})
            if str(protocol.get("workflow_id") or "") != workflow_id:
                continue
            if str(protocol.get("stage") or candidate.task_type or "") == stage:
                output = candidate
                break

        payload = {
            "stage": (
                cls.evaluate_stage(output=output, actor=actor) if output is not None
                else {
                    "scope": cls.SCOPE_STAGE, "stage": stage,
                    "stage_label": STAGE_LABELS.get(stage, stage),
                    "output_id": "", "judged": False, "passed": None,
                    "reason": "该阶段尚未产生产出", "scores": {},
                    "checked_at": timezone.now().isoformat(),
                }
            ),
            "end_to_end": cls.evaluate_end_to_end(
                project=project, workflow_id=workflow_id, actor=actor,
            ),
            "recorded_at": timezone.now().isoformat(),
        }

        gate = WorkflowStageGate.objects.filter(
            project_id=project.pk, workflow_id=workflow_id, stage=stage
        ).first()
        if gate is not None:
            gate.detail = {**(gate.detail or {}), "evaluation": payload}
            gate.save(update_fields=["detail", "updated_at"])
            payload["recorded"] = True
            payload["gate_id"] = str(gate.pk)
        else:
            payload["recorded"] = False
            payload["gate_id"] = ""
        return payload
