"""阶段确认报告：把 Agent 产出变成一张"人只需要填四列"的表。

设计上的取舍很清楚：**报告只承载人工判断，不承载人工创作**。
新增方案项、新增用例一律走文件上传（T10），不在表里逐行录入。理由是
"这张表要能被人用 Excel 打开、填完、传回来"，列一多就没人填了。

三条硬约束：

1. **人工列初始必须为空**。空白 = 未审核；不提供"待确认"这种默认值——
   默认值会被当成已填，从而把"没人看过"伪装成"审核通过"。
2. **草稿与提交是两回事**。有空白可以存草稿，但草稿不能进 Skill 进化；
   "修改后采纳"缺修改类型/修改内容、"删除"缺修改类型都只能停在草稿。
3. **统计由服务端重算，不信 Excel 公式**。人可能整列粘贴、可能删行、
   可能改了公式，Excel 缓存里的数字和明细对不上是常态。
"""
from __future__ import annotations

import hashlib
import io
import json
from typing import Any

from .evidence_graph import iter_evidence_issues
from .quality_summary import QUALITY_SUMMARY_VERSION

#: 生成器版本（派生产物上要带）。
REVIEW_REPORT_VERSION = "review-report/v1"

#: 确认报告文件名（平台派生产物）。
REVIEW_REPORT_FILENAME = "阶段确认报告.xlsx"

# ---------------------------------------------------------------------------
# 语义集合真值（模块级）
# ---------------------------------------------------------------------------

#: 人工留空列，顺序即表头顺序。
HUMAN_COLUMNS: tuple[str, ...] = ("人工结论", "修改类型", "修改内容", "备注")

#: 人工结论。**没有"待确认"**——空白就是未审核。
REVIEW_VERDICTS: tuple[str, ...] = ("采纳", "修改后采纳", "删除")

#: 修改类型。
EDIT_CATEGORIES: tuple[str, ...] = (
    "内容错误", "覆盖不足", "粒度不当", "证据错误", "优先级不当", "重复或无效", "其他",
)

#: 结论 → 必填字段。空元组表示该结论下这些列必须为空（而不是"随便填"）。
VERDICT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "采纳": (),
    "修改后采纳": ("修改类型", "修改内容"),
    "删除": ("修改类型",),
}

#: 四个 Sheet。最后一个保留「采纳率」标签以兼容既有解析器。
SHEET_ITEM_ROWS = "方案项确认"
SHEET_EVIDENCE_ISSUES = "异常证据"
SHEET_EXECUTION_SUMMARY = "执行摘要"
SHEET_TOTALS = "确认汇总"
SHEET_NAMES: tuple[str, ...] = (
    SHEET_ITEM_ROWS, SHEET_EVIDENCE_ISSUES, SHEET_EXECUTION_SUMMARY, SHEET_TOTALS,
)

#: 兼容标签：既有解析器按这个名字找采纳率，换名字会让下游静默读不到。
LEGACY_ACCEPTANCE_RATE_LABEL = "采纳率"


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# 明细行
# ---------------------------------------------------------------------------


def build_review_rows(stage_result: Any, *, graph: dict | None = None) -> list[dict]:
    """生成「方案项确认」Sheet 的行。

    平台列（只读）+ 四个人工列的固定结构。人工列**一律空串**，
    不写"待确认"、不写默认结论。
    """
    evidence_status = {
        evidence_id: str(entry.get("status") or "")
        for evidence_id, entry in ((graph or {}).get("evidence_status") or {}).items()
    }

    rows: list[dict] = []
    for position, item in enumerate(_as_list(getattr(stage_result, "items", []))):
        if not isinstance(item, dict):
            continue
        evidence_ids = [str(ref) for ref in _as_list(item.get("evidence_ids")) if str(ref or "").strip()]
        statuses = [evidence_status.get(evidence_id, "") for evidence_id in evidence_ids]
        if not statuses:
            evidence_state = "无证据"
        elif all(status == "verified" for status in statuses):
            evidence_state = "已定位"
        elif any(status in ("invalid", "contradicted") for status in statuses):
            evidence_state = "存在问题"
        else:
            evidence_state = "未验证"

        row = {
            "序号": position + 1,
            "方案项 ID": str(item.get("id") or ""),
            "方案项标题": str(item.get("title") or ""),
            "模块": str(item.get("module") or ""),
            "场景类型": str(item.get("scenario_type") or ""),
            "优先级": str(item.get("priority") or ""),
            "需求": "、".join(str(ref) for ref in _as_list(item.get("requirement_ids"))),
            "证据": "、".join(evidence_ids),
            "证据状态": evidence_state,
            "可判定预期": str(item.get("expected") or ""),
        }
        for column in HUMAN_COLUMNS:
            row[column] = ""
        rows.append(row)
    return rows


