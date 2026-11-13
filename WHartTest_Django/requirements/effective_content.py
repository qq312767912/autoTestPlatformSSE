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
    records = []
    for index in range(0, len(parts), 2):
        value = parts[index]
        for number, paragraph in enumerate(re.split(r'\n\s*\n', value), 1):
            removed = re.findall(r'(?<![\\~])~~(?!~)([\s\S]*?)(?<!~)~~(?!~)', paragraph)
            removed.extend(m.group(2) for m in re.finditer(r'<(del|s|strike)\b[^>]*>([\s\S]*?)</\1\s*>', paragraph, re.I))
            if removed:
                current = re.sub(r'<(del|s|strike)\b[^>]*>[\s\S]*?</\1\s*>', '', paragraph, flags=re.I)
                current = re.sub(r'(?<![\\~])~~(?!~)([\s\S]*?)(?<!~)~~(?!~)', '', current)
                before = re.sub(r'</?(?:del|s|strike)\b[^>]*>', '', paragraph, flags=re.I).replace('~~', '')
                records.append({'source': f'Markdown内容块 {index // 2 + 1} 段落 {number}', 'before': before, 'deleted': '；'.join(removed), 'current': current})
        value = re.sub(r'<(del|s|strike)\b[^>]*>[\s\S]*?</\1\s*>', '', value, flags=re.I)
        value = re.sub(r'(?<![\\~])~~(?!~)([\s\S]*?)(?<!~)~~(?!~)', '', value)
        parts[index] = value
    result = ''.join(parts)
    notes = change_requirements(records)
    return result + ('\n\n' + notes if notes else '')


def change_requirements(records):
    if not records:
        return ''
    lines = ['## 取消与修改需求（删除标记产生的待确认变更）',
             '以下内容也是需求：需要识别取消或修改原有功能/规则的范围，并保留相应测试影响。',
             '删除原文是变更证据，不是仍需实现的现行规则；局部文字被删除不代表整个功能取消。单文件无法确定迭代归属，需人工确认。']
    for index, record in enumerate(records, 1):
        lines.extend([f'### 变更需求 {index}', f"来源：{record['source']}",
                      '类型：' + ('修改原规则' if record['current'].strip() else '取消原规则（范围待确认）')])
        for label, value in [('原文上下文（含删除及现行文字）', record['before']), ('删除原文', record['deleted']), ('当前有效内容', record['current'] or '此处无剩余正文')]:
            # Literal evidence is protected from a subsequent Markdown cleanup pass.
            fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', value)), default=3))
            lines.extend([label + '：', fence + 'text', value, fence])
    return '\n\n'.join(lines)


def word_change_requirements(data):
    from docx.oxml.ns import qn
    parsed = ParsedDocument(data)
    records = []
    for index, paragraph in enumerate(parsed.doc.element.body.iter(qn('w:p')), 1):
        deleted, before = [], []
        for run in paragraph.iter(qn('w:r')):
            text = ''.join(n.text or '' for n in run.iter() if n.tag in (qn('w:t'), qn('w:delText')))
            ancestors = {node.tag for node in run.iterancestors()}
            if qn('w:ins') not in ancestors and qn('w:moveTo') not in ancestors:
                before.append(text)
            if parsed.strike(run) or qn('w:del') in ancestors or qn('w:moveFrom') in ancestors:
                deleted.append(text)
        if ''.join(deleted).strip():
            records.append({'source': f'Word正文段落 {index}（含表格内段落）', 'before': ''.join(before),
                            'deleted': ''.join(deleted), 'current': parsed.text(parsed.clean(paragraph))})
    return change_requirements(records)
