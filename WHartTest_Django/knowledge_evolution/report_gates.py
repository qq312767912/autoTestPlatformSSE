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

from .capability_registry import (
    LEGACY_WORKFLOW_STAGES,
    STAGE_LABELS,
    WORKFLOW_STAGES,
)
from .evaluation_models import EvaluationResult
from .models import GenerationOutput
from .workflow_models import GATE_PASSING_STATES

logger = logging.getLogger(__name__)

#: 报告阶段的阶段名。它既是**当前主链路**的收口阶段（方案 → 用例 → 执行 → 报告），
#: 也是契约字段别名表的默认解析目标。**不要**把它当成"唯一的收口阶段"，
#: 判"是不是收口"请用 ``is_closing_stage``。
REPORT_STAGE = "report_generation"


def _upstream_of(stage: str) -> tuple[str, ...]:
    """某阶段的上游阶段集合 = 包含它的那套模板里除它自己以外的阶段。

    不能按"当前模板取这个、历史模板取那个"来写死：两套模板的**收口阶段刚好互换**
    （当前是 ``report_generation``，历史是 ``issue_tracking``），写死任一边都会让
    另一边的上游集合错掉——严重时收口阶段的上游里会冒出它自己。
    """
    for template in (WORKFLOW_STAGES, LEGACY_WORKFLOW_STAGES):
        if stage in template:
            return tuple(item for item in template if item != stage)
    return ()


#: 收口阶段 -> 契约参数。主链路口径变动过（两套模板的收口分别是报告与问题跟踪），
#: 两个阶段的"收口内容"本来就不同，用同一套必需字段只会逼出编造的数字：
#:
#: - ``report_generation``：完整四件套（覆盖率/通过率/失败分布/未闭环问题）。
#: - ``issue_tracking``：问题跟踪是"问题清单 + 闭环状态"，没有覆盖率与通过率
#:   这两个概念，硬要求只会让人填假数——契约的意义是把编造拦在门外，不是逼人编造。
CLOSING_CONTRACTS: dict[str, dict] = {
    REPORT_STAGE: {
        "upstream": _upstream_of(REPORT_STAGE),
        "stats": ("coverage", "pass_rate", "failure_distribution", "unclosed_issues"),
    },
    "issue_tracking": {
        "upstream": _upstream_of("issue_tracking"),
        "stats": ("failure_distribution", "unclosed_issues"),
    },
}

#: 契约字段别名默认按报告阶段解析；``issue_tracking`` 走同一张表。
DEFAULT_CLOSING_STAGE = REPORT_STAGE

#: 报告正文契约版本。写进校验结果，便于将来放宽/收紧时区分历史结论。
REPORT_SCHEMA_VERSION = "workflow-report/v1"

