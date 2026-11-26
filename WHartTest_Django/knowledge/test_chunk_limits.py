"""无需数据库的最终分块长度与无损回归测试。"""
import unittest

try:
    from .chunkers import StructuredChunk, StructuredTextChunker
except ImportError:
    from chunkers import StructuredChunk, StructuredTextChunker


class ChunkLimitTests(unittest.TestCase):
    def test_long_blocks_preserve_content_and_metadata(self):
        text = ''.join(chr(0x4e00 + i % 1000) for i in range(10165))
        for block_type in ('paragraph', 'table', 'code_block'):
            for size, overlap in ((1000, 200), (80, 0), (10, 20), (1, 200)):
                with self.subTest(block_type=block_type, size=size, overlap=overlap):
                    chunker = StructuredTextChunker(size, overlap)
                    chunks = list(chunker._enforce_chunk_limit([
                        StructuredChunk(text, {'block_type': block_type, 'section': '章节'})]))
                    restored = ''
                    previous_end = 0
                    for chunk in chunks:
                        self.assertLessEqual(len(chunk.page_content), size)
                        start = chunk.metadata['source_chunk_char_start']
                        end = chunk.metadata['source_chunk_char_end']
                        self.assertEqual(chunk.page_content, text[start:end])
                        self.assertEqual(chunk.metadata['section'], '章节')
                        restored += chunk.page_content[max(0, previous_end - start):]
                        previous_end = end
                    self.assertEqual(restored, text)

    def test_markdown_public_output_is_bounded(self):
        for text in ('长段落' * 4000, '```python\n' + 'print(1)\n' * 1000 + '```',
                     '| a | b |\n|---|---|\n' + '| 数据 | 值 |\n' * 1000):
            chunks = StructuredTextChunker(1000, 200).split_text(text, document_type='md')
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(0 < len(c.page_content) <= 1000 for c in chunks))

    def test_configured_size_changes_final_output(self):
        text = '连续长文本' * 2500
        maxima = []
        for size in (500, 1000, 2000):
            chunks = StructuredTextChunker(size, 200).split_text(text, document_type='md')
            maxima.append(max(len(c.page_content) for c in chunks))
            self.assertTrue(all(len(c.page_content) <= size for c in chunks))
        self.assertEqual(maxima, [500, 1000, 2000])

    def test_small_chunk_unchanged(self):
        chunk = StructuredChunk('短文本', {'section': '原章节'})
        self.assertEqual(list(StructuredTextChunker()._enforce_chunk_limit([chunk])), [chunk])


if __name__ == '__main__':
    unittest.main()
