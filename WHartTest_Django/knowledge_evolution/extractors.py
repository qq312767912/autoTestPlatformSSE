"""任务 6：从来源快照中规则化提炼知识候选（不依赖 LLM）。

当前只做结构提取：标题 → 概念/规则；表格/列表 → 事实/规则；代码块 → 流程。
去重、分级、冲突检测在这里完成，LLM 提炼留作后续增强。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterator

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import KnowledgeAsset, KnowledgeCandidate, KnowledgeConflict, SourceSnapshot


@dataclass
class ExtractedCandidate:
    """中间结构，入库前转换为 KnowledgeCandidate。"""

    kind: str  # concept/rule/fact/procedure/relation/summary
    origin: str  # extraction / manual / distillation
    payload: dict
    level: str  # L1/L2/L3
    confidence: float
    evidence: list[dict] = field(default_factory=list)

    def normalized_content(self) -> str:
        parts = [self.kind]
        if "title" in self.payload:
            parts.append(self.payload["title"])
        if "content" in self.payload:
            parts.append(self.payload["content"])
        return "\n".join(parts)


def _classify_level(text: str) -> str:
    """按措辞强度分级。"""
    strong = {"必须", "不得", "禁止", "严禁", "应", "须", "应当", "强制"}
    weak = {"建议", "应当参考", "宜", "可", "可以", "推荐"}
    if any(s in text for s in strong):
        return "L1"
    if any(s in text for s in weak):
        return "L2"
    return "L3"


def _normalize_key(title: str) -> str:
    """生成稳定业务 key：去标点后取前 60 字符。"""
    title = re.sub(r"[\s\n]+", " ", title).strip()
    title = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9_\- ]", "", title)
    return title[:60].strip().replace(" ", "_")


class RuleBasedExtractor:
    """从来源快照文本中提炼候选。"""

    def __init__(self, chunk_size: int = 1000):
        self.chunk_size = chunk_size

    def extract(self, snapshot: SourceSnapshot) -> Iterator[ExtractedCandidate]:
        text = snapshot.parsed_text or snapshot.raw_content or ""
        if not text.strip():
            return

        doc_type = snapshot.source_type
        # markdown / 文本统一按 markdown 解析更稳；HTML 也先用文本路径
        yield from self._extract_from_markdown(snapshot, text, doc_type)

    # ------------------------------------------------------------------ markdown 提取

    def _extract_from_markdown(self, snapshot: SourceSnapshot, text: str, doc_type: str) -> Iterator[ExtractedCandidate]:
        lines = text.split("\n")
        in_code = False
        code_lang = ""
        code_buffer: list[str] = []
        code_start = 0
        current_section = ""
        para_buffer: list[str] = []
        para_start = 0

        def flush_code(start: int, end: int):
            nonlocal code_buffer, code_lang
            if not code_buffer:
                return
            content = "\n".join(code_buffer).strip()
            if not content:
                return
            yield ExtractedCandidate(
                kind="procedure",
                origin="extraction",
                payload={
                    "title": f"{current_section or '代码示例'}",
                    "language": code_lang,
                    "content": content,
                },
                level="L3",
                confidence=0.7,
                evidence=[{"snapshot_id": str(snapshot.id), "location": {"start": start, "end": end}}],
            )
            code_buffer = []
            code_lang = ""

        def flush_paragraph(start: int, end: int):
            nonlocal para_buffer, current_section
            if not para_buffer:
                return
            content = "\n".join(para_buffer).strip()
            if not content:
                return
            # 标题行
            m = re.match(r"^(#{1,6})\s+(.+)$", content)
            if m:
                title = m.group(2).strip()
                current_section = title
                kind = "rule" if _classify_level(title) == "L1" else "concept"
                yield ExtractedCandidate(
                    kind=kind,
                    origin="extraction",
                    payload={"title": title, "heading_level": len(m.group(1))},
                    level=_classify_level(title),
                    confidence=0.8,
                    evidence=[{"snapshot_id": str(snapshot.id), "location": {"start": start, "end": end}}],
                )
                para_buffer = []
                return
            # 普通段落：尝试当作规则/事实
            kind = "rule" if _classify_level(content) == "L1" else "fact"
            yield ExtractedCandidate(
                kind=kind,
                origin="extraction",
                payload={
                    "title": current_section or "段落",
                    "content": content,
                },
                level=_classify_level(content),
                confidence=0.6,
                evidence=[{"snapshot_id": str(snapshot.id), "location": {"start": start, "end": end}}],
            )

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            if line.startswith("```"):
                if not in_code:
                    yield from flush_paragraph(para_start, i)
                    in_code = True
                    code_lang = line[3:].strip()
                    code_start = i
                    para_buffer = []
                else:
                    yield from flush_code(code_start, i + 1)
                    in_code = False
                    para_start = i + 1
            elif in_code:
                code_buffer.append(lines[i])
            elif line.strip().startswith("|"):
                # 表格：收集完整表格
                yield from flush_paragraph(para_start, i)
                table_lines = []
                table_start = i
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                content = "\n".join(table_lines)
                yield ExtractedCandidate(
                    kind="fact",
                    origin="extraction",
                    payload={"title": current_section or "表格", "content": content},
                    level="L3",
                    confidence=0.7,
                    evidence=[{"snapshot_id": str(snapshot.id), "location": {"start": table_start, "end": i}}],
                )
                para_start = i
                continue
            elif re.match(r"^#{1,6}\s+", line):
                yield from flush_paragraph(para_start, i)
                para_buffer = [line]
                para_start = i
                yield from flush_paragraph(para_start, i + 1)
                para_start = i + 1
            elif line.strip() == "":
                yield from flush_paragraph(para_start, i)
                para_start = i + 1
            else:
                if not para_buffer:
                    para_start = i
                para_buffer.append(lines[i])
            i += 1

        if in_code:
            yield from flush_code(code_start, len(lines))
        else:
            yield from flush_paragraph(para_start, len(lines))


class CandidateDeduplicator:
    """候选去重与冲突检测。"""

    def __init__(self, project, actor=None):
        self.project = project
        self.actor = actor

    def dedup_and_conflicts(
        self, candidates: list[ExtractedCandidate],
    ) -> tuple[list[ExtractedCandidate], list[dict]]:
        """按 project+kind+content_hash 去重；相同 key 不同内容报冲突。"""
        seen_keys: dict[str, ExtractedCandidate] = {}
        conflicts = []
        unique = []
        for c in candidates:
            key = KnowledgeCandidate.build_dedup_key(
                self.project.pk, c.kind, c.normalized_content()
            )
            if key in seen_keys:
                existing = seen_keys[key]
                # 内容相同 → 去重；内容不同 → 冲突
                if existing.normalized_content() != c.normalized_content():
                    conflicts.append({
                        "left": existing,
                        "right": c,
                        "type": "duplicate",
                    })
                continue
            seen_keys[key] = c
            unique.append(c)
        return unique, conflicts

    def save_candidates(self, snapshot: SourceSnapshot, candidates: list[ExtractedCandidate]) -> list[KnowledgeCandidate]:
        """把候选入库，幂等。"""
        saved = []
        with transaction.atomic():
            for c in candidates:
                payload = dict(c.payload)
                payload["extractor"] = "rule_based_v1"
                key = KnowledgeCandidate.build_dedup_key(
                    snapshot.project_id, c.kind, c.normalized_content()
                )
                try:
                    obj, created = KnowledgeCandidate.objects.get_or_create(
                        dedup_key=key,
                        defaults={
                            "project": self.project,
                            "kind": c.kind,
                            "origin": c.origin,
                            "payload": payload,
                            "level": c.level,
                            "confidence": c.confidence,
                            "source_snapshot": snapshot,
                            "evidence": c.evidence,
                            "state": "pending",
                            "dedup_key": key,
                            "created_by": self.actor,
                            "extracted_by": "rule_based_v1",
                        },
                    )
                    saved.append(obj)
                except ValidationError as e:
                    # 数据不合法时跳过，不阻断批次
                    print(f"跳过非法候选: {e}")
        return saved

    def save_conflicts(self, conflicts: list[dict]) -> list[KnowledgeConflict]:
        """把冲突记录入库。当前只做近重复/内容冲突。"""
        saved = []
        with transaction.atomic():
            for item in conflicts:
                left, right = item["left"], item["right"]
                # 未保存 ExtractedCandidate 没有对应资产，这里只记录冲突元信息
                saved.append(KnowledgeConflict.objects.create(
                    project=self.project,
                    left_asset=None,
                    right_asset=None,
                    conflict_type=item.get("type", "duplicate"),
                    state="open",
                    detail=json.dumps({
                        "left_kind": left.kind,
                        "right_kind": right.kind,
                        "left_preview": left.normalized_content()[:200],
                        "right_preview": right.normalized_content()[:200],
                    }, ensure_ascii=False),
                ))
        return saved
