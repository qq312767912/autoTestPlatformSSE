"""Effective requirement text, shared by Word and Markdown ingestion."""
import re
from io import BytesIO
from .document_comparison import ParsedDocument


def paragraph_change(parsed, paragraph, index):
    from docx.oxml.ns import qn
    deleted, context = [], []
    for run in paragraph.iter(qn('w:r')):
        if next((p for p in run.iterancestors() if p.tag == qn('w:p')), None) is not paragraph:
            continue
        text = ''.join(n.text or '' for n in run.iter() if n.tag in (qn('w:t'), qn('w:delText')))
        ancestors = list(run.iterancestors())
        context.append(text)
        row_deleted = any(p.tag == qn('w:tr') and p.find('./'+qn('w:trPr')+'/'+qn('w:del')) is not None for p in ancestors)
        if parsed.strike(run) or row_deleted or any(p.tag in (qn('w:del'), qn('w:moveFrom')) for p in ancestors):
            deleted.append(text)
    if not ''.join(deleted).strip(): return None
    return {'source': f'Word原段落 {index}（含表格内段落）', 'before': ''.join(context),
            'deleted': ''.join(deleted), 'current': parsed.text(parsed.clean(paragraph))}


def inline_change(record):
    def literal(value):
        value = re.sub(r'\x00CODE\d+\x00', '〔代码片段保留在正文〕', value)
        return value.replace('~', r'\~').replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' / ')
    return '\n'.join(['> 【取消/修改需求】以下删除标记同样是业务变更，不是仍生效的旧规则。',
        '> 类型：' + ('修改原规则' if record['current'].strip() else '取消原规则（范围待确认）'),
        '> 来源：' + record['source'], '> 删除原文：' + literal(record['deleted']),
        '> 原文上下文：' + literal(record['before']),
        '> 当前有效内容：' + literal(record['current'] or '此处无剩余正文'),
        '> 处理要求：识别取消或修改的功能/规则范围及测试影响；局部删字不等于整项功能取消，迭代归属需确认。'])


def effective_docx(data):
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    parsed = ParsedDocument(data)
    marker = '{urn:wharttest:source}paragraph'
    records = {}
    for index, paragraph in enumerate(parsed.doc.element.body.iter(qn('w:p')), 1):
        record = paragraph_change(parsed, paragraph, index)
        if record:
            key = str(index); records[key] = record; paragraph.set(marker, key)
    body = parsed.doc.element.body
    for original in list(body):
        if original.tag == qn('w:sectPr'): continue
        source_keys = {p.get(marker) for p in original.iter(qn('w:p')) if p.get(marker)}
        clean = parsed.clean(original)
        found = set()
        for paragraph in clean.iter(qn('w:p')):
            key = paragraph.attrib.pop(marker, None)
            if not key: continue
            found.add(key)
            run = Paragraph(paragraph, parsed.doc).add_run('\n' + inline_change(records[key]))
            run.font.strike = False; run.font.double_strike = False
        body.replace(original, clean)
        # A tracked-deleted table row vanishes from the clean tree. Preserve its
        # cancellation requirement directly after that table, before the next section.
        offset = body.index(clean) + 1
        for key in sorted(source_keys-found, key=int):
            node = parsed.doc.add_paragraph(inline_change(records[key]))._p
            body.insert(offset, node); offset += 1
    output = BytesIO(); parsed.doc.save(output)
    return output.getvalue()


def effective_markdown(text):
    # Protect literal code, and attach each deletion to its original section.
    protected = []
    def protect(match):
        protected.append(match.group()); return f'\x00CODE{len(protected)-1}\x00'
    masked = re.sub(r'```[\s\S]*?```|~~~[^\n]*\n[\s\S]*?\n~~~|`[^`\n]*`', protect, text)
    pieces = re.split(r'(\n[ \t]*\n|^#{1,6}[ \t]+[^\n]*(?:\n|$))', masked, flags=re.M)
    for index, paragraph in enumerate(pieces):
        removed = re.findall(r'(?<![\\~])~~(?!~)([\s\S]*?)(?<!~)~~(?!~)', paragraph)
        removed.extend(m.group(2) for m in re.finditer(r'<(del|s|strike)\b[^>]*>([\s\S]*?)</\1\s*>', paragraph, re.I))
        if not removed: continue
        current = re.sub(r'<(del|s|strike)\b[^>]*>[\s\S]*?</\1\s*>', '', paragraph, flags=re.I)
        current = re.sub(r'(?<![\\~])~~(?!~)([\s\S]*?)(?<!~)~~(?!~)', '', current)
        before = re.sub(r'</?(?:del|s|strike)\b[^>]*>', '', paragraph, flags=re.I).replace('~~', '')
        record = {'source': f'Markdown原内容块 {index+1}', 'before': before, 'deleted': '；'.join(removed), 'current': current}
        pieces[index] = current.rstrip() + '\n\n' + inline_change(record) + '\n\n'
    result = ''.join(pieces)
    for index, code in enumerate(protected): result = result.replace(f'\x00CODE{index}\x00', code)
    return result


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
