"""Adapt PDF text to the existing document comparison and report pipeline."""
from io import BytesIO
import re
from zipfile import ZipFile

from docx import Document

from .document_comparison import ComparisonError


def pdf_as_docx(data):
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ComparisonError('请先解除 PDF 加密后再上传。')
        pages = [page.extract_text() or '' for page in reader.pages]
    except ComparisonError:
        raise
    except Exception as exc:
        raise ComparisonError('无法读取 PDF，请检查文件是否损坏。') from exc

    document = Document()
    for number, page in enumerate(pages, 1):
        lines = [re.sub(r'[\x00-\x08\x0b-\x1f]', '', line).strip() for line in page.splitlines()]
        if any(lines):
            document.add_heading(f'第 {number} 页', level=1)
            for line in filter(None, lines):
                document.add_paragraph(line)
    if not document.paragraphs:
        raise ComparisonError('PDF 未提取到文字。扫描件请先进行 OCR 后重新上传。')

    raw, output = BytesIO(), BytesIO()
    document.save(raw)
    # Fixed ZIP timestamps keep the source hash stable across comparison and export.
    with ZipFile(raw) as source, ZipFile(output, 'w') as target:
        for entry in source.infolist():
            content = source.read(entry)
            entry.date_time = (1980, 1, 1, 0, 0, 0)
            target.writestr(entry, content)
    return output.getvalue()