def build_evidence_issue_rows(graph: dict | None) -> list[dict]:
    """「异常证据」Sheet：只列定位失败与反证的引用。

    已定位的引用不在这里出现——把全部引用都列出来会让"哪几条有问题"
    淹没在正常数据里，而这一页存在的唯一目的就是让人一眼看到问题。
    """
    rows: list[dict] = []
    for issue in iter_evidence_issues(graph or {}):
        rows.append({
            "证据 ID": issue["evidence_id"],
            "状态": issue["status"],
            "文档": issue["document_id"],
            "版本": issue["document_version"],
            "Chunk": issue["chunk_id"],
            "问题": "；".join(
                str(entry.get("message") or "") for entry in issue["issues"]
            ) or _STATUS_LABELS.get(issue["status"], issue["status"]),
        })
    return rows


_STATUS_LABELS = {
    "invalid": "引用无法定位",
    "contradicted": "存在反证",
    "declared": "平台未验证（缺原文索引）",
    "retrieved": "仅检索到，未被任何方案项引用",
}


def build_execution_summary_rows(quality: dict | None) -> list[dict]:
    """「执行摘要」Sheet：把质量摘要摊平成"标签 / 值"两列，便于人读。"""
    if not quality:
        return []
    rows: list[dict] = []

    def emit(label: str, value: Any) -> None:
        rows.append({"指标": label, "值": value})

    emit("生成器版本", quality.get("generator_version", ""))
    emit("阶段", quality.get("stage", ""))
    emit("产出状态", quality.get("status", ""))
    items = quality.get("items") or {}
    emit("方案项总数", items.get("total", 0))
    emit("有关联需求的方案项", items.get("with_requirement", 0))
    emit("有证据的方案项", items.get("with_evidence", 0))
    emit("证据已定位的方案项", items.get("with_verified_evidence", 0))
    requirements = quality.get("requirements") or {}
    emit("声明需求数", requirements.get("declared", 0))
    emit("被引用需求数", requirements.get("referenced", 0))
    for code in sorted((quality.get("evidence") or {}).get("by_status", {})):
        emit(f"证据状态 / {code}", quality["evidence"]["by_status"][code])
    for scenario in sorted(quality.get("scenario_types") or {}):
        emit(f"场景类型 / {scenario}", quality["scenario_types"][scenario])
    for priority in sorted(quality.get("priorities") or {}):
        emit(f"优先级 / {priority}", quality["priorities"][priority])
    gate = quality.get("gate") or {}
    emit("质量门禁", "通过" if gate.get("passed") else "未通过")
    for failure in gate.get("failures") or []:
        emit(f"门禁未通过 / {failure.get('code', '')}", failure.get("message", ""))
    for warning in quality.get("warnings") or []:
        emit(f"提示 / {warning.get('code', '')}", warning.get("message", ""))
    return rows


# ---------------------------------------------------------------------------
# 统计（服务端重算，不信 Excel 公式）
# ---------------------------------------------------------------------------


def compute_statistics(rows: list[dict], *, evidence_total: int = 0, evidence_verified: int = 0) -> dict:
    """按明细重算全部指标。

    指标口径写死在这里而不是散在报告生成代码里：口径变更时只有一处要改，
    而且测试可以直接对着这个函数断言，不必去解析 xlsx。
    """
    total = len(rows)
    reviewed = 0
    accepted = 0
    accepted_with_edit = 0
    deleted = 0
    blank = 0

    for row in rows:
        verdict = str(row.get("人工结论") or "").strip()
        if not verdict:
            blank += 1
            continue
        reviewed += 1
        if verdict == "采纳":
            accepted += 1
        elif verdict == "修改后采纳":
            accepted_with_edit += 1
        elif verdict == "删除":
            deleted += 1

    def ratio(numerator: int) -> float:
        # 分母为 0 时返回 0.0 而不是 None：报告要能被 Excel 直接读成数字。
        return round(numerator / total, 4) if total else 0.0

    return {
        "原始项数": total,
        "已审核数": reviewed,
        "未审核数": blank,
        "采纳数": accepted,
        "修改后采纳数": accepted_with_edit,
        "删除数": deleted,
        # 兼容标签：既有解析器按「采纳率」找原样采纳率，不能改名。
        LEGACY_ACCEPTANCE_RATE_LABEL: ratio(accepted),
        "原样采纳率": ratio(accepted),
        "有效保留率": ratio(accepted + accepted_with_edit),
        "修改率": ratio(accepted_with_edit),
        "删除率": ratio(deleted),
        "确认完成率": ratio(reviewed),
        "证据准确率": round(evidence_verified / evidence_total, 4) if evidence_total else 0.0,
    }


