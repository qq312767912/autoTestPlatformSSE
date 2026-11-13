import unittest
from .effective_content import effective_markdown,effective_docx
from .test_document_comparison import document
from docx import Document
from io import BytesIO

class EffectiveContentTests(unittest.TestCase):
    def test_markdown_deletion_and_literal_code(self):
        text='有效~~旧要求~~新要求 <del>删除</del> `<del>代码</del>`\n```md\n~~代码~~\n```'
        result=effective_markdown(text)
        self.assertNotIn('旧要求',result)
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
        self.assertIn('共同前提',text);self.assertIn('共同后置规则',text);self.assertNotIn('超时30秒',text)
