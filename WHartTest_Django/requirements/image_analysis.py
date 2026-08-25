"""Requirement image extraction and Vision MCP analysis workflow."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from asgiref.sync import async_to_sync
from django.core.files import File
from django.db import close_old_connections
from django.db.models import Q
from langchain_mcp_adapters.client import MultiServerMCPClient

from mcp_tools.models import RemoteMCPConfig

from .models import DocumentImage, RequirementDocument, RequirementModule


def _plain_result(value: Any) -> Any:
    """Normalize LangChain/MCP tool results into plain Python values."""
    if isinstance(value, dict):
        if set(value) == {"result"}:
            return _plain_result(value["result"])
        if value.get("type") == "text" and "text" in value:
            return _plain_result(value["text"])
        return value
    if isinstance(value, str):
        try:
            return _plain_result(json.loads(value))
        except json.JSONDecodeError:
            return value
    if isinstance(value, (list, int, float, bool)) or value is None:
        return value
    content = getattr(value, "content", None)
    if content is not None:
        return _plain_result(content)
    text = getattr(value, "text", None)
    if text is not None:
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return text
    return value


async def _call_vision_tool(name: str, arguments: dict) -> dict:
    config = await _get_vision_config()
    client = MultiServerMCPClient({
        "vision-mcp": {
            "url": config["url"],
            "transport": config["transport"].replace("-", "_"),
            **({"headers": config["headers"]} if config["headers"] else {}),
        }
    })
    tools = await client.get_tools()
    tool = next((item for item in tools if item.name == name), None)
    if not tool:
        raise RuntimeError(f"Vision MCP 未提供工具: {name}")
    # 模型密钥从数据库解密后仅在本次 MCP 调用中传递，不写日志、不写环境变量。
    runtime_config = await _get_vision_model_runtime_config()
    if runtime_config and name in {
        "analyze_requirement_ui", "extract_text_from_screenshot", "ui_diff_check",
        "compare_requirement_with_page_model", "image_analysis",
    }:
        arguments = {**arguments, "runtime_config_json": json.dumps(runtime_config, ensure_ascii=False)}
    result = _plain_result(await tool.ainvoke(arguments))
    if isinstance(result, list) and len(result) == 1:
        result = _plain_result(result[0])
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise RuntimeError(f"Vision MCP 工具 {name} 返回格式异常")
    return result


async def _get_vision_config() -> dict:
    from asgiref.sync import sync_to_async

    config = await sync_to_async(
        lambda: RemoteMCPConfig.objects.filter(name="vision-mcp", is_active=True).first()
    )()
    if config:
        return {"url": config.url, "transport": config.transport or "streamable-http", "headers": config.headers or {}}
    return {
        "url": os.environ.get("VISION_MCP_URL", "http://vision-mcp:8010/mcp"),
        "transport": "streamable-http",
        "headers": {},
    }


async def _get_vision_model_runtime_config() -> dict | None:
    from asgiref.sync import sync_to_async
    from mcp_tools.models import VisionModelConfig

    config = await sync_to_async(
        lambda: VisionModelConfig.objects.filter(is_active=True).order_by("pk").first()
    )()
    if not config:
        return None
    api_key = await sync_to_async(config.get_api_key)()
    if not api_key:
        raise RuntimeError("已启用的 Vision MCP 模型配置未填写 API Key")
    return {
        "base_url": config.base_url,
        "api_key": api_key,
        "model": config.model,
        "chat_completions_path": config.chat_completions_path,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
    }


def _best_module(document: RequirementDocument, context: str) -> RequirementModule | None:
    context_lower = (context or "").lower()
    best = None
    best_score = 0
    for module in document.modules.all():
        title = (module.title or "").lower()
        tokens = [token for token in title.replace("-", " ").split() if len(token) > 1]
        score = sum(3 for token in tokens if token in context_lower)
        if title and title in context_lower:
            score += 10
        if score > best_score:
            best, best_score = module, score
    return best


def _module_from_image_marker(
    document: RequirementDocument, image_id: str
) -> RequirementModule | None:
    """Bind an extracted DOCX image to the split module that still contains its marker."""
    markers = [f"docimg://{image_id}", f"/images/{image_id}/", f"{image_id}"]
    for module in document.modules.all():
        content = module.content or ""
        if any(marker in content for marker in markers):
            return module
    return None


def assign_modules_from_document_markers(document: RequirementDocument) -> int:
    assigned = 0
    for image in document.images.filter(module__isnull=True):
        module = _module_from_image_marker(document, image.image_id)
        if module:
            image.module = module
            image.save(update_fields=["module", "updated_at"])
            assigned += 1
    return assigned


class RequirementImageAnalysisService:
    def __init__(self, document: RequirementDocument):
        self.document = document

    def prepare(self, force: bool = False) -> list[DocumentImage]:
        """Extract PDF images through Vision MCP. DOCX images already exist after text extraction."""
        if self.document.images.exists() and not force:
            assign_modules_from_document_markers(self.document)
            return list(self.document.images.select_related("module"))
        if not self.document.file:
            return []
        if self.document.document_type not in {"pdf", "docx"}:
            return []

        output_dir = str(Path(self.document.file.path).parent / str(self.document.id) / "vision-images")
        manifest = async_to_sync(_call_vision_tool)(
            "extract_requirement_images",
            {"document_path": self.document.file.path, "output_dir": output_dir},
        )
        if force:
            self.document.images.all().delete()
        existing_ids = set(self.document.images.values_list("image_id", flat=True))
        created = []
        for index, item in enumerate(manifest.get("images", []), 1):
            image_id = f"vision_{index:03d}"
            if image_id in existing_ids:
                continue
            image_path = Path(item["image_path"])
            with image_path.open("rb") as image_stream:
                instance = DocumentImage(
                    document=self.document,
                    image_id=image_id,
                    order=index,
                    original_filename=image_path.name,
                    content_type="image/" + image_path.suffix.lstrip(".").lower().replace("jpg", "jpeg"),
                    file_size=image_path.stat().st_size,
                    nearby_text=item.get("context", ""),
                )
                instance.module = _best_module(self.document, instance.nearby_text)
                instance.image_file.save(image_path.name, File(image_stream), save=True)
                created.append(instance)
        count = self.document.images.count()
        self.document.has_images = count > 0
        self.document.image_count = count
        self.document.image_analysis_status = "not_started"
        self.document.save(update_fields=["has_images", "image_count", "image_analysis_status"])
        return list(self.document.images.select_related("module"))

    def _analyze_one(self, image_id: str) -> tuple[bool, str]:
        """Analyze one image in an isolated worker thread and persist progress."""
        close_old_connections()
        image = self.document.images.select_related("module").get(id=image_id)
        image.review_status = "processing"
        image.analysis_error = ""
        image.save(update_fields=["review_status", "analysis_error", "updated_at"])
        try:
            analysis = async_to_sync(_call_vision_tool)(
                "analyze_requirement_ui",
                {
                    "image_path": image.image_file.path,
                    "document_context": image.nearby_text,
                    "change_hint": image.change_description,
                },
            )
            ocr = analysis.get("ocr") or {}
            image.ocr_text = ocr.get("full_text", "")
            image.analysis_result = analysis
            image.page_title = analysis.get("page_title", "")
            image.table_markdown = analysis.get("table_markdown", "")
            image.suggested_test_points = analysis.get("suggested_test_points") or []
            image.confidence = analysis.get("overall_confidence")
            annotations = analysis.get("annotations") or []
            if annotations:
                image.change_type = annotations[0].get("change_type", "unknown")
                image.change_description = "\n".join(
                    str(item.get("evidence") or item.get("target") or "") for item in annotations
                ).strip()
            image.analysis_error = analysis.get("analysis_error", "")
            if not image.module:
                image.module = _module_from_image_marker(self.document, image.image_id)
            if not image.module:
                suggested_module = analysis.get("suggested_module_title", "")
                image.module = _best_module(
                    self.document,
                    f"{suggested_module}\n{image.page_title}\n{image.nearby_text}\n{image.ocr_text}",
                )
            image.review_status = "analyzed"
            image.save()
            return True, image.image_id
        except Exception as error:
            image.review_status = "error"
            image.analysis_error = str(error)[:1000]
            image.save(update_fields=["review_status", "analysis_error", "updated_at"])
            return False, image.image_id
        finally:
            close_old_connections()

    def analyze(self, image_ids: list[str] | None = None, max_workers: int = 2) -> dict:
        images = self.document.images.filter(is_enabled=True)
        if image_ids:
            images = images.filter(id__in=image_ids)
        else:
            images = images.filter(
                Q(review_status__in=["pending", "error", "processing"]) | ~Q(analysis_error="")
            )
        selected_ids = [str(value) for value in images.values_list("id", flat=True)]
        self.document.image_analysis_status = "processing"
        self.document.save(update_fields=["image_analysis_status"])
        analyzed = 0
        failed = 0
        if not selected_ids:
            self.document.image_analysis_status = "user_reviewing"
            self.document.save(update_fields=["image_analysis_status"])
            return {"analyzed": 0, "failed": 0, "total": 0}
        if selected_ids:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(selected_ids))) as executor:
                futures = [executor.submit(self._analyze_one, image_id) for image_id in selected_ids]
                for future in as_completed(futures):
                    success, _ = future.result()
                    analyzed += int(success)
                    failed += int(not success)
        self.document.image_analysis_status = "user_reviewing" if analyzed else "failed"
        self.document.save(update_fields=["image_analysis_status"])
        return {"analyzed": analyzed, "failed": failed, "total": len(selected_ids)}


_DOC_IMAGE_MARKER_RE = re.compile(r"!\[[^\]]*\]\(docimg://(?P<image_id>[^)]+)\)")


def _format_prompt_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        lines = []
        for item in value:
            rendered = _format_prompt_value(item)
            if rendered:
                lines.append(f"- {rendered}")
        return "\n".join(lines)
    if isinstance(value, dict):
        return "\n".join(
            f"- {key}：{_format_prompt_value(item)}"
            for key, item in value.items()
            if _format_prompt_value(item)
        )
    return str(value)


def confirmed_image_prompt_block(image: DocumentImage) -> str:
    """Build concise prompt context: structured vision result first, OCR only as fallback."""
    analysis = image.analysis_result if isinstance(image.analysis_result, dict) else {}
    structured_fields = [
        ("页面内容摘要", analysis.get("content_summary")),
        ("识别字段", analysis.get("detected_fields")),
        ("业务规则", analysis.get("business_rules")),
        ("页面区域与控件", analysis.get("regions")),
        ("变更标注", analysis.get("annotations")),
        ("表格Markdown", image.table_markdown or analysis.get("table_markdown")),
        ("不确定项", analysis.get("uncertain_items")),
    ]
    rendered_structured = [
        (label, _format_prompt_value(value)) for label, value in structured_fields
    ]
    rendered_structured = [(label, value) for label, value in rendered_structured if value]

    lines = [
        f"[用户已确认的需求图片 {image.image_id}]",
        f"页面：{image.page_title or analysis.get('page_title') or '未标注'}",
        f"变更类型：{image.get_change_type_display()}",
        f"变更说明：{image.change_description or '未填写'}",
    ]
    for label, value in rendered_structured:
        lines.append(f"{label}：\n{value}")

    # OCR 永久保存在数据库中，但只有结构化视觉结果不可用时才进入 LLM 上下文。
    if not rendered_structured:
        lines.append(f"OCR兜底：\n{image.ocr_text or '无可用OCR内容'}")

    points = _format_prompt_value(image.suggested_test_points or [])
    if points:
        lines.append(f"建议测试点：\n{points}")
    if image.user_notes:
        lines.append(f"用户备注：\n{image.user_notes}")
    return "\n".join(lines)


def _expand_confirmed_images(content: str, images: list[DocumentImage]) -> str:
    """Temporarily expand confirmed image markers without changing stored document text."""
    image_map = {image.image_id: image for image in images}
    expanded_ids: set[str] = set()

    def replace_marker(match: re.Match) -> str:
        image_id = match.group("image_id")
        image = image_map.get(image_id)
        if not image:
            return match.group(0)
        expanded_ids.add(image_id)
        return confirmed_image_prompt_block(image)

    expanded = _DOC_IMAGE_MARKER_RE.sub(replace_marker, content or "")
    unreferenced = [
        confirmed_image_prompt_block(image)
        for image in images
        if image.image_id not in expanded_ids
    ]
    if unreferenced:
        expanded = f"{expanded}\n\n=== 用户已确认的需求图片上下文 ===\n" + "\n\n".join(unreferenced)
    return expanded.strip()


def confirmed_image_context(module: RequirementModule) -> str:
    images = list(
        module.document_images.filter(is_enabled=True, review_status="confirmed").order_by("order")
    )
    return "\n\n".join(confirmed_image_prompt_block(image) for image in images)


def confirmed_module_content(module: RequirementModule) -> str:
    images = list(
        module.document_images.filter(is_enabled=True, review_status="confirmed").order_by("order")
    )
    return _expand_confirmed_images(module.content or "", images)


def confirmed_document_content(document: RequirementDocument) -> str:
    images = list(
        document.images.filter(is_enabled=True, review_status="confirmed").order_by("order")
    )
    return _expand_confirmed_images(document.content or "", images)
