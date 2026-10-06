"""阶段产出与人工确认之间的差异（T11 / §8.2）。

把"人到底改了什么"从自由文本里解放出来。确认报告里人工只能写一列自由文本
（``修改内容``），但**人在 Excel 里会顺手把平台列的取值改掉**——改了优先级、
删掉一条需求、把预期改得更可判定。这些改动才是真正可归因的信号，而它们
藏在单元格里，不比对就看不见。

三类差异分开算，因为它们指向的责任层完全不同：

- ``adopted`` / ``modified`` / ``removed``：人对**业务项**的判断；
- ``evidence_error`` / ``requirement_gap``：人对**依据**的判断；
- ``contract_issue``：产出与协议/schema 的契约问题，与人的判断无关。

⚠️ 差异服务**只描述事实，不给结论**。把"这是 Skill 的错"写在这里，会让
后面的归因无法反驳自己——而归因必须是可以被人工驳回的（见 T11 归因工作区）。
"""
from __future__ import annotations

from typing import Any, Iterable

# ---------------------------------------------------------------- 差异种类真值

DIFF_KIND_ADOPTED = "adopted"
DIFF_KIND_MODIFIED = "modified"
DIFF_KIND_REMOVED = "removed"
DIFF_KIND_ADDED = "added"
DIFF_KIND_EVIDENCE_ERROR = "evidence_error"
DIFF_KIND_REQUIREMENT_GAP = "requirement_gap"
DIFF_KIND_CONTRACT_ISSUE = "contract_issue"

DIFF_KINDS: tuple[str, ...] = (
    DIFF_KIND_ADOPTED, DIFF_KIND_MODIFIED, DIFF_KIND_REMOVED, DIFF_KIND_ADDED,
    DIFF_KIND_EVIDENCE_ERROR, DIFF_KIND_REQUIREMENT_GAP, DIFF_KIND_CONTRACT_ISSUE,
)

DIFF_KIND_LABELS: dict[str, str] = {
    DIFF_KIND_ADOPTED: "原样采纳",
    DIFF_KIND_MODIFIED: "修改后采纳",
    DIFF_KIND_REMOVED: "删除",
    DIFF_KIND_ADDED: "人工补充",
    DIFF_KIND_EVIDENCE_ERROR: "证据问题",
    DIFF_KIND_REQUIREMENT_GAP: "需求未覆盖",
    DIFF_KIND_CONTRACT_ISSUE: "格式契约问题",
}

#: 报告单元格 → ``stage_result`` 里的标量字段。
FIELD_COLUMN_MAP: dict[str, str] = {
    "方案项标题": "title",
    "模块": "module",
    "场景类型": "scenario_type",
    "优先级": "priority",
    "可判定预期": "expected",
}

#: 报告单元格 → ``stage_result`` 里的列表字段。
#:
#: 列表字段必须**归一化后再比**：报告里用「、」连接，人手工调整顺序
#: （``REQ-02、REQ-01``）不是一次内容修改，按字符串比会产生一批假差异，
#: 而假差异会一路变成假归因。
LIST_FIELD_COLUMN_MAP: dict[str, str] = {
    "需求": "requirement_ids",
    "证据": "evidence_ids",
}

#: 列表型单元格的分隔符（中英文都收）。
_LIST_SEPARATORS = "、,，;；/|"

#: 报告里「证据状态」的取值 → 是否算"这条依据本身有问题"。
EVIDENCE_PROBLEM_STATES: frozenset[str] = frozenset({"存在问题", "无证据"})

VERSION = "stage-diff/v1"


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def normalize_refs(value: Any) -> list[str]:
    """把「REQ-01、REQ-02」这类单元格归一成有序去重的引用列表。"""
    if isinstance(value, (list, tuple)):
        raw = [str(item or "").strip() for item in value]
    else:
        text = str(value or "")
        for separator in _LIST_SEPARATORS[1:]:
            text = text.replace(separator, _LIST_SEPARATORS[0])
        raw = [part.strip() for part in text.split(_LIST_SEPARATORS[0])]
    seen: list[str] = []
    for item in raw:
        if item and item not in seen:
            seen.append(item)
    return sorted(seen)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _items_index(stage_result: Any) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for position, item in enumerate(_as_list(getattr(stage_result, "items", []))):
        if not isinstance(item, dict):
            continue
        key = _text(item.get("id")) or f"TP#{position}"
        index[key] = item
    return index


