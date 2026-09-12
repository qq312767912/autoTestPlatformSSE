"""基于 test-case-clarity-review Skill 的测试用例文件审查。"""

import csv
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from django.core.files.base import ContentFile
from django.utils import timezone
from langchain_core.messages import HumanMessage, SystemMessage
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from langgraph_integration.models import LLMConfig
from requirements.services import create_llm_instance, safe_llm_invoke

from .models import TestCaseReview

logger = logging.getLogger(__name__)

FALLBACK_SKILL = """审查测试用例的可执行性与验收清晰度。不要凭空补造业务规则；未知标准标记为待业务确认。按风险检查前置条件、测试数据、步骤、预期、业务结果、边界异常、数据一致性、证据与可维护性。"""


def build_skill_snapshot(skill=None):
    """固化本次审查所用 Skill，避免执行期间 Skill 被编辑或删除。"""
    if skill and skill.skill_content:
        content = skill.skill_content
        try:
            skill_path = skill.get_full_path()
            if not skill_path:
                return content
            references = Path(skill_path) / "references"
            if references.exists():
                for path in sorted(references.rglob("*.md")):
                    content += f"\n\n# 参考规则：{path.name}\n" + path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("读取 Skill 参考规则失败: %s", skill.name)
        return content
    bundled = Path("/app/bundled_skills/test-case-clarity-review")
    skill_md = bundled / "SKILL.md"
    rules_md = bundled / "references" / "review-rules.md"
    if skill_md.exists():
        return skill_md.read_text(encoding="utf-8") + ("\n\n" + rules_md.read_text(encoding="utf-8") if rules_md.exists() else "")
    return FALLBACK_SKILL


def _skill_prompt(review):
    return review.skill_snapshot or build_skill_snapshot(review.selected_skill)


def _read_rows(path):
    suffix = Path(path).suffix.lower()
    rows = []
    if suffix == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                for row_number, values in enumerate(sheet.iter_rows(values_only=True), 1):
                    cells = [str(value).strip() if value is not None else "" for value in values]
                    if any(cells):
                        rows.append({"sheet": sheet.title, "row": row_number, "cells": cells})
        finally:
            workbook.close()
    else:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row_number, values in enumerate(csv.reader(handle), 1):
                cells = [str(value).strip() for value in values]
                if any(cells):
                    rows.append({"sheet": "CSV", "row": row_number, "cells": cells})
    return rows


