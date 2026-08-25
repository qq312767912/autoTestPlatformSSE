"""Vision MCP server for text-only agents and WHartTest."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .document_images import extract_document_images as extract_images
from .ocr import local_ocr
from .page_compare import compare_page_models as deterministic_compare
from .vision_client import VisionClient


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _runtime_config(runtime_config_json: str = "") -> dict[str, Any]:
    if not runtime_config_json:
        return {}
    try:
        value = json.loads(runtime_config_json)
    except json.JSONDecodeError as exc:
        raise ValueError("运行时视觉模型配置不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("运行时视觉模型配置必须是 JSON 对象")
    return value


def _client(runtime_config_json: str = "") -> VisionClient:
    runtime = _runtime_config(runtime_config_json)
    return VisionClient(
        base_url=runtime.get("base_url") or os.environ.get("VISION_MCP_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        api_key=runtime.get("api_key") or os.environ.get("VISION_MCP_API_KEY") or None,
        model=runtime.get("model") or os.environ.get("VISION_MCP_MODEL", "glm-4.6v-flash"),
        chat_completions_path=runtime.get("chat_completions_path") or os.environ.get("VISION_MCP_CHAT_COMPLETIONS_PATH", "/chat/completions"),
        timeout_seconds=float(runtime.get("timeout_seconds") or os.environ.get("VISION_MCP_TIMEOUT_SECONDS", "120")),
        max_retries=int(runtime.get("max_retries") if runtime.get("max_retries") is not None else _env_int("VISION_MCP_MAX_RETRIES", 2)),
    )


def _json_result(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        # Models occasionally stop after producing a useful but truncated JSON prefix.
        # Close the current string/container stack so partial structured evidence remains usable.
        candidate = value.rstrip()
        stack: list[str] = []
        in_string = False
        escaped = False
        for char in candidate:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}" and stack:
                stack.pop()
        if escaped:
            candidate = candidate[:-1]
        if in_string:
            candidate += '"'
        candidate = candidate.rstrip()
        if candidate.endswith(":"):
            candidate += " null"
        elif candidate.endswith(","):
            candidate = candidate[:-1]
        candidate += "".join("}" if opener == "{" else "]" for opener in reversed(stack))
        try:
            data = json.loads(candidate)
            data["partial_result_repaired"] = True
        except json.JSONDecodeError as exc:
            raise ValueError(f"视觉模型未返回有效JSON: {raw[:300]}") from exc
    if not isinstance(data, dict):
        raise ValueError("视觉模型返回值必须是JSON对象")
    return data


def _vision(prompt: str, system_prompt: str, images: list[str], max_tokens: int = 4096, runtime_config_json: str = "") -> dict[str, Any]:
    client = _client(runtime_config_json)
    try:
        return _json_result(client.chat(
            prompt=prompt,
            image_paths=images,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
        ))
    finally:
        client.close()


def _safe_local_ocr(image_path: str) -> tuple[dict[str, Any], str]:
    """Keep an OCR copy without allowing OCR failure to block vision analysis."""
    try:
        return local_ocr(image_path), ""
    except Exception as exc:
        return {
            "provider": "unavailable",
            "full_text": "",
            "text_blocks": [],
            "average_confidence": 0.0,
        }, str(exc)[:1000]


def _has_meaningful_vision_content(result: dict[str, Any]) -> bool:
    return any(
        result.get(key)
        for key in (
            "content_summary",
            "regions",
            "detected_fields",
            "business_rules",
            "table_markdown",
            "annotations",
        )
    )


mcp = FastMCP(
    "vision-mcp",
    instructions="为纯文本测试智能体提供需求截图解析、OCR和页面差异分析。",
    host=os.environ.get("VISION_MCP_HOST", "0.0.0.0"),
    port=_env_int("VISION_MCP_PORT", 8010),
    streamable_http_path=os.environ.get("VISION_MCP_PATH", "/mcp"),
    stateless_http=os.environ.get("VISION_MCP_STATELESS_HTTP", "true").lower() == "true",
)


@mcp.tool(description="从DOCX或PDF需求文档中提取图片，并保留图片所在段落或页面文本上下文")
def extract_requirement_images(document_path: str, output_dir: str = "") -> dict[str, Any]:
    return extract_images(document_path, output_dir or None)


@mcp.tool(description="OCR提取截图中的可见文字，返回结构化JSON")
def extract_text_from_screenshot(image_path: str, provider: str = "rapidocr", runtime_config_json: str = "") -> dict[str, Any]:
    if provider != "vision":
        return local_ocr(image_path, provider)
    return _vision(
        "提取全部可见文字，按区域和阅读顺序输出。无法确认的文字放入uncertain_items。",
        "只返回JSON：{\"full_text\":\"\",\"text_blocks\":[{\"text\":\"\",\"region\":\"\",\"confidence\":0.0}],\"uncertain_items\":[]}",
        [image_path], runtime_config_json=runtime_config_json,
    )


@mcp.tool(description="把需求文档中的UI截图解析成可供纯文本模型比较的结构化页面模型")
def analyze_requirement_ui(image_path: str, document_context: str = "", change_hint: str = "", runtime_config_json: str = "") -> dict[str, Any]:
    prompt = f"""分析需求截图中的页面对象和需求变更标注。
