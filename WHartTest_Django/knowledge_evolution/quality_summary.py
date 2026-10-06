"""阶段质量摘要：这份产出"看起来完成"还是"真的站得住"。

摘要只回答可核验的问题，不做主观打分：

- 结构化方案项有多少、其中有多少挂上了需求与**已定位**的证据；
- 声明了的需求里有多少被覆盖（覆盖缺口会明说是哪几条）；
- 引用的证据里有多少是 verified、多少是 invalid；
- 场景类型与优先级分布是否偏科（全是正向、全是 P0 都是值得提示的信号）。

**它不回答"这份方案好不好"**。质量好坏要等人工确认报告出来才谈得上，
这里给出的是"能不能进人工确认"的门槛——门禁不过就不该往下走。
"""
from __future__ import annotations

from typing import Any

from .evidence_graph import collect_evidence_status

#: 生成器版本。派生产物上要带它，便于回答"换了判定逻辑之后结论为什么变了"。
QUALITY_SUMMARY_VERSION = "stage-quality-summary/v1"

QUALITY_SUMMARY_FILENAME = "stage_quality_summary.json"

#: 门禁规则真值（模块级）。每条 = (代码, 说明)；触发即不通过。
#:
#: 刻意只有三条、且都指向**结构性**缺陷。把"覆盖率低于 90% 就不通过"这类阈值
#: 放进来会让门禁退化成调参游戏，而覆盖多少本来是人工判断的事。
QUALITY_GATE_RULES: tuple[tuple[str, str], ...] = (
    ("no_items", "没有任何结构化方案项，无法进入人工确认"),
    ("item_without_requirement", "存在没有关联需求的方案项，无法归因"),
    ("item_without_evidence", "存在没有任何证据的方案项，无法反查依据"),
)

#: 覆盖不足的提示阈值：被引用的需求占比低于此值时给提示（不影响门禁通过）。
COVERAGE_HINT_THRESHOLD = 0.6

#: 场景类型分布偏科的提示阈值：单一场景类型占比超过此值时给提示。
SCENARIO_SKEW_THRESHOLD = 0.9


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def build_quality_summary(
    stage_result: Any,
    *,
    graph: dict | None = None,
    trace: Any = None,
) -> dict:
    """生成 ``stage_quality_summary.json`` 的内容。

    纯函数，不含时间戳：同一份产出重复生成必须逐字节一致，否则"这条摘要变了"
    就无法区分是"产出变了"还是"只是又跑了一次生成器"。
    """
    items = [item for item in _as_list(getattr(stage_result, "items", [])) if isinstance(item, dict)]
    requirements = [
        requirement for requirement in _as_list(getattr(stage_result, "requirements", []))
        if isinstance(requirement, dict)
    ]
    evidence = [entry for entry in _as_list(getattr(stage_result, "evidence", [])) if isinstance(entry, dict)]

    evidence_status = collect_evidence_status(graph) if graph else {
        str(entry.get("id") or ""): "declared" for entry in evidence
    }
    verified_ids = {
        evidence_id for evidence_id, status in evidence_status.items() if status == "verified"
    }

    items_with_requirement = [
        str(item.get("id") or "") for item in items if _as_list(item.get("requirement_ids"))
    ]
    items_with_evidence = [
        str(item.get("id") or "") for item in items if _as_list(item.get("evidence_ids"))
    ]
    items_with_verified = [
        str(item.get("id") or "")
        for item in items
        if any(str(ref) in verified_ids for ref in _as_list(item.get("evidence_ids")))
    ]

    referenced_requirements: set[str] = set()
    for item in items:
        for ref in _as_list(item.get("requirement_ids")):
            if str(ref or "").strip():
                referenced_requirements.add(str(ref))

    declared_requirements = {str(requirement.get("id") or "") for requirement in requirements}
    declared_requirements.discard("")
    uncovered = sorted(declared_requirements - referenced_requirements)

    scenario_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    for item in items:
        scenario = str(item.get("scenario_type") or "")
        priority = str(item.get("priority") or "")
        if scenario:
            scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1
        if priority:
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

    status_counts: dict[str, int] = {}
    for status in evidence_status.values():
        status_counts[status] = status_counts.get(status, 0) + 1

    total_items = len(items)
    summary = {
        "generator_version": QUALITY_SUMMARY_VERSION,
        "stage": str(getattr(stage_result, "stage", "") or ""),
        "status": str(getattr(stage_result, "status", "") or ""),
        "level": str(getattr(stage_result, "level", "") or ""),
        "items": {
            "total": total_items,
            "with_requirement": len(items_with_requirement),
            "with_evidence": len(items_with_evidence),
            "with_verified_evidence": len(items_with_verified),
        },
        "requirements": {
            "declared": len(declared_requirements),
            "referenced": len(referenced_requirements & declared_requirements)
            if declared_requirements else len(referenced_requirements),
            "uncovered": uncovered,
        },
        "evidence": {
            "total": len(evidence_status),
            "by_status": {key: status_counts[key] for key in sorted(status_counts)},
        },
        "scenario_types": {key: scenario_counts[key] for key in sorted(scenario_counts)},
        "priorities": {key: priority_counts[key] for key in sorted(priority_counts)},
        "warnings": [],
    }

    gate = evaluate_quality_gate(summary, items=items, declared_requirements=declared_requirements)
    summary["gate"] = gate
    summary["warnings"] = _build_warnings(summary, gate)
    return summary