def _extract_json(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise ValueError("模型未返回可解析的 JSON")
        return json.loads(match.group(0))


def _review_chunk(llm, skill_prompt, rows, business_context):
    schema = {
        "issues": [{
            "sheet": "Sheet", "row": 2, "case_id": "编号或名称", "module": "模块",
            "original": "原文", "severity": "高|中|低", "issue_type": "问题类型",
            "description": "问题说明", "suggestion": "具体修改建议", "judgement": "确定缺陷|待业务确认|优化建议",
            "rewrite": "可直接替换的改写示例或空字符串",
        }],
        "pending_confirmations": ["待确认事项"], "governance_suggestions": ["治理建议"],
    }
    prompt = (
        "请严格按下方 Skill 审查这些测试用例行。只输出一个 JSON 对象，不要 Markdown。"
        "同一根因在同一用例内合并；高风险必须语义复核；识别并跳过表头，不要把表头当作测试用例。"
        "行内容是按原表列顺序提供的。\n"
        f"业务背景：{business_context or '未提供；未知业务规则必须标为待业务确认'}\n"
        f"输出结构示例：{json.dumps(schema, ensure_ascii=False)}\n"
        f"待审查数据：{json.dumps(rows, ensure_ascii=False)}"
    )
    # ChatOpenAI 的内部重试在本功能中已关闭。审查请求可能生成较长 JSON，
    # 单次失败后由用户明确重试整项任务，避免同一分片重复占用模型数十分钟。
    response = safe_llm_invoke(
        llm,
        [SystemMessage(content=skill_prompt), HumanMessage(content=prompt)],
        max_retries=1,
        retry_delay=2,
    )
    return _extract_json(response.content)


def _write_report(review, rows, issues, pending, governance):
    workbook = Workbook()
    info = workbook.active
    info.title = "审查摘要"
    counts = Counter(str(item.get("severity") or "中") for item in issues)
    type_counts = Counter(str(item.get("issue_type") or "其他") for item in issues)
    module_counts = Counter(str(item.get("module") or "未标注模块") for item in issues)
    issue_rows = {(str(item.get("sheet")), int(item.get("row") or 0)) for item in issues}
    sheet_count = len({str(row.get("sheet")) for row in rows})
    estimated_cases = max(len(rows) - sheet_count, 0)
    top_issue = type_counts.most_common(1)[0][0] if type_counts else "未发现明确问题"
    summary_rows = [
        ("源文件", review.source_name), ("扫描时间", timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")),
        ("用例数量（按表头后非空行估算）", estimated_cases), ("扫描非空行数", len(rows)),
        ("存在问题的用例/行", len(issue_rows)), ("问题总数", len(issues)),
        ("高风险", counts.get("高", 0)), ("中风险", counts.get("中", 0)), ("低风险", counts.get("低", 0)),
        ("最常见问题", top_issue),
        ("审查方式", review.get_review_mode_display()), ("使用 Skill", review.skill_name),
        ("业务背景", review.business_context or "未提供"), ("自定义审查规则", review.custom_rules or "未提供"),
        ("审查口径", f"{review.skill_name} + 用户自定义审查规则"),
    ]
    info.merge_cells("A1:B1")
    info["A1"] = "测试用例质量审查报告"
    info.merge_cells("A2:B2")
    info["A2"] = "审查结果仅用于提升用例的可执行性与验收清晰度，未知业务规则需由业务方确认。"
    info.append(["基本信息", "内容"])
    for key, value in summary_rows:
        info.append([key, value])
    info.column_dimensions["A"].width = 24
    info.column_dimensions["B"].width = 90

    distribution = workbook.create_sheet("问题分布")
    distribution.append(["维度", "分类", "数量"])
    for label, counter in (("严重程度", counts), ("问题类型", type_counts), ("模块", module_counts)):
        for name, count in counter.most_common():
            distribution.append([label, name, count])

    detail = workbook.create_sheet("问题明细")
    headers = ["Sheet", "行号", "用例编号/名称", "模块", "原文", "严重程度", "问题类型", "问题说明", "修改建议", "判定"]
    detail.append(headers)
    for item in issues:
        detail.append([item.get(k, "") for k in ["sheet", "row", "case_id", "module", "original", "severity", "issue_type", "description", "suggestion", "judgement"]])

    examples = workbook.create_sheet("改写示例")
    examples.append(["用例编号/名称", "原文", "改写示例"])
    for item in issues:
        if item.get("rewrite") and item.get("severity") in {"高", "中"}:
            examples.append([item.get("case_id", ""), item.get("original", ""), item.get("rewrite", "")])

    confirms = workbook.create_sheet("待确认事项")
    confirms.append(["序号", "待确认内容"])
    for index, value in enumerate(dict.fromkeys(pending), 1):
        confirms.append([index, value])
    governance_sheet = workbook.create_sheet("治理建议")
    governance_sheet.append(["序号", "建议"])
    for index, value in enumerate(dict.fromkeys(governance), 1):
        governance_sheet.append([index, value])

    navy = "1D3F66"
    blue = "2F75B5"
    pale_blue = "EAF2F8"
    stripe = "F7FAFC"
    border_color = "D6E0EA"
    thin_border = Border(
        left=Side(style="thin", color=border_color), right=Side(style="thin", color=border_color),
        top=Side(style="thin", color=border_color), bottom=Side(style="thin", color=border_color),
    )
    risk_styles = {
        "高": ("FCE8E6", "B42318"),
        "中": ("FFF2D6", "B54708"),
        "低": ("E8F3FF", "175CD3"),
    }

    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.tabColor = blue
        header_row = 3 if sheet is info else 1
        sheet.freeze_panes = f"A{header_row + 1}" if sheet.max_row > header_row else None
        if sheet is not info and sheet.max_row > 1:
            sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[header_row]:
            cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=blue)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
        sheet.row_dimensions[header_row].height = 26
        for column in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(column)].width = min(max(14, max(len(str(sheet.cell(row, column).value or "")) for row in range(1, min(sheet.max_row, 100) + 1)) + 2), 60)
        for row_index in range(header_row + 1, sheet.max_row + 1):
            fill = PatternFill("solid", fgColor=stripe if row_index % 2 == 0 else "FFFFFF")
            for cell in sheet[row_index]:
                cell.font = Font(name="Arial", size=10, color="26384A")
                cell.fill = fill
                cell.border = thin_border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            sheet.row_dimensions[row_index].height = 34 if sheet in {detail, examples} else 24

    info["A1"].fill = PatternFill("solid", fgColor=navy)
    info["A1"].font = Font(name="Arial", size=18, bold=True, color="FFFFFF")
    info["A1"].alignment = Alignment(vertical="center")
    info["B1"].fill = PatternFill("solid", fgColor=navy)
    info.row_dimensions[1].height = 44
    info["A2"].fill = PatternFill("solid", fgColor=pale_blue)
    info["B2"].fill = PatternFill("solid", fgColor=pale_blue)
    info["A2"].font = Font(name="Arial", size=10, italic=True, color="52677D")
    info["A2"].alignment = Alignment(vertical="center", wrap_text=True)
    info.row_dimensions[2].height = 34
    for row_index in range(4, info.max_row + 1):
        info.cell(row_index, 1).font = Font(name="Arial", size=10, bold=True, color="27445E")
        if info.cell(row_index, 1).value in risk_styles:
            fill_color, font_color = risk_styles[info.cell(row_index, 1).value]
            for cell in info[row_index]:
                cell.fill = PatternFill("solid", fgColor=fill_color)
            info.cell(row_index, 2).font = Font(name="Arial", size=11, bold=True, color=font_color)

    detail.page_setup.orientation = "landscape"
    detail.sheet_properties.tabColor = "C73E1D"
    detail.column_dimensions["A"].width = 16
    detail.column_dimensions["B"].width = 9
    detail.column_dimensions["C"].width = 28
    detail.column_dimensions["D"].width = 20
    detail.column_dimensions["E"].width = 48
    detail.column_dimensions["F"].width = 12
    detail.column_dimensions["G"].width = 24
    detail.column_dimensions["H"].width = 48
    detail.column_dimensions["I"].width = 48
    detail.column_dimensions["J"].width = 16
    for row_index in range(2, detail.max_row + 1):
        severity = str(detail.cell(row_index, 6).value or "")
        if severity in risk_styles:
            fill_color, font_color = risk_styles[severity]
            cell = detail.cell(row_index, 6)
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.font = Font(name="Arial", size=10, bold=True, color=font_color)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        judgement = str(detail.cell(row_index, 10).value or "")
        judgement_colors = {"确定缺陷": ("FCE8E6", "B42318"), "待业务确认": ("FFF2D6", "B54708"), "优化建议": ("EAF2F8", "175CD3")}
        if judgement in judgement_colors:
            fill_color, font_color = judgement_colors[judgement]
            cell = detail.cell(row_index, 10)
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.font = Font(name="Arial", size=10, bold=True, color=font_color)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row_index in range(2, distribution.max_row + 1):
        if distribution.cell(row_index, 1).value == "严重程度":
            severity = str(distribution.cell(row_index, 2).value or "")
            if severity in risk_styles:
                fill_color, font_color = risk_styles[severity]
                for cell in distribution[row_index]:
                    cell.fill = PatternFill("solid", fgColor=fill_color)
                distribution.cell(row_index, 3).font = Font(name="Arial", size=10, bold=True, color=font_color)

    from io import BytesIO
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    stem = Path(review.source_name).stem
    filename = f"{stem}_测试用例质量审查报告.xlsx"
    review.report_file.save(filename, ContentFile(output.read()), save=False)
    return {"total_rows": estimated_cases, "scanned_rows": len(rows), "problem_cases": len(issue_rows), "issue_count": len(issues), "high": counts.get("高", 0), "medium": counts.get("中", 0), "low": counts.get("低", 0)}


