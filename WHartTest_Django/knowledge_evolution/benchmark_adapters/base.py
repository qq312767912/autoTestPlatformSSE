"""公开集适配器基类：统一对账、校验与拒绝原因。

三条硬规则（对应 R2/R5）：
1. 转换失败的样本必须记录原因，不静默跳过；
2. 空金标不得自动推断为干净样本，只有上游显式标签才可置 `is_clean=True/False`；
3. 只保留平台三语言（Python / Java / C++），其余记 `language_not_supported` 拒绝。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..benchmark_catalog import (
    CATALOG,
    CLEAN_SOURCE_UNKNOWN,
    SUPPORTED_LANGUAGES,
    normalize_language,
)

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_DESCRIPTION = 2000


class AdapterError(Exception):
    """适配器不可恢复错误（文件缺失、格式整体不符）。"""


class AdapterDependencyError(AdapterError):
    """缺少解析依赖（如 parquet 需要 pyarrow）。必须让准备作业失败而非跳过样本。"""


@dataclass
class ConversionOutcome:
    cases: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def reject(self, upstream_instance_id: str, reason: str, detail: str = "") -> None:
        self.rejections.append({
            "upstream_instance_id": str(upstream_instance_id)[:255],
            "reason": reason,
            "detail": str(detail)[:1000],
        })

    @property
    def accepted(self) -> int:
        return len(self.cases)


def _clean_text(value: Any, limit: int = MAX_DESCRIPTION) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_issue(raw: dict[str, Any]) -> dict[str, Any] | None:
    """把各基准的金标字段收敛成同一形态。

    行号可以缺失（部分基准只给文件级上下文），此时 `located=False`；
    只要文件与描述都在，就仍是一条可用于文件级匹配的金标。
    """
    if not isinstance(raw, dict):
        return None
    file_path = str(raw.get("file") or raw.get("path") or "").strip()
    description = _clean_text(raw.get("description") or raw.get("text") or raw.get("note"))
    if not file_path or not description:
        return None
    def _as_int(value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    line = _as_int(raw.get("line")) or _as_int(raw.get("to_line")) or _as_int(raw.get("end_line"))
    start = _as_int(raw.get("start_line")) or _as_int(raw.get("from_line")) or line
    end = _as_int(raw.get("end_line")) or _as_int(raw.get("to_line")) or line
    issue = {
        "file": file_path,
        "line": line or start,
        "start_line": start,
        "end_line": end,
        "located": bool(line or start),
        "description": description,
        "category": _clean_text(raw.get("category"), 64),
        "severity": _clean_text(raw.get("severity"), 32),
    }
    for optional in ("side", "source_model", "is_ai_comment"):
        if raw.get(optional) not in (None, ""):
            issue[optional] = raw[optional]
    return issue


def build_case(
    *,
    upstream_instance_id: Any,
    repo: Any,
    base_sha: Any,
    head_sha: Any,
    language: Any,
    gold_issues: Iterable[dict[str, Any]],
    is_clean: bool | None,
    is_clean_source: str = CLEAN_SOURCE_UNKNOWN,
    source_url: Any = "",
    raw_ref: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """校验并生成统一协议样本；返回 (case, reason)，reason 为空表示通过。"""
    instance = str(upstream_instance_id or "").strip()
    if not instance:
        return None, "missing_instance_id"
    if len(instance) > 255:
        return None, "instance_id_too_long"
    base = str(base_sha or "").strip().lower()
    head = str(head_sha or "").strip().lower()
    if not SHA_PATTERN.match(base) or not SHA_PATTERN.match(head):
        return None, "invalid_commit_sha"
    if base == head:
        return None, "identical_commit_pair"
    repo_name = str(repo or "").strip()
    if not repo_name or "/" not in repo_name:
        return None, "invalid_repo_identifier"
    language_code = normalize_language(language)
    if language_code is None:
        return None, f"language_not_supported:{str(language or 'unknown').lower()[:24]}"
    issues = [issue for issue in (normalize_issue(item) for item in (gold_issues or [])) if issue]
    if not issues and is_clean is not True:
        # 空金标且上游未声明干净：既不能算召回，也不能算误报，只能拒绝。
        return None, "empty_gold_without_clean_label"
    if is_clean is None or is_clean_source == CLEAN_SOURCE_UNKNOWN:
        is_clean_value, clean_source = None, CLEAN_SOURCE_UNKNOWN
    else:
        is_clean_value, clean_source = bool(is_clean), is_clean_source
    if language_code not in SUPPORTED_LANGUAGES:  # 兜底，normalize 已过滤
        return None, f"language_not_supported:{language_code}"
    return {
        "upstream_instance_id": instance,
        "repo": repo_name[:255],
        "base_sha": base,
        "head_sha": head,
        "language": language_code,
        "gold_issues": issues,
        "is_clean": is_clean_value,
        "is_clean_source": clean_source,
        "source_url": str(source_url or "")[:500],
        "raw_ref": raw_ref or {},
    }, ""


def dataset_default_language(benchmark_id: str) -> str | None:
    """数据集级默认语言：仅当上游实测只覆盖一种平台语言时可用。

    这用于「干净样本没有代码改动、无法从文件后缀推断语言」的场景；
    依据是 manifest 中已实测的语言构成，不是猜测。多语言数据集返回 None。
    """
    verified = (CATALOG.get(benchmark_id, {}).get("languages") or {}).get("verified") or {}
    keys = [key for key in verified if normalize_language(key)]
    return normalize_language(keys[0]) if len(keys) == 1 else None


def language_from_path(path: str) -> str | None:
    lowered = str(path or "").lower()
    for suffix, language in (
        (".py", "python"), (".java", "java"),
        (".cpp", "c++"), (".cc", "c++"), (".cxx", "c++"), (".hpp", "c++"), (".hh", "c++"),
    ):
        if lowered.endswith(suffix):
            return language
    return None


class BenchmarkAdapter:
    """子类实现 `iter_records` 与 `adapt_record`，`convert` 统一负责去重与统计。"""

    benchmark_id: str = ""
    version: str = ""
    requires: tuple[str, ...] = ()

    def iter_records(self, raw_dir: str) -> Iterable[dict[str, Any]]:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def adapt_record(self, record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:  # pragma: no cover
        raise NotImplementedError

    def convert(self, raw_dir: str) -> ConversionOutcome:
        outcome = ConversionOutcome()
        seen: set[str] = set()
        total = 0
        for record in self.iter_records(raw_dir):
            total += 1
            instance = record.get("instance_id") or record.get("upstream_instance_id")
            if instance and str(instance) in seen:
                outcome.reject(instance, "duplicate_instance_id")
                continue
            try:
                case, reason = self.adapt_record(record)
            except AdapterError:
                raise
            except Exception as exc:  # 单条坏样本不应中断整批
                outcome.reject(instance or f"row-{total}", "adapter_exception", repr(exc))
                continue
            if case is None:
                outcome.reject(instance or f"row-{total}", reason or "unknown_reason")
                continue
            seen.add(case["upstream_instance_id"])
            outcome.cases.append(case)
        outcome.stats = {
            "records": total,
            "accepted": len(outcome.cases),
            "rejected": len(outcome.rejections),
        }
        return outcome