def validate_submission(rows: list[dict]) -> dict:
    """正式提交前的校验。

    返回 ``{"ok": bool, "errors": [...], "blank_count": n}``。
    ``errors`` 非空即拒绝提交；``blank_count`` 只用于提示——空白允许存在，
    只是这份反馈不能进 Skill 进化（见 T09）。
    """
    errors: list[dict] = []
    blank_count = 0

    for row in rows:
        label = str(row.get("方案项 ID") or row.get("序号") or "")
        verdict = str(row.get("人工结论") or "").strip()
        if not verdict:
            blank_count += 1
            continue
        if verdict not in REVIEW_VERDICTS:
            errors.append({
                "row": label,
                "code": "unknown_verdict",
                "message": f"人工结论只能是 {'、'.join(REVIEW_VERDICTS)}，当前为「{verdict}」",
            })
            continue
        for field in VERDICT_REQUIRED_FIELDS.get(verdict, ()):
            if not str(row.get(field) or "").strip():
                errors.append({
                    "row": label,
                    "code": "missing_required_field",
                    "message": f"「{verdict}」必须填写{field}",
                })
        category = str(row.get("修改类型") or "").strip()
        if category and category not in EDIT_CATEGORIES:
            errors.append({
                "row": label,
                "code": "unknown_edit_category",
                "message": f"修改类型不在允许范围内：{category}",
            })

    return {"ok": not errors, "errors": errors, "blank_count": blank_count}


def rows_fingerprint(rows: list[dict]) -> str:
    """明细内容指纹，用于"同一文件哈希重复提交保持幂等"（T09）与版本化反馈。"""
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 工作簿
# ---------------------------------------------------------------------------


def build_review_workbook(
    stage_result: Any,
    *,
    graph: dict | None = None,
    quality: dict | None = None,
) -> bytes:
    """生成确认报告 xlsx（四个 Sheet）。

    ⚠️ **字节不保证逐次一致**：xlsx 是 zip，内部时间戳由 openpyxl 决定。
    验收"同一输入重复生成内容一致"是对**行与统计**而言的（见 ``build_review_rows``
    与 ``compute_statistics``），不是对压缩字节。对字节的期望会让人去追一个
    与业务无关的差异，并把真正的确定性问题掩盖掉。
    """
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)

    item_rows = build_review_rows(stage_result, graph=graph)
    _write_sheet(workbook, SHEET_ITEM_ROWS, item_rows)
    _write_sheet(workbook, SHEET_EVIDENCE_ISSUES, build_evidence_issue_rows(graph))
    _write_sheet(workbook, SHEET_EXECUTION_SUMMARY, build_execution_summary_rows(quality))

    status = (quality or {}).get("evidence") or {}
    by_status = status.get("by_status") or {}
    statistics = compute_statistics(
        item_rows,
        evidence_total=int(status.get("total") or 0),
        evidence_verified=int(by_status.get("verified") or 0),
    )
    _write_sheet(
        workbook, SHEET_TOTALS,
        [{"指标": key, "值": value} for key, value in statistics.items()],
    )

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_sheet(workbook: Any, title: str, rows: list[dict]) -> None:
    sheet = workbook.create_sheet(title=title)
    if not rows:
        # 空 Sheet 也要写个标记行：完全没有内容的工作簿，人打开会以为生成失败。
        sheet.append(["（无数据）"])
        return
    headers = list(rows[0].keys())
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])


def report_statistics_from_workbook(payload: bytes) -> dict:
    """从生成的工作簿里把「确认汇总」读回来，供验收与回归使用。"""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(payload))
    sheet = workbook[SHEET_TOTALS]
    statistics: dict[str, Any] = {}
    for label, value in sheet.iter_rows(min_row=2, values_only=True):
        if label is None:
            continue
        statistics[str(label)] = value
    return statistics


# ---------------------------------------------------------------------------
# 读回人工填写结果（T09）
# ---------------------------------------------------------------------------


def _cells_to_rows(sheet) -> list[dict]:
    """把 Sheet 读成 ``[{表头: 值}]``。空 Sheet（只有「（无数据）」标记行）返回空列表。"""
    iterator = sheet.iter_rows(values_only=True)
    try:
        headers = [str(cell or "") for cell in next(iterator)]
    except StopIteration:
        return []
    if headers and headers[0] == "（无数据）":
        return []
    rows: list[dict] = []
    for values in iterator:
        if values is None or all(value is None for value in values):
            continue
        rows.append({
            header: (values[index] if index < len(values) and values[index] is not None else "")
            for index, header in enumerate(headers)
            if header
        })
    return rows


