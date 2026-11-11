"""Deterministic DOCX comparison and extraction. No model or network calls."""
from copy import deepcopy
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile, BadZipFile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from lxml import etree


class ComparisonError(ValueError):
    pass


def enabled(element):
    return element is not None and element.get(qn('w:val'), 'true').lower() not in ('false', '0', 'off')


class ParsedDocument:
    def __init__(self, data):
        self.digest = sha256(data).hexdigest()
        try:
            with ZipFile(BytesIO(data)) as package:
                if 'word/document.xml' not in package.namelist():
                    raise ComparisonError('文件不是有效的 DOCX 文档。')
            self.doc = Document(BytesIO(data))
        except (BadZipFile, etree.XMLSyntaxError, KeyError, ValueError) as exc:
            raise ComparisonError('无法读取 DOCX，请检查文件是否损坏或加密。') from exc
        self.styles = {s.style_id: s for s in self.doc.styles}
        self.default_style_id = self.doc.styles.default(WD_STYLE_TYPE.PARAGRAPH).style_id
        self.blocks = []
        self.warnings = set()
        headings = []
        for index, element in enumerate(self.body_blocks(self.doc.element.body)):
            if element.tag not in (qn('w:p'), qn('w:tbl')):
                if element.tag != qn('w:sectPr'):
                    self.warnings.add('存在非标准正文结构，未纳入对比，请核对原文。')
                continue
            clean = self.clean(element)
            text = self.text(clean)
            if any(etree.QName(e).localname in ('footnoteReference', 'endnoteReference', 'OLEObject', 'chart', 'object', 'fldChar', 'instrText', 'cellDel', 'cellMerge') for e in clean.iter()):
                self.warnings.add('存在脚注、域、图表或特殊表格修订；若选择相关内容，导出会要求调整范围，避免静默丢失。')
            if not text.strip():
                continue
            level = self.heading_level(element)
            if level is not None:
                headings = [h for h in headings if h[0] < level]
                headings.append((level, text))
            self.blocks.append({'id': f'b{index}', 'text': text, 'element': clean,
                                'path': ' / '.join(h[1] for h in headings),
                                'headings': [h[1] for h in headings],
                                'kind': 'table' if element.tag == qn('w:tbl') else 'paragraph',
                                'key': self.comparison_key(clean, text)})
        self.warnings.add('表格按整表显示差异，图片按文件内容标识对比；自动编号和纯样式变化不作为业务差异。')
        self.warnings.add('对比范围为正文和表格；页眉页脚、批注和纯格式变化不参与对比。')

    def body_blocks(self, parent):
        for child in parent:
            local = etree.QName(child).localname
            if local in ('sdt', 'sdtContent', 'customXml', 'ins', 'moveTo'):
                yield from self.body_blocks(child)
            elif local not in ('sdtPr', 'sdtEndPr', 'del', 'moveFrom'):
                yield child

    def style_chain(self, style_id):
        visited = set()
        while style_id and style_id not in visited:
            visited.add(style_id)
            style = self.styles.get(style_id)
            if style is None:
                break
            yield style.element
            style_id = style.base_style.style_id if style.base_style else None

    def strike(self, run):
        # Per-property precedence: direct run > character style > paragraph style > defaults.
        sources = []
        rpr = run.find(qn('w:rPr'))
        if rpr is not None:
            sources.append(rpr)
            sid = rpr.find(qn('w:rStyle'))
            if sid is not None:
                sources.extend(s.find(qn('w:rPr')) for s in self.style_chain(sid.get(qn('w:val'))))
        paragraph = next((a for a in run.iterancestors() if a.tag == qn('w:p')), None)
        pstyle = paragraph.find('./' + qn('w:pPr') + '/' + qn('w:pStyle')) if paragraph is not None else None
        sid = pstyle.get(qn('w:val')) if pstyle is not None else self.default_style_id
        sources.extend(s.find(qn('w:rPr')) for s in self.style_chain(sid))
        sources.append(self.doc.styles.element.find('./' + qn('w:docDefaults') + '/' + qn('w:rPrDefault') + '/' + qn('w:rPr')))
        for prop in ('strike', 'dstrike'):
            for source in sources:
                node = source.find(qn('w:' + prop)) if source is not None else None
                if node is not None:
                    if enabled(node):
                        return True
                    break
        return False

    def clean(self, element):
        clone = deepcopy(element)
        for row in list(clone.iter(qn('w:tr'))):
            if row.find('./' + qn('w:trPr') + '/' + qn('w:del')) is not None:
                row.getparent().remove(row)
        # Compute inherited formatting before removing revision wrappers.
        for run in list(clone.iter(qn('w:r'))):
            if self.strike(run):
                run.getparent().remove(run)
        for node in list(clone.iter()):
            parent = node.getparent()
            if parent is None:
                continue
            local = etree.QName(node).localname
            if local in ('del', 'moveFrom'):
                parent.remove(node)
            elif local in ('ins', 'moveTo'):
                # Property markers aren't text wrappers.
                if parent.tag in (qn('w:trPr'), qn('w:rPr'), qn('w:pPr'), qn('w:tcPr')):
                    parent.remove(node)
                else:
                    offset = parent.index(node)
                    for child in list(node):
                        parent.insert(offset, child)
                        offset += 1
                    parent.remove(node)
            elif local.endswith('Change') or local in ('commentRangeStart', 'commentRangeEnd', 'commentReference', 'sectPr'):
                parent.remove(node)
        return clone

    def heading_level(self, element):
        if element.tag != qn('w:p'):
            return None
        ppr = element.find(qn('w:pPr'))
        if ppr is None:
            return None
        outline = ppr.find(qn('w:outlineLvl'))
        sid = ppr.find(qn('w:pStyle'))
        if outline is None and sid is not None:
            for style in self.style_chain(sid.get(qn('w:val'))):
                outline = style.find('./' + qn('w:pPr') + '/' + qn('w:outlineLvl'))
                if outline is not None:
                    break
        try:
            value = int(outline.get(qn('w:val'))) if outline is not None else None
            return value if value is not None and value < 9 else None
        except (ValueError, TypeError):
            return None

    def comparison_key(self, element, text):
        if element.tag != qn('w:tbl'):
            return text
        # Cell boundaries and merges are meaningful even when flattened text matches.
        layout = []
        for node in element.iter():
            local = etree.QName(node).localname
            if local in ('tr', 'tc', 'gridSpan', 'vMerge'):
                layout.append((local, node.get(qn('w:val'), '')))
        return text + '\0' + repr(layout)

    def text(self, element):
        if element.tag == qn('w:tbl'):
            return '\n'.join(' | '.join(self.text(cell) for cell in row.findall(qn('w:tc')))
                             for row in element.findall(qn('w:tr')))
        parts = []
        for node in element.iter():
            if node.tag == qn('w:t'):
                parts.append(node.text or '')
            elif node.tag == qn('w:hyperlink'):
                rid = node.get(qn('r:id'))
                rel = self.doc.part.rels.get(rid)
                if rel is not None and rel.is_external:
                    parts.append('[链接:' + rel.target_ref + ']')
            elif node.tag == qn('w:tab'):
                parts.append('\t')
            elif node.tag == qn('w:p') and node is not element:
                parts.append('\n')
            elif node.tag in (qn('w:br'), qn('w:cr')):
                parts.append('\n')
            elif node.tag in (qn('a:blip'), '{urn:schemas-microsoft-com:vml}imagedata'):
                rid = node.get(qn('r:embed')) or node.get(qn('r:id'))
                related = self.doc.part.related_parts.get(rid)
                parts.append('[图片:' + (sha256(related.blob).hexdigest()[:12] if related else '外部引用') + ']')
        return ''.join(parts).strip()