#: 引用字段别名：阶段 -> 正文中可能出现的引用字段名。
#: 规范来源始终是协议 ``parent_output_ids``；这里的别名只是为了兼容
#: "把引用内联进正文"的产出风格，不是第二套规范。
REF_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "risk_identification": ("risk_output_ids", "risk_refs", "risk_identification_output_ids"),
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
    """收口契约校验；结论同时供门禁、页面和端到端评测读取。"""

    @staticmethod
    def is_closing_stage(stage: str) -> bool:
        """该阶段是不是链路的收口阶段（需要过契约校验）。

        新流程的收口是「报告产出」，存量流程是「问题跟踪」，两个都要认。
        """
        return str(stage or "") in CLOSING_CONTRACTS

    @staticmethod
    def contract_for(stage: str) -> dict:
        """取某收口阶段的契约参数（上游阶段集合 + 必需统计项）。

        未知阶段退化成报告契约：宁可多要几项、让人一眼看出"这阶段的契约是按
        报告那套在要求"，也不要静默放行——契约的意义就是把"没写清结论"挡在门外。
        """
        return CLOSING_CONTRACTS.get(str(stage or ""), CLOSING_CONTRACTS[DEFAULT_CLOSING_STAGE])

    @staticmethod
    def stage_of(output) -> str:
        protocol = ((getattr(output, "metadata", None) or {}).get("protocol") or {})
        return str(protocol.get("stage") or getattr(output, "task_type", "") or "")

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
    def _collect_references(output, body: dict, upstream: tuple[str, ...]) -> dict:
        """汇总引用来源：协议 parent_output_ids ∪ 正文内联引用字段。"""
        protocol = ((output.metadata or {}).get("protocol") or {})
        protocol_ids = [
            str(item) for item in (protocol.get("parent_output_ids") or []) if str(item or "")
        ]
        by_stage: dict[str, list[str]] = {stage: [] for stage in upstream}
        inline_ids: list[str] = []
        for stage, aliases in REF_FIELD_ALIASES.items():
            if stage not in by_stage:
                continue
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
        """校验一份收口产出；**不抛异常**，把结论作为数据返回。

        返回结构固定，页面与门禁读同一份：
        ``ok`` / ``upstream`` / ``schema`` / ``errors``。
        """
        output = cls._as_output(output)
        stage = cls.stage_of(output)
        contract = cls.contract_for(stage)
        upstream = tuple(contract["upstream"])
        required_stats = tuple(contract["stats"])
        protocol = ((output.metadata or {}).get("protocol") or {})
        workflow_id = str(protocol.get("workflow_id") or "")
        body, parsable = cls.body(output)
        refs = cls._collect_references(output, body, upstream)
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
            if item_stage in upstream:
                covered.add(item_stage)

        missing_stages = [name for name in upstream if name not in covered]
        upstream_ok = not missing_stages and not unresolved and not foreign
        if missing_stages:
            errors.append(
                f"{STAGE_LABELS.get(stage, stage)}未引用上游阶段产出："
                + "、".join(STAGE_LABELS.get(name, name) for name in missing_stages)
            )
        if unresolved:
            errors.append(f"引用了不存在的产出 ID：{'、'.join(unresolved)}")
        if foreign:
            errors.append(f"引用了不属于本链路（{workflow_id or '未指定工作流'}）的产出：{'、'.join(foreign)}")

        # ---------------- 正文契约 ----------------
        missing_fields: list[str] = []
        type_errors: list[str] = []
        range_errors: list[str] = []
        stats: dict[str, Any] = {}
        if not parsable:
            errors.append(
                "产出正文不是合法的 JSON 对象，无法校验"
                + "/".join(REQUIRED_STATS[item][1] for item in required_stats)
            )
        else:
            for canonical in required_stats:
                expected, label, aliases = REQUIRED_STATS[canonical]
                key, value = _pick(body, (canonical,) + aliases)
                if not key:
                    missing_fields.append(canonical)
                    errors.append(f"缺少「{label}」（字段名 {canonical}）")
                    continue
                if not _matched_type(value, expected):
                    type_errors.append(f"{canonical}:{type(value).__name__}")
                    errors.append(f"「{label}」字段类型不合法：{key}")
                    continue
                if canonical in RATE_FIELDS:
                    if not 0.0 <= float(value) <= 1.0:
                        range_errors.append(f"{canonical}={value}")
                        errors.append(f"「{label}」超出 0~1 取值区间：{value}")
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
                # 契约按阶段不同（报告生成 vs 问题跟踪），所以把"这一份按哪套判"也回给页面：
                # 否则页面看到"缺覆盖率"会以为系统坏了，其实是这一阶段的契约里没有它。
                "stage": stage,
                "required_stages": list(upstream),
                "covered_stages": sorted(covered),
                "missing_stages": missing_stages,
                "missing_stage_labels": [
                    STAGE_LABELS.get(name, name) for name in missing_stages
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
                "required_fields": list(required_stats),
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
        return cls.is_closing_stage(stage)

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

        contract = ReportGateService.validate(output) if ReportGateService.is_closing_stage(stage) else None
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
        # 端到端覆盖率的分母必须是**这条流程自己的阶段数**：口径变更后退化成
        # 全局默认序列，会让存量流程永远算出"缺风险识别、缺问题跟踪"，
        # 于是历史链路永远判不通过。
        from .operations import WorkflowGateService

        stage_order = WorkflowGateService.stage_order_for(project.pk, workflow_id)
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
        for stage in stage_order:
            output = outputs.get(stage)
            gate = gates.get(stage)
            lock = locks.get(stage)
            if output is None:
                missing_stages.append(stage)
            gate_status = gate.status if gate is not None else ("pending" if output else "missing")
            # 放行集合必须与 ``WorkflowStageGate.GATE_PASSING_STATES`` 同源：
            # 端到端覆盖率的"是否放行"若自己写一份，新增 ``confirmed`` 之后
            # 会出现"页面显示可继续、端到端评测却报 open_gates"的两套结论。
            gate_passed = gate_status in GATE_PASSING_STATES
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

        stage_count = len(stage_order) or 1
        coverage = round(stage_count - len(missing_stages), 6) / stage_count
        closing_stage = stage_order[-1] if stage_order else DEFAULT_CLOSING_STAGE
        report_output = outputs.get(closing_stage)
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
            failures.append(
                f"尚无{STAGE_LABELS.get(closing_stage, closing_stage)}阶段产出，无法判定链路终态"
            )
        elif not report_ok:
            failures.append(
                f"{STAGE_LABELS.get(closing_stage, closing_stage)}阶段的收口契约校验未通过"
            )

        return {
            "scope": cls.SCOPE_END_TO_END,
            "workflow_id": workflow_id,
            "project_id": project.pk,
            "stage_order": list(stage_order),
            "stages": stages,
            "coverage": coverage,
            "covered_count": stage_count - len(missing_stages),
            "stage_count": stage_count,
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
    def evaluate_and_record(cls, *, project, workflow_id: str, stage: str = "",
                            actor=None) -> dict:
        """同时生成两份评测，并把结论落到该阶段门禁的 ``detail``（供页面读取）。

        刻意不改变门禁状态：状态机只由 ``WorkflowGateService.evaluate`` 推进。
        评测是**输入**，不是放行决定本身——否则"跑一次评测"就等同于"批准进入下一步"。

        Args:
            stage: 缺省时取**该流程的收口阶段**（新流程=问题跟踪，存量=报告生成），
                而不是写死报告阶段：写死之后新链路的端到端评测会落不到任何门禁上，
                页面上表现为"跑了评测但哪里都没变"。
        """
        from .operations import WorkflowGateService
        from .workflow_models import WorkflowStageGate

        workflow_id = str(workflow_id or "").strip()
        if not stage:
            order = WorkflowGateService.stage_order_for(project.pk, workflow_id)
            stage = order[-1] if order else DEFAULT_CLOSING_STAGE
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
