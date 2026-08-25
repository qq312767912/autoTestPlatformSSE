"""Deterministic comparison for expected and actual page models."""

from __future__ import annotations

import re
from typing import Any


def _norm(value: Any) -> str:
    return re.sub(r"[\s:：*（）()【】\[\]]+", "", str(value or "")).lower()


def _elements(model: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in model.get("elements", []) or []:
        if isinstance(item, dict):
            found.append(item)
    for region in model.get("regions", []) or []:
        if isinstance(region, dict):
            for item in region.get("elements", []) or []:
                if isinstance(item, dict):
                    found.append(item)
    for header in model.get("table_headers", []) or []:
        found.append({"type": "table_header", "name": header, "visible_text": header})
    for table in model.get("tables", []) or []:
        if isinstance(table, dict):
            for header in table.get("headers", []) or []:
                found.append({"type": "table_header", "name": header, "visible_text": header})
    return found


def _identity(item: dict[str, Any]) -> str:
    return _norm(item.get("name") or item.get("visible_text") or item.get("label") or item.get("text"))


def compare_page_models(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    expected_items = [item for item in _elements(expected) if _identity(item)]
    actual_items = [item for item in _elements(actual) if _identity(item)]
    actual_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in actual_items:
        actual_by_name.setdefault(_identity(item), []).append(item)
    matched, missing, mismatches = [], [], []
    used_actual: set[int] = set()
    for exp in expected_items:
        name = _identity(exp)
        candidates = actual_by_name.get(name, [])
        if not candidates:
            missing.append(exp)
            continue
        act = candidates[0]
        used_actual.add(id(act))
        matched.append({"expected": exp, "actual": act})
        exp_type, act_type = _norm(exp.get("type")), _norm(act.get("type") or act.get("role"))
        if exp_type and act_type and exp_type != act_type:
            mismatches.append({"object": exp.get("name") or exp.get("visible_text"), "property": "type", "expected": exp_type, "actual": act_type})
    unexpected = [item for item in actual_items if id(item) not in used_actual]
    denominator = len(expected_items)
    return {
        "summary": {
            "expected_count": denominator,
            "actual_count": len(actual_items),
            "matched_count": len(matched),
            "match_rate": round(len(matched) / denominator, 4) if denominator else 1.0,
        },
        "matched": matched,
        "missing_on_actual": missing,
        "unexpected_on_actual": unexpected,
        "property_mismatches": mismatches,
    }