def compare_documents(old_data, new_data):
    old, new = ParsedDocument(old_data), ParsedDocument(new_data)
    matcher = SequenceMatcher(None, [b['key'] for b in old.blocks], [b['key'] for b in new.blocks], autojunk=False)
    hunks = []
    added = deleted = 0
    for number, operations in enumerate(matcher.get_grouped_opcodes(2)):
        rows = []
        for tag, i1, i2, j1, j2 in operations:
            if tag == 'equal':
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    rows.append(row('context', old.blocks[i], i+1, j+1))
            else:
                if tag in ('delete', 'replace'):
                    for i in range(i1, i2):
                        rows.append(row('delete', old.blocks[i], i+1, None))
                        deleted += 1
                if tag in ('insert', 'replace'):
                    for j in range(j1, j2):
                        rows.append(row('add', new.blocks[j], None, j+1))
                        added += 1
        hunks.append({'id': f'h{number}', 'rows': rows})
    return {'old_hash': old.digest, 'new_hash': new.digest, 'hunks': hunks,
            'added': added, 'deleted': deleted, 'warnings': sorted(old.warnings | new.warnings),
            'old_blocks': len(old.blocks), 'new_blocks': len(new.blocks)}


def row(kind, block, old_line, new_line):
    return {'kind': kind, 'text': block['text'], 'block_id': block['id'],
            'path': block['path'], 'block_kind': block['kind'],
            'old_line': old_line, 'new_line': new_line}


