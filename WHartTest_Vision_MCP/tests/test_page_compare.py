from vision_mcp.page_compare import compare_page_models


def test_compare_page_models_matches_regions_and_table_headers():
    expected = {
        "regions": [{"name": "查询", "elements": [{"type": "select", "name": "状态"}]}],
        "table_headers": ["任务状态", "操作人"],
    }
    actual = {
        "elements": [{"type": "select", "name": "状态", "ui_object_id": "UI-1"}],
        "tables": [{"headers": ["任务状态", "操作人", "操作"]}],
    }
    result = compare_page_models(expected, actual)
    assert result["summary"]["matched_count"] == 3
    assert result["summary"]["match_rate"] == 1.0
    assert [item["name"] for item in result["unexpected_on_actual"]] == ["操作"]


def test_compare_page_models_reports_missing_and_type_mismatch():
    expected = {"elements": [{"type": "button", "name": "导入"}, {"type": "select", "name": "状态"}]}
    actual = {"elements": [{"type": "link", "name": "导入"}]}
    result = compare_page_models(expected, actual)
    assert result["missing_on_actual"][0]["name"] == "状态"
    assert result["property_mismatches"][0]["object"] == "导入"
