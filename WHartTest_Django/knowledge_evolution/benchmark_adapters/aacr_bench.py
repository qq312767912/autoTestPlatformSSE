"""AACR-Bench 适配器（唯一同时覆盖 Python / Java / C++ 的公开集）。

原始字段映射（与官方 `evaluation/converters/aacr_bench.py` 一致）：
    githubPrUrl           -> repo（owner/name）
    source_commit         -> base_sha
    target_commit         -> head_sha
    project_main_language -> language（仅保留 Python / Java / C++）
    comments[]            -> gold_issues（path / note / from_line / to_line）
    instance_id           -> 官方无现成 ID，按 repo + target 前 7 位推导

只读 `positive_samples.json`。`negative_samples.json` 未被官方 converter/evaluator/metrics
引用，标签语义未定义，本平台不使用（详见 verification-t01.md 第五节）。
无干净样本标签：`is_clean` 恒为 None，不参与误报分母。
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .base import AdapterError, BenchmarkAdapter, build_case


class AacrBenchAdapter(BenchmarkAdapter):
    benchmark_id = "aacr_bench"
    version = "1.0.0"
    requires = ()
    FILE_NAME = "positive_samples.json"
    EXCLUDED_FILES = ("negative_samples.json",)

    def iter_records(self, raw_dir: str) -> Iterable[dict[str, Any]]:
        path = os.path.join(raw_dir, self.FILE_NAME)
        if not os.path.exists(path):
            raise AdapterError(f"缺少原始文件 {self.FILE_NAME}，请先完成数据准备")
        with open(path, "r", encoding="utf-8") as handle:
            try:
                payload = json.load(handle)
            except json.JSONDecodeError as exc:
                raise AdapterError(f"{self.FILE_NAME} 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, list):
            raise AdapterError(f"{self.FILE_NAME} 顶层应为数组，实际为 {type(payload).__name__}")
        yield from payload

    def adapt_record(self, record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(record, dict):
            return None, "invalid_record"
        url = str(record.get("githubPrUrl") or "")
        repo = ""
        if "/pull/" in url:
            repo = url.split("github.com/")[-1].split("/pull/")[0] if "github.com/" in url else ""
        target = record.get("target_commit")
        source = record.get("source_commit")
        instance = f"{repo.replace('/', '__')}@{str(target or '')[:7]}" if repo and target else None
        issues = []
        for comment in record.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            issues.append({
                "file": comment.get("path"),
                "line": comment.get("to_line") or comment.get("from_line"),
                "start_line": comment.get("from_line"),
                "end_line": comment.get("to_line"),
                "description": comment.get("note"),
                "category": comment.get("category") or record.get("category") or "",
                "side": comment.get("side"),
                "is_ai_comment": comment.get("is_ai_comment"),
                "source_model": comment.get("source_model"),
            })
        language = record.get("project_main_language")
        return build_case(
            upstream_instance_id=instance,
            repo=repo,
            base_sha=source,
            head_sha=target,
            language=language,
            gold_issues=issues,
            is_clean=None,
            source_url=url,
            raw_ref={
                "project_main_language": language,
                "declared_category": record.get("category"),
                "change_line_count": record.get("change_line_count"),
                "comment_count": len(issues),
                "created_at": record.get("created_at"),
                "merged_at": record.get("merged_at"),
            },
        )
