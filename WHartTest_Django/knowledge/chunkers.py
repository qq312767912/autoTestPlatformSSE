"""结构感知文档分块器（tasks.md 任务 5）。

把按 token 硬切升级为按标题/段落/表格/代码块切，保留页码、章节与原文定位。

输出保持与 LangChain Document 兼容：.page_content + .metadata。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class StructuredChunk:
    """与 LangChain Document 字段兼容的分块结果。"""

    page_content: str
    metadata: dict = field(default_factory=dict)


class StructuredTextChunker:
    """按文档结构分块。

    策略：
    - 先按「结构边界」拆成原子块（标题、段落、表格、代码块、列表）。
    - 再把相邻原子块合并到 max_chunk_size 附近，优先保持章节内连续。
    - 标题永远和它后面的第一段合并，避免标题孤立。
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = max(1, chunk_size)
        self.chunk_overlap = max(0, chunk_overlap)

    def split_text(self, text: str, *, document_type: str = "txt") -> list[StructuredChunk]:
        atoms = list(self._atomic_blocks(text, document_type=document_type))
        if not atoms:
            return []
        return list(self._merge_atoms(atoms))

    # ------------------------------------------------------------------ 原子块拆分

    def _atomic_blocks(self, text: str, *, document_type: str) -> Iterator[StructuredChunk]:
        if document_type in ("md", "markdown"):
            yield from self._parse_markdown(text)
        elif document_type in ("html", "htm", "url"):
            yield from self._parse_html(text)
        elif document_type in ("docx", "doc"):
            # 当前 docx 提取成文本后按通用段落解析
            yield from self._parse_plain(text)
        else:
            yield from self._parse_plain(text)

    def _parse_markdown(self, text: str) -> Iterator[StructuredChunk]:
        """解析 markdown：代码块、表格、标题、段落。"""
        lines = text.split("\n")
        buffer: list[str] = []
        current_section = ""
        in_code = False
        code_lang = ""
        code_lines: list[str] = []
        block_start = 0

        def flush_paragraph(start: int, end: int):
            nonlocal buffer, current_section
            if not buffer:
                return
            content = "\n".join(buffer).strip()
            if content:
                # 检测是不是标题行单独占一行
                heading_match = re.match(r"^(#{1,6})\s+(.+)$", content)
                if heading_match:
                    level = len(heading_match.group(1))
                    title = heading_match.group(2).strip()
                    current_section = title
                    yield StructuredChunk(
                        page_content=f"{'#' * level} {title}",
                        metadata={
                            "block_type": "heading",
                            "section": title,
                            "heading_level": level,
                            "start_index": start,
                            "end_index": end,
                        },
                    )
                else:
                    yield StructuredChunk(
                        page_content=content,
                        metadata={
                            "block_type": "paragraph",
                            "section": current_section,
                            "start_index": start,
                            "end_index": end,
                        },
                    )
            buffer = []

        def flush_code(start: int, end: int):
            nonlocal code_lines, code_lang
            if not code_lines:
                return
            content = "\n".join(code_lines).strip()
            if content:
                yield StructuredChunk(
                    page_content=f"```{code_lang}\n{content}\n```",
                    metadata={
                        "block_type": "code_block",
                        "section": current_section,
                        "language": code_lang,
                        "start_index": start,
                        "end_index": end,
                    },
                )
            code_lines = []
            code_lang = ""

        i = 0
        while i < len(lines):
            raw_line = lines[i]
            line = raw_line.rstrip()
            if line.startswith("```"):
                if not in_code:
                    # 先清空段落
                    yield from flush_paragraph(block_start, i)
                    in_code = True
                    code_lang = line[3:].strip()
                    block_start = i
                else:
                    yield from flush_code(block_start, i + 1)
                    in_code = False
                    block_start = i + 1
            elif in_code:
                code_lines.append(raw_line)
            elif line.startswith("|"):
                # 表格行：继续读直到非表格行
                yield from flush_paragraph(block_start, i)
                table_lines = []
                table_start = i
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                yield StructuredChunk(
                    page_content="\n".join(table_lines),
                    metadata={
                        "block_type": "table",
                        "section": current_section,
                        "start_index": table_start,
                        "end_index": i,
                    },
                )
                buffer = []
                block_start = i
                continue
            elif re.match(r"^#{1,6}\s+", line):
                yield from flush_paragraph(block_start, i)
                buffer = [line]
                block_start = i
                yield from flush_paragraph(block_start, i + 1)
                block_start = i + 1
            elif line.strip() == "":
                yield from flush_paragraph(block_start, i)
                block_start = i + 1
            else:
                if not buffer:
                    block_start = i
                buffer.append(raw_line)
            i += 1

        if in_code:
            yield from flush_code(block_start, len(lines))
        else:
            yield from flush_paragraph(block_start, len(lines))

    def _parse_plain(self, text: str) -> Iterator[StructuredChunk]:
        """通用文本：按空行分段，标题启发式；超长段落按句子二次切分。"""
        lines = text.split("\n")
        buffer: list[str] = []
        current_section = ""
        block_start = 0
        page = 1
        page_break = re.compile(r"\f|^\s*[-=]{3,}\s*$|^\s*第\s*\d+\s*页\s*$", re.M)
        sentence_end = re.compile(r"([。！？.!?]\s*)")

        def subsplit_paragraph(content: str, start: int, end: int):
            """把超长段落按句子边界切成子块，保持连续。"""
            if len(content) <= self.chunk_size:
                yield StructuredChunk(
                    page_content=content,
                    metadata={
                        "block_type": "paragraph",
                        "section": current_section,
                        "start_index": start,
                        "end_index": end,
                        "page": page,
                    },
                )
                return
            parts = sentence_end.split(content)
            # 没有句子边界时，按 chunk_size 硬切作为退化
            if len(parts) <= 1:
                for idx in range(0, len(content), self.chunk_size):
                    piece = content[idx:idx + self.chunk_size]
                    if piece.strip():
                        yield StructuredChunk(
                            page_content=piece,
                            metadata={
                                "block_type": "paragraph",
                                "section": current_section,
                                "start_index": start + idx,
                                "end_index": start + idx + len(piece),
                                "page": page,
                            },
                        )
                return
            # split 后偶数索引是文本，奇数索引是分隔符
            current = ""
            sub_start = start
            for idx in range(0, len(parts), 2):
                text_part = parts[idx]
                sep = parts[idx + 1] if idx + 1 < len(parts) else ""
                candidate = current + text_part + sep
                if current and len(candidate) > self.chunk_size:
                    yield StructuredChunk(
                        page_content=current,
                        metadata={
                            "block_type": "paragraph",
                            "section": current_section,
                            "start_index": sub_start,
                            "end_index": sub_start + len(current),
                            "page": page,
                        },
                    )
                    sub_start += len(current)
                    current = text_part + sep
                else:
                    current = candidate
            if current.strip():
                yield StructuredChunk(
                    page_content=current.strip(),
                    metadata={
                        "block_type": "paragraph",
                        "section": current_section,
                        "start_index": sub_start,
                        "end_index": sub_start + len(current),
                        "page": page,
                    },
                )

        def flush(start: int, end: int):
            nonlocal buffer, current_section
            if not buffer:
                return
            content = "\n".join(buffer).strip()
            if not content:
                buffer = []
                return
            # 启发式标题：短行、末尾无标点、前面空行
            heading_match = re.match(r"^(\d+[\.\s]+)?([A-Za-z\u4e00-\u9fa5][^。！？.!?]*?)$", content)
            is_heading = (
                heading_match
                and len(content) < 80
                and not re.search(r"[。！？.!?]$", content)
                and content.count("\n") == 0
            )
            if is_heading:
                current_section = content
                yield StructuredChunk(
                    page_content=content,
                    metadata={
                        "block_type": "heading",
                        "section": current_section,
                        "start_index": start,
                        "end_index": end,
                        "page": page,
                    },
                )
            else:
                yield from subsplit_paragraph(content, start, end)
            buffer = []

        for i, line in enumerate(lines):
            if page_break.match(line):
                yield from flush(block_start, i)
                page += 1
                block_start = i + 1
                continue
            if line.strip() == "":
                yield from flush(block_start, i)
                block_start = i + 1
                continue
            if not buffer:
                block_start = i
            buffer.append(line)

        yield from flush(block_start, len(lines))

    def _parse_html(self, text: str) -> Iterator[StructuredChunk]:
        """简单 HTML 解析：把 h1-h6/p/pre/table 转成块。"""
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            yield from self._parse_plain(text)
            return

        soup = BeautifulSoup(text, "html.parser")
        current_section = ""
        index = 0
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "table"]):
            content = tag.get_text("\n", strip=True)
            if not content:
                continue
            if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                current_section = content
                block_type = "heading"
            elif tag.name == "pre":
                block_type = "code_block"
            elif tag.name == "table":
                block_type = "table"
            else:
                block_type = "paragraph"
            yield StructuredChunk(
                page_content=content,
                metadata={
                    "block_type": block_type,
                    "section": current_section,
                    "tag": tag.name,
                    "start_index": index,
                    "end_index": index + 1,
                },
            )
            index += 1

    # ------------------------------------------------------------------ 合并原子块

    def _merge_atoms(self, atoms: list[StructuredChunk]) -> Iterator[StructuredChunk]:
        """把原子块合并成接近 chunk_size 的块，优先保留章节内连续。"""
        buffer: list[StructuredChunk] = []
        current_len = 0

        def flush():
            nonlocal buffer, current_len
            if not buffer:
                return
            contents = [a.page_content for a in buffer]
            # 标题永远置顶，确保上下文里有章节名
            section = ""
            for a in reversed(buffer):
                if a.metadata.get("block_type") == "heading":
                    section = a.metadata.get("section") or ""
                    break
            meta = dict(buffer[-1].metadata)
            meta.update({
                "section": section or meta.get("section", ""),
                "start_index": buffer[0].metadata.get("start_index", 0),
                "end_index": buffer[-1].metadata.get("end_index", 0),
                "block_type": "mixed",
                "merged_blocks": [a.metadata.get("block_type", "paragraph") for a in buffer],
            })
            yield StructuredChunk(page_content="\n\n".join(contents), metadata=meta)
            # 重叠：保留最后一块的一部分
            overlap = []
            overlap_len = 0
            for a in reversed(buffer):
                text = a.page_content
                if overlap_len + len(text) <= self.chunk_overlap:
                    overlap.insert(0, a)
                    overlap_len += len(text) + 2
                else:
                    break
            buffer = overlap
            current_len = overlap_len

        for atom in atoms:
            atom_len = len(atom.page_content)
            if atom_len >= self.chunk_size:
                # 大块单独成块（表格/代码块通常较长）
                if buffer:
                    yield from flush()
                yield atom
                continue
            if buffer and current_len + atom_len > self.chunk_size:
                yield from flush()
            buffer.append(atom)
            current_len += atom_len + 2  # \n\n

        yield from flush()