def _evidence_totals(workbook) -> tuple[int, int]:
    """从「执行摘要」页还原证据总数与已定位数。

    刻意从报告里读而不是回查数据库：这份报告可能已经离线流转了几天，
    重新算一次统计必须基于**它自己带来的那份摘要**，否则"人评的是 A 版、
    系统按 B 版算统计"这种错会在统计数字上体现为一次无法解释的波动。
    """
    if SHEET_EXECUTION_SUMMARY not in workbook.sheetnames:
        return 0, 0
    total = 0
    verified = 0
    for label, value in workbook[SHEET_EXECUTION_SUMMARY].iter_rows(min_row=2, values_only=True):
        text = str(label or "")
        if not text.startswith("证据状态 / "):
            continue
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            count = 0
        total += count
        if text.endswith("/ verified"):
            verified += count
    return total, verified


class ReviewReportFormatError(Exception):
    """报告格式不对（不是本平台导出的确认报告）。"""


def parse_review_workbook(payload: bytes) -> dict:
    """把人工填过的确认报告读回成结构化结果（T09）。

    返回 ``{"rows", "human_rows", "statistics", "validation", "evidence"}``：

    - ``rows``：全部明细行（含人工四列）；
    - ``human_rows``：**至少填了一列的**行，供差异与归因使用（T11）——
      未填的行是"没审"，与"审了但结论是空白"不是一回事；
    - ``statistics``：服务端按明细重算的全部指标，**不读 Excel 公式缓存**；
    - ``validation``：正式提交前的校验结论（缺字段 / 非法取值 / 空白数）。

    只接受本平台导出的报告结构：缺「方案项确认」页直接拒绝并说清期望格式。
    拿一份别的 Excel 静默产生一组全 0 统计，比报错更糟糕——它看起来像
    "这次评审全部未采纳"。
    """
    if not payload:
        raise ReviewReportFormatError("报告文件为空，无法解析")

    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(payload), data_only=True, read_only=True)
    except Exception as exc:  # pragma: no cover - openpyxl 异常类型不稳定
        raise ReviewReportFormatError(f"报告无法作为 Excel 打开：{exc}") from exc

    if SHEET_ITEM_ROWS not in workbook.sheetnames:
        raise ReviewReportFormatError(
            f"报告里没有「{SHEET_ITEM_ROWS}」页，不是平台导出的确认报告；"
            f"请先下载本阶段确认报告，填写人工列后再上传"
        )

    rows = _cells_to_rows(workbook[SHEET_ITEM_ROWS])
    evidence_total, evidence_verified = _evidence_totals(workbook)
    human_rows = [
        row for row in rows
        if any(str(row.get(column) or "").strip() for column in HUMAN_COLUMNS)
    ]
    return {
        "rows": rows,
        "human_rows": human_rows,
        "statistics": compute_statistics(
            rows, evidence_total=evidence_total, evidence_verified=evidence_verified,
        ),
        "validation": validate_submission(rows),
        "evidence": {"total": evidence_total, "verified": evidence_verified},
        "fingerprint": rows_fingerprint(rows),
        "sheet_names": list(workbook.sheetnames),
    }


def settle_statistics(rows: list[dict], *, evidence_total: int = 0, evidence_verified: int = 0) -> dict:
    """服务端统计重算的公开入口（供视图层在草稿/正式提交时统一调用）。

    与 ``compute_statistics`` 是同一实现，单独导出是为了让"统计一律重算"
    这件事在调用点看起来是一次**刻意的决定**，而不是顺手复用了某个内部函数。
    """
    return compute_statistics(
        rows, evidence_total=evidence_total, evidence_verified=evidence_verified,
    )


__all__ = [
    "HUMAN_COLUMNS", "REVIEW_VERDICTS", "EDIT_CATEGORIES", "VERDICT_REQUIRED_FIELDS",
    "SHEET_NAMES", "SHEET_ITEM_ROWS", "SHEET_EVIDENCE_ISSUES", "SHEET_EXECUTION_SUMMARY",
    "SHEET_TOTALS", "LEGACY_ACCEPTANCE_RATE_LABEL", "REVIEW_REPORT_FILENAME",
    "REVIEW_REPORT_VERSION", "QUALITY_SUMMARY_VERSION",
    "build_review_rows", "build_evidence_issue_rows", "build_execution_summary_rows",
    "compute_statistics", "validate_submission", "rows_fingerprint",
    "build_review_workbook", "report_statistics_from_workbook",
    # T09：读回人工填写结果
    "ReviewReportFormatError", "parse_review_workbook", "settle_statistics",
]