def field_diffs(item: dict, row: dict) -> list[dict]:
    """逐字段比对"平台产出 vs 人回传的取值"。

    ``before`` 一律取产出里的值、``after`` 取报告里的值：方向固定，页面才能
    稳定地显示成"平台 → 人工"。反过来取一次，用户会以为平台改了他填的东西。
    """
    diffs: list[dict] = []
    for column, field in FIELD_COLUMN_MAP.items():
        if column not in row:
            # 报告里没有这一列（旧版报告）不能当成"人清空了它"。
            continue
        before, after = _text(item.get(field)), _text(row.get(column))
        if before != after:
            diffs.append({"field": field, "column": column, "before": before, "after": after})

    for column, field in LIST_FIELD_COLUMN_MAP.items():
        if column not in row:
            continue
        before, after = normalize_refs(item.get(field)), normalize_refs(row.get(column))
        if before != after:
            diffs.append({
                "field": field, "column": column, "before": before, "after": after,
                "list_field": True,
            })
    return diffs


def classify_item(row: dict, *, diffs: list[dict]) -> list[str]:
    """一条业务项命中了哪几类差异（可以同时命中多类）。"""
    kinds: list[str] = []
    verdict = _text(row.get("人工结论"))
    category = _text(row.get("修改类型"))

    if verdict == "采纳":
        kinds.append(DIFF_KIND_ADOPTED)
    elif verdict == "修改后采纳":
        kinds.append(DIFF_KIND_MODIFIED)
    elif verdict == "删除":
        kinds.append(DIFF_KIND_REMOVED)

    evidence_state = _text(row.get("证据状态"))
    if category == "证据错误" or evidence_state in EVIDENCE_PROBLEM_STATES:
        if DIFF_KIND_EVIDENCE_ERROR not in kinds:
            kinds.append(DIFF_KIND_EVIDENCE_ERROR)
    if category == "覆盖不足":
        kinds.append(DIFF_KIND_REQUIREMENT_GAP)
    # 改了字段但没填结论（存草稿时常见）：也要算一次"修改后采纳"，
    # 否则草稿阶段的差异在页面上完全看不见。
    if diffs and DIFF_KIND_MODIFIED not in kinds and verdict != "删除":
        kinds.append(DIFF_KIND_MODIFIED)
    return kinds


def build_item_diffs(stage_result: Any, rows: Iterable[dict]) -> list[dict]:
    """逐行算出差异。只保留**填过人工列或改过平台列**的行。

    未填且未改的行是"没审"，不是"审完没意见"——把它算成采纳是这一层最容易
    犯的错，而它的后果很实在：一次没人看的评审会让 Skill 进化以为"这版挺好"。
    """
    index = _items_index(stage_result)
    results: list[dict] = []

    for position, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        item_id = _text(row.get("方案项 ID")) or _text(row.get("序号")) or f"ROW#{position}"
        item = index.get(item_id, {})
        diffs = field_diffs(item, row)
        touched = any(
            _text(row.get(column)) for column in ("人工结论", "修改类型", "修改内容", "备注")
        )
        if not touched and not diffs:
            continue

        kinds = classify_item(row, diffs=diffs)
        results.append({
            "item_id": item_id,
            "title": _text(item.get("title")) or _text(row.get("方案项标题")),
            "verdict": _text(row.get("人工结论")),
            "edit_category": _text(row.get("修改类型")),
            "edit_content": _text(row.get("修改内容")),
            "note": _text(row.get("备注")),
            "evidence_state": _text(row.get("证据状态")),
            "requirement_ids": normalize_refs(item.get("requirement_ids")),
            "evidence_ids": normalize_refs(item.get("evidence_ids")),
            "field_diffs": diffs,
            "kinds": kinds,
            "kind_labels": [DIFF_KIND_LABELS[kind] for kind in kinds],
        })
    return results


def build_additions(attachments: Iterable[Any]) -> list[dict]:
    """人工补充物品（T10 的补充产物 / 确认后完整产物）。

    只有这两类算"补了东西"：参考附件与问题证据是上下文，人没有因为它们
    宣称产出有缺口。把四类一股脑算成"人工补充"会让缺口数虚高。
    """
    from .feedback_attachments import (
        ATTACHMENT_PURPOSE_CONFIRMED, ATTACHMENT_PURPOSE_SUPPLEMENT,
    )

    counted = {ATTACHMENT_PURPOSE_CONFIRMED, ATTACHMENT_PURPOSE_SUPPLEMENT}
    additions: list[dict] = []
    for attachment in attachments or []:
        purpose = str(getattr(attachment, "purpose", "") or "")
        if purpose not in counted:
            continue
        if getattr(attachment, "retired_at", None) is not None:
            # 已退役的不计入：人是"撤回"了它，再算成缺口会自相矛盾。
            continue
        additions.append({
            "item_id": f"ATT-{getattr(attachment, 'pk', '')}",
            "purpose": purpose,
            "filename": str(getattr(attachment, "original_name", "") or ""),
            "stage": str(getattr(attachment, "stage", "") or ""),
            "sha256": str(getattr(attachment, "sha256", "") or ""),
            "uploaded_by": (
                getattr(getattr(attachment, "uploaded_by", None), "username", "") or ""
            ),
            "kinds": [DIFF_KIND_ADDED],
            "kind_labels": [DIFF_KIND_LABELS[DIFF_KIND_ADDED]],
        })
    return additions


