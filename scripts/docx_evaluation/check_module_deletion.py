"""Read-only integration smoke test. Run inside the backend runtime:
python manage.py shell < /workspace/scripts/docx_evaluation/check_module_deletion.py
No model calls or database writes are performed.
"""
from requirements.services import DocumentProcessor,ModuleSplitter
from docx import Document
from io import BytesIO

d=Document();d.add_heading('公司公告',1)
p=d.add_paragraph('默认展示最新公告，');p.add_run('通过日期区间筛选').font.strike=True
p=d.add_paragraph('检索：证券代码或简称');p.add_run('关键字、板块分类查询').font.strike=True
cell=d.add_table(rows=1,cols=1).cell(0,0);cell.paragraphs[0].add_run('表格旧规则').font.strike=True
d.add_heading('债券公告',1);d.add_paragraph('独立规则')
f=BytesIO();d.save(f)
processor=DocumentProcessor();content=processor.preprocess_content(processor._extract_from_word(f))
splitter=object.__new__(ModuleSplitter)
modules=splitter._split_by_heading_level(content,'h1',False)
assert len(modules)==2,modules
assert '删除原文：通过日期区间筛选' in modules[0]['content'],modules[0]
assert '删除原文：关键字、板块分类查询' in modules[0]['content']
assert '表格旧规则' in modules[0]['content']
assert '删除原文' not in modules[1]['content']
assert '取消与修改需求（' not in content
print('PASS: actual Word parser -> preprocessing -> module splitter; paragraph/table deletion stays in company module')
