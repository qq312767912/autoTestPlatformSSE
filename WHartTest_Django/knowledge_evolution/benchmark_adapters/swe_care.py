"""SWE-CARE（CodeFuse-CR-Bench）适配器。

原始字段映射（官方 HF 数据集，固定 revision 见 benchmark_catalog）：
    instance_id                      -> upstream_instance_id
    repo                             -> repo
    base_commit                      -> base_sha
    commit_to_review.head_commit     -> head_sha
    language                         -> language
    reference_review_comments[]      -> gold_issues
        path / line|original_line / start_line / text

无干净样本标签：`is_clean` 恒为 None，绝不因「金标为空」推断为干净。
parquet 需要 pyarrow；缺失时准备作业必须失败，不允许静默跳过样本。
离线包可预转换为同名 `test.jsonl`（一行一条记录），此时无需 pyarrow。
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .base import AdapterDependencyError, AdapterError, BenchmarkAdapter, build_case


class SweCareAdapter(BenchmarkAdapter):
    benchmark_id = "swe_care"
    version = "1.0.0"
    requires = ("pyarrow",)
    PARQUET_NAME = "test.parquet"
    JSONL_NAME = "test.jsonl"

    def iter_records(self, raw_dir: str) -> Iterable[dict[str, Any]]:
        jsonl_path = os.path.join(raw_dir, self.JSONL_NAME)
        if os.path.exists(jsonl_path):
            with open(jsonl_path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        yield {"instance_id": f"line-{line_number}", "_parse_error": str(exc)}
            return
        parquet_path = os.path.join(raw_dir, self.PARQUET_NAME)
        if not os.path.exists(parquet_path):
            raise AdapterError(f"缺少原始文件 {self.PARQUET_NAME}，请先完成数据准备")
        try:
            import pyarrow.parquet as pq  # noqa: PLC0415 - 依赖可选，缺失时给出明确错误
        except ImportError as exc:
            raise AdapterDependencyError(
                "SWE-CARE 原始数据为 parquet，解析需要 pyarrow；"
                "请在镜像中安装 pyarrow，或使用运维预置的同版本离线包（test.jsonl）"
            ) from exc
        reader = pq.ParquetFile(parquet_path)
        for batch in reader.iter_batches(batch_size=32):
            for row in batch.to_pylist():
                yield row

    def adapt_record(self, record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if record.get("_parse_error"):
            return None, "invalid_json"
        if not isinstance(record, dict):
            return None, "invalid_record"
        commit = record.get("commit_to_review") or {}
        head = commit.get("head_commit") if isinstance(commit, dict) else None
        base = record.get("base_commit")
        repo = record.get("repo")
        instance = record.get("instance_id")
        comments = record.get("reference_review_comments") or []
        issues = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            issues.append({
                "file": comment.get("path"),
                "line": comment.get("line") or comment.get("original_line"),
                "start_line": comment.get("start_line") or comment.get("original_start_line")
                              or comment.get("original_line") or comment.get("line"),
                "end_line": comment.get("line") or comment.get("original_line"),
                "description": comment.get("text"),
            })
        pull_number = record.get("pull_number")
        source_url = f"https://github.com/{repo}/pull/{pull_number}" if repo and pull_number else ""
        metadata = record.get("metadata") or {}
        return build_case(
            upstream_instance_id=instance,
            repo=repo,
            base_sha=base,
            head_sha=head,
            language=record.get("language"),
            gold_issues=issues,
            is_clean=None,
            source_url=source_url,
            raw_ref={
                "pull_number": pull_number,
                "commit_count": 1 if head else 0,
                "comment_count": len(issues),
                "difficulty": (metadata or {}).get("difficulty") if isinstance(metadata, dict) else None,
                "problem_domain": (metadata or {}).get("problem_domain") if isinstance(metadata, dict) else None,
            },
        )
