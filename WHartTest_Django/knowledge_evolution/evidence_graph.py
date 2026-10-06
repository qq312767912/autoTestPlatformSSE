"""执行证据图谱：把「这条结论凭什么」变成一张可查询的图。

平台不依赖模型自述，只依赖三样**可核验**的东西：知识原句能不能回到原文、结构化决策
记了什么、执行轨迹里实际调用了什么。图谱就是把这三样与业务项连起来。

两条硬约束：

1. **定位验证通过才叫 verified**。Skill 声称用了某句话，和平台能凭
   `document/version/chunk/quote` 把这句话找回来，是两件事。找不到就标 `invalid`，
   绝不让它冒充可信证据——否则后续所有归因都建立在假证据上。
2. **同一输入必须生成同一张图**。所以节点/边的 id 全部由稳定键推导，
   图里不含时间戳、不含随机 id、不含遍历顺序敏感的字段。
   确认报告、质量摘要也照此办理：同一份产出重复生成，内容必须逐字节一致。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

#: 生成器版本。派生产物上要带它，否则"换了生成逻辑之后同一份源产出给出两张不同的图"
#: 这件事无法追溯。
GRAPH_GENERATOR_VERSION = "evidence-graph/v1"

#: 图谱文件名（平台派生产物）。
GRAPH_FILENAME = "execution_evidence_graph.json"

#: 产出锚点：轨迹、决策、主产物统一挂在"这份产出"节点下。
#: 不把它们挂到某个方案项上——挂错了会让"改一条方案项"看起来像"改了整条链路"。
GRAPH_OUTPUT_ANCHOR = "<output>"

# ---------------------------------------------------------------------------
# 语义集合真值（模块级 —— 写成类属性会在 import 期炸，且 ``manage.py check`` 碰不到）
# ---------------------------------------------------------------------------

#: 引用状态，由弱到强。区分它们是 §5.3 的全部意义：
#: 「检索到了」不等于「Agent 说用了」，更不等于「平台验证通过」。
EVIDENCE_STATUSES: tuple[str, ...] = (
    "retrieved",     # 轨迹里检索到，但没有任何业务项引用它
    "declared",      # 业务项声称引用，但平台无从验证（没有原文索引）
    "verified",      # 定位验证通过
    "contradicted",  # 存在反证
    "invalid",       # 引用无法定位
)

NODE_TYPES: tuple[str, ...] = (
    "Requirement", "InputDocument", "KnowledgeDocument", "KnowledgeChunk",
    "EvidenceQuote", "RetrievalQuery", "AgentStep", "ToolCall", "DecisionRecord",
    "OutputClaim", "PlanItem", "Artifact", "HumanEdit", "Attribution",
    # T11：反查链的终点。人工改的是一条方案项，但要回答"哪条 Skill 规则让它这么写"，
    # 链必须能落到 Skill 包本身；停在 AgentStep 会把"模型跑偏"和"指令本身有歧义"
    # 混成同一件事——而只有后者能通过改 Skill 修掉。
    "SkillRule",
)

EDGE_TYPES: tuple[str, ...] = (
    "DERIVED_FROM", "RETRIEVED_FROM", "QUOTES", "SUPPORTS", "CONTRADICTS",
    "USED_BY", "GENERATED_BY", "PRODUCES", "MODIFIED_TO", "OMITTED_BY",
    "ATTRIBUTED_TO", "FIXED_BY", "VERIFIED_BY", "SUPERSEDES",
)


# ---------------------------------------------------------------------------
# 原文索引与定位验证
# ---------------------------------------------------------------------------


@dataclass
class ChunkLocation:
    text: str
    start_offset: int = 0


@dataclass
class CorpusIndex:
    """知识原文索引：``(document_id, version, chunk_id) -> 片段``。

    为什么不用 ``document_id`` 单独做键：同一份文档的不同版本里，
    `chunk-108` 很可能存在但内容不同。只按文档+片段查会把"依据的是旧版那句话"
    验证成通过——而"当时依据的是哪一版"恰恰是归因最需要回答的问题。
    """

    chunks: dict[tuple[str, str, str], ChunkLocation] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "CorpusIndex":
        index = cls()
        if not isinstance(payload, dict):
            return index
        for document in payload.get("documents") or []:
            if not isinstance(document, dict):
                continue
            document_id = str(document.get("document_id") or "")
            version = str(document.get("document_version") or "")
            for chunk in document.get("chunks") or []:
                if not isinstance(chunk, dict):
                    continue
                chunk_id = str(chunk.get("chunk_id") or "")
                if not (document_id and version and chunk_id):
                    continue
                try:
                    start = int(chunk.get("start_offset") or 0)
                except (TypeError, ValueError):
                    start = 0
                index.chunks[(document_id, version, chunk_id)] = ChunkLocation(
                    text=str(chunk.get("text") or ""), start_offset=start,
                )
        return index

    def __bool__(self) -> bool:
        return bool(self.chunks)

    def lookup(self, document_id: str, version: str, chunk_id: str) -> ChunkLocation | None:
        return self.chunks.get((str(document_id or ""), str(version or ""), str(chunk_id or "")))


def _quote_hash(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def verify_evidence(entry: dict, corpus: CorpusIndex | None) -> tuple[str, list[dict]]:
    """校验一条引用，返回 ``(状态, 问题清单)``。

    ``corpus`` 为空（运行时拿不到原文索引）时返回 ``declared`` 而**不是** ``invalid``：
    "验不了"和"验错了"必须分开——前者是平台能力缺口，后者是 Skill 产出的缺陷，
    两者对应的处置完全不同（补索引 / 退回去重写）。
    """
    issues: list[dict] = []
    document_id = str(entry.get("document_id") or "")
    version = str(entry.get("document_version") or "")
    chunk_id = str(entry.get("chunk_id") or "")
    quote = str(entry.get("quote") or "")

    if not corpus:
        return "declared", issues

    location = corpus.lookup(document_id, version, chunk_id)
    if location is None:
        issues.append({
            "code": "location_not_found",
            "message": f"索引里没有 {document_id}@{version}/{chunk_id}",
        })
        return "invalid", issues

    index_in_chunk = location.text.find(quote) if quote else -1
    if not quote or index_in_chunk < 0:
        issues.append({
            "code": "quote_not_in_chunk",
            "message": f"原句未出现在 {chunk_id} 中，引用可能已过期或被改写",
        })
        return "invalid", issues

    # offset 校验：片段内有起始偏移时，原句的绝对位置应当对得上。
    # 对不上通常意味着"引用的那句话没错，但定位信息是上一个版本留下的"。
    absolute_start = location.start_offset + index_in_chunk
    if entry.get("start_offset") is not None:
        try:
            declared_start = int(entry["start_offset"])
        except (TypeError, ValueError):
            declared_start = -1
        if declared_start != absolute_start:
            issues.append({
                "code": "offset_mismatch",
                "message": f"start_offset 不一致：声明 {declared_start}，实际 {absolute_start}",
            })
            return "invalid", issues

    declared_hash = str(entry.get("content_hash") or "").split(":", 1)[-1].strip().lower()
    if declared_hash and declared_hash != _quote_hash(quote):
        issues.append({
            "code": "content_hash_mismatch",
            "message": "content_hash 与 quote 内容不一致，原句可能已被替换",
        })
        return "invalid", issues

    return "verified", issues


# ---------------------------------------------------------------------------
# 轨迹归一化
# ---------------------------------------------------------------------------


def _span_field(span: Any, key: str, default: Any = None) -> Any:
    if isinstance(span, dict):
        return span.get(key, default)
    return getattr(span, key, default)


def _normalize_spans(trace: Any) -> list[Any]:
    """轨迹可以是 ``GenerationOutput.trace.spans``、Span 列表或纯 dict 列表。

    统一成列表而不是要求调用方先查库：图谱构建是纯函数，测试里不该被迫造数据库。
    """
    if trace is None:
        return []
    spans = getattr(trace, "spans", None)
    if spans is None:
        spans = trace
    if hasattr(spans, "all"):
        spans = spans.all()
    try:
        return list(spans)
    except TypeError:
        return []


def _decision_key(record: Any, position: int) -> str:
    """决策记录的稳定键。

    优先用记录自己的键（``id`` / ``key``）；没有就用内容哈希——
    用位置当键会让"在中间插了一条决策"导致后面所有节点的 id 全变，图谱 diff 全是噪音。
    """
    if isinstance(record, dict):
        explicit = str(record.get("id") or record.get("key") or "").strip()
        if explicit:
            return explicit
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    else:
        payload = json.dumps(str(record), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 图谱构建
# ---------------------------------------------------------------------------


class GraphBuilder:
    """按稳定键累积节点与边。

    节点用 ``dict`` 去重（同 id 后写覆盖），边用 ``set`` 去重后排序输出——
    这样"同一输入生成同一张图"是结构上保证的，而不是靠调用顺序碰巧一致。
    """

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: set[tuple[str, str, str]] = set()

    def node(self, node_type: str, key: str, **attributes: Any) -> str:
        node_id = f"{node_type.lower()}:{key}"
        payload = {"id": node_id, "type": node_type, "key": str(key)}
        payload.update(attributes)
        self.nodes[node_id] = payload
        return node_id

    def edge(self, source: str, target: str, edge_type: str) -> None:
        if source and target:
            self.edges.add((source, target, edge_type))

    def build(self) -> dict:
        return {
            "nodes": [self.nodes[key] for key in sorted(self.nodes)],
            "edges": [
                {"source": source, "target": target, "type": edge_type}
                for source, target, edge_type in sorted(self.edges)
            ],
        }


def build_evidence_graph(
    stage_result: Any,
    *,
    trace: Any = None,
    corpus: CorpusIndex | dict | None = None,
    output_id: str = "",
    human_edits: list[dict] | None = None,
    skill: dict | None = None,
) -> dict:
    """生成 ``execution_evidence_graph.json`` 的内容。

    纯函数：不读数据库、不写文件、不含时间戳。落盘与登记由
    ``derived_artifacts`` 负责。

    ``human_edits`` / ``skill`` 是**可选**的（T11）：不传时输出与改造前逐字节一致。
    刻意不复用"有没有人工修改"来分支图结构——图是产出的快照，人工评审发生在
    产出之后，把它做成必需参数会让"先建图、后评审"这条真实顺序走不通。
    """
    if isinstance(corpus, dict):
        corpus = CorpusIndex.from_payload(corpus)
    index: CorpusIndex | None = corpus if isinstance(corpus, CorpusIndex) else None

    builder = GraphBuilder()
    evidence_status: dict[str, dict] = {}

    # -- 需求 --------------------------------------------------------------
    for position, requirement in enumerate(getattr(stage_result, "requirements", []) or []):
        if not isinstance(requirement, dict):
            continue
        requirement_id = str(requirement.get("id") or f"REQ#{position}")
        builder.node(
            "Requirement", requirement_id,
            title=str(requirement.get("title") or ""),
            source=str(requirement.get("source") or ""),
        )

    # -- 证据：先定位，再挂需求 -------------------------------------------
    spans = _normalize_spans(trace)
    retrieved_ids: set[str] = set()
    for span in spans:
        for entry in _span_field(span, "evidence", []) or []:
            if isinstance(entry, dict) and entry.get("id"):
                retrieved_ids.add(str(entry["id"]))
            elif isinstance(entry, str) and entry:
                retrieved_ids.add(entry)

    counterevidence_ids = {
        str(item.get("evidence_id") or item.get("id") or "")
        for item in (getattr(stage_result, "counterevidence", []) or [])
        if isinstance(item, dict) and item.get("conflicts")
    }

    for position, entry in enumerate(getattr(stage_result, "evidence", []) or []):
        if not isinstance(entry, dict):
            continue
        evidence_id = str(entry.get("id") or f"EV#{position}")
        status, issues = verify_evidence(entry, index)
        if status == "verified" and evidence_id in counterevidence_ids:
            status = "contradicted"

        evidence_status[evidence_id] = {
            "status": status,
            "document_id": str(entry.get("document_id") or ""),
            "document_version": str(entry.get("document_version") or ""),
            "chunk_id": str(entry.get("chunk_id") or ""),
            "issues": issues,
        }

        document_key = f"{entry.get('document_id')}@{entry.get('document_version')}"
        document_node = builder.node(
            "KnowledgeDocument", document_key,
            document_id=str(entry.get("document_id") or ""),
            document_version=str(entry.get("document_version") or ""),
        )
        chunk_node = builder.node(
            "KnowledgeChunk", str(entry.get("chunk_id") or ""),
            chunk_id=str(entry.get("chunk_id") or ""),
            document=document_key,
        )
        quote_node = builder.node(
            "EvidenceQuote", evidence_id,
            quote=str(entry.get("quote") or ""),
            status=status,
            content_hash=str(entry.get("content_hash") or ""),
        )
        builder.edge(quote_node, chunk_node, "QUOTES")
        builder.edge(chunk_node, document_node, "DERIVED_FROM")

    # 检索到但没有任何业务项引用：状态是 retrieved 而不是 verified——
    # 「检索到」只是"它在那"，不构成"这条结论用了它"。
    for evidence_id in sorted(retrieved_ids - set(evidence_status)):
        builder.node("EvidenceQuote", evidence_id, quote="", status="retrieved")
        evidence_status[evidence_id] = {"status": "retrieved", "issues": []}

    # -- 方案项、产出声明与决策 -------------------------------------------
    for position, item in enumerate(getattr(stage_result, "items", []) or []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or f"TP#{position}")
        item_node = builder.node(
            "PlanItem", item_id,
            title=str(item.get("title") or ""),
            module=str(item.get("module") or ""),
            scenario_type=str(item.get("scenario_type") or ""),
            priority=str(item.get("priority") or ""),
        )
        for requirement_id in item.get("requirement_ids") or []:
            requirement_id = str(requirement_id or "")
            if requirement_id:
                builder.edge(item_node, f"requirement:{requirement_id}", "DERIVED_FROM")
        for evidence_id in item.get("evidence_ids") or []:
            evidence_id = str(evidence_id or "")
            if evidence_id:
                builder.edge(item_node, f"evidencequote:{evidence_id}", "SUPPORTS")
        for decision_position, record in enumerate(item.get("decisions") or []):
            decision_node = builder.node(
                "DecisionRecord", _decision_key(record, decision_position),
                owner=item_id, scope="item",
            )
            builder.edge(item_node, decision_node, "USED_BY")

    # -- 产出锚点：轨迹、决策与主产物统一挂到"这份产出"下 -----------------
    #
    # 锚点必须先建出来再被引用。只写边不建节点，图谱里就会出现一批指向
    # 不存在节点的边——列表能渲染、图渲染器会静默丢边，"这份产出是怎么来的"
    # 这条链在界面上直接断掉，而数据层看不出任何异常。
    output_anchor = builder.node(
        "OutputClaim", GRAPH_OUTPUT_ANCHOR,
        output_id=str(output_id or ""), stage=str(getattr(stage_result, "stage", "") or ""),
    )

    for decision_position, record in enumerate(getattr(stage_result, "decision_summary", []) or []):
        decision_node = builder.node(
            "DecisionRecord", _decision_key(record, decision_position),
            owner="", scope="stage",
        )
        builder.edge(decision_node, output_anchor, "DERIVED_FROM")

    # -- 主产物与产出绑定 ---------------------------------------------------
    for position, artifact in enumerate(getattr(stage_result, "primary_artifacts", []) or []):
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or f"artifact#{position}")
        artifact_node = builder.node(
            "Artifact", name, name=name, kind=str(artifact.get("kind") or ""),
        )
        builder.edge(output_anchor, artifact_node, "PRODUCES")

    # -- 轨迹：检索与工具调用 ---------------------------------------------
    step_nodes: list[str] = []
    for position, span in enumerate(spans):
        span_key = str(_span_field(span, "id", "") or f"span#{position}")
        step_type = str(_span_field(span, "step_type", "") or "")
        if step_type == "tool":
            node_type, edge_type = "ToolCall", "GENERATED_BY"
            key = f"{_span_field(span, 'tool_name', '') or 'tool'}@{span_key}"
        else:
            node_type, edge_type = "AgentStep", "GENERATED_BY"
            key = span_key
        node = builder.node(
            node_type, key,
            step_type=step_type,
            status=str(_span_field(span, "status", "") or ""),
            latency_ms=int(_span_field(span, "latency_ms", 0) or 0),
            error_type=str(_span_field(span, "error_type", "") or ""),
        )
        builder.edge(node, output_anchor, edge_type)
        step_nodes.append(node)
        for entry in _span_field(span, "evidence", []) or []:
            evidence_id = str(entry.get("id") if isinstance(entry, dict) else entry or "")
            if evidence_id:
                builder.edge(node, f"evidencequote:{evidence_id}", "RETRIEVED_FROM")

    # -- Skill 规则节点（T11，可选）---------------------------------------
    #
    # 链的终点。没有它，反查只能停在 AgentStep，"这条方案项是模型写歪了"
    # 与"Skill 指令本身就有歧义"无法区分，而只有后者能通过改 Skill 修掉。
    skill_node = ""
    if skill:
        skill_node = builder.node(
            "SkillRule",
            str(skill.get("skill_version_id") or skill.get("skill_name") or "unversioned"),
            skill_name=str(skill.get("skill_name") or ""),
            skill_version=str(skill.get("skill_version") or ""),
            package_sha256=str(skill.get("package_sha256") or ""),
        )
        # 每个执行节点都归到这条规则下：反查链的最后两跳
        # （AgentStep / ToolCall → SkillRule）靠它接上。
        for step_node in step_nodes:
            builder.edge(step_node, skill_node, "GENERATED_BY")

    # -- 人工修改（T11，可选）---------------------------------------------
    #
    # 每个方案项一条 HumanEdit 节点，挂在它所改的那条业务项上（MODIFIED_TO），
    # 并直连 Skill 规则（DERIVED_FROM）：评审要回答的是"改了哪一条、指向哪份指令"，
    # 中间那条长链由 ``reverse_trace`` 按需展开，不在这里预生成。
    for edit in human_edits or []:
        if not isinstance(edit, dict):
            continue
        item_id = str(edit.get("item_id") or "")
        if not item_id:
            continue
        edit_node = builder.node(
            "HumanEdit", item_id,
            verdict=str(edit.get("verdict") or ""),
            edit_category=str(edit.get("edit_category") or ""),
            edit_content=str(edit.get("edit_content") or ""),
            field_count=len(edit.get("field_diffs") or []),
        )
        builder.edge(edit_node, f"planitem:{item_id}", "MODIFIED_TO")
        if skill_node:
            builder.edge(edit_node, skill_node, "DERIVED_FROM")

    graph = builder.build()
    graph["generator_version"] = GRAPH_GENERATOR_VERSION
    graph["stage_result_version"] = str(getattr(stage_result, "level", ""))
    graph["output_id"] = str(output_id or "")
    graph["evidence_status"] = {key: evidence_status[key] for key in sorted(evidence_status)}
    graph["summary"] = _graph_summary(graph)
    return graph


#: 反查链的**语义顺序真值**（设计 §8.2）。
#:
#: 写成模块级常量而不是散在遍历代码里，是因为这条顺序同时被三处使用：
#: 遍历要按它排、页面要按它展示、归因要按它决定"停在哪一层"。
#: 一旦分叉，页面显的链和归因用的链就不是同一条。
REVERSE_CHAIN_ORDER: tuple[str, ...] = (
    "HumanEdit", "PlanItem", "DecisionRecord", "EvidenceQuote",
    "KnowledgeChunk", "Requirement", "AgentStep", "ToolCall", "SkillRule",
)


def reverse_trace(graph: dict, *, item_id: str, depth_limit: int = 6) -> dict:
    """从人工修改出发，沿图谱反查到 Skill 规则（T11 / §8.2）。

    返回 ``{"item_id", "chain", "missing", "complete"}``：

    - ``chain``：按 ``REVERSE_CHAIN_ORDER`` 排序的节点，每项带 ``via``
      （从上一层怎么走过来的）。
    - ``missing``：链上**没找到**的节点类型。非空不等于失败 —— 旧产出的图谱里
      本来就没有 SkillRule 或 HumanEdit，页面要能把"断在哪"显示出来，
      而不是画一条看起来完整的链。
    - ``complete``：``missing`` 为空且至少含一个业务项节点。

    为什么按类型排序而不是按遍历顺序：图谱里的边是**无向可达**的，
    遍历顺序取决于边的插入顺序，同一份数据换一次插入顺序就会给出不同的链，
    而人在页面上读的是"从修改到规则"这条固定叙事。
    """
    nodes = {node["id"]: node for node in (graph.get("nodes") or []) if isinstance(node, dict)}
    if not nodes:
        return {"item_id": str(item_id), "chain": [], "missing": list(REVERSE_CHAIN_ORDER), "complete": False}

    root = f"humanedit:{item_id}"
    if root not in nodes:
        # 没有人工修改节点时退到业务项：评审可能删掉了整条方案项，
        # 此时"改了什么"无从谈起，但"这条是从哪来的"仍然要能查。
        root = f"planitem:{item_id}"
    if root not in nodes:
        return {"item_id": str(item_id), "chain": [], "missing": list(REVERSE_CHAIN_ORDER), "complete": False}

    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source, target, edge_type = edge.get("source"), edge.get("target"), edge.get("type") or ""
        if source and target:
            adjacency.setdefault(source, []).append((target, edge_type))
            adjacency.setdefault(target, []).append((source, edge_type))

    # 广度优先收集可达节点，并记下每一类的"第一次是怎么走到的"。
    reached: dict[str, dict] = {}
    queue: list[tuple[str, int, str, str]] = [(root, 0, "", "")]
    seen = {root}
    while queue:
        node_id, depth, via, from_id = queue.pop(0)
        node = nodes.get(node_id)
        if node is None:
            continue
        node_type = str(node.get("type") or "")
        reached.setdefault(node_type, {
            "id": node_id, "type": node_type, "key": node.get("key", ""),
            "via": via, "from": from_id, "depth": depth,
            "attributes": {
                key: value for key, value in node.items()
                if key not in {"id", "type", "key"}
            },
        })
        if depth >= depth_limit:
            continue
        for neighbour, edge_type in adjacency.get(node_id, []):
            if neighbour in seen:
                continue
            seen.add(neighbour)
            queue.append((neighbour, depth + 1, edge_type, node_id))

    chain = [reached[node_type] for node_type in REVERSE_CHAIN_ORDER if node_type in reached]
    missing = [node_type for node_type in REVERSE_CHAIN_ORDER if node_type not in reached]
    return {
        "item_id": str(item_id),
        "chain": chain,
        "missing": missing,
        "complete": not missing and "PlanItem" in reached,
    }


def _graph_summary(graph: dict) -> dict:
    counts: dict[str, int] = {}
    for node in graph["nodes"]:
        counts[node["type"]] = counts.get(node["type"], 0) + 1
    statuses: dict[str, int] = {}
    for entry in graph["evidence_status"].values():
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    return {
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "node_types": {key: counts[key] for key in sorted(counts)},
        "evidence_status": {key: statuses[key] for key in sorted(statuses)},
    }


def stable_json(payload: Any) -> str:
    """派生产物的统一序列化口径。

    排序 + 固定分隔符：否则同一份数据换一次 Python 版本、换一次 dict 插入顺序
    就会给出不同字节，而"重复生成内容一致"正是这一层的验收条件。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def collect_evidence_status(graph: dict) -> dict[str, str]:
    """从图谱里取出 ``证据 id -> 状态``，供质量摘要与确认报告复用。"""
    return {
        evidence_id: str(entry.get("status") or "")
        for evidence_id, entry in (graph.get("evidence_status") or {}).items()
    }


def iter_evidence_issues(graph: dict) -> Iterable[dict]:
    """逐条吐出定位失败/反证的引用，供「异常证据」Sheet 使用。"""
    for evidence_id, entry in sorted((graph.get("evidence_status") or {}).items()):
        status = str(entry.get("status") or "")
        if status in ("verified", ""):
            continue
        yield {
            "evidence_id": evidence_id,
            "status": status,
            "document_id": entry.get("document_id", ""),
            "document_version": entry.get("document_version", ""),
            "chunk_id": entry.get("chunk_id", ""),
            "issues": entry.get("issues") or [],
        }
