import unittest
from .effective_content import effective_markdown,effective_docx
from .test_document_comparison import document
from docx import Document
from io import BytesIO

class EffectiveContentTests(unittest.TestCase):
    def test_markdown_deletion_and_literal_code(self):
        text='有效~~旧要求~~新要求 <del>删除</del> `<del>代码</del>`\n```md\n~~代码~~\n```'
        result=effective_markdown(text)
        self.assertNotIn('旧要求',result.split('## 取消与修改需求')[0]); self.assertIn('旧要求',result.split('## 取消与修改需求')[1])
        self.assertNotIn('<del>删除',result)
        self.assertIn('~~代码~~',result)
        self.assertIn('`<del>代码</del>`',result)
    def test_word_strike(self):
        d=Document();p=d.add_paragraph('有效');p.add_run('删除').font.strike=True
        stream=BytesIO();d.save(stream)
        result=Document(BytesIO(effective_docx(stream.getvalue())))
        self.assertEqual(result.paragraphs[0].text,'有效')

    def test_export_preserves_unchanged_section_context(self):
        from .document_comparison import compare_documents,export_selection
        from zipfile import ZipFile
        old=Document();old.add_heading('登录规则',1);old.add_paragraph('共同前提');old.add_paragraph('超时30秒');old.add_paragraph('共同后置规则')
        a=BytesIO();old.save(a)
        old.paragraphs[2].text='超时60秒'
        b=BytesIO();old.save(b)
        result=compare_documents(a.getvalue(),b.getvalue())
        bundle=export_selection(b.getvalue(),result,[h['id'] for h in result['hunks']],'迭代')
        with ZipFile(BytesIO(bundle)) as z:
            doc=Document(BytesIO(z.read('迭代需求.docx')))
        text='\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('共同前提',text);self.assertIn('共同后置规则',text);self.assertIn('超时30秒',text);self.assertTrue(any(r.font.strike for p in doc.paragraphs for r in p.runs if '30' in r.text))

    def test_change_evidence_is_preserved_and_idempotent(self):
        from .effective_content import word_change_requirements
        d=Document();p=d.add_paragraph('超时');p.add_run('30秒').font.strike=True;p.add_run('60秒')
        out=BytesIO();d.save(out)
        notes=word_change_requirements(out.getvalue())
        self.assertIn('30秒',notes);self.assertIn('超时60秒',notes);self.assertIn('修改原规则',notes)
        md=effective_markdown('~~取消短信登录~~')
        self.assertIn('取消原规则',md);self.assertIn('取消短信登录',md)
        self.assertEqual(effective_markdown(md),md)

    def test_original_renderer_layout_and_manual_strike_fix(self):
        from .report_vendor import extract_all_revisions as renderer
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from docx.enum.style import WD_STYLE_TYPE
        d=Document();d.add_heading('业务规则',1)
        p=d.add_paragraph('保留规则');p.add_run('取消规则').font.strike=True
        d.add_paragraph().add_run('双删除规则').font.double_strike=True
        style=d.styles.add_style('InheritedDelete',WD_STYLE_TYPE.CHARACTER);style.font.strike=True
        d.add_paragraph().add_run('继承删除规则').style=style
        with TemporaryDirectory() as folder:
            source=Path(folder)/'原始需求.docx';out=Path(folder)/'报告.docx';d.save(source)
            info=renderer.analyze(str(source));self.assertGreater(info['stats']['del']+info['stats']['mod'],0)
            renderer.build_report_docx(info,str(out));report=Document(out)
            from zipfile import ZipFile
            with ZipFile(out) as package:self.assertEqual(package.read('word/styles.xml'),renderer.STYLES_XML.encode('utf-8'))
        text='\n'.join(p.text for p in report.paragraphs)
        self.assertEqual(report.paragraphs[0].text,'文档修订内容汇总报告')
        self.assertIn('一、修订总体情况',text);self.assertIn('二、修订内容（按原文档模块，完整保留小节内容）',text)
        deleted=''.join(r.text for p in report.paragraphs for r in p.runs if r.font.strike)
        for expected in ['取消规则','双删除规则','继承删除规则']:self.assertIn(expected,deleted)
        stream=BytesIO();report.save(stream)
        from .effective_content import word_change_requirements
        notes=word_change_requirements(stream.getvalue())
        self.assertIn('取消规则',notes);self.assertIn('取消与修改需求',notes)

    def test_diff_uses_inline_redline_and_copies_deleted_images(self):
        from .document_comparison import compare_documents,export_selection
        from zipfile import ZipFile
        from .test_document_comparison import document
        old,new=document('超时30秒'),document('超时60秒')
        r=compare_documents(old,new)
        with ZipFile(BytesIO(export_selection(new,r,['h0'],'迭代',old))) as z:
            out=Document(BytesIO(z.read('迭代需求.docx')))
        paragraph=next(p for p in out.paragraphs if '30' in p.text and '60' in p.text)
        self.assertTrue(any(run.font.strike and '3' in run.text for run in paragraph.runs))
        self.assertTrue(any(run.font.color.rgb and str(run.font.color.rgb)=='1B6B3A' and '6' in run.text for run in paragraph.runs))
        import base64
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        d=Document();d.add_picture(BytesIO(png));buffer=BytesIO();d.save(buffer);old=buffer.getvalue();new=document()
        r=compare_documents(old,new)
        with ZipFile(BytesIO(export_selection(new,r,['h0'],'图片删除',old))) as z:
            out=Document(BytesIO(z.read('迭代需求.docx')))
        self.assertTrue(any(rel.target_part.blob==png for rel in out.part.rels.values() if rel.reltype.endswith('/image')))
