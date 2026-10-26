"""内置公开测评集目录：固定 ID、官方来源、版本、校验和与许可。

本模块是「唯一事实来源」：界面展示、准备作业、离线包校验都从这里取元数据。
所有数值均为实测所得，来源证据见 `specs/code-review-public-benchmarks/verification-t01.md`。
不得写入未验证的语言或样本数；缺失一律写 None，由展示层显示「未覆盖」。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# 统一的样本协议字段（三个适配器都必须产出这些字段）
CASE_FIELDS = (
    "upstream_instance_id",
    "repo",
    "base_sha",
    "head_sha",
    "language",
    "gold_issues",
    "is_clean",
    "is_clean_source",
    "source_url",
    "raw_ref",
)

# is_clean 的取值来源。空金标不等于干净样本，必须由上游显式标签或适配器规则确定。
CLEAN_SOURCE_UPSTREAM = "upstream_label"
CLEAN_SOURCE_ADAPTER = "adapter_rule"
CLEAN_SOURCE_UNKNOWN = "unknown"

SUPPORTED_LANGUAGES = ("python", "java", "c++")
LANGUAGE_ALIASES = {
    "python": "python",
    "py": "python",
    "java": "java",
    "c++": "c++",
    "cpp": "c++",
    "cxx": "c++",
    "cc": "c++",
}

CATALOG: dict[str, dict[str, Any]] = {
    "swe_care": {
        "benchmark_id": "swe_care",
        "display_name": "SWE-CARE（CodeFuse-CR-Bench）",
        "short_name": "SWE-CARE",
        "publisher": "inclusionAI / CodeFuse",
        "homepage": "https://huggingface.co/datasets/inclusionAI/SWE-CARE",
        "paper": "arXiv:2509.14856",
        "source_kind": "huggingface",
        "default_split": "test",
        "upstream": {
            "dataset": "inclusionAI/SWE-CARE",
            "revision": "3b3a625ef26bd497a3a13485c0dc9ece537f24d0",
            "last_modified": "2026-09-09T08:24:11.000Z",
        },
        "files": [
            {
                "name": "test.parquet",
                "split": "test",
                "size": 12126296,
                "sha256": "be45381d2579e74d64abee7aedb1438843a83cd0a22a29d9bb8e141c35d26b07",
                "urls": [
                    "https://hf-mirror.com/datasets/inclusionAI/SWE-CARE/resolve/{revision}/data/test-00000-of-00001.parquet",
                    "https://huggingface.co/datasets/inclusionAI/SWE-CARE/resolve/{revision}/data/test-00000-of-00001.parquet",
                ],
            }
        ],
        "license": {
            "spdx": "Apache-2.0",
            "source": "Hugging Face dataset card（cardData.license）",
            "redistributable": True,
            "requires_attribution": True,
            "note": "可再分发；对外交付需保留来源与许可声明。",
        },
        "languages": {
            "declared": ["python"],
            "verified": {"python": 671},
            "evidence": "Hugging Face cardData.splits 与论文均为 Python 项目；test.parquet 明文仓库名抽样全部为 Python 仓库。",
            "uncovered": ["java", "c++"],
        },
        "expected": {
            "case_count": 671,
            "gold_count": None,
            "clean_count": None,
            "note": "671 来自官方 splits.num_examples；金标条数须在准备阶段逐条统计，禁止预估。",
        },
        "reconciliation": {
            "upstream_records": 671,
            "converted_cases": None,
            "rejected": None,
            "converted_gold": None,
            "note": "需 pyarrow 才能对账；未装 pyarrow 时准备作业应以 dependency_missing 失败，不产生版本。",
        },
        "adapter": {
            "version": "1.0.0",
            "module": "knowledge_evolution.benchmark_adapters.swe_care",
            "requires": ["pyarrow"],
            "gold_semantics": "reference_review_comments（真实评审意见，含 path/line/diff_hunk）",
            "clean_semantics": "无显式干净标签，is_clean 恒为 None，不参与误报分母",
        },
        "notes": [
            "仓库级上下文：base_commit 与 commit_to_review.head_commit 都是精确 Commit。",
            "平台口径与官方口径不同，官方 SEM-F1 不得与本平台指标混用。",
        ],
    },
    "swr_bench": {
        "benchmark_id": "swr_bench",
        "display_name": "SWR-Bench",
        "short_name": "SWR-Bench",
        "publisher": "北京大学（Proc. ACM Softw. Eng. / FSE 2026）",
        "homepage": "https://github.com/ZZR0/SWRench",
        "paper": "arXiv:2509.01494",
        "source_kind": "github",
        "default_split": "main",
        "upstream": {
            "repo": "ZZR0/SWRench",
            "revision": "67ae1d4395ac05f800d62b0e13678eadeb9fa5c7",
            "committed_at": "2026-06-05T04:10:11Z",
        },
        "files": [
            {
                "name": "swr_datasets_d5c5.jsonl",
                "split": "main",
                "size": 84826523,
                "sha256": "7048e7a92ea9f7a1e6812646694a040582448cc1dd9d515264a4b55071aff95d",
                "urls": [
                    "https://codeload.github.com/ZZR0/SWRench/tar.gz/{revision}",
                    "https://github.com/ZZR0/SWRench/archive/{revision}.tar.gz",
                ],
                "archive_member": "data/swr_datasets_d5c5.jsonl",
            }
        ],
        "license": {
            "spdx": "MIT",
            "source": "仓库 LICENSE 文件",
            "redistributable": True,
            "requires_attribution": True,
            "note": "MIT 允许再分发；须保留版权声明。",
        },
        "languages": {
            "declared": ["python"],
            "verified": {"python": 1000},
            "evidence": "1000 条全部来自 12 个 SWE-bench Python 仓库（django、sympy、matplotlib 等），实测 instance_id 唯一 1000 条。",
            "uncovered": ["java", "c++"],
        },
        "expected": {
            "case_count": 1000,
            "gold_count": 949,
            "clean_count": 500,
            "note": "500 条 change_introduced=false 为干净 PR，是全场唯一成规模负例；949 为 defect 侧 change 条目总数。",
        },
        "reconciliation": {
            "upstream_records": 1000,
            "converted_cases": 943,
            "rejected": 57,
            "rejected_reason": "empty_gold_without_clean_label（change_snippet 无任何文件路径，无法定位，也不能当负例）",
            "converted_gold": 771,
            "located_gold": 695,
            "note": "实测于 2026-10-25；949 条上游 change 中有 178 条因无文件路径被丢弃或在被拒样本内。",
        },
        "adapter": {
            "version": "1.0.0",
            "module": "knowledge_evolution.benchmark_adapters.swr_bench",
            "requires": [],
            "gold_semantics": "changes[].change_introducing（含 commit_sha、code_snippet、path 与行号）",
            "clean_semantics": "change_introduced=false 且 changes 为空 → 上游显式干净标签",
        },
        "notes": [
            "不得把 1000 条全部称为负例：只有 500 条是干净 PR。",
            "pr_commits 给出 head commit，可用于仓库快照定位。",
        ],
    },
    "aacr_bench": {
        "benchmark_id": "aacr_bench",
        "display_name": "AACR-Bench",
        "short_name": "AACR-Bench",
        "publisher": "南京大学 + 阿里",
        "homepage": "https://github.com/alibaba/aacr-bench",
        "paper": "arXiv:2601.19494",
        "source_kind": "github",
        "default_split": "positive",
        "upstream": {
            "repo": "alibaba/aacr-bench",
            "revision": "ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df",
            "committed_at": "2026-10-15T07:13:55Z",
        },
        "files": [
            {
                "name": "positive_samples.json",
                "split": "positive",
                "size": 1117816,
                "sha256": "02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097",
                "urls": [
                    "https://codeload.github.com/alibaba/aacr-bench/tar.gz/{revision}",
                    "https://github.com/alibaba/aacr-bench/archive/{revision}.tar.gz",
                ],
                "archive_member": "dataset/positive_samples.json",
            },
            {
                "name": "negative_samples.json",
                "split": "negative",
                "size": 508475,
                "sha256": "d4e9f5b610033914e5e36b066ee4c6177f037642a011949f68dd4e20bfa77e2b",
                "urls": [
                    "https://codeload.github.com/alibaba/aacr-bench/tar.gz/{revision}",
                    "https://github.com/alibaba/aacr-bench/archive/{revision}.tar.gz",
                ],
                "archive_member": "dataset/negative_samples.json",
                "excluded": True,
                "excluded_reason": "官方 converter、evaluator 与 metrics 文档均未引用该分片，标签语义未定义；"
                                  "实测其评论与 positive 分片交集为 0 且同为真实缺陷意见，不能当作干净负例。",
            },
        ],
        "license": {
            "spdx": "Apache-2.0",
            "source": "GitHub API license.spdx_id",
            "redistributable": True,
            "requires_attribution": True,
            "note": "可再分发；数据集由真实 PR 评论 + AI 辅助生成，对外引用需同时标注两条来源。",
        },
        "languages": {
            "declared": ["python", "java", "c++"],
            "verified": {"python": 21, "java": 30, "c++": 33},
            "evidence": "positive_samples.json 按 project_main_language 实测：Python 21 / Java 30 / C++ 33（合计 84 PR、631 条评论、15 仓库）；"
                        "评论文件后缀与项目主语言逐条比对无不一致。",
            "uncovered": [],
        },
        "expected": {
            "case_count": 84,
            "gold_count": 631,
            "clean_count": 0,
            "note": "只纳入 Python/Java/C++ 三语言；其余 Go/Rust/PHP/TS/JS/C#/C 不进入平台样本。无干净负例，不参与误报分母。",
        },
        "reconciliation": {
            "upstream_records": 196,
            "converted_cases": 84,
            "rejected": 112,
            "rejected_reason": "language_not_supported（Go 22 / C# 10 / C 19 / JS 11 / TS 29 / PHP 11 / Rust 10）",
            "converted_gold": 631,
            "located_gold": 631,
            "note": "实测与 manifest 声明完全一致（84 PR / 631 条评论 / 15 仓库）。",
        },
        "adapter": {
            "version": "1.0.0",
            "module": "knowledge_evolution.benchmark_adapters.aacr_bench",
            "requires": [],
            "gold_semantics": "comments[]（path/from_line/to_line/note/category/is_ai_comment）",
            "clean_semantics": "无干净标签，is_clean 恒为 None",
        },
        "notes": [
            "README 声称 200 PR / 2145 条评论，实测 positive 分片为 196 PR / 1506 条，按实测登记。",
            "negative_samples.json 已排除，不作为负例来源。",
        ],
    },
}

BENCHMARK_IDS = tuple(CATALOG)


def version_label(benchmark_id: str) -> str:
    """数据版本号 = 上游 revision 前 12 位 + 适配器版本，版本号与内容一一对应。"""
    entry = CATALOG[benchmark_id]
    revision = entry["upstream"].get("revision") or ""
    return f"{revision[:12]}-a{entry['adapter']['version']}"


def license_summary(benchmark_id: str) -> dict[str, Any]:
    return dict(CATALOG[benchmark_id]["license"])


def resolve_files(benchmark_id: str) -> list[dict[str, Any]]:
    """返回该基准的原始文件清单，URL 已按固定 revision 展开。"""
    entry = CATALOG[benchmark_id]
    revision = entry["upstream"]["revision"]
    resolved = []
    for item in entry["files"]:
        record = dict(item)
        record["urls"] = [url.format(revision=revision) for url in item["urls"]]
        resolved.append(record)
    return resolved


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest(benchmark_id: str) -> dict[str, Any]:
    """生成可离线校验的 manifest；离线包与准备作业都用它做对账。"""
    if benchmark_id not in CATALOG:
        raise KeyError(f"未知公开测评集：{benchmark_id}")
    entry = CATALOG[benchmark_id]
    return {
        "benchmark_id": benchmark_id,
        "version": version_label(benchmark_id),
        "display_name": entry["display_name"],
        "upstream": entry["upstream"],
        "files": [
            {
                "name": item["name"],
                "split": item["split"],
                "size": item["size"],
                "sha256": item["sha256"],
                "excluded": bool(item.get("excluded")),
            }
            for item in entry["files"]
        ],
        "license": entry["license"],
        "languages": entry["languages"],
        "expected": entry["expected"],
        "adapter": {"version": entry["adapter"]["version"], "requires": entry["adapter"]["requires"]},
        "manifest_digest": manifest_digest(benchmark_id),
    }


def manifest_digest(benchmark_id: str) -> str:
    entry = CATALOG[benchmark_id]
    payload = {
        "benchmark_id": benchmark_id,
        "upstream": entry["upstream"],
        "files": [{k: item.get(k) for k in ("name", "split", "size", "sha256")} for item in entry["files"]],
        "adapter_version": entry["adapter"]["version"],
        "license": entry["license"]["spdx"],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_language(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return LANGUAGE_ALIASES.get(text)


def catalog_payload() -> list[dict[str, Any]]:
    """服务端下发的目录：界面只按这里渲染，避免前后端各写一份语言清单。"""
    payload = []
    for benchmark_id in BENCHMARK_IDS:
        entry = CATALOG[benchmark_id]
        payload.append({
            "benchmark_id": benchmark_id,
            "display_name": entry["display_name"],
            "short_name": entry["short_name"],
            "publisher": entry["publisher"],
            "homepage": entry["homepage"],
            "paper": entry["paper"],
            "source_kind": entry["source_kind"],
            "default_split": entry["default_split"],
            "version": version_label(benchmark_id),
            "upstream_revision": entry["upstream"].get("revision"),
            "license": entry["license"],
            "languages": entry["languages"],
            "expected": entry["expected"],
            "adapter": entry["adapter"],
            "notes": entry["notes"],
            "manifest": manifest(benchmark_id),
        })
    return payload