文档上下文：{document_context or '未提供'}
变更提示：{change_hint or '未提供'}
识别页面区域、控件、表格字段、弹窗和红框/箭头/新增修改标记。若主体是表格，必须在table_markdown中按视觉行列关系输出完整Markdown表格；合并单元格的值要补到相关行，无法确认的单元格写[待确认]。同时输出内容摘要、可证明的业务规则、字段清单、建议归属模块标题和建议测试点。OCR原文无需重复输出，区域与控件只保留测试所需信息，每类最多8项。不要凭截图判断无法证明的业务规则。"""
    system = """你是软件测试需求截图分析专家。只返回JSON：
{"page_title":"","content_summary":"","suggested_module_title":"","regions":[{"name":"","elements":[{"type":"button/input/select/date_picker/table/dialog/text/unknown","name":"","visible_text":"","properties":{},"confidence":0.0}]}],"detected_fields":[],"business_rules":[],"suggested_test_points":[],"table_headers":[],"table_markdown":"|列1|列2|\n|---|---|\n|值1|值2|","annotations":[{"change_type":"add/change/remove/unknown","target":"","evidence":"","confidence":0.0}],"uncertain_items":[],"overall_confidence":0.0}
置信度范围0到1。仅凭修改后截图无法证明是新增时，change_type必须为unknown。"""
    try:
        # Vision is deliberately called before OCR and receives the original image only.
        # This prevents ambiguous OCR text from biasing or overriding visual structure.
        result = _vision(prompt, system, [image_path], max_tokens=4096, runtime_config_json=runtime_config_json)
    except Exception as exc:
        ocr_result, ocr_error = _safe_local_ocr(image_path)
        return {
            "page_title": "",
            "content_summary": "",
            "suggested_module_title": "",
            "regions": [],
            "detected_fields": [],
            "business_rules": [],
            "suggested_test_points": [],
            "table_headers": [],
            "table_markdown": "",
            "annotations": [],
            "uncertain_items": ["视觉模型未返回可解析结果"],
            "overall_confidence": None,
            "ocr": ocr_result,
            "vision_unavailable": True,
            "analysis_error": str(exc)[:1000],
            "evidence_sources": ["local_ocr"],
            "primary_content_source": "local_ocr_fallback",
            "ocr_role": "fallback_only",
            **({"ocr_error": ocr_error} if ocr_error else {}),
        }

    ocr_result, ocr_error = _safe_local_ocr(image_path)
    result["ocr"] = ocr_result
    result["evidence_sources"] = ["vision_model", "document_context", "local_ocr_fallback"]
    result["primary_content_source"] = "vision_model"
    result["ocr_role"] = "fallback_only"
    result["vision_content_complete"] = _has_meaningful_vision_content(result)
    if ocr_error:
        result["ocr_error"] = ocr_error
    return result


@mcp.tool(description="确定性比较两个结构化页面模型，不调用视觉API")
def compare_page_models(expected_page_model_json: str, actual_page_model_json: str) -> dict[str, Any]:
    try:
        expected = json.loads(expected_page_model_json)
        actual = json.loads(actual_page_model_json)
    except json.JSONDecodeError as exc:
        raise ValueError("两个页面模型参数都必须是有效JSON") from exc
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        raise ValueError("页面模型必须是JSON对象")
    return deterministic_compare(expected, actual)


@mcp.tool(description="对比需求截图与当前页面截图，输出面向测试设计的UI差异")
def ui_diff_check(requirement_path: str, implementation_path: str, requirement_context: str = "", runtime_config_json: str = "") -> dict[str, Any]:
    prompt = f"""第一张是需求截图，第二张是当前实现截图。需求上下文：{requirement_context or '未提供'}。