def build_contract_issues(quality: dict | None) -> list[dict]:
    """产出与协议/schema 的契约问题。

    从质量摘要里取，**不从人工结论里猜**：格式契约是可以机器判定的，
    让人的主观描述来决定它是否存在，等于把可自动化的检查降级成人工劳动。
    """
    if not quality:
        return []
    issues: list[dict] = []
    for entry in (quality.get("gate") or {}).get("failures") or []:
        issues.append({
            "code": str(entry.get("code") or ""),
            "message": str(entry.get("message") or ""),
            "severity": "failure",
        })
    for entry in quality.get("warnings") or []:
        issues.append({
            "code": str(entry.get("code") or ""),
            "message": str(entry.get("message") or ""),
            "severity": "warning",
        })
    return [
        {**issue, "kinds": [DIFF_KIND_CONTRACT_ISSUE],
         "kind_labels": [DIFF_KIND_LABELS[DIFF_KIND_CONTRACT_ISSUE]]}
        for issue in issues
    ]


def summarize(items: list[dict], additions: list[dict], contract_issues: list[dict]) -> dict:
    """按差异种类计数 + 派生两个比例。

    ``field_diff_count`` 单独给出来，是因为它才是"人到底动了多少内容"的直接
    度量；只报"修改后采纳数"会让人以为一改就是一条，看不见一条里改了 5 个字段。
    """
    counts = {kind: 0 for kind in DIFF_KINDS}
    for item in items:
        for kind in item["kinds"]:
            counts[kind] = counts.get(kind, 0) + 1
    if additions:
        counts[DIFF_KIND_ADDED] = len(additions)
    if contract_issues:
        counts[DIFF_KIND_CONTRACT_ISSUE] = len(contract_issues)

    reviewed = len(items)
    field_diff_count = sum(len(item["field_diffs"]) for item in items)
    return {
        "version": VERSION,
        "reviewed_items": reviewed,
        "field_diff_count": field_diff_count,
        "counts": {kind: counts.get(kind, 0) for kind in DIFF_KINDS},
        "counts_labels": {
            DIFF_KIND_LABELS[kind]: counts.get(kind, 0) for kind in DIFF_KINDS
        },
        # 有差异就说明这一版产出被人工实质改动过；全为 0 时下游不该把它当"金标"。
        "has_human_change": bool(reviewed or additions or contract_issues),
    }


def build_stage_diff(
    stage_result: Any,
    rows: Iterable[dict],
    *,
    attachments: Iterable[Any] = (),
    quality: dict | None = None,
) -> dict:
    """差异总入口：业务项差异 + 人工补充 + 契约问题 + 汇总。"""
    items = build_item_diffs(stage_result, rows)
    additions = build_additions(attachments)
    contract_issues = build_contract_issues(quality)
    return {
        "version": VERSION,
        "items": items,
        "additions": additions,
        "contract_issues": contract_issues,
        "by_item": {item["item_id"]: item for item in items},
        "summary": summarize(items, additions, contract_issues),
    }


__all__ = [
    "VERSION",
    "DIFF_KINDS", "DIFF_KIND_LABELS",
    "DIFF_KIND_ADOPTED", "DIFF_KIND_MODIFIED", "DIFF_KIND_REMOVED", "DIFF_KIND_ADDED",
    "DIFF_KIND_EVIDENCE_ERROR", "DIFF_KIND_REQUIREMENT_GAP", "DIFF_KIND_CONTRACT_ISSUE",
    "FIELD_COLUMN_MAP", "LIST_FIELD_COLUMN_MAP", "EVIDENCE_PROBLEM_STATES",
    "normalize_refs", "field_diffs", "classify_item",
    "build_item_diffs", "build_additions", "build_contract_issues",
    "summarize", "build_stage_diff",
]
