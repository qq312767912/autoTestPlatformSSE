"""Extract images and nearby text from DOCX/PDF requirement documents."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _safe_output_dir(document: Path, output_dir: str | None) -> Path:
    target = Path(output_dir).expanduser().resolve() if output_dir else document.parent / f"{document.stem}_images"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _record(path: Path, document: Path, index: int, context: str, location: str) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "image_id": f"IMG-{index:03d}-{digest[:8]}",
        "image_path": str(path.resolve()),
        "document": str(document.resolve()),
        "location": location,
        "context": context.strip(),
        "sha256": digest,
    }


def extract_docx_images(document: Path, output: Path) -> list[dict]:
    results: list[dict] = []
    with zipfile.ZipFile(document) as archive:
        rels_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        rels = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels_root.findall("pr:Relationship", NS)
            if rel.attrib.get("Type", "").endswith("/image")
        }
        root = ET.fromstring(archive.read("word/document.xml"))
        paragraphs = root.findall(".//w:body/w:p", NS)
        for paragraph_index, paragraph in enumerate(paragraphs, 1):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
            nearby = []
            for offset in (-2, -1, 0, 1, 2):
                idx = paragraph_index - 1 + offset
                if 0 <= idx < len(paragraphs):
                    value = "".join(node.text or "" for node in paragraphs[idx].findall(".//w:t", NS)).strip()
                    if value:
                        nearby.append(value)
            for blip in paragraph.findall(".//a:blip", NS):
                rel_id = blip.attrib.get(f"{{{NS['r']}}}embed")
                target = rels.get(rel_id or "")
                if not target:
                    continue
                member = "word/" + target.lstrip("/")
                suffix = Path(target).suffix or ".png"
                image_path = output / f"image_{len(results) + 1:03d}{suffix}"
                image_path.write_bytes(archive.read(member))
                results.append(_record(image_path, document, len(results) + 1, "\n".join(nearby) or text, f"paragraph:{paragraph_index}"))
    return results


def extract_pdf_images(document: Path, output: Path) -> list[dict]:
    try:
        import fitz
    except ImportError:
        return _extract_pdf_images_with_pypdf(document, output)
    results: list[dict] = []
    pdf = fitz.open(document)
    try:
        for page_index, page in enumerate(pdf, 1):
            context = page.get_text("text")
            for image in page.get_images(full=True):
                info = pdf.extract_image(image[0])
                suffix = "." + info.get("ext", "png")
                image_path = output / f"page_{page_index:03d}_image_{len(results) + 1:03d}{suffix}"
                image_path.write_bytes(info["image"])
                results.append(_record(image_path, document, len(results) + 1, context, f"page:{page_index}"))
    finally:
        pdf.close()
    return results


def _extract_pdf_images_with_pypdf(document: Path, output: Path) -> list[dict]:
    """Alpine/ARM64 fallback that avoids the manylinux-only PyMuPDF runtime."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF图片提取需要安装 pymupdf 或 pypdf") from exc

    results: list[dict] = []
    reader = PdfReader(str(document))
    for page_index, page in enumerate(reader.pages, 1):
        context = page.extract_text() or ""
        for image in page.images:
            suffix = Path(image.name or "image.png").suffix or ".png"
            image_path = output / f"page_{page_index:03d}_image_{len(results) + 1:03d}{suffix}"
            image_path.write_bytes(image.data)
            results.append(_record(image_path, document, len(results) + 1, context, f"page:{page_index}"))
    return results


def extract_document_images(document_path: str, output_dir: str | None = None) -> dict:
    document = Path(document_path).expanduser().resolve()
    if not document.is_file():
        raise FileNotFoundError(f"文档不存在: {document}")
    output = _safe_output_dir(document, output_dir)
    suffix = document.suffix.lower()
    if suffix == ".docx":
        images = extract_docx_images(document, output)
    elif suffix == ".pdf":
        images = extract_pdf_images(document, output)
    else:
        raise ValueError("仅支持 .docx 和 .pdf 文档")
    manifest = {"document": str(document), "output_dir": str(output), "count": len(images), "images": images}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
