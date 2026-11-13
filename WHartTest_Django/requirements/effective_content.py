"""Effective requirement text, shared by Word and Markdown ingestion."""
import re
from io import BytesIO
from .document_comparison import ParsedDocument


def effective_docx(data):
    parsed = ParsedDocument(data)
    body = parsed.doc.element.body
    for element in list(body):
        if element.tag.endswith('}sectPr'):
            continue
        body.replace(element, parsed.clean(element))
    output = BytesIO()
    parsed.doc.save(output)
    return output.getvalue()


def effective_markdown(text):
    # Keep code fences and inline code literal; strike-through is prose formatting.
    parts = re.split(r'(```[\s\S]*?```|~~~[^\n]*\n[\s\S]*?\n~~~|`[^`\n]*`)', text)
    for index in range(0, len(parts), 2):
        value = parts[index]
        value = re.sub(r'<(del|s|strike)\b[^>]*>[\s\S]*?</\1\s*>', '', value, flags=re.I)
        value = re.sub(r'(?<![\\~])~~(?!~)([\s\S]*?)(?<!~)~~(?!~)', '', value)
        parts[index] = value
    return ''.join(parts)