def run_testcase_review(review_id):
    review = TestCaseReview.objects.get(pk=review_id)
    review.status = "running"
    review.started_at = timezone.now()
    review.current_step = "读取测试用例"
    review.progress = 5
    review.save(update_fields=["status", "started_at", "current_step", "progress", "updated_at"])
    rows = _read_rows(review.source_file.path)
    if not rows:
        raise ValueError("文件中没有可审查的非空内容")
    config = LLMConfig.objects.filter(is_active=True).first()
    if not config:
        raise ValueError("没有已启用的 LLM 配置")
    skill_prompt = _skill_prompt(review)
    if review.custom_rules.strip():
        skill_prompt += "\n\n# 本次用户指定的审查规则（在不违反质量边界的前提下优先执行）\n" + review.custom_rules.strip()
    issues, pending, governance = [], [], []
    # 内网模型网关通常有固定的 120 秒上游限制；每批 25 行可以控制
    # JSON 输出规模，最多两路并发也不会瞬间压满私有模型服务。
    chunk_size = 25
    chunks = [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]
    max_workers = min(2, len(chunks))
    review_timeout = max(30, min(int(config.request_timeout or 120), 600))

    def review_one(index, chunk):
        llm = create_llm_instance(
            config,
            temperature=0.1,
            timeout=review_timeout,
            max_retries=0,
        )
        return index, _review_chunk(llm, skill_prompt, chunk, review.business_context)

    review.current_step = (
        f"Skill 审查 0/{len(chunks)}（并发 {max_workers}，"
        f"单次最长 {review_timeout} 秒）"
    )
    review.progress = 10
    review.save(update_fields=["current_step", "progress", "updated_at"])
    completed = 0
    ordered_results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(review_one, index, chunk)
            for index, chunk in enumerate(chunks, 1)
        ]
        for future in as_completed(futures):
            index, result = future.result()
            ordered_results[index] = result
            completed += 1
            review.current_step = (
                f"Skill 审查 {completed}/{len(chunks)}（并发 {max_workers}，"
                f"单次最长 {review_timeout} 秒）"
            )
            review.progress = 10 + int(completed / len(chunks) * 75)
            review.save(update_fields=["current_step", "progress", "updated_at"])

    # 按原始分片顺序汇总，保证报告顺序稳定可追溯。
    for index in range(1, len(chunks) + 1):
        result = ordered_results[index]
        issues.extend(result.get("issues") or [])
        pending.extend(result.get("pending_confirmations") or [])
        governance.extend(result.get("governance_suggestions") or [])
    review.current_step = "生成 Excel 报告"
    review.progress = 90
    review.summary = _write_report(review, rows, issues, pending, governance)
    review.status = "completed"
    review.progress = 100
    review.current_step = "审查完成"
    review.completed_at = timezone.now()
    review.error_message = ""
    review.save()
    return review.summary
