"""Report adapter for the user-provided docx-revision-report 1.0.4 renderer.

The report layout is owned by report_vendor/extract_all_revisions.py, not recreated
here. This adapter only supplies revisions, including manually struck text.
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from itertools import count
from difflib import SequenceMatcher
import re
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.part import Part
from docx.opc.packuri import PackURI
from .document_comparison import ParsedDocument, ComparisonError
from .report_vendor import extract_all_revisions as renderer


def mark_runs(element, kind, ids):
    for run in list(element.iter(qn('w:r'))):
        parent = run.getparent()
        marker = OxmlElement('w:' + kind)
        marker.set(qn('w:id'), str(next(ids)))
        marker.set(qn('w:author'), '文档版本对比')
        if kind == 'del':
            for text in run.iter(qn('w:t')):
                text.tag = qn('w:delText')
        parent.replace(run, marker)
        marker.append(run)
    if kind == 'del':
        for paragraph in element.iter(qn('w:p')):
            props = paragraph.get_or_add_pPr()
            rpr = props.find(qn('w:rPr'))
            if rpr is None:
                rpr = OxmlElement('w:rPr'); props.append(rpr)
            rpr.append(OxmlElement('w:del'))


def manual_strikes_as_revisions(data):
    parsed = ParsedDocument(data)
    ids = count(1000000)
    for run in list(parsed.doc.element.body.iter(qn('w:r'))):
        if parsed.strike(run) and not any(a.tag in (qn('w:del'), qn('w:moveFrom')) for a in run.iterancestors()):
            parent = run.getparent()
            marker = OxmlElement('w:del')
            marker.set(qn('w:id'), str(next(ids)))
            for text in run.iter(qn('w:t')): text.tag = qn('w:delText')
            parent.replace(run, marker); marker.append(run)
    output = BytesIO(); parsed.doc.save(output)
    return output.getvalue()


def render_report(data, source_name='需求文档.docx'):
    data = manual_strikes_as_revisions(data)
    with TemporaryDirectory(prefix='requirement-report-') as folder:
        name = Path(source_name).name
        if not name.lower().endswith('.docx'): name += '.docx'
        source, output = Path(folder)/'source.docx', Path(folder)/'report.docx'
        source.write_bytes(data)
        info = renderer.analyze(str(source))
        info['src_name'] = name
        renderer.build_report_docx(info, str(output))
        return output.read_bytes()


def comparison_report(new_data, comparison, selected, old_data=None, source_name='需求文档.docx'):
    new = ParsedDocument(new_data)
    old = ParsedDocument(old_data) if old_data else None
    doc = new.doc
    body = doc.element.body
    for element in list(body):
        if element.tag != qn('w:sectPr'): body.remove(element)
    elements = {}
    for block in new.blocks:
        element = deepcopy(block['element'])
        elements[block['id']] = element
        body.insert(len(body)-1, element)
    old_elements = {b['id']: b['element'] for b in old.blocks} if old else {}
    for hunk in comparison['hunks']:
        if hunk['id'] in selected: continue
        for row in hunk['rows']:
            if row['kind'] == 'add':
                element = elements.get(row['block_id'])
                if element is not None and element.getparent() is body and new.heading_level(element) is None: body.remove(element)

    # Import an entire selected attachment graph, retaining relationship IDs inside parts.
    imported = {}
    serial = count(1)
    def clone_part(source):
        if id(source) in imported: return imported[id(source)]
        suffix = Path(str(source.partname)).suffix
        part = Part(PackURI(f'/word/revision-assets/part{next(serial)}{suffix}'), source.content_type, source.blob, doc.part.package)
        imported[id(source)] = part
        for rel in source.rels.values():
            target = rel.target_ref if rel.is_external else clone_part(rel.target_part)
            part.rels.add_relationship(rel.reltype, target, rel.rId, rel.is_external)
        return part
    def import_relations(element):
        if old is None: return
        for node in element.iter():
            for attr in (qn('r:id'), qn('r:embed'), qn('r:link')):
                rid = node.get(attr)
                if not rid: continue
                rel = old.doc.part.rels.get(rid)
                if rel is None: raise ComparisonError('旧版变更内容含失效附件引用。')
                target = rel.target_ref if rel.is_external else clone_part(rel.target_part)
                node.set(attr, doc.part.relate_to(target, rel.reltype, is_external=rel.is_external))
    ids = count(1)
    def can_inline(left, right):
        return left is not None and right is not None and left.tag == right.tag == qn('w:p') and all(
            child.tag in (qn('w:pPr'), qn('w:r')) and (child.tag != qn('w:r') or all(x.tag in (qn('w:rPr'), qn('w:t')) for x in child))
            for paragraph in (left,right) for child in paragraph) and sum(len(t.text or '') for paragraph in (left,right) for t in paragraph.iter(qn('w:t'))) < 16000
    def inline_paragraph(left, right):
        left_runs, right_runs = list(left.findall(qn('w:r'))), list(right.findall(qn('w:r')))
        def raw(runs): return ''.join(''.join(n.text or '' for n in r.iter(qn('w:t'))) for r in runs)
        a,b = raw(left_runs),raw(right_runs)
        if len(a)+len(b)>16000: return False
        for run in right_runs: right.remove(run)
        def emit(runs,start,end,kind):
            offset=0
            for original in runs:
                text=''.join(n.text or '' for n in original.iter(qn('w:t')))
                low,high=max(start-offset,0),min(end-offset,len(text))
                offset+=len(text)
                if high<=low: continue
                run=OxmlElement('w:r');props=original.find(qn('w:rPr'))
                if props is not None: run.append(deepcopy(props))
                node=OxmlElement('w:delText' if kind=='del' else 'w:t');node.text=text[low:high]
                node.set('{http://www.w3.org/XML/1998/namespace}space','preserve');run.append(node)
                if kind:
                    marker=OxmlElement('w:'+kind);marker.set(qn('w:id'),str(next(ids)));marker.set(qn('w:author'),'文档版本对比');marker.append(run);right.append(marker)
                else: right.append(run)
        left_tokens=re.findall(r'\d+(?:[.,]\d+)*|[A-Za-z_]+|\s+|.',a,re.S)
        right_tokens=re.findall(r'\d+(?:[.,]\d+)*|[A-Za-z_]+|\s+|.',b,re.S)
        left_offsets=[0];right_offsets=[0]
        for token in left_tokens:left_offsets.append(left_offsets[-1]+len(token))
        for token in right_tokens:right_offsets.append(right_offsets[-1]+len(token))
        for tag,i,ii,j,jj in SequenceMatcher(None,left_tokens,right_tokens,autojunk=False).get_opcodes():
            i,ii,j,jj=left_offsets[i],left_offsets[ii],right_offsets[j],right_offsets[jj]
            if tag in ('replace','delete'): emit(left_runs,i,ii,'del')
            if tag in ('replace','insert'): emit(right_runs,j,jj,'ins')
            if tag=='equal': emit(right_runs,j,jj,None)
        return True
    paired_paragraphs = {}
    paired_tables = {}
    paired_old = set()
    if old:
        for hunk in comparison['hunks']:
            if hunk['id'] not in selected: continue
            for pair in hunk.get('pairs', []):
                left, right = pair.get('old'), pair.get('new')
                if left and right and left['kind'] == 'delete' and right['kind'] == 'add' and left['block_kind'] == right['block_kind'] == 'table':
                    paired_tables[right['block_id']] = old_elements[left['block_id']]
                    paired_old.add(left['block_id'])
                elif left and right and left['kind']=='delete' and right['kind']=='add' and can_inline(old_elements.get(left['block_id']),elements.get(right['block_id'])) and len(left['text'])+len(right['text'])<16000:
                    paired_paragraphs[right['block_id']]=old_elements[left['block_id']]
                    paired_old.add(left['block_id'])
    def mark_cell(old_cell, new_cell):
        before = [e for e in old_cell if e.tag in (qn('w:p'), qn('w:tbl'))]
        after = [e for e in new_cell if e.tag in (qn('w:p'), qn('w:tbl'))]
        matcher = SequenceMatcher(None, [old.text(e) for e in before], [new.text(e) for e in after], autojunk=False)
        for tag, a, aa, b, bb in matcher.get_opcodes():
            if tag == 'equal': continue
            if tag=='replace' and aa-a==bb-b and all(can_inline(x,y) and len(old.text(x))+len(new.text(y))<16000 for x,y in zip(before[a:aa],after[b:bb])):
                for x,y in zip(before[a:aa],after[b:bb]): inline_paragraph(x,y)
                continue
            anchor = after[b] if b < len(after) else None
            for original in before[a:aa]:
                deleted = deepcopy(original); import_relations(deleted); mark_runs(deleted, 'del', ids)
                new_cell.insert(new_cell.index(anchor) if anchor is not None else len(new_cell), deleted)
            for element in after[b:bb]: mark_runs(element, 'ins', ids)
    def mark_table(before, after):
        old_rows, new_rows = before.findall(qn('w:tr')), after.findall(qn('w:tr'))
        if old.comparison_key(before, old.text(before)) != new.comparison_key(after, new.text(after)) and old.text(before) == new.text(after):
            return False
        matcher = SequenceMatcher(None, [old.text(r) for r in old_rows], [new.text(r) for r in new_rows], autojunk=False)
        for tag, a, aa, b, bb in matcher.get_opcodes():
            if tag == 'equal': continue
            if tag == 'replace' and aa-a == bb-b and all(len(x.findall(qn('w:tc'))) == len(y.findall(qn('w:tc'))) for x,y in zip(old_rows[a:aa],new_rows[b:bb])):
                for x,y in zip(old_rows[a:aa],new_rows[b:bb]):
                    for left,right in zip(x.findall(qn('w:tc')),y.findall(qn('w:tc'))): mark_cell(left,right)
            else:
                anchor = new_rows[b] if b < len(new_rows) else None
                for original in old_rows[a:aa]:
                    deleted = deepcopy(original); import_relations(deleted); mark_runs(deleted,'del',ids)
                    after.insert(after.index(anchor) if anchor is not None else len(after),deleted)
                for element in new_rows[b:bb]: mark_runs(element,'ins',ids)
        return True
    for hunk in comparison['hunks']:
        if hunk['id'] not in selected: continue
        rows = hunk['rows']
        for index, row in enumerate(rows):
            if row['kind'] == 'add':
                element = elements.get(row['block_id'])
                if element is not None:
                    if any(e.tag in (qn('w:footnoteReference'), qn('w:endnoteReference')) for e in element.iter()):
                        raise ComparisonError('所选内容含脚注或尾注，尚不能保证完整导出。')
                    if row['block_id'] in paired_paragraphs:
                        inline_paragraph(paired_paragraphs[row['block_id']], element)
                    elif row['block_id'] in paired_tables:
                        if mark_table(paired_tables[row['block_id']], element) is False:
                            deleted = deepcopy(paired_tables[row['block_id']]); import_relations(deleted); mark_runs(deleted,'del',ids)
                            body.insert(body.index(element),deleted); mark_runs(element,'ins',ids)
                    else:
                        mark_runs(element, 'ins', ids)
            elif row['kind'] == 'delete':
                if row['block_id'] in paired_old: continue
                original = old_elements.get(row['block_id'])
                if original is not None:
                    element = deepcopy(original); import_relations(element)
                else:
                    element = OxmlElement('w:p'); run = OxmlElement('w:r'); text = OxmlElement('w:t')
                    text.text = row['text']; run.append(text); element.append(run)
                mark_runs(element, 'del', ids)
                next_line = next((r['new_line'] for r in rows[index+1:] if r.get('new_line')), None)
                anchor = next((elements[b['id']] for b in new.blocks[next_line-1:] if elements[b['id']].getparent() is body), None) if next_line else None
                body.insert(body.index(anchor) if anchor is not None else len(body)-1, element)
    output = BytesIO(); doc.save(output)
    return render_report(output.getvalue(), source_name)
