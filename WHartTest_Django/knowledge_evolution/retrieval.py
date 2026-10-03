"""任务 9：可版本化检索编排器。

设计 §6：多路召回（Dense/Sparse/Structured/Graph/Historical）→ 加权 RRF → Reranker → MMR → 证据包。
证据包带可验证 citation、冲突标注、延迟/Token 预算控制、任务类型路由。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from django.db.models import Q

from knowledge.models import DocumentChunk

from .graph import GraphSource, PostgreSQLGraphSource
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import KnowledgeConflict
from .models import RetrievalTrace
from .retrieval_models import RetrievalPolicy


@dataclass
class RetrievalRequest:
    project_id: int
    query: str
    task_type: str = "knowledge_query"
    principal: Any = None
    selected_sources: Optional[List[str]] = None
    required_levels: Optional[List[str]] = None
    time_budget_ms: int = 3000
    token_budget: int = 4000
    graph_policy: Optional[str] = None  # always / conditional / never
    top_k: int = 10
    policy: Optional[RetrievalPolicy] = None
    policy_overrides: Optional[dict] = None
    query_rewrite: Optional[str] = None


@dataclass
class RetrievalCandidate:
    id: str
    content: str
    source: str
    source_type: str
    score: float = 0.0
    original_rank: int = 0
    metadata: dict = field(default_factory=dict)
    citation: dict = field(default_factory=dict)
    asset_id: Optional[str] = None
    version_id: Optional[str] = None
    chunk_id: Optional[str] = None
    external_id: Optional[str] = None
    conflicts: List[dict] = field(default_factory=list)

    def to_evidence(self) -> dict:
        return {
            "id": self.id,
            "citation_id": self.citation.get("citation_id") or self.id,
            "content": self.content,
            "source": self.source,
            "source_type": self.source_type,
            "score": round(self.score, 6),
            "metadata": self.metadata,
            "citation": self.citation,
            "conflicts": self.conflicts,
        }


# ------------------------------------------------------------------ 召回源

class BaseRetriever:
    name = "base"

    def search(
        self, request: RetrievalRequest, query: str, config: dict
    ) -> List[RetrievalCandidate]:
        raise NotImplementedError


class DenseRetriever(BaseRetriever):
    """稠密向量召回。默认委托外部函数；未注入时返回空列表（避免强依赖 Qdrant）。"""

    name = "dense"

    def __init__(self, search_fn: Optional[Callable] = None):
        self.search_fn = search_fn

    def search(self, request, query, config):
        if not self.search_fn:
            return []
        cfg = config.get("sources", {}).get("dense", {})
        k = cfg.get("k", 40)
        results = self.search_fn(query, request.project_id, k=k)
        return self._to_candidates(results, "dense")

    @staticmethod
    def _to_candidates(results, source_type):
        candidates = []
        for rank, r in enumerate(results):
            payload = r.get("metadata") or r.get("payload") or {}
            candidates.append(
                RetrievalCandidate(
                    id=payload.get("vector_id") or f"{source_type}:{rank}",
                    content=r.get("content", ""),
                    source=payload.get("source", source_type),
                    source_type=source_type,
                    score=float(r.get("similarity_score", 0)),
                    original_rank=rank,
                    metadata=payload,
                    citation={
                        "citation_id": payload.get("vector_id") or f"{source_type}:{rank}",
                        "source": payload.get("source", source_type),
                        "location": payload.get("location", {}),
                        "freshness": payload.get("freshness", "unknown"),
                        "access_scope": payload.get("access_scope", "project"),
                    },
                    chunk_id=str(payload.get("chunk_id")) if payload.get("chunk_id") else None,
                )
            )
        return candidates


class SparseRetriever(DenseRetriever):
    """稀疏向量召回；结构与稠密相同，复用转换逻辑。"""

    name = "sparse"


class GraphRetriever(BaseRetriever):
    """文档图谱召回：先搜节点，再按关系扩展。"""

    name = "graph"

    def __init__(self, graph_source: Optional[GraphSource] = None):
        self.graph = graph_source or PostgreSQLGraphSource()

    def search(self, request, query, config):
        cfg = config.get("sources", {}).get("graph", {})
        k = cfg.get("k", 80)
        project_id = request.project_id

        # 1. 命中节点
        nodes = self.graph.search_nodes(
            project_id=project_id, keyword=query, limit=k
        )
        center_ids = [n["external_id"] for n in nodes]

        # 2. 关系扩展 1 跳
        subgraph = self.graph.subgraph(
            project_id=project_id,
            center_external_ids=center_ids,
            depth=1,
            limit=k,
        )
        seen = set()
        candidates = []
        for node in subgraph["nodes"]:
            eid = node["external_id"]
            if eid in seen:
                continue
            seen.add(eid)
            # 只把 chunk / fact / rule / concept / asset / version 当证据
            if node["node_type"] not in {
                "chunk", "fact", "rule", "concept", "asset", "version", "evidence",
            }:
                continue
            candidates.append(
                RetrievalCandidate(
                    id=eid,
                    content=node.get("name", "") or node.get("properties", {}).get("content_preview", ""),
                    source=f"graph:{node['node_type']}",
                    source_type="graph",
                    score=0.5,
                    metadata=node.get("properties", {}),
                    citation={
                        "citation_id": eid,
                        "source": node.get("properties", {}).get("source", "document_graph"),
                        "location": node.get("properties", {}).get("location", {}),
                        "freshness": "projected",
                        "access_scope": "project",
                    },
                    external_id=eid,
                    asset_id=str(node.get("asset_id")) if node.get("asset_id") else None,
                    version_id=str(node.get("version_id")) if node.get("version_id") else None,
                )
            )
        return candidates


class StructuredRetriever(BaseRetriever):
    """结构化召回：基于 DocumentChunk 的 section / block_type / 文本匹配。"""

    name = "structured"

    def search(self, request, query, config):
        cfg = config.get("sources", {}).get("structured", {})
        k = cfg.get("k", 20)
        q = Q(document__knowledge_base__project_id=request.project_id)
        for word in query.split():
            q &= Q(content__icontains=word)
        chunks = DocumentChunk.objects.filter(q).select_related("document", "document__knowledge_base")[:k]
        candidates = []
        for rank, chunk in enumerate(chunks):
            candidates.append(
                RetrievalCandidate(
                    id=str(chunk.id),
                    content=chunk.content,
                    source=f"{chunk.document.title} ({chunk.document.knowledge_base.name})",
                    source_type="structured",
                    score=0.4,
                    original_rank=rank,
                    metadata={
                        "block_type": chunk.block_type,
                        "section_title": chunk.section_title,
                        "location": chunk.location,
                    },
                    citation={
                        "citation_id": str(chunk.id),
                        "source": chunk.document.title,
                        "location": {**chunk.location, "section": chunk.section_title},
                        "freshness": "indexed",
                        "access_scope": "project",
                    },
                    chunk_id=str(chunk.id),
                )
            )
        return candidates


class HistoricalRetriever(BaseRetriever):
    """历史高质量轨迹召回：复用过去成功检索结果。"""

    name = "historical"

    def search(self, request, query, config):
        cfg = config.get("sources", {}).get("historical", {})
        k = cfg.get("k", 10)
        traces = RetrievalTrace.objects.filter(
            project_id=request.project_id,
            query__icontains=query,
            status="completed",
        ).order_by("-created_at")[:k]
        candidates = []
        for rank, trace in enumerate(traces):
            for i, cand in enumerate(trace.candidates[:3]):
                content = cand.get("content", "")
                if not content:
                    continue
                cid = f"hist:{trace.id}:{i}"
                candidates.append(
                    RetrievalCandidate(
                        id=cid,
                        content=content,
                        source="historical_trace",
                        source_type="historical",
                        score=0.3,
                        original_rank=rank * 3 + i,
                        metadata={"trace_id": str(trace.id), "task_type": trace.task_type},
                        citation={
                            "citation_id": cid,
                            "source": "historical_trace",
                            "location": {"trace_id": str(trace.id)},
                            "freshness": "historical",
                            "access_scope": "project",
                        },
                    )
                )
        return candidates


# ------------------------------------------------------------------ 编排器

class RetrievalOrchestrator:
    """可版本化检索编排器（tasks.md 任务 9）。"""

    def __init__(
        self,
        graph_source: Optional[GraphSource] = None,
        retrievers: Optional[Dict[str, BaseRetriever]] = None,
    ):
        self.graph = graph_source or PostgreSQLGraphSource()
        self.retrievers = retrievers or {
            "dense": DenseRetriever(),
            "sparse": SparseRetriever(),
            "graph": GraphRetriever(self.graph),
            "structured": StructuredRetriever(),
            "historical": HistoricalRetriever(),
        }

    @staticmethod
    def _resolve_config(request: RetrievalRequest) -> dict:
        if request.policy:
            return request.policy.resolve_config(request.policy_overrides or {})
        policy = RetrievalPolicy.get_default(request.project_id)
        if policy:
            return policy.resolve_config(request.policy_overrides or {})
        return RetrievalPolicy(name="default", project_id=request.project_id).resolve_config(
            request.policy_overrides or {}
        )

    @staticmethod
    def _graph_decision(
        request: RetrievalRequest, config: dict, direct_scores: List[float],
    ) -> tuple[bool, str, dict]:
        """图谱通道要不要跑、**不跑的首要原因**、以及附带的判断依据。

        为什么要把"原因"也返回：原先无论因为什么被摘掉，状态里都写
        ``direct_recall_confident``。于是一个"该任务类型不在图谱白名单里"的配置问题，
        在溯源里表现为"直连召回已经很确定了" —— 顺着这句话排查，方向必然跑到
        召回质量上去，而真正要改的是检索策略里那一行白名单。

        ``conditional`` 下的两级判断是**有顺序**的，原因要跟着顺序走：

        1. 任务类型在白名单里 → 必用图谱，不受直连分数影响（这是四阶段的保障）。
        2. 不在白名单里 → 图谱退化为"直连召回信心不足时补一次"。
           此时**首要原因是"该任务类型不适用"**，因为如果它在白名单里，
           这次就不会被跳过 —— 这才是可操作的那一条。
           直连分数高只作为附带依据（``detail["direct_recall_confident"]``）报出来，
           两者都保留，但主因不能被次因盖住。
        """
        policy = request.graph_policy or config.get("graph_policy", "conditional")
        if policy == "never":
            return False, "policy_never", {}
        if policy == "always":
            return True, "", {}
        if request.task_type in config.get("graph_task_types", []):
            return True, "", {}
        confident = bool(direct_scores) and max(direct_scores) >= 0.6
        if confident:
            return False, "not_applicable_task_type", {"direct_recall_confident": True}
        return True, "", {}

    @staticmethod
    def _should_use_graph(request: RetrievalRequest, config: dict, direct_scores: List[float]) -> bool:
        """兼容入口：只要结论。原因见 :meth:`_graph_decision`。"""
        return RetrievalOrchestrator._graph_decision(request, config, direct_scores)[0]

    def _collect_candidates(
        self, request: RetrievalRequest, config: dict, budget: dict
    ) -> tuple[Dict[str, List[RetrievalCandidate]], Dict[str, dict]]:
        """按源并发召回；超预算时跳过可选源。

        返回值多带一个 ``status``：每个候选源的**实际执行结果**。之所以必须记下来 ——
        图谱通道会在"直连召回已经很确定"时被主动摘掉（见 ``_should_use_graph``），
        而摘掉之后产出里看不出任何异常。事后想回答"这条产出为什么没有溯源"，
        没有这份状态就只能靠猜。状态里带 ``reason``，把"没跑"与"跑了没命中"分开。
        """
        query = request.query_rewrite or request.query
        source_cfg = config.get("sources", {})
        per_source: Dict[str, List[RetrievalCandidate]] = {}
        status: Dict[str, dict] = {}
        direct_scores: List[float] = []
        start = time.perf_counter()

        ordered_sources = [
            ("dense", source_cfg.get("dense", {}), True),
            ("sparse", source_cfg.get("sparse", {}), True),
            ("structured", source_cfg.get("structured", {}), True),
            ("graph", source_cfg.get("graph", {}), False),
            ("historical", source_cfg.get("historical", {}), False),
        ]

        for name, cfg, mandatory in ordered_sources:
            if request.selected_sources and name not in request.selected_sources:
                status[name] = {"enabled": False, "reason": "not_selected"}
                continue
            if not cfg.get("enabled", name in ("dense", "sparse", "structured")):
                status[name] = {"enabled": False, "reason": "disabled_by_config"}
                continue
            elapsed_ms = (time.perf_counter() - start) * 1000
            if not mandatory and elapsed_ms > budget["time_ms"] * 0.6:
                status[name] = {"enabled": False, "reason": "budget_skipped"}
                continue
            retriever = self.retrievers.get(name)
            if not retriever:
                status[name] = {"enabled": False, "reason": "no_retriever"}
                continue
            try:
                candidates = retriever.search(request, query, config)
                per_source[name] = candidates
                status[name] = {"enabled": True, "hits": len(candidates)}
                if name in ("dense", "sparse"):
                    direct_scores.extend([c.score for c in candidates[:5]])
            except Exception:
                per_source[name] = []
                status[name] = {"enabled": True, "hits": 0, "error": "retriever_failed"}

        # graph 路由决策。被摘掉时记下**原因与当时的策略**：
        # 只写 enabled=False 会让人误以为图谱被全局关停，而实际是这一次的路由结论；
        # 原因必须区分"策略关停"与"任务类型不适用" ——
        # 它们指向两个完全不同的排查方向（改策略开关 vs 改白名单）。
        if "graph" in per_source:
            use_graph, skip_reason, detail = self._graph_decision(
                request, config, direct_scores,
            )
            if not use_graph:
                per_source.pop("graph", None)
                status["graph"] = {
                    "enabled": False,
                    "reason": skip_reason or "not_applicable_task_type",
                    "policy": request.graph_policy or config.get("graph_policy", "conditional"),
                    **detail,
                }

        return per_source, status

    @staticmethod
    def _weighted_rrf(
        per_source: Dict[str, List[RetrievalCandidate]],
        weights: Dict[str, float],
        rrf_k: int,
    ) -> List[RetrievalCandidate]:
        """按源权重做 RRF 融合。候选去重以 id 为准。"""
        scores: Dict[str, float] = {}
        seen_candidates: Dict[str, RetrievalCandidate] = {}
        for source, candidates in per_source.items():
            weight = weights.get(source, 0.2)
            if weight <= 0:
                continue
            for rank, cand in enumerate(candidates):
                key = cand.id
                seen_candidates[key] = cand
                scores[key] = scores.get(key, 0.0) + weight / (rrf_k + rank + 1)
        # 填充 score
        for key, cand in seen_candidates.items():
            cand.score = scores.get(key, 0.0)
        return sorted(seen_candidates.values(), key=lambda c: c.score, reverse=True)

    @staticmethod
    def _apply_rerank(
        candidates: List[RetrievalCandidate],
        query: str,
        config: dict,
    ) -> List[RetrievalCandidate]:
        rerank_cfg = config.get("rerank", {})
        if not rerank_cfg.get("enabled"):
            return candidates
        # 未配置外部 reranker 时跳过
        return candidates

    @staticmethod
    def _apply_mmr(candidates: List[RetrievalCandidate], config: dict) -> List[RetrievalCandidate]:
        mmr_cfg = config.get("mmr", {})
        if not mmr_cfg.get("enabled", True):
            return candidates[: mmr_cfg.get("final_k", 10)]
        final_k = mmr_cfg.get("final_k", 10)
        lambda_param = mmr_cfg.get("lambda_param", 0.7)

        def tokenize(text: str) -> Set[str]:
            """用字符 2-gram 处理中文，避免按空格切分导致无 overlap。"""
            chars = list(text.lower())
            if len(chars) <= 1:
                return set(chars)
            return set("".join(chars[i : i + 2]) for i in range(len(chars) - 1))

        def jaccard(a: Set[str], b: Set[str]) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        if len(candidates) <= final_k:
            return candidates
        token_sets = [tokenize(c.content) for c in candidates]
        selected = [0]
        selected_tokens = [token_sets[0]]
        while len(selected) < final_k:
            best_idx, best_score = -1, -float("inf")
            for i in range(len(candidates)):
                if i in selected:
                    continue
                relevance = candidates[i].score
                redundancy = max((jaccard(token_sets[i], st) for st in selected_tokens), default=0.0)
                mmr_score = lambda_param * relevance - (1 - lambda_param) * redundancy
                if mmr_score > best_score:
                    best_score, best_idx = mmr_score, i
            if best_idx < 0:
                break
            selected.append(best_idx)
            selected_tokens.append(token_sets[best_idx])
        return [candidates[i] for i in selected]

    @staticmethod
    def _detect_conflicts(
        candidates: List[RetrievalCandidate], project_id: int
    ) -> List[RetrievalCandidate]:
        """为候选标注开放冲突和图上的 CONTRADICTS 关系。"""
        asset_ids = set()
        version_ids = set()
        for c in candidates:
            if c.asset_id:
                asset_ids.add(c.asset_id)
            if c.version_id:
                version_ids.add(c.version_id)

        open_conflicts = KnowledgeConflict.objects.filter(
            project_id=project_id, state="open"
        ).filter(Q(left_asset_id__in=asset_ids) | Q(right_asset_id__in=asset_ids))

        conflict_map: Dict[str, List[dict]] = {}
        for conflict in open_conflicts:
            for aid in (str(conflict.left_asset_id), str(conflict.right_asset_id)):
                conflict_map.setdefault(aid, []).append({
                    "id": str(conflict.id),
                    "type": conflict.conflict_type,
                    "trust_penalty": conflict.trust_penalty,
                })

        # 图上的直接矛盾边
        node_ids = set()
        for c in candidates:
            if c.external_id:
                node_ids.add(c.external_id)
        contradict_edges = GraphEdge.objects.filter(
            from_node__external_id__in=node_ids,
            to_node__external_id__in=node_ids,
            relation="CONTRADICTS",
        ).select_related("from_node", "to_node")
        for edge in contradict_edges:
            from_id = edge.from_node.external_id
            to_id = edge.to_node.external_id
            conflict_map.setdefault(from_id, []).append({
                "id": str(edge.id),
                "type": "graph_contradicts",
                "with": to_id,
            })
            conflict_map.setdefault(to_id, []).append({
                "id": str(edge.id),
                "type": "graph_contradicts",
                "with": from_id,
            })

        for c in candidates:
            key = c.asset_id or c.version_id or c.external_id
            c.conflicts = conflict_map.get(key, [])
        return candidates

    def retrieve(self, request: RetrievalRequest) -> dict:
        started_at = time.perf_counter()
        config = self._resolve_config(request)
        budget = config.get("budget", {})
        top_k = request.top_k or config.get("mmr", {}).get("final_k", 10)

        per_source, channel_status = self._collect_candidates(request, config, budget)

        weights = {
            name: cfg.get("weight", 0.2)
            for name, cfg in config.get("sources", {}).items()
        }
        fused = self._weighted_rrf(
            per_source, weights, config.get("rrf_k", 60)
        )

        fused = self._apply_rerank(fused, request.query, config)
        fused = self._apply_mmr(fused, config)

        # 应用级别过滤
        if request.required_levels:
            fused = [
                c for c in fused
                if (c.metadata.get("level") or "L3") in request.required_levels
            ]

        fused = self._detect_conflicts(fused, request.project_id)

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        token_estimate = sum(len(c.content) for c in fused)

        # 若超预算且结果多，进一步截断
        if elapsed_ms > budget.get("time_ms", 3000) and len(fused) > top_k:
            fused = fused[:top_k]

        return {
            "query": request.query,
            "query_rewrite": request.query_rewrite,
            "task_type": request.task_type,
            "evidence_package": [c.to_evidence() for c in fused[:top_k]],
            "candidates_count": {k: len(v) for k, v in per_source.items()},
            # 通道实况：谁跑了、谁被跳过、为什么。落 RetrievalTrace.channels 的就是它，
            # 用来回答"这条产出为什么没有/有图谱溯源"。
            "channels": channel_status,
            "fusion_count": len(fused),
            "latency_ms": round(elapsed_ms, 3),
            "token_estimate": token_estimate,
            "budget": {"time_ms": budget.get("time_ms", 3000), "token": budget.get("token", 4000)},
            "policy": config,
        }
