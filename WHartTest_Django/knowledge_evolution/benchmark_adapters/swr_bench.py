"""SWR-Bench 适配器。

原始字段映射：
    instance_id                       -> upstream_instance_id
    repo                              -> repo
    base_commit                       -> base_sha
    pr_commits[-1].sha                -> head_sha
    changes[].change_introducing      -> gold_issues（文件 + 行号，尽力解析）
    change_introduced == False        -> **唯一**的上游显式干净标签（is_clean=True）

关键约束（需求 R1）：只有 `change_introduced=false` 的 500 条是负例，
不得把 1000 条全部当作负例。

行号说明：`change_introducing.code_snippet` 有三种形态——标准 unified diff、
`# Relevant context from <path>` 提示、纯片段引用。因此定位按四级回退，
仍取不到行号时保留文件级金标（`located=False`），不丢样本也不虚构行号。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from ..benchmark_catalog import CLEAN_SOURCE_UPSTREAM
from .base import (
    BenchmarkAdapter,
    build_case,
    dataset_default_language,
    language_from_path,
)

HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
CONTEXT_HINT = re.compile(r"#\s*Relevant context from\s+(\S+)")
DIFF_HEADER = re.compile(r"^(?:diff --git a/(\S+) b/\S+|--- a/(\S+)|\+\+\+ b/(\S+))")
CODE_PATH = re.compile(
    r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.(?:py|pyx|pxd|java|cpp|cc|cxx|hpp|h|rst|md|txt|cfg|toml|yaml|yml|ini|json))"
)


def _looks_like_path(text: str) -> bool:
    candidate = text.strip()
    return bool(candidate) and " " not in candidate and "/" in candidate and not candidate.startswith(("@@", "+", "-"))


def parse_snippet(snippet: str) -> tuple[str, int | None, int | None, str]:
    """返回 (文件路径, 新侧起始行, 新侧行数, 定位级别)；级别用于报告区分精度。"""
    text = str(snippet or "")
    lines = text.splitlines()
    path, level = "", "none"
    if lines and _looks_like_path(lines[0]):
        path, level = lines[0].strip(), "diff_header"
    if not path:
        hint = CONTEXT_HINT.search(text)
        if hint:
            path, level = hint.group(1).strip(), "context_hint"
    if not path:
        for line in lines[:3]:
            header = DIFF_HEADER.match(line.strip())
            if header:
                path = next(group for group in header.groups() if group)
                level = "diff_header"
                break
    if not path:
        token = CODE_PATH.search(text)
        if token:
            path, level = token.group(1), "inline_token"
    start_line, line_count = None, None
    for line in lines:
        match = HUNK_PATTERN.match(line.strip())
        if match:
            start_line = int(match.group(1))
            line_count = int(match.group(2)) if match.group(2) else 1
            break
    return path, start_line, line_count, level


def _snippet_body(snippet: str, path: str) -> str:
    body = str(snippet or "")
    if path and body.startswith(path):
        body = body[len(path):]
    body = HUNK_PATTERN.sub("", body)
    kept = [line for line in body.splitlines() if not line.startswith(("@@", "---", "+++"))]
    return " ".join(" ".join(kept).split())[:600]


class SwrBenchAdapter(BenchmarkAdapter):
    benchmark_id = "swr_bench"
    version = "1.0.0"
    requires = ()
    FILE_NAME = "swr_datasets_d5c5.jsonl"

    def iter_records(self, raw_dir: str) -> Iterable[dict[str, Any]]:
        path = os.path.join(raw_dir, self.FILE_NAME)
        if not os.path.exists(path):
            raise FileNotFoundError(f"缺少原始文件 {self.FILE_NAME}，请先完成数据准备")
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    yield {"instance_id": f"line-{line_number}", "_parse_error": str(exc)}

    def adapt_record(self, record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if record.get("_parse_error"):
            return None, "invalid_json"
        instance = record.get("instance_id")
        commits = [item for item in (record.get("pr_commits") or []) if isinstance(item, dict)]
        head = commits[-1].get("sha") if commits else None
        base = record.get("base_commit")
        changes = [item for item in (record.get("changes") or []) if isinstance(item, dict)]
        introduced = record.get("change_introduced")
        issues, languages, levels = [], set(), {}
        for change in changes:
            introducing = change.get("change_introducing") or {}
            snippet = introducing.get("code_snippet") or ""
            path, start_line, count, level = parse_snippet(snippet)
            levels[level] = levels.get(level, 0) + 1
            language = language_from_path(path)
            if language:
                languages.add(language)
            discussion = (change.get("change_discussion") or {}).get("discussion_summary")
            description = discussion or _snippet_body(snippet, path) or change.get("change_type")
            issues.append({
                "file": path,
                "line": start_line,
                "start_line": start_line,
                "end_line": (start_line + count - 1) if start_line and count else start_line,
                "description": description,
                "category": change.get("change_type") or "",
                "locate_level": level,
            })
        if introduced is False:
            is_clean, clean_source, issues = True, CLEAN_SOURCE_UPSTREAM, []
        else:
            is_clean, clean_source = None, "unknown"
        language = next(iter(sorted(languages))) if languages else dataset_default_language(self.benchmark_id)
        raw_ref = {
            "change_introduced": introduced,
            "change_count": len(changes),
            "pr_commit_count": len(commits),
            "language_source": "changed_files" if languages else "dataset_manifest",
            "gold_locate_levels": levels,
            "gold_without_file": sum(1 for issue in issues if not issue.get("file")),
        }
        source_url = ""
        if record.get("repo") and instance:
            pull_number = str(instance).rsplit("-", 1)[-1]
            source_url = f"https://github.com/{record.get('repo')}/pull/{pull_number}"
        return build_case(
            upstream_instance_id=instance,
            repo=record.get("repo"),
            base_sha=base,
            head_sha=head,
            language=language,
            gold_issues=issues,
            is_clean=is_clean,
            is_clean_source=clean_source,
            source_url=source_url,
            raw_ref=raw_ref,
        )