比较页面对象、字段、文字、布局和状态；不要把纯视觉差异误判为业务缺陷。"""
    system = """你是UI测试分析专家。只返回JSON：
{"matched":[],"missing_on_actual":[],"unexpected_on_actual":[],"property_mismatches":[{"object":"","expected":"","actual":"","severity":"critical/major/minor","confidence":0.0}],"visual_differences":[],"uncertain":[],"suggested_test_points":[],"overall_confidence":0.0}"""
    return _vision(prompt, system, [requirement_path, implementation_path], runtime_config_json=runtime_config_json)


@mcp.tool(description="将需求截图页面模型与Playwright采集的当前页面JSON结构进行语义对比")
def compare_requirement_with_page_model(
    requirement_image_path: str,
    actual_page_model_json: str,
    document_context: str = "",
    actual_screenshot_path: str = "",
    runtime_config_json: str = "",
) -> dict[str, Any]:
    try:
        actual_model = json.loads(actual_page_model_json)
    except json.JSONDecodeError as exc:
        raise ValueError("actual_page_model_json必须是有效JSON") from exc
    expected_model = analyze_requirement_ui(requirement_image_path, document_context, runtime_config_json=runtime_config_json)
    deterministic_diff = deterministic_compare(expected_model, actual_model)
    prompt = f"""复核需求页面模型与Playwright采集的当前页面模型差异。
文档上下文：{document_context or '未提供'}
需求页面模型：{json.dumps(expected_model, ensure_ascii=False)}
当前页面模型：{json.dumps(actual_model, ensure_ascii=False)}
确定性初步差异：{json.dumps(deterministic_diff, ensure_ascii=False)}
DOM/可访问性数据用于判断对象是否存在；截图用于补充布局、颜色、自绘控件。输出可追溯的测试差异，不直接把不一致判定为缺陷。"""
    system = """你是测试需求与页面实现对比专家。只返回JSON：
{"matched":[{"expected":"","actual_object_id":"","confidence":0.0}],"missing_on_actual":[],"unexpected_on_actual":[],"property_mismatches":[],"requirement_test_points":[{"title":"","evidence":"requirement_image/page_model/both","priority":"P0/P1/P2","confidence":0.0}],"uncertain":[],"overall_confidence":0.0}"""
    images = [requirement_image_path]
    if actual_screenshot_path:
        images.append(actual_screenshot_path)
    result = _vision(prompt, system, images, runtime_config_json=runtime_config_json)
    result["deterministic_diff"] = deterministic_diff
    result["expected_page_model"] = expected_model
    return result


@mcp.tool(description="通用图片理解，返回结构化对象、文字和场景信息")
def image_analysis(image_path: str, question: str = "详细描述这张图片", runtime_config_json: str = "") -> dict[str, Any]:
    return _vision(
        question,
        "只返回JSON：{\"summary\":\"\",\"objects\":[],\"scene\":\"\",\"text_content\":\"\",\"uncertain_items\":[],\"confidence\":0.0}",
        [image_path], runtime_config_json=runtime_config_json,
    )


def main() -> None:
    transport = os.environ.get("VISION_MCP_TRANSPORT", "streamable-http")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("VISION_MCP_TRANSPORT必须是stdio、sse或streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
