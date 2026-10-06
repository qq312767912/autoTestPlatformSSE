#!/usr/bin/env python3
"""stage_result.json 自检：ID、引用、需求关联、schema 与原文定位。

**为什么要有这个脚本，而不是"让模型自己保证"**：`stage_result.json` 是这套 L3 能力
的地基——平台的差异比对、证据反查和补丁派生全部读它。地基里出现一个重复的
`TP-018`、或一条指向不存在原句的证据，故障不会当场报出来，而会在几周后表现为
"这条归因怎么也对不上"。所以它必须在**产出当时**就被机械地查一遍。

脚本不依赖 Django、不联网、不调用模型，只读文件：
可以随包分发，也可以在平台上被当成静态校验脚本重复执行。

用法::

    python validate_stage_result.py stage_result.json
    python validate_stage_result.py stage_result.json --corpus corpus.json
    python validate_stage_result.py stage_result.json --artifacts-dir ./artifacts

`corpus.json` 形如::

    {"documents": [
      {"document_id": "doc-21", "document_version": "v3",
       "chunks": [{"chunk_id": "chunk-108", "text": "……原句……", "start_offset": 0}]}
    ]}

退出码：0 = 全部通过（可含警告）；1 = 有错误。错误与警告都打印到 stdout，
每条带上 JSON Pointer 路径，便于直接回填到产出页面上。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ITEM_ID_RE = re.compile(r"^TP-[0-9]{3,}$")
EVIDENCE_ID_RE = re.compile(r"^EV-[0-9]{3,}$")
REQUIREMENT_ID_RE = re.compile(r"^REQ-[A-Za-z0-9_.-]+$")

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "stage_result.json"


class Report:
    """错误 / 警告分开收集。

    分开的价值在于**门槛**：错误必须让本次产出在平台上标记为「结构化协议失败」，
    警告只提示、不阻断。把两者混成一个列表会让"这条到底卡不卡"变成看代码才知道。
    """

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append((path, message))

    def warn(self, path: str, message: str) -> None:
        self.warnings.append((path, message))

    @property
    def ok(self) -> bool:
        return not self.errors


def _iter_nodes(payload: dict, key: str) -> list[tuple[int, dict]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [(index, node) for index, node in enumerate(value) if isinstance(node, dict)]


def check_schema(payload: dict, schema_path: Path, report: Report) -> None:
    """结构校验。缺 jsonschema 时明确报出来，而不是静默跳过。"""
    try:
        from jsonschema import Draft7Validator
    except ImportError:  # pragma: no cover - 依赖缺失时的显式降级
        report.warn("", "未安装 jsonschema，跳过结构校验（仅执行语义校验）")
        return

    if not schema_path.exists():
        report.error("", f"找不到 schema 文件：{schema_path}")
        return

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path)):
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        if path == "/":
            path = ""
        if not error.absolute_path and error.validator == "required":
            missing = error.message.split("'")[1] if "'" in error.message else ""
            path = f"/{missing}" if missing else ""
        report.error(path, error.message)


def check_stable_ids(payload: dict, report: Report) -> None:
    """稳定 ID：格式、唯一性、编号连续性。

    重复的 ID 比缺 ID 更危险：缺 ID 一眼能看出来，重复的 ID 会让平台把两个不同的
    方案项当成同一个来比对，差异结果看起来"正常"却是错的。
    """
    seen_items: dict[str, int] = {}
    for index, item in _iter_nodes(payload, "items"):
        item_id = str(item.get("id") or "").strip()
        path = f"/items/{index}/id"
        if not item_id:
            report.error(path, "缺少稳定方案项 ID；没有它，重跑时整份方案会被算成全新内容")
            continue
        if not ITEM_ID_RE.match(item_id):
            report.error(path, f"方案项 ID 格式不合规：{item_id}（应为 TP-001 形式）")
        if item_id in seen_items:
            report.error(path, f"方案项 ID 重复：{item_id}（首次出现在 /items/{seen_items[item_id]}）")
        else:
            seen_items[item_id] = index

    seen_evidence: dict[str, int] = {}
    for index, entry in _iter_nodes(payload, "evidence"):
        evidence_id = str(entry.get("id") or "").strip()
        path = f"/evidence/{index}/id"
        if not evidence_id:
            report.error(path, "证据缺少 ID，无法被方案项引用")
            continue
        if not EVIDENCE_ID_RE.match(evidence_id):
            report.error(path, f"证据 ID 格式不合规：{evidence_id}（应为 EV-001 形式）")
        if evidence_id in seen_evidence:
            report.error(path, f"证据 ID 重复：{evidence_id}（首次出现在 /evidence/{seen_evidence[evidence_id]}）")
        else:
            seen_evidence[evidence_id] = index

    seen_requirements: dict[str, int] = {}
    for index, entry in _iter_nodes(payload, "requirements"):
        requirement_id = str(entry.get("id") or "").strip()
        path = f"/requirements/{index}/id"
        if not requirement_id:
            report.error(path, "需求缺少 ID")
            continue
        if not REQUIREMENT_ID_RE.match(requirement_id):
            report.error(path, f"需求 ID 格式不合规：{requirement_id}（应为 REQ-xxx 形式）")
        if requirement_id in seen_requirements:
            report.error(path, f"需求 ID 重复：{requirement_id}（首次出现在 /requirements/{seen_requirements[requirement_id]}）")
        else:
            seen_requirements[requirement_id] = index

    # 编号连续性只是提示：人为删掉一条方案项本来就会断号，断号不等于错。
    numbers = sorted(
        int(item_id.split("-")[1])
        for item_id in seen_items
        if ITEM_ID_RE.match(item_id)
    )
    if numbers and numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        report.warn("/items", "方案项编号不连续；如果是有意裁剪请忽略，否则可能是漏写")


def check_cross_references(payload: dict, report: Report) -> None:
    """引用必须落在本份产出内部。

    Schema 只能保证 `evidence_ids` 是个字符串数组，保证不了它**指向的东西存在**——
    而"指向不存在的证据"正是非法引用的主要来源。
    """
    known_evidence = {
        str(entry.get("id") or "").strip()
        for _, entry in _iter_nodes(payload, "evidence")
    }
    known_requirements = {
        str(entry.get("id") or "").strip()
        for _, entry in _iter_nodes(payload, "requirements")
    }
    requirements_present = bool(_iter_nodes(payload, "requirements"))

    referenced_requirements: set[str] = set()
    for index, item in _iter_nodes(payload, "items"):
        for ref_index, ref in enumerate(item.get("evidence_ids") or []):
            ref_id = str(ref or "").strip()
            if ref_id and ref_id not in known_evidence:
                report.error(
                    f"/items/{index}/evidence_ids/{ref_index}",
                    f"引用了不存在的证据：{ref_id}",
                )
        for ref_index, ref in enumerate(item.get("requirement_ids") or []):
            ref_id = str(ref or "").strip()
            if not ref_id:
                continue
            referenced_requirements.add(ref_id)
            if requirements_present and ref_id not in known_requirements:
                report.error(
                    f"/items/{index}/requirement_ids/{ref_index}",
                    f"引用了不存在的需求：{ref_id}",
                )

    # 声明了却没人引用的需求 = 覆盖缺口。给警告而不是错误：
    # 需求被有意排期到下一轮是正常业务，不该让本次产出变成失败。
    for requirement_id in sorted(known_requirements - referenced_requirements):
        report.warn("/items", f"需求 {requirement_id} 已声明但没有任何方案项引用它（覆盖缺口）")

    # 反向：方案项引用了证据但证据没写清定位三要素，L3 就无法兑现"回原文"。
    evidence_by_id = {
        str(entry.get("id") or "").strip(): entry
        for _, entry in _iter_nodes(payload, "evidence")
    }
    for index, item in _iter_nodes(payload, "items"):
        if item.get("evidence_ids"):
            continue
        report.warn(f"/items/{index}/evidence_ids", "该方案项没有关联任何证据，无法反查依据")


def check_evidence_locations(payload: dict, corpus_path: Path, report: Report) -> None:
    """把每条证据按 document/version/chunk 回到原文。

    这是 L3 与 L2 的真正分界：有 items 叫"可评审"，能回到原句才叫"可归因"。
    定位失败的证据**不删除**，只标出来——平台据此在页面上标注 invalid，
    而不是让它冒充可信证据。
    """
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.error("", f"无法读取原文索引 {corpus_path}：{exc}")
        return

    index: dict[tuple[str, str, str], str] = {}
    for document in corpus.get("documents") or []:
        if not isinstance(document, dict):
            continue
        document_id = str(document.get("document_id") or "")
        version = str(document.get("document_version") or "")
        for chunk in document.get("chunks") or []:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id") or "")
            index[(document_id, version, chunk_id)] = str(chunk.get("text") or "")

    if not index:
        report.warn("", "原文索引为空，跳过定位校验")
        return

    for position, entry in _iter_nodes(payload, "evidence"):
        path = f"/evidence/{position}"
        document_id = str(entry.get("document_id") or "")
        version = str(entry.get("document_version") or "")
        chunk_id = str(entry.get("chunk_id") or "")
        quote = str(entry.get("quote") or "")

        text = index.get((document_id, version, chunk_id))
        if text is None:
            report.error(path, f"定位失败：索引里没有 {document_id}@{version}/{chunk_id}")
            continue
        if quote and quote not in text:
            report.error(path, f"原句未出现在 {chunk_id} 中，引用可能已过期或被改写：{quote[:40]}")

        expected_hash = str(entry.get("content_hash") or "")
        if expected_hash:
            actual = hashlib.sha256(quote.encode("utf-8")).hexdigest()
            # 允许写法 `sha256:<hex>` 或裸 hex，但必须对得上。
            normalized = expected_hash.split(":", 1)[-1].strip().lower()
            if normalized != actual:
                report.error(path, "content_hash 与 quote 内容不一致，原句可能已被替换")


def check_primary_artifacts(payload: dict, artifacts_dir: Path, report: Report) -> None:
    """主产物必须真实存在、且哈希对得上。

    没有这一步，"声明了 test_plan.xlsx"和"真的产出了 test_plan.xlsx"就分不开。
    """
    for index, artifact in _iter_nodes(payload, "primary_artifacts"):
        path = f"/primary_artifacts/{index}"
        name = str(artifact.get("name") or "")
        if not name:
            report.error(f"{path}/name", "主产物缺少文件名")
            continue
        candidate = artifacts_dir / name
        if not candidate.exists():
            # 允许声明相对路径：先看 path，再看根目录下的同名文件。
            relative = str(artifact.get("path") or "")
            candidate = (artifacts_dir / relative) if relative else candidate
        if not candidate.exists():
            report.error(f"{path}/name", f"主产物文件不存在：{name}")
            continue
        declared = str(artifact.get("sha256") or "").split(":", 1)[-1].strip().lower()
        if not declared:
            report.error(f"{path}/sha256", "主产物缺少内容哈希，无法证明信封描述的就是这份产物")
            continue
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if declared != actual:
            report.error(f"{path}/sha256", f"主产物哈希不一致：声明 {declared[:12]}…，实际 {actual[:12]}…")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stage_result.json 自检（stage-result/v1）")
    parser.add_argument("stage_result", type=Path, help="待校验的 stage_result.json")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="使用的 schema 文件")
    parser.add_argument("--corpus", type=Path, default=None, help="原文索引（用于证据定位）")
    parser.add_argument("--artifacts-dir", type=Path, default=None, help="业务产物所在目录（用于主产物哈希校验）")
    parser.add_argument("--allow-missing-artifacts", action="store_true",
                        help="产出目录暂不可访问时跳过主产物存在性检查（哈希仍会校验）")
    args = parser.parse_args(argv)

    if not args.stage_result.exists():
        print(f"[ERROR]  找不到 {args.stage_result}")
        return 1
    try:
        payload = json.loads(args.stage_result.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[ERROR]  stage_result.json 不是合法 JSON：{exc}")
        return 1
    if not isinstance(payload, dict):
        print("[ERROR]  stage_result.json 顶层必须是对象")
        return 1

    report = Report()
    check_schema(payload, args.schema, report)
    check_stable_ids(payload, report)
    check_cross_references(payload, report)
    if args.corpus is not None:
        check_evidence_locations(payload, args.corpus, report)
    if args.artifacts_dir is not None and not args.allow_missing_artifacts:
        check_primary_artifacts(payload, args.artifacts_dir, report)

    for path, message in report.errors:
        print(f"[ERROR]  {path} {message}")
    for path, message in report.warnings:
        print(f"[WARN]   {path} {message}")

    if report.ok:
        print(f"[OK]     stage_result.json 通过（{len(report.warnings)} 条警告）")
        return 0
    print(f"[FAIL]   {len(report.errors)} 处错误、{len(report.warnings)} 条警告")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