def export_selection(new_data, comparison, selected, title):
    if not selected or not isinstance(selected, list) or any(not isinstance(v, str) for v in selected):
        raise ComparisonError('请至少选择一个变更块。')
    hunks = {h['id']: h for h in comparison['hunks']}
    if set(selected) - set(hunks):
        raise ComparisonError('所选变更块已失效，请重新对比。')
    parsed = ParsedDocument(new_data)
    if parsed.digest != comparison['new_hash']:
        raise ComparisonError('源文档已变化，请重新对比。')
    selected_rows = [r for h in comparison['hunks'] if h['id'] in selected for r in h['rows'] if r['kind'] != 'context']
    wanted = {r['block_id'] for r in selected_rows if r['kind'] == 'add'}
    target = Document()
    # Use fresh package: never ship hidden, unselected source bodies or comments.
    target.part._styles_part._element = deepcopy(parsed.doc.styles.element)
    target.part.numbering_part._element = deepcopy(parsed.doc.part.numbering_part.element)
    target.add_heading(title, 0)
    target.add_paragraph('本文件包含人工确认的新增或修改后的内容。删除记录另见变更说明.md。')
    previous_path = None
    for block in parsed.blocks:
        if block['id'] not in wanted:
            continue
        if block['path'] and block['path'] != previous_path and block['text'] != block['headings'][-1]:
            target.add_paragraph('来源章节：' + block['path'])
        previous_path = block['path']
        node = deepcopy(block['element'])
        if any(etree.QName(e).localname in ('footnoteReference', 'endnoteReference', 'altChunk', 'OLEObject', 'chart', 'object', 'fldChar', 'instrText', 'cellDel', 'cellMerge', 'numPicBullet') for e in node.iter()):
            raise ComparisonError('所选内容含脚注、域、图表或嵌入对象，暂不能完整导出。请调整选择或在原文中另存普通内容。')
        for element in node.iter():
            for attr in list(element.attrib):
                if attr not in (qn('r:id'), qn('r:embed'), qn('r:link')):
                    continue
                rid = element.get(attr)
                rel = parsed.doc.part.rels.get(rid)
                if rel is None:
                    raise ComparisonError('所选内容存在失效的附件引用。')
                if rel.reltype.endswith('/image') and not rel.is_external:
                    new_rid, _ = target.part.get_or_add_image(BytesIO(rel.target_part.blob))
                elif rel.reltype.endswith('/hyperlink') and rel.is_external:
                    new_rid = target.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
                else:
                    raise ComparisonError('所选内容含暂不支持的外部图片或附件引用。')
                element.set(attr, new_rid)
        target.element.body.insert(len(target.element.body)-1, node)
    if not wanted:
        target.add_paragraph('本次选择只有删除项，没有新增或修改后的正文。请查阅变更说明中的删除记录。')
    output = BytesIO()
    target.save(output)
    report = [f'# {title}', '', f"旧版 SHA256：{comparison['old_hash']}", f"新版 SHA256：{comparison['new_hash']}", '',
              '以下是人工选择的变更记录，删除内容不属于有效需求正文。', '']
    for r in selected_rows:
        report.extend([f"## {'新增或修改后' if r['kind'] == 'add' else '删除或修改前'} · {r['path'] or '正文'}", '', r['text'], ''])
    bundle = BytesIO()
    with ZipFile(bundle, 'w') as archive:
        archive.writestr('迭代需求.docx', output.getvalue())
        archive.writestr('变更说明.md', '\n'.join(report))
    return bundle.getvalue()
