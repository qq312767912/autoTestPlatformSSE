#!/usr/bin/env python3
"""Vision analysis Skill CLI. Vision-first, OCR-fallback, no MCP dependency."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def _json_result(raw: str) -> dict[str, Any]:
    value = (raw or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"视觉模型未返回有效JSON: {value[:300]}")
        result = json.loads(value[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("视觉模型返回值必须是JSON对象")
    return result


def _image_data_url(image_path: str) -> str:
    if image_path.startswith(("http://", "https://", "data:")):
        return image_path
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {path}")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过{MAX_IMAGE_BYTES}字节限制: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _vision(prompt: str, system: str, image_paths: list[str], max_tokens: int = 4096) -> dict[str, Any]:
    base_url = _env("VISION_API_BASE_URL", "VISION_MCP_BASE_URL", default="https://api.xiaomimimo.com/v1")
    api_key = _env("VISION_API_KEY", "VISION_MCP_API_KEY", "MIMO_API_KEY")
    model = _env("VISION_MODEL", "VISION_MCP_MODEL", default="mimo-v2.5")
    chat_path = _env("VISION_API_CHAT_PATH", "VISION_MCP_CHAT_COMPLETIONS_PATH", default="/chat/completions")
    if not api_key:
        raise RuntimeError("未配置VISION_API_KEY、VISION_MCP_API_KEY或MIMO_API_KEY")

    content = [
        {"type": "image_url", "image_url": {"url": _image_data_url(path)}}
        for path in image_paths
    ]
    content.append({"type": "text", "text": prompt})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/{chat_path.strip('/')}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    timeout = float(_env("VISION_API_TIMEOUT_SECONDS", "VISION_MCP_TIMEOUT_SECONDS", default="120"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"视觉API返回HTTP {exc.code}: {detail}") from exc
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("视觉API响应缺少choices[0]")
    return _json_result((choices[0].get("message") or {}).get("content", ""))


def _local_ocr(image_path: str) -> tuple[dict[str, Any], str]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return {"provider": "unavailable", "full_text": "", "text_blocks": [], "average_confidence": 0.0}, "RapidOCR未安装"
    try:
        result, _ = RapidOCR()(str(Path(image_path).expanduser().resolve()))
        blocks = []
        for box, text, score in result or []:
            blocks.append({"text": str(text), "bbox": box, "confidence": float(score)})
        confidence = sum(item["confidence"] for item in blocks) / len(blocks) if blocks else 0.0
        return {
            "provider": "rapidocr",
            "full_text": "\n".join(item["text"] for item in blocks),
            "text_blocks": blocks,
            "average_confidence": round(confidence, 4),
        }, ""
    except Exception as exc:
        return {"provider": "error", "full_text": "", "text_blocks": [], "average_confidence": 0.0}, str(exc)[:500]


def _meaningful(result: dict[str, Any]) -> bool:
    return any(result.get(key) for key in ("content_summary", "regions", "detected_fields", "business_rules", "table_markdown", "annotations"))


def analyze_requirement_ui(image_path: str, document_context: str = "", change_hint: str = "") -> dict[str, Any]:
    prompt = f"""分析需求截图中的页面对象和需求变更标注。
文档上下文：{document_context or '未提供'}
变更提示：{change_hint or '未提供'}
识别页面区域、控件、表格字段、弹窗和标注。表格必须按视觉行列关系输出完整Markdown；无法确认的单元格写[待确认]。输出内容摘要、可证明业务规则、字段清单和建议测试点。不要推测截图无法证明的规则。"""
    system = """你是软件测试需求截图分析专家。只返回JSON：
{"page_title":"","content_summary":"","suggested_module_title":"","regions":[],"detected_fields":[],"business_rules":[],"suggested_test_points":[],"table_headers":[],"table_markdown":"","annotations":[],"uncertain_items":[],"overall_confidence":0.0}。置信度范围0到1。"""
    try:
        result = _vision(prompt, system, [image_path])
        ocr, ocr_error = _local_ocr(image_path)
        result.update({
            "ocr": ocr,
            "primary_content_source": "vision_model",
            "ocr_role": "fallback_only",
            "vision_content_complete": _meaningful(result),
            "evidence_sources": ["vision_model", "document_context", "local_ocr_fallback"],
        })
        if ocr_error:
            result["ocr_error"] = ocr_error
        return result
    except Exception as exc:
        ocr, ocr_error = _local_ocr(image_path)
        return {
            "page_title": "", "content_summary": "", "suggested_module_title": "",
            "regions": [], "detected_fields": [], "business_rules": [],
            "suggested_test_points": [], "table_headers": [], "table_markdown": "",
            "annotations": [], "uncertain_items": ["视觉模型不可用，已保留OCR兜底"],
            "overall_confidence": None, "ocr": ocr, "vision_unavailable": True,
            "analysis_error": str(exc)[:1000], "primary_content_source": "local_ocr_fallback",
            "ocr_role": "fallback_only", "evidence_sources": ["local_ocr"],
            **({"ocr_error": ocr_error} if ocr_error else {}),
        }


def image_analysis(image_path: str, question: str) -> dict[str, Any]:
    return _vision(
        question or "详细描述图片中的页面、对象、文字和业务信息",
        '只返回JSON：{"summary":"","objects":[],"scene":"","text_content":"","uncertain_items":[],"confidence":0.0}',
        [image_path],
    )


def ui_diff_check(requirement_path: str, implementation_path: str, context: str = "") -> dict[str, Any]:
    prompt = f"""第一张是需求截图，第二张是当前实现截图。需求上下文：{context or '未提供'}。
