"""任务 8：中性 GraphSource API 与文档图谱适配器。

设计约束（design.md §3 / tasks.md 任务 8）：
- 提供统一 GraphSource 接口，不直接依赖任何具体图数据库 SDK。
- 默认用 PostgreSQL 邻接表实现，不改动 CRG 代码图谱数据库。
- KnowledgeDocumentGraphAdapter 从 SourceSnapshot / DocumentChunk / KnowledgeVersion / KnowledgeCandidate 建图。
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional

from django.db import transaction

from knowledge.chunkers import StructuredTextChunker

from .graph_models import GraphEdge, GraphNode
from .knowledge_models import KnowledgeAsset, KnowledgeCandidate, KnowledgeVersion, SourceSnapshot


@dataclass
class GraphNodeSpec:
    node_type: str
    external_id: str
    name: str = ""
    properties: dict = field(default_factory=dict)
    snapshot_id: Optional[str] = None
    asset_id: Optional[str] = None
    version_id: Optional[str] = None
    candidate_id: Optional[str] = None
    chunk_id: Optional[str] = None


@dataclass
class GraphEdgeSpec:
    from_external_id: str
    to_external_id: str
    relation: str
    properties: dict = field(default_factory=dict)
    weight: float = 1.0


class GraphSource(ABC):
    """中性图数据源接口。"""

    @abstractmethod
    def clear_projection(self, projection_id: str) -> int:
        """删除某个 projection 下的全部节点和边，返回删除边数。"""
        raise NotImplementedError

    @abstractmethod
    def upsert_nodes(
        self, *, project_id, projection_id: Optional[str], nodes: Iterable[GraphNodeSpec]
    ) -> dict[str, GraphNode]:
        """批量写入/更新节点，返回 external_id -> GraphNode 映射。"""
        raise NotImplementedError

    @abstractmethod
    def add_edges(
        self, *, project_id, projection_id: Optional[str], edges: Iterable[GraphEdgeSpec]
    ) -> int:
        """批量写入边；节点不存在时静默跳过。返回写入边数。"""
        raise NotImplementedError

    @abstractmethod
    def neighbors(
        self, *, project_id, external_id: str, direction: str = "both",
        relations: Optional[List[str]] = None, depth: int = 1, limit: int = 100,
    ) -> dict:
        """返回以 external_id 为中心的邻域子图（nodes + edges）。"""
        raise NotImplementedError

    @abstractmethod
    def subgraph(
        self, *, project_id, center_external_ids: List[str], depth: int = 1, limit: int = 200,
    ) -> dict:
        """返回多中心子图。"""
        raise NotImplementedError

    @abstractmethod
    def search_nodes(
        self, *, project_id, node_type: Optional[str] = None, keyword: str = "", limit: int = 50,
    ) -> List[dict]:
        """按节点类型/名称关键字搜索节点。"""
        raise NotImplementedError


class PostgreSQLGraphSource(GraphSource):
    """基于 PostgreSQL 的 GraphSource 实现。

    不引入额外图数据库依赖，节点/边按项目隔离，可通过 IndexProjection 做版本化投影。
    """

    def __init__(self, batch_size: int = 500):
        self.batch_size = batch_size

    @staticmethod
    def _node_to_dict(node: GraphNode) -> dict:
        return {
            "id": str(node.pk),
            "external_id": node.external_id,
            "node_type": node.node_type,
            "name": node.name,
            "properties": node.properties,
            "snapshot_id": str(node.snapshot_id) if node.snapshot_id else None,
            "asset_id": str(node.asset_id) if node.asset_id else None,
            "version_id": str(node.version_id) if node.version_id else None,
            "candidate_id": str(node.candidate_id) if node.candidate_id else None,
            "chunk_id": str(node.chunk_id) if node.chunk_id else None,
        }

    @staticmethod
    def _edge_to_dict(edge: GraphEdge) -> dict:
        return {
            "id": str(edge.pk),
            "from_id": str(edge.from_node_id),
            "from_external_id": edge.from_node.external_id,
            "to_id": str(edge.to_node_id),
            "to_external_id": edge.to_node.external_id,
            "relation": edge.relation,
            "weight": edge.weight,
            "properties": edge.properties,
        }

    def clear_projection(self, projection_id: str) -> int:
        with transaction.atomic():
            edge_count = GraphEdge.objects.filter(projection_id=projection_id).count()
            GraphEdge.objects.filter(projection_id=projection_id).delete()
            GraphNode.objects.filter(projection_id=projection_id).delete()
        return edge_count

    def upsert_nodes(
        self, *, project_id, projection_id: Optional[str], nodes: Iterable[GraphNodeSpec]
    ) -> dict[str, GraphNode]:
        result = {}
        with transaction.atomic():
            for spec in nodes:
                node, _ = GraphNode.objects.update_or_create(
                    project_id=project_id,
                    external_id=spec.external_id,
                    defaults={
                        "projection_id": projection_id or None,
                        "node_type": spec.node_type,
                        "name": spec.name or spec.external_id,
                        "properties": spec.properties or {},
                        "snapshot_id": spec.snapshot_id or None,
                        "asset_id": spec.asset_id or None,
                        "version_id": spec.version_id or None,
                        "candidate_id": spec.candidate_id or None,
                        "chunk_id": spec.chunk_id or None,
                    },
                )
                result[spec.external_id] = node
        return result

    def add_edges(
        self, *, project_id, projection_id: Optional[str], edges: Iterable[GraphEdgeSpec]
    ) -> int:
        created = 0
        with transaction.atomic():
            external_ids = set()
            for e in edges:
                external_ids.add(e.from_external_id)
                external_ids.add(e.to_external_id)
            node_map = {
                n.external_id: n
                for n in GraphNode.objects.filter(
                    project_id=project_id, external_id__in=external_ids
                )
            }
            for e in edges:
                from_node = node_map.get(e.from_external_id)
                to_node = node_map.get(e.to_external_id)
                if not from_node or not to_node:
                    continue
                GraphEdge.objects.update_or_create(
                    project_id=project_id,
                    projection_id=projection_id or None,
                    from_node=from_node,
                    to_node=to_node,
                    relation=e.relation,
                    defaults={
                        "properties": e.properties or {},
                        "weight": e.weight,
                    },
                )
                created += 1
        return created

    def neighbors(
        self, *, project_id, external_id: str, direction: str = "both",
        relations: Optional[List[str]] = None, depth: int = 1, limit: int = 100,
    ) -> dict:
        start = GraphNode.objects.filter(project_id=project_id, external_id=external_id).first()
        if not start:
            return {"nodes": [], "edges": [], "center": external_id}

        visited_node_ids = {start.pk}
        current_ids = {start.pk}
        collected_edges = []
        for _ in range(max(1, depth)):
            if not current_ids:
                break
            if direction in ("out", "both"):
                q = GraphEdge.objects.filter(from_node_id__in=current_ids)
                if relations:
                    q = q.filter(relation__in=relations)
                out_edges = list(q.select_related("from_node", "to_node")[:limit])
            else:
                out_edges = []
            if direction in ("in", "both"):
                q = GraphEdge.objects.filter(to_node_id__in=current_ids)
                if relations:
                    q = q.filter(relation__in=relations)
                in_edges = list(q.select_related("from_node", "to_node")[:limit])
            else:
                in_edges = []
            next_ids = set()
            for edge in out_edges + in_edges:
                collected_edges.append(edge)
                next_ids.add(edge.from_node_id)
                next_ids.add(edge.to_node_id)
            current_ids = next_ids - visited_node_ids
            visited_node_ids |= next_ids
        nodes = GraphNode.objects.filter(pk__in=visited_node_ids)
        return {
            "center": external_id,
            "nodes": [self._node_to_dict(n) for n in nodes],
            "edges": [self._edge_to_dict(e) for e in collected_edges[:limit]],
        }

    def subgraph(
        self, *, project_id, center_external_ids: List[str], depth: int = 1, limit: int = 200,
    ) -> dict:
        centers = GraphNode.objects.filter(
            project_id=project_id, external_id__in=center_external_ids
        )
        if not centers.exists():
            return {"nodes": [], "edges": [], "centers": center_external_ids}
        result = {"centers": center_external_ids, "nodes": [], "edges": []}
        for center in centers:
            sub = self.neighbors(
                project_id=project_id,
                external_id=center.external_id,
                depth=depth,
                limit=limit,
            )
            result["nodes"].extend(sub["nodes"])
            result["edges"].extend(sub["edges"])
        # 去重
        result["nodes"] = list({n["id"]: n for n in result["nodes"]}.values())
        result["edges"] = list({e["id"]: e for e in result["edges"]}.values())
        return result

    def search_nodes(
        self, *, project_id, node_type: Optional[str] = None, keyword: str = "", limit: int = 50,
    ) -> List[dict]:
        q = GraphNode.objects.filter(project_id=project_id)
        if node_type:
            q = q.filter(node_type=node_type)
        if keyword:
            q = q.filter(name__icontains=keyword)
        return [self._node_to_dict(n) for n in q[:limit]]


class KnowledgeDocumentGraphAdapter:
    """把文档来源快照、结构分块、知识候选/资产/版本投影成文档图谱。

    设计 §4.3 节点类型：Document / Section / Chunk / Concept / Rule / Evidence / Asset / Version / Candidate。
    设计 §4.3 关系：CONTAINS / PART_OF / NEXT / MENTIONS / DEFINES / SUPPORTED_BY / DERIVED_FROM / SUPERSEDES。
    """

    def __init__(self, graph_source: Optional[GraphSource] = None, chunk_size: int = 1000):
        self.graph = graph_source or PostgreSQLGraphSource()
        self.chunker = StructuredTextChunker(chunk_size=chunk_size)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _hash(*parts: str) -> str:
        payload = "|".join(str(p) for p in parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _build_external_id(self, projection_id: Optional[str], local_id: str) -> str:
        if projection_id:
            return f"proj:{projection_id}:{local_id}"
        return local_id

    # ------------------------------------------------------------------ public

    def build_from_snapshot(
        self,
        snapshot: SourceSnapshot,
        projection_id: Optional[str] = None,
    ) -> dict:
        """基于 SourceSnapshot 的 parsed_text 建文档图。"""
        project_id = snapshot.project_id
        text = snapshot.parsed_text or snapshot.raw_content or ""
        chunks = self.chunker.split_text(text, document_type=snapshot.source_type)

        nodes: List[GraphNodeSpec] = []
        edges: List[GraphEdgeSpec] = []

        source_external = self._build_external_id(projection_id, f"source:{snapshot.id}")
        nodes.append(
            GraphNodeSpec(
                node_type="source",
                external_id=source_external,
                name=f"来源快照 {snapshot.source_type}:{snapshot.source_id}",
                properties={
                    "source_type": snapshot.source_type,
                    "source_id": snapshot.source_id,
                    "revision": snapshot.revision,
                    "authority": snapshot.authority,
                },
                snapshot_id=str(snapshot.pk),
            )
        )

        doc_external = self._build_external_id(projection_id, f"document:{snapshot.id}")
        nodes.append(
            GraphNodeSpec(
                node_type="document",
                external_id=doc_external,
                name=snapshot.location.get("title") or f"Document {snapshot.source_id}",
                properties={"source_id": snapshot.source_id, "revision": snapshot.revision},
                snapshot_id=str(snapshot.pk),
            )
        )
        edges.append(GraphEdgeSpec(source_external, doc_external, "DERIVED_FROM"))

        prev_external = None
        for idx, chunk in enumerate(chunks):
            block_type = chunk.metadata.get("block_type", "paragraph")
            section = chunk.metadata.get("section", "")
            local_id = self._build_external_id(
                projection_id,
                f"chunk:{snapshot.id}:{idx}:{self._hash(chunk.page_content)}",
            )
            node_type = {
                "heading": "section",
                "paragraph": "chunk",
                "table": "fact",
                "code_block": "procedure",
                "list": "chunk",
                "mixed": "chunk",
            }.get(block_type, "chunk")
            title = section if block_type != "heading" else chunk.page_content
            nodes.append(
                GraphNodeSpec(
                    node_type=node_type,
                    external_id=local_id,
                    name=(title or f"块 {idx}")[:500],
                    properties={
                        "block_type": block_type,
                        "section": section,
                        "chunk_index": idx,
                        "location": chunk.metadata,
                        "content_preview": chunk.page_content[:500],
                    },
                    snapshot_id=str(snapshot.pk),
                )
            )
            parent = doc_external
            if section:
                section_external = self._build_external_id(
                    projection_id, f"section:{snapshot.id}:{self._hash(section)}"
                )
                # 确保 section 节点存在；重复写入 update_or_create 可接受
                nodes.append(
                    GraphNodeSpec(
                        node_type="section",
                        external_id=section_external,
                        name=section,
                        properties={"level": chunk.metadata.get("heading_level", 0)},
                        snapshot_id=str(snapshot.pk),
                    )
                )
                edges.append(GraphEdgeSpec(doc_external, section_external, "CONTAINS"))
                parent = section_external
            edges.append(GraphEdgeSpec(parent, local_id, "CONTAINS"))
            if prev_external:
                edges.append(GraphEdgeSpec(prev_external, local_id, "NEXT"))
            prev_external = local_id

        node_map = self.graph.upsert_nodes(
            project_id=project_id, projection_id=projection_id, nodes=nodes
        )
        edge_count = self.graph.add_edges(
            project_id=project_id, projection_id=projection_id, edges=edges
        )
        return {
            "source_external_id": source_external,
            "document_external_id": doc_external,
            "node_count": len(node_map),
            "edge_count": edge_count,
            "chunk_count": len(chunks),
        }

    def build_from_version(
        self,
        version: KnowledgeVersion,
        projection_id: Optional[str] = None,
    ) -> dict:
        """把 KnowledgeVersion / KnowledgeAsset 投影成图节点，并绑定到来源快照。"""
        project_id = version.project_id
        nodes: List[GraphNodeSpec] = []
        edges: List[GraphEdgeSpec] = []

        asset = version.asset
        asset_external = self._build_external_id(projection_id, f"asset:{asset.id}")
        version_external = self._build_external_id(projection_id, f"version:{version.id}")

        nodes.append(
            GraphNodeSpec(
                node_type="asset",
                external_id=asset_external,
                name=asset.title,
                properties={
                    "key": asset.key,
                    "asset_type": asset.asset_type,
                    "level": asset.level,
                    "status": asset.status,
                },
                asset_id=str(asset.pk),
            )
        )
        nodes.append(
            GraphNodeSpec(
                node_type="version",
                external_id=version_external,
                name=f"{asset.title} v{version.version}",
                properties={
                    "version": version.version,
                    "status": version.status,
                    "content_hash": version.content_hash,
                },
                version_id=str(version.pk),
            )
        )
        edges.append(GraphEdgeSpec(asset_external, version_external, "DEFINES"))
        if version.previous_version_id:
            prev_external = self._build_external_id(projection_id, f"version:{version.previous_version_id}")
            edges.append(GraphEdgeSpec(prev_external, version_external, "SUPERSEDES"))

        for evidence in version.evidences.select_related("snapshot"):
            snap_external = self._build_external_id(projection_id, f"source:{evidence.snapshot_id}")
            nodes.append(
                GraphNodeSpec(
                    node_type="source",
                    external_id=snap_external,
                    name=f"来源快照 {evidence.snapshot.source_type}:{evidence.snapshot.source_id}",
                    properties={
                        "source_type": evidence.snapshot.source_type,
                        "source_id": evidence.snapshot.source_id,
                        "authority": evidence.snapshot.authority,
                    },
                    snapshot_id=str(evidence.snapshot_id),
                )
            )
            edges.append(GraphEdgeSpec(version_external, snap_external, "SUPPORTED_BY"))

        node_map = self.graph.upsert_nodes(
            project_id=project_id, projection_id=projection_id, nodes=nodes
        )
        edge_count = self.graph.add_edges(
            project_id=project_id, projection_id=projection_id, edges=edges
        )
        return {
            "asset_external_id": asset_external,
            "version_external_id": version_external,
            "node_count": len(node_map),
            "edge_count": edge_count,
        }

    def build_from_candidate(
        self,
        candidate: KnowledgeCandidate,
        projection_id: Optional[str] = None,
    ) -> dict:
        """把候选连接到来源快照，作为待审知识原子。"""
        project_id = candidate.project_id
        candidate_external = self._build_external_id(projection_id, f"candidate:{candidate.id}")
        nodes = [
            GraphNodeSpec(
                node_type="candidate",
                external_id=candidate_external,
                name=candidate.payload.get("title", "候选"),
                properties={
                    "kind": candidate.kind,
                    "level": candidate.level,
                    "confidence": candidate.confidence,
                    "state": candidate.state,
                },
                candidate_id=str(candidate.pk),
            )
        ]
        edges = []
        if candidate.source_snapshot_id:
            snap_external = self._build_external_id(projection_id, f"source:{candidate.source_snapshot_id}")
            nodes.append(
                GraphNodeSpec(
                    node_type="source",
                    external_id=snap_external,
                    name=f"来源快照 {candidate.source_snapshot_id}",
                    properties={},
                    snapshot_id=str(candidate.source_snapshot_id),
                )
            )
            edges.append(GraphEdgeSpec(candidate_external, snap_external, "DERIVED_FROM"))
        if candidate.promoted_asset_id:
            asset_external = self._build_external_id(projection_id, f"asset:{candidate.promoted_asset_id}")
            edges.append(GraphEdgeSpec(candidate_external, asset_external, "ACCEPTED_BY"))

        node_map = self.graph.upsert_nodes(
            project_id=project_id, projection_id=projection_id, nodes=nodes
        )
        edge_count = self.graph.add_edges(
            project_id=project_id, projection_id=projection_id, edges=edges
        )
        return {
            "candidate_external_id": candidate_external,
            "node_count": len(node_map),
            "edge_count": edge_count,
        }

    def build_full_for_snapshot(
        self,
        snapshot: SourceSnapshot,
        projection_id: Optional[str] = None,
    ) -> dict:
        """把快照 + 关联版本 + 候选一并建图。"""
        result = {"snapshot": self.build_from_snapshot(snapshot, projection_id)}
        version_results = []
        candidate_results = []
        for version in snapshot.versions.select_related("asset"):
            version_results.append(self.build_from_version(version, projection_id))
        for candidate in snapshot.candidates.all():
            candidate_results.append(self.build_from_candidate(candidate, projection_id))
        result["versions"] = version_results
        result["candidates"] = candidate_results
        return result
