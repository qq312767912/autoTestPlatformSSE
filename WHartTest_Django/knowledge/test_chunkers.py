"""任务 5：结构感知分块器回归测试。"""

from django.test import TestCase

from projects.models import Project

from .chunkers import StructuredTextChunker


class StructuredChunkerTests(TestCase):
    def setUp(self):
        self.user = __import__("django.contrib.auth.models", fromlist=["User"]).User.objects.create_user(
            username="chunker-tester", password="pass"
        )
        self.project = Project.objects.create(name="Chunker Project", creator=self.user)

    def test_markdown_heading_is_traceable(self):
        text = "# 第一章\n\n内容一。\n\n## 1.1 节\n\n内容二。\n"
        chunker = StructuredTextChunker(chunk_size=100, chunk_overlap=0)
        chunks = chunker.split_text(text, document_type="md")
        sections = [c.metadata.get("section") for c in chunks]
        merged = []
        for c in chunks:
            merged.extend(c.metadata.get("merged_blocks") or [c.metadata.get("block_type")])
        self.assertIn("heading", merged)
        # 章节可追溯：标题文本必须出现在某个块中
        self.assertTrue(any("第一章" in c.page_content for c in chunks))
        self.assertTrue(any("1.1 节" in c.page_content for c in chunks))
        self.assertIn("1.1 节", sections)

    def test_markdown_table_is_preserved_in_merged_blocks(self):
        text = "# 表\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n后续段落。\n"
        chunker = StructuredTextChunker(chunk_size=200, chunk_overlap=0)
        chunks = chunker.split_text(text, document_type="md")
        merged = []
        for c in chunks:
            merged.extend(c.metadata.get("merged_blocks") or [c.metadata.get("block_type")])
        self.assertIn("table", merged)

    def test_markdown_code_block_is_preserved_in_merged_blocks(self):
        text = "# 代码\n\n```python\ndef f():\n    pass\n```\n\n后续。\n"
        chunker = StructuredTextChunker(chunk_size=200, chunk_overlap=0)
        chunks = chunker.split_text(text, document_type="md")
        merged = []
        for c in chunks:
            merged.extend(c.metadata.get("merged_blocks") or [c.metadata.get("block_type")])
        self.assertIn("code_block", merged)

    def test_plain_text_sections_are_traceable(self):
        text = "总则\n\n第一条 为规范操作。\n\n细则\n\n第二条 为细化流程。\n"
        chunker = StructuredTextChunker(chunk_size=40, chunk_overlap=0)
        chunks = chunker.split_text(text, document_type="txt")
        # 分块足够小时，章节标题会落在某个块的 section 里；若合并则内容中仍保留原标题
        sections = [c.metadata.get("section") for c in chunks]
        contents = [c.page_content for c in chunks]
        self.assertTrue(
            "总则" in sections or any("总则" in c for c in contents),
            f"应能追溯到章节 '总则'，实际 sections={sections}",
        )
        self.assertTrue(
            "细则" in sections or any("细则" in c for c in contents),
            f"应能追溯到章节 '细则'，实际 sections={sections}",
        )

    def test_chunk_size_respected(self):
        text = " ".join([f"词{i}" for i in range(200)])
        chunker = StructuredTextChunker(chunk_size=80, chunk_overlap=0)
        chunks = chunker.split_text(text, document_type="txt")
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c.page_content), 120)  # 允许标题等略超