比较页面对象、字段、文字、布局和状态；不要把纯视觉差异直接判定为业务缺陷。"""
    system = '只返回JSON：{"matched":[],"missing_on_actual":[],"unexpected_on_actual":[],"property_mismatches":[],"visual_differences":[],"uncertain":[],"suggested_test_points":[],"overall_confidence":0.0}'
    return _vision(prompt, system, [requirement_path, implementation_path])


def _elements(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output = {}
    for region in model.get("regions") or []:
        for element in region.get("elements") or []:
            key = str(element.get("name") or element.get("visible_text") or "").strip().lower()
            if key:
                output[key] = element
    return output


def compare_page_models(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    expected_elements, actual_elements = _elements(expected), _elements(actual)
    expected_keys, actual_keys = set(expected_elements), set(actual_elements)
    mismatches = []
    for key in sorted(expected_keys & actual_keys):
        expected_type = expected_elements[key].get("type")
        actual_type = actual_elements[key].get("type")
        if expected_type and actual_type and expected_type != actual_type:
            mismatches.append({"object": key, "expected_type": expected_type, "actual_type": actual_type})
    return {
        "matched": sorted(expected_keys & actual_keys),
        "missing_on_actual": sorted(expected_keys - actual_keys),
        "unexpected_on_actual": sorted(actual_keys - expected_keys),
        "property_mismatches": mismatches,
        "expected_count": len(expected_keys),
        "actual_count": len(actual_keys),
    }


def extract_requirement_images(document_path: str, output_dir: str) -> dict[str, Any]:
    source = Path(document_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"文档不存在: {source}")
    target = Path(output_dir or source.parent / f"{source.stem}-images").expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    images = []

    if source.suffix.lower() == ".docx":
        with zipfile.ZipFile(source) as archive:
            names = sorted(name for name in archive.namelist() if name.startswith("word/media/") and not name.endswith("/"))
            for index, name in enumerate(names, 1):
                suffix = Path(name).suffix or ".png"
                output = target / f"img_{index:03d}{suffix}"
                output.write_bytes(archive.read(name))
                images.append({"image_id": f"img_{index:03d}", "image_path": str(output), "context": "", "source_entry": name})
    elif source.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF图片提取需要运行环境安装pypdf") from exc
        reader = PdfReader(str(source))
        index = 0
        for page_number, page in enumerate(reader.pages, 1):
            for image in getattr(page, "images", []) or []:
                index += 1
                suffix = Path(image.name).suffix or ".png"
                output = target / f"img_{index:03d}{suffix}"
                output.write_bytes(image.data)
                images.append({"image_id": f"img_{index:03d}", "image_path": str(output), "context": (page.extract_text() or "")[:2000], "page": page_number})
    else:
        raise ValueError("仅支持DOCX和PDF文档")
    return {"document_path": str(source), "output_dir": str(target), "total": len(images), "images": images}


def _load_json(value: str, file_path: str) -> dict[str, Any]:
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if value:
        return json.loads(value)
    raise ValueError("必须提供JSON字符串或JSON文件")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vision analysis Skill")
    parser.add_argument("--action", required=True, choices=["analyze_requirement_ui", "extract_requirement_images", "ui_diff_check", "compare_page_models", "image_analysis"])
    parser.add_argument("--image_path", default="")
    parser.add_argument("--document_path", default="")
    parser.add_argument("--output_dir", default="")
    parser.add_argument("--document_context", default="")
    parser.add_argument("--change_hint", default="")
    parser.add_argument("--requirement_path", default="")
    parser.add_argument("--implementation_path", default="")
    parser.add_argument("--question", default="详细描述这张图片")
    parser.add_argument("--expected_json", default="")
    parser.add_argument("--actual_json", default="")
    parser.add_argument("--expected_json_file", default="")
    parser.add_argument("--actual_json_file", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.action == "analyze_requirement_ui":
            result = analyze_requirement_ui(args.image_path, args.document_context, args.change_hint)
        elif args.action == "extract_requirement_images":
            result = extract_requirement_images(args.document_path, args.output_dir)
        elif args.action == "ui_diff_check":
            result = ui_diff_check(args.requirement_path, args.implementation_path, args.document_context)
        elif args.action == "compare_page_models":
            result = compare_page_models(
                _load_json(args.expected_json, args.expected_json_file),
                _load_json(args.actual_json, args.actual_json_file),
            )
        else:
            result = image_analysis(args.image_path, args.question)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