def evaluate_quality_gate(
    summary: dict,
    *,
    items: list[dict],
    declared_requirements: set[str],
) -> dict:
    """三条结构性门禁。"""
    failures: list[dict] = []

    if not items:
        failures.append({"code": "no_items", "message": QUALITY_GATE_RULES[0][1]})
    if summary["items"]["with_requirement"] < summary["items"]["total"]:
        failures.append({
            "code": "item_without_requirement",
            "message": QUALITY_GATE_RULES[1][1],
        })
    if summary["items"]["with_evidence"] < summary["items"]["total"]:
        failures.append({
            "code": "item_without_evidence",
            "message": QUALITY_GATE_RULES[2][1],
        })

    return {
        "passed": not failures,
        "failures": failures,
        "rule_codes": [code for code, _ in QUALITY_GATE_RULES],
    }


def _build_warnings(summary: dict, gate: dict) -> list[dict]:
    """提示项：不阻断流程，但值得在页面上说一句。"""
    warnings: list[dict] = []

    total_items = summary["items"]["total"]
    if gate["passed"] and summary["items"]["with_verified_evidence"] < total_items:
        warnings.append({
            "code": "evidence_not_verified",
            "message": (
                f"{total_items - summary['items']['with_verified_evidence']} 个方案项的证据"
                "尚未通过定位验证（缺原文索引或引用不合法）"
            ),
        })

    declared = summary["requirements"]["declared"]
    if declared:
        coverage = summary["requirements"]["referenced"] / declared
        if coverage < COVERAGE_HINT_THRESHOLD:
            warnings.append({
                "code": "low_coverage",
                "message": f"需求覆盖率 {coverage:.0%}，未覆盖：{'、'.join(summary['requirements']['uncovered'])}",
            })

    if total_items:
        for scenario, count in summary["scenario_types"].items():
            if count / total_items > SCENARIO_SKEW_THRESHOLD and total_items > 1:
                warnings.append({
                    "code": "scenario_skew",
                    "message": f"场景类型集中于 {scenario}（{count}/{total_items}），可能缺少反向或边界覆盖",
                })

    if not summary["scenario_types"] and total_items:
        warnings.append({
            "code": "scenario_type_missing",
            "message": "方案项未标注场景类型，无法判断正向/反向覆盖是否均衡",
        })

    return warnings
