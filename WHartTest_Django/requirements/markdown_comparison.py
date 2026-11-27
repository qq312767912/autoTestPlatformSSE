"""Convert Markdown lines and headings into comparison blocks."""
import re

from docx import Document

from .comparison_docx import comparison_docx_bytes
from .document_comparison import ComparisonError


def markdown_as_docx(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ComparisonError('请将 Markdown 文件另存为 UTF-8 后重新上传。') from exc

    document = Document()
    fence = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if re.search(r'[\x00-\x08\x0b-\x1f]', line):
            raise ComparisonError('Markdown 文件含无效控制字符。')
        fence_match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence_match:
            marker, rest = fence_match.groups()
            if fence is None:
                fence = marker
            elif marker.startswith(fence) and not rest.strip():
                fence = None
            document.add_paragraph(line)
            continue
        heading = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+)$', line) if fence is None else None
        if heading:
            title = re.sub(r'[ \t]+#+[ \t]*$', '', heading[2]).strip()
            document.add_heading(title, level=len(heading[1]))
        else:
            document.add_paragraph(line)

    return comparison_docx_bytes(document)
