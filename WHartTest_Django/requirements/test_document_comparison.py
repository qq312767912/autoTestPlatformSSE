"""Deterministic engine regression tests; no running Django required."""
import unittest
from io import BytesIO
from zipfile import ZipFile
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from .document_comparison import ParsedDocument, ComparisonError, compare_documents, export_selection


def save(doc):
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def document(*texts):
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    return save(doc)


def extract(data, result, ids):
    with ZipFile(BytesIO(export_selection(data, result, ids, '迭代'))) as package:
        return Document(BytesIO(package.read('迭代需求.docx'))), package.read('变更说明.md').decode()


class DocumentComparisonTests(unittest.TestCase):
    def test_numeric_change_and_separate_deletion_report(self):
        old, new = document('超时30秒'), document('超时60秒')
        result = compare_documents(old, new)
        self.assertEqual((result['added'], result['deleted']), (1, 1))
        doc, report = extract(new, result, ['h0'])
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('超时60秒', text)
        self.assertNotIn('超时30秒', text)
        self.assertIn('超时30秒', report)
        self.assertEqual(Document(BytesIO(old)).paragraphs[0].text, '超时30秒')

    def test_inherited_strike_double_strike_direct_false(self):
        doc = Document()
        style = doc.styles.add_style('Removed', WD_STYLE_TYPE.PARAGRAPH)
        style.font.strike = True
        p = doc.add_paragraph(style='Removed')
        p.add_run('继承删除')
        p.add_run('保留').font.strike = False
        p = doc.add_paragraph()
        p.add_run('双线删除').font.double_strike = True
        p.add_run('有效')
        self.assertEqual([b['text'] for b in ParsedDocument(save(doc)).blocks], ['保留', '有效'])

    def test_revisions(self):
        doc = Document()
        p = doc.add_paragraph('规则：')
        for tag, text in [('del', '旧值'), ('ins', '新值')]:
            node = OxmlElement('w:' + tag)
            run = OxmlElement('w:r')
            t = OxmlElement('w:delText' if tag == 'del' else 'w:t')
            t.text = text
            run.append(t)
            node.append(run)
            p._p.append(node)
        self.assertEqual(ParsedDocument(save(doc)).blocks[0]['text'], '规则：新值')

    def test_selection_excludes_unselected_content(self):
        old = document('旧A', *[f'共同{i}' for i in range(8)], '旧B')
        new = document('新A', *[f'共同{i}' for i in range(8)], '新B')
        result = compare_documents(old, new)
        self.assertEqual(len(result['hunks']), 2)
        doc, report = extract(new, result, ['h0'])
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('新A', text)
        self.assertNotIn('新B', text)
        self.assertNotIn('旧B', report)

    def test_deleted_table_row_and_table_export(self):
        doc = Document()
        table = doc.add_table(rows=2, cols=1)
        table.cell(0, 0).text = '保留行'
        table.cell(1, 0).text = '删除行'
        props = OxmlElement('w:trPr')
        props.append(OxmlElement('w:del'))
        table.rows[1]._tr.insert(0, props)
        data = save(doc)
        self.assertEqual(ParsedDocument(data).blocks[0]['text'], '保留行')
        result = compare_documents(document('旧内容'), data)
        exported, _ = extract(data, result, ['h0'])
        self.assertEqual(len(exported.tables[0].rows), 1)
        self.assertEqual(exported.tables[0].cell(0, 0).text, '保留行')

    def test_image_export(self):
        import base64
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        doc = Document()
        doc.add_picture(BytesIO(png))
        data = save(doc)
        result = compare_documents(document('原文'), data)
        exported, _ = extract(data, result, ['h0'])
        self.assertEqual(len(exported.inline_shapes), 1)
        self.assertEqual(next(r.target_part.blob for r in exported.part.rels.values() if r.reltype.endswith('/image')), png)

    def test_validation_and_deletion_only(self):
        old, new = document('删掉'), document()
        result = compare_documents(old, new)
        doc, report = extract(new, result, ['h0'])
        self.assertIn('删掉', report)
        self.assertNotIn('删掉', '\n'.join(p.text for p in doc.paragraphs))
        for ids in [[], ['missing'], 'h0', [1]]:
            with self.assertRaises(ComparisonError):
                extract(new, result, ids)
        with self.assertRaises(ComparisonError):
            extract(document('变化'), result, ['h0'])
        with self.assertRaises(ComparisonError):
            ParsedDocument(b'not docx')
        self.assertEqual(compare_documents(old, old)['hunks'], [])

    def test_unsupported_reference_is_not_silently_lost(self):
        doc = Document()
        doc.add_paragraph('带脚注').add_run()._r.append(OxmlElement('w:footnoteReference'))
        data = save(doc)
        result = compare_documents(document('旧'), data)
        with self.assertRaisesRegex(ComparisonError, '脚注'):
            extract(data, result, ['h0'])

    def test_content_control_body_is_compared(self):
        doc = Document()
        paragraph = doc.add_paragraph('内容控件中的需求')
        wrapper = OxmlElement('w:sdt')
        content = OxmlElement('w:sdtContent')
        wrapper.append(content)
        doc.element.body.insert(0, wrapper)
        content.append(paragraph._p)
        result = compare_documents(document('旧'), save(doc))
        self.assertEqual(result['hunks'][0]['rows'][-1]['text'], '内容控件中的需求')

    def test_merge_change_and_hyperlink_target_are_detected(self):
        doc = Document()
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = '同样文字'
        table.cell(0, 1).text = '另一个字段'
        old = save(doc)
        table.cell(0, 0).merge(table.cell(0, 1))
        self.assertGreater(compare_documents(old, save(doc))['added'], 0)
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from docx.oxml.ns import qn
        def linked(url):
            document = Document()
            p = document.add_paragraph()
            link = OxmlElement('w:hyperlink')
            link.set(qn('r:id'), document.part.relate_to(url, RT.HYPERLINK, is_external=True))
            run = OxmlElement('w:r')
            text = OxmlElement('w:t')
            text.text = '接口文档'
            run.append(text)
            link.append(run)
            p._p.append(link)
            return save(document)
        result = compare_documents(linked('https://example.org/old'), linked('https://example.org/new'))
        self.assertEqual(result['added'], 1)
        self.assertIn('https://example.org/new', result['hunks'][0]['rows'][-1]['text'])

    def test_word_fields_export_cached_results_and_split_highlights(self):
        from docx.oxml.ns import qn
        doc = Document()
        p = doc.add_paragraph('见：')
        for field_type in ['begin', 'separate']:
            run = p.add_run()._r
            marker = OxmlElement('w:fldChar')
            marker.set(qn('w:fldCharType'), field_type)
            run.append(marker)
            if field_type == 'begin':
                instruction = OxmlElement('w:instrText')
                instruction.text = ' PAGEREF _Toc123 '
                p.add_run()._r.append(instruction)
        p.add_run('第60页')
        end = OxmlElement('w:fldChar')
        end.set(qn('w:fldCharType'), 'end')
        p.add_run()._r.append(end)
        simple = OxmlElement('w:fldSimple')
        simple.set(qn('w:instr'), 'REF section')
        simple.append(OxmlElement('w:r'))
        simple[0].append(OxmlElement('w:t'))
        simple[0][0].text = '业务章节'
        p._p.append(simple)
        data = save(doc)
        result = compare_documents(document('见：第30页'), data)
        pair = result['hunks'][0]['pairs'][0]
        self.assertTrue(any(s['changed'] and '6' in s['text'] for s in pair['new']['segments']))
        exported, _ = extract(data, result, ['h0'])
        self.assertIn('第60页业务章节', '\n'.join(p.text for p in exported.paragraphs))
        self.assertEqual(list(exported.element.iter(qn('w:fldChar'))), [])
        self.assertEqual(list(exported.element.iter(qn('w:instrText'))), [])

    def test_bookmarks_and_choice_fallback_do_not_duplicate_or_warn(self):
        from docx.oxml.ns import qn
        from lxml import etree
        doc = Document()
        p = doc.add_paragraph()
        mc = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
        alternate = etree.Element(mc+'AlternateContent')
        for tag in ['Choice', 'Fallback']:
            branch = etree.SubElement(alternate, mc+tag)
            run = OxmlElement('w:r')
            text = OxmlElement('w:t')
            text.text = '唯一正文'
            run.append(text)
            branch.append(run)
        p._p.append(alternate)
        doc.element.body.insert(0, OxmlElement('w:bookmarkEnd'))
        parsed = ParsedDocument(save(doc))
        self.assertEqual(parsed.blocks[0]['text'], '唯一正文')
        self.assertEqual(parsed.issues, [])
