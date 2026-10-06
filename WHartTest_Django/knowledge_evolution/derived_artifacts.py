"""平台派生产物：把阶段产出"解读"成图谱、摘要与确认报告，并**登记留痕**（T07）。

职责边界
--------
本模块做三件事，且只做这三件：

1. **生成**：调协议层/图谱层/报告层的纯函数，拿到三份内容；
2. **落盘**：写到 `<MEDIA_ROOT>/derived_artifacts/<project>/<output>/` 下；
3. **登记**：写 ``StageDerivedArtifact``，带生成器版本与源产出哈希。

它**不校验**（校验在 ``stage_outputs``）、**不归因**（在 ``attribution``）、
**不判定人机一致**（在 ``workflow_feedback``）。把判断散进来，会让
"重复生成内容一致"这个验收条件无法定位到某一个函数。

为什么必须"先算图谱，再算摘要与报告"
------------------------------------
图谱是唯一执行证据定位的地方：每条引用是 ``verified`` 还是 ``invalid`` 由它给出。
摘要的"证据准确率"和报告的"异常证据"页都读这个结果。顺序颠倒会得到
"证据准确率 100%"这种看起来正常的假数字——因为没有任何引用被定位过。

为什么写入失败不能让调用方抛错（``best_effort``）
------------------------------------------------
派生物是**观测面**。业务产出此刻已经落库、Skill 也跑完了，如果因为一次
"写报告失败"就让整个请求失败，等于让观测面把业务产物一起丢掉（需求 R15）。
所以对外入口默认 best-effort：失败只记日志并返回空列表；需要严格语义
（测试、离线重算）时显式传 ``best_effort=False``。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from django.conf import settings

from .evidence_graph import GRAPH_FILENAME, GRAPH_GENERATOR_VERSION, stable_json
from .quality_summary import QUALITY_SUMMARY_FILENAME, QUALITY_SUMMARY_VERSION
from .review_reports import (
    REVIEW_REPORT_FILENAME,
    REVIEW_REPORT_VERSION,
    build_review_workbook,
)

logger = logging.getLogger(__name__)

#: 派生层自身的版本：落盘布局或登记语义变更时 +1。
DERIVED_ARTIFACT_VERSION = "stage-derived-artifacts/v1"

#: 派生产物的根目录名（相对 ``MEDIA_ROOT``）。
DERIVED_ROOT_NAME = "derived_artifacts"

KIND_EVIDENCE_GRAPH = "evidence_graph"
KIND_QUALITY_SUMMARY = "quality_summary"
KIND_REVIEW_REPORT = "review_report"

#: 种类 → 落盘文件名。真值只有这一处：``StageDerivedArtifact.KIND_CHOICES``
#: 定的是"有哪些种类"，这里定"各自叫什么"，两者分开是刻意的——
#: 加一个种类要同时动两处，想漏也漏不掉（缺名字会在生成时直接报错）。
KIND_FILENAMES: dict[str, str] = {
    KIND_EVIDENCE_GRAPH: GRAPH_FILENAME,
    KIND_QUALITY_SUMMARY: QUALITY_SUMMARY_FILENAME,
    KIND_REVIEW_REPORT: REVIEW_REPORT_FILENAME,
}

#: 种类 → 生成器版本。换了判定逻辑就换版本，历史记录据此解释"结论为什么变了"。
KIND_GENERATOR_VERSIONS: dict[str, str] = {
    KIND_EVIDENCE_GRAPH: GRAPH_GENERATOR_VERSION,
    KIND_QUALITY_SUMMARY: QUALITY_SUMMARY_VERSION,
    KIND_REVIEW_REPORT: REVIEW_REPORT_VERSION,
}

#: 种类 → MIME（下载接口用）。
KIND_CONTENT_TYPES: dict[str, str] = {
    KIND_EVIDENCE_GRAPH: "application/json",
    KIND_QUALITY_SUMMARY: "application/json",
    KIND_REVIEW_REPORT: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}

#: 种类 → 是否文本（文本用 UTF-8 编码，二进制直接写字节）。
KIND_TEXTUAL: dict[str, bool] = {
    KIND_EVIDENCE_GRAPH: True,
    KIND_QUALITY_SUMMARY: True,
    KIND_REVIEW_REPORT: False,
}

#: **业务产物保留名单**：这些文件名属于 Skill，平台派生层绝不使用。
#:
#: 写死一份名单而不是靠"文件名不同"这种巧合：将来有人把确认报告改名叫
#: ``stage_result.json`` 以"统一命名"时，这一条会立刻把它拦下来。
BUSINESS_ARTIFACT_FILENAMES: frozenset[str] = frozenset({
    "stage_result.json",
    "test_plan.xlsx",
    "test_cases.xlsx",
    "test_report.xlsx",
})

#: ``metadata['history']`` 最多留几条。够回答"最近几次为什么变了"即可；
#: 无限追加会把一张登记表变成日志表，而它本来只负责"当前是哪一份"。
HISTORY_LIMIT = 10


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def derived_root() -> Path:
    """派生产物根目录。**每次调用重新求值**，以便测试用 ``override_settings``。"""
    return Path(settings.MEDIA_ROOT) / DERIVED_ROOT_NAME


def output_directory(output) -> Path:
    """某份产出的派生物目录（绝对路径）。"""
    return derived_root() / str(output.project_id) / str(output.pk)


def relative_path_for(output, kind: str) -> str:
    """某份产出某个种类的派生物路径（相对 ``MEDIA_ROOT``）。"""
    filename = KIND_FILENAMES.get(kind) or _raise_unknown_kind(kind)
    return f"{DERIVED_ROOT_NAME}/{output.project_id}/{output.pk}/{filename}"


def _raise_unknown_kind(kind: str) -> str:
    raise ValueError(
        f"未登记的派生物种类 {kind!r}；可用取值：{sorted(KIND_FILENAMES)}"
    )


def assert_not_business_artifact(path: Path) -> None:
    """确认目标路径不会覆盖 Skill 的业务主产物。"""
    if path.name in BUSINESS_ARTIFACT_FILENAMES:
        raise ValueError(
            f"派生物文件名 {path.name} 与 Skill 业务主产物重名，拒绝写入："
            "平台派生物必须与业务产物分开存放，否则一次平台重算会无声覆盖业务文件"
        )


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encode_payload(kind: str, payload: Any) -> bytes:
    """按种类编码：JSON 用稳定序列化口径，二进制原样。"""
    if KIND_TEXTUAL.get(kind, True):
        text = payload if isinstance(payload, str) else stable_json(payload)
        return text.encode("utf-8")
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    raise TypeError(f"{kind} 需要二进制内容，收到 {type(payload).__name__}")


def build_derived_contents(
    stage_result: Any,
    *,
    trace: Any = None,
    corpus: Any = None,
    output_id: str = "",
) -> dict[str, bytes]:
    """生成三份派生物内容（**不落盘、不登记**）。

    顺序有依赖：图谱必须先算完，摘要与报告才拿得到引用定位结果。
    见模块 docstring 里"先算图谱"那一段。
    """
    from .evidence_graph import build_evidence_graph
    from .quality_summary import build_quality_summary

    graph = build_evidence_graph(
        stage_result, trace=trace, corpus=corpus, output_id=output_id,
    )
    quality = build_quality_summary(stage_result, graph=graph, trace=trace)
    report = build_review_workbook(stage_result, graph=graph, quality=quality)

    return {
        KIND_EVIDENCE_GRAPH: encode_payload(KIND_EVIDENCE_GRAPH, graph),
        KIND_QUALITY_SUMMARY: encode_payload(KIND_QUALITY_SUMMARY, quality),
        KIND_REVIEW_REPORT: encode_payload(KIND_REVIEW_REPORT, report),
    }


def source_output_hash(payload: Any) -> str:
    """源产出哈希 = Skill 侧 ``stage_result.json`` 的 sha256。

    dict 与原始字节都接受：dict 走稳定序列化，字节直接哈希。**两种入参得到的
    哈希不同**是有意的——字节哈希证明"平台读到的就是 Skill 写的那份"，
    而对象哈希证明"语义上没变"，两者回答的是不同问题，不能混用。
    """
    if isinstance(payload, (bytes, bytearray)):
        return hash_bytes(bytes(payload))
    return hash_bytes(stable_json(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# 登记
# ---------------------------------------------------------------------------


def register(
    *,
    output,
    stage_result: Any,
    contents: dict[str, bytes] | None = None,
    source_hash: str = "",
    source_path: str = "",
    trace: Any = None,
    corpus: Any = None,
    actor=None,
    attempt=None,
) -> list[Any]:
    """落盘 + 登记，返回 ``StageDerivedArtifact`` 列表。

    幂等口径：``(output, kind)`` 唯一，重复登记**覆盖当前版本**而不是新增记录。
    内容一致时 ``content_hash`` 不变，页面据此判断"只是又跑了一次生成器"；
    内容变了（换了生成器版本、或源产出被替换）则哈希变化并追加到
    ``metadata['history']``，据此回答"结论为什么变了"。
    """
    from .models import GenerationOutput  # noqa: F401  （确保应用模型已注册）
    from .workflow_models import DERIVED_ARTIFACT_KINDS, StageDerivedArtifact

    known_kinds = {code for code, _ in DERIVED_ARTIFACT_KINDS}
    if contents is None:
        contents = build_derived_contents(
            stage_result, trace=trace, corpus=corpus, output_id=str(output.pk),
        )

    directory = output_directory(output)
    directory.mkdir(parents=True, exist_ok=True)

    level = str(getattr(stage_result, "level", "") or "")
    stage = str(getattr(stage_result, "stage", "") or "")
    workflow_id = str(
        ((output.metadata or {}).get("protocol") or {}).get("workflow_id", "") or ""
    )

    if attempt is None:
        # 反向关系是 ``stage_attempts``（一条产出可能被多次尝试指向，取最近一次）。
        candidate = output.stage_attempts.order_by("-created_at").first()
        attempt = candidate

    artifacts: list[Any] = []
    for kind, payload in contents.items():
        if kind not in known_kinds:
            # 未登记的种类直接跳过而不是静默写盘：写了一份没人认识的派生物，
            # 除了占空间没有任何用处。
            logger.warning("跳过未登记的派生物种类：%s", kind)
            continue
        target = directory / KIND_FILENAMES[kind]
        assert_not_business_artifact(target)
        target.write_bytes(payload)
        digest = hash_bytes(payload)

        existing = StageDerivedArtifact.objects.filter(output=output, kind=kind).first()
        artifact, _ = StageDerivedArtifact.objects.update_or_create(
            output=output,
            kind=kind,
            defaults={
                "project_id": output.project_id,
                "attempt": attempt,
                "workflow_id": workflow_id,
                "stage": stage,
                "filename": target.name,
                "path": str(target.relative_to(Path(settings.MEDIA_ROOT))),
                "content_hash": digest,
                "byte_size": len(payload),
                "generator_version": KIND_GENERATOR_VERSIONS.get(kind, ""),
                "source_output_hash": source_hash,
                "source_path": source_path,
                "level": level,
                "created_by": actor,
                "metadata": _merge_metadata(
                    kind=kind, digest=digest, contents=contents,
                    source_hash=source_hash, existing=existing,
                ),
            },
        )
        artifacts.append(artifact)
    return artifacts


def _merge_metadata(
    *, kind: str, digest: str, contents: dict[str, bytes], source_hash: str, existing=None,
) -> dict:
    """把本轮生成结果与历史留下。历史只留摘要行，不留内容。

    历史**保留旧版本**再追加本轮：只留当前一版的话，"换了判定逻辑之后结论为什么
    变了"就只剩当前值，问题本身消失了。同一条目重复追加会被去掉（幂等：
    又跑了一次同样的生成器不该在历史里留下两条一模一样的记录）。
    """
    metadata: dict[str, Any] = {"derived_version": DERIVED_ARTIFACT_VERSION}

    if kind == KIND_EVIDENCE_GRAPH:
        try:
            graph = json.loads(contents[KIND_EVIDENCE_GRAPH].decode("utf-8"))
            metadata["summary"] = graph.get("summary") or {}
        except (UnicodeDecodeError, json.JSONDecodeError):  # pragma: no cover - 防御
            metadata["summary"] = {}
    elif kind == KIND_QUALITY_SUMMARY:
        try:
            quality = json.loads(contents[KIND_QUALITY_SUMMARY].decode("utf-8"))
            metadata["gate"] = quality.get("gate") or {}
            metadata["warnings"] = quality.get("warnings") or []
        except (UnicodeDecodeError, json.JSONDecodeError):  # pragma: no cover - 防御
            metadata["gate"] = {}

    history = list(((existing.metadata if existing else {}) or {}).get("history") or [])
    entry = {
        "content_hash": digest,
        "generator_version": KIND_GENERATOR_VERSIONS.get(kind, ""),
        "source_output_hash": source_hash,
    }
    if not history or history[-1] != entry:
        history.append(entry)
    metadata["history"] = history[-HISTORY_LIMIT:]
    return metadata


def register_for_output(
    output,
    *,
    stage_result_payload: Any = None,
    source_path: str = "",
    source_hash: str = "",
    declared_level: str = "",
    trace: Any = None,
    corpus: Any = None,
    actor=None,
    attempt=None,
    best_effort: bool = True,
) -> list[Any]:
    """对外入口：给定产出（可带 Skill 的 ``stage_result.json``）生成并登记派生物。

    返回空列表的三种情况，含义**各不相同**，调用方要能区分：

    - 该阶段没有适配器 → 平台还不支持解读这个阶段（是"没做"，不是"失败"）；
    - 结构化协议校验不通过 → 业务产出仍在，只是无法被结构化解读（页面要显示
      "结构化协议失败"而不是"生成失败"）；
    - 落盘/登记抛错 → 观测面故障（``best_effort=True`` 时只记日志）。
    """
    from .stage_outputs import StageProtocolError, get_adapter, validate_stage_result

    adapter = get_adapter(getattr(output, "task_type", ""))
    if adapter is None:
        logger.info("阶段 %s 暂无派生物适配器，跳过派生物生成", getattr(output, "task_type", ""))
        return []

    if stage_result_payload is None:
        logger.info("产出 %s 未提供 stage_result 负载，跳过派生物生成", getattr(output, "pk", ""))
        return []

    try:
        validation = validate_stage_result(
            stage_result_payload,
            expected_stage=adapter.stage,
            declared_level=declared_level,
        )
        if not validation.ok:
            raise StageProtocolError(validation)
        stage_result = adapter.parse(
            validation.payload or {}, declared_level=declared_level,
        )
        contents = build_derived_contents(
            stage_result, trace=trace, corpus=corpus, output_id=str(output.pk),
        )
        return register(
            output=output,
            stage_result=stage_result,
            contents=contents,
            source_hash=source_hash or source_output_hash(stage_result_payload),
            source_path=source_path,
            trace=trace,
            corpus=corpus,
            actor=actor,
            attempt=attempt,
        )
    except StageProtocolError:
        # 结构化失败是**业务可解释**的结果，必须往上抛：页面要区分
        # "结构化协议失败"与"业务生成失败"（T05 验收项），吞掉它就没法区分。
        raise
    except Exception:
        if not best_effort:
            raise
        logger.exception("派生产物生成失败（业务产出不受影响）")
        return []


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def list_for_output(output):
    from .workflow_models import StageDerivedArtifact

    return StageDerivedArtifact.objects.filter(output=output).order_by("kind")


def absolute_path(artifact) -> Path:
    """把登记的相对路径还原成绝对路径。"""
    return Path(settings.MEDIA_ROOT) / artifact.path


def read_artifact(artifact) -> bytes:
    return absolute_path(artifact).read_bytes()


def output_hash_changed(artifact, output) -> bool:
    """登记的源产出哈希是否与产出当前携带的不一致。

    供页面提示"这份解读是基于旧产出算的"。用于替换语义（T12）：
    产出被新版本取代后，旧派生物应当显式显示为过期，而不是继续当作有效解读。
    """
    protocol = (output.metadata or {}).get("protocol") or {}
    current = str(protocol.get("stage_result_hash") or "")
    if not current:
        return False
    return bool(artifact.source_output_hash) and artifact.source_output_hash != current


__all__ = [
    "DERIVED_ARTIFACT_VERSION", "DERIVED_ROOT_NAME",
    "KIND_EVIDENCE_GRAPH", "KIND_QUALITY_SUMMARY", "KIND_REVIEW_REPORT",
    "KIND_FILENAMES", "KIND_GENERATOR_VERSIONS", "KIND_CONTENT_TYPES", "KIND_TEXTUAL",
    "BUSINESS_ARTIFACT_FILENAMES", "HISTORY_LIMIT",
    "derived_root", "output_directory", "relative_path_for", "assert_not_business_artifact",
    "hash_bytes", "encode_payload", "build_derived_contents", "source_output_hash",
    "register", "register_for_output",
    "list_for_output", "absolute_path", "read_artifact", "output_hash_changed",
]
