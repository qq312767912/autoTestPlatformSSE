"""Local OCR adapter with an optional RapidOCR backend."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _rapidocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "RapidOCR 加载失败：请确认已安装 vision-mcp[ocr]，"
            "并检查 OpenCV 所需的系统动态库是否齐全"
        ) from exc
    return RapidOCR()


def local_ocr(image_path: str, provider: str | None = None) -> dict[str, Any]:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {path}")
    selected = (provider or os.environ.get("VISION_MCP_OCR_PROVIDER", "rapidocr")).lower()
    if selected == "none":
        return {"provider": "none", "full_text": "", "text_blocks": [], "average_confidence": 0.0}
    if selected != "rapidocr":
        raise ValueError(f"不支持的OCR provider: {selected}")
    result, _elapsed = _rapidocr_engine()(str(path))
    blocks = []
    for item in result or []:
        box, text, score = item
        blocks.append({
            "text": str(text),
            "bbox": [[float(point[0]), float(point[1])] for point in box],
            "confidence": float(score),
        })
    confidence = sum(block["confidence"] for block in blocks) / len(blocks) if blocks else 0.0
    return {
        "provider": selected,
        "full_text": "\n".join(block["text"] for block in blocks),
        "text_blocks": blocks,
        "average_confidence": round(confidence, 4),
    }
