"""文档/知识/需求/测试用例图谱的 GraphSource 浏览客户端。

对应设计 docs/specs/knowledge-graph-workbench/design.md 的统一图谱协议，
把 PostgreSQLGraphSource 的节点/边翻译成前端 GraphSnapshot 格式。
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Iterable, List, Optional

from django.db.models import Q

from .graph import KnowledgeDocumentGraphAdapter, PostgreSQLGraphSource
from .graph_adapters import RequirementGraphAdapter, TestCaseGraphAdapter
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import SourceSnapshot


def _node_to_frontend(node: dict, *, source_type: str) -> dict:
    props = node.get("properties") or {}
    snapshot_id = str(node.get("snapshot_id") or "")
    source_id = props.get("source_id", "")
    loc = props.get("location") or {}
    return {
        "id": str(node["id"]),
        "kind": node["node_type"],
        "label": node.get("name") or node["external_id"],
        "qualified_name": node["external_id"],
        "path": f"{props.get('source_type', source_type)}:{source_id}" if source_id else node["external_id"],
        "location": {
            "line_start": loc.get("line_start"),
            "line_end": loc.get("line_end"),
        },
        "language": source_type,
        "is_test": source_type == "test_case",
        "properties": {
            **props,
            "external_id": node["external_id"],
            "snapshot_id": snapshot_id,
            "asset_id": str(node.get("asset_id") or ""),
            "version_id": str(node.get("version_id") or ""),
            "candidate_id": str(node.get("candidate_id") or ""),
            "chunk_id": str(node.get("chunk_id") or ""),
        },
        "provenance": {
            "source_type": source_type,
            "source_id": source_id,
            "snapshot_id": snapshot_id,
            "confidence": 1.0,
            "location": props.get("location", ""),
        },
    }


def _edge_to_frontend(edge: dict, *, source_type: str) -> dict:
    return {
        "id": str(edge["id"]),
        "kind": edge["relation"],
        "source": str(edge["from_id"]),
        "target": str(edge["to_id"]),
        "confidence": edge.get("weight", 1.0),
        "properties": edge.get("properties") or {},
        "provenance": {
            "source_type": source_type,
            "source_id": "",
            "snapshot_id": "",
            "confidence": 1.0,
            "location": "",
        },
    }


class KnowledgeDocumentGraphClient:
    """面向前端知识图谱工作台的文档图客户端。

    - list_sources：列出项目下每个文档来源的最新快照。
    - source：把单个 SourceSnapshot 包装成 GraphSource。
    - browse：搜索/邻域浏览，支持 node_kinds / edge_kinds 过滤；未建图时自动构建。
    """

    def __init__(self, graph_source: Optional[PostgreSQLGraphSource] = None):
        self.graph = graph_source or PostgreSQLGraphSource()

    # ------------------------------------------------------------------ source

    @staticmethod
    def _snapshot_stats(snapshot: SourceSnapshot) -> tuple[int, int, bool]:
        node_count = GraphNode.objects.filter(snapshot=snapshot).count()
        edge_count = GraphEdge.objects.filter(
            Q(from_node__snapshot=snapshot) | Q(to_node__snapshot=snapshot)
        ).count()
        return node_count, edge_count, node_count > 0

    def source(self, snapshot: SourceSnapshot) -> dict:
        loc = snapshot.location or {}
        title = loc.get("title") or snapshot.source_id
        node_count, edge_count, has_graph = self._snapshot_stats(snapshot)
        return {
            "id": f"document:{snapshot.pk}",
            "type": "knowledge_document",
            "name": title,
            "description": title,
            "project": {"id": snapshot.project_id, "name": snapshot.project.name},
            "snapshot": {
                "id": str(snapshot.pk),
                "commit": (snapshot.content_hash or "")[:10] or (snapshot.revision or "")[:10] or "",
                "base_commit": "",
                "created_at": snapshot.created_at.isoformat() if snapshot.created_at else "",
                "crg_version": "",
            },
            "status": "completed" if has_graph else "not_built",
            "stats": {
                "nodes": node_count,
                "edges": edge_count,
                "changed_symbols": 0,
                "affected_files": 0,
                "related_tests": 0,
            },
            "capabilities": ["overview", "search", "filter", "neighborhood", "provenance"],
            "provenance": {
                "source_type": "knowledge_document",
                "source_id": snapshot.source_id,
                "snapshot_id": str(snapshot.pk),
                "analysis_task_id": "",
            },
        }

    def list_sources(self, project_id: int) -> List[dict]:
        """每个 source_id 只返回最新快照，避免重复。"""
        latest = (
            SourceSnapshot.objects.filter(project_id=project_id, source_type="document")
            .order_by("source_id", "-created_at")
            .distinct("source_id")
        )
        return [self.source(s) for s in latest]

    # ------------------------------------------------------------------ browse

    def _ensure_built(self, snapshot: SourceSnapshot) -> None:
        """未建图时自动构建文档图（幂等：external_id 唯一约束保证不重复）。"""
        if not GraphNode.objects.filter(snapshot=snapshot).exists():
            adapter = KnowledgeDocumentGraphAdapter()
            adapter.build_from_snapshot(snapshot)

    def _resolve_center(self, project_id: int, center: str) -> Optional[GraphNode]:
        if not center:
            return None
        # center 通常是前端 node.id（UUID）
        try:
            return GraphNode.objects.get(project_id=project_id, pk=center)
        except (GraphNode.DoesNotExist, ValueError, TypeError):
            pass
        # 兼容 external_id
        return GraphNode.objects.filter(project_id=project_id, external_id=center).first()

    def browse(
        self,
        *,
        snapshot: SourceSnapshot,
        search: str = "",
        node_kinds: Optional[Iterable[str]] = None,
        edge_kinds: Optional[Iterable[str]] = None,
        center: str = "",
        depth: int = 1,
        limit: int = 120,
    ) -> dict:
        start = time.time()
        project_id = snapshot.project_id
        self._ensure_built(snapshot)

        node_kinds = set(node_kinds or [])
        edge_kinds = set(edge_kinds or [])
        limit = max(10, min(int(limit), 300))
        depth = max(1, min(int(depth), 3))

        raw_nodes: List[dict] = []
        raw_edges: List[dict] = []
        truncated = False

        center_node = self._resolve_center(project_id, center)
        if center_node:
            sub = self.graph.neighbors(
                project_id=project_id,
                external_id=center_node.external_id,
                depth=depth,
                limit=limit,
            )
            raw_nodes, raw_edges = sub["nodes"], sub["edges"]
        elif search:
            q = GraphNode.objects.filter(
                project_id=project_id
            ).filter(
                Q(name__icontains=search) | Q(properties__content_preview__icontains=search)
            )
            if node_kinds:
                q = q.filter(node_type__in=node_kinds)
            matched = list(q.order_by("-created_at")[:limit])
            if matched:
                sub = self.graph.subgraph(
                    project_id=project_id,
                    center_external_ids=[n.external_id for n in matched],
                    depth=depth,
                    limit=limit,
                )
                raw_nodes, raw_edges = sub["nodes"], sub["edges"]
        else:
            # 默认展示顶层文档/资产/候选节点及其 1 跳邻域
            top_types = {"document", "asset", "candidate", "source"}
            if node_kinds:
                top_types &= node_kinds
            q = GraphNode.objects.filter(project_id=project_id, node_type__in=top_types)
            top = list(q.order_by("-created_at")[:limit])
            if top:
                sub = self.graph.subgraph(
                    project_id=project_id,
                    center_external_ids=[n.external_id for n in top],
                    depth=1,
                    limit=limit,
                )
                raw_nodes, raw_edges = sub["nodes"], sub["edges"]

        if node_kinds:
            raw_nodes = [n for n in raw_nodes if n["node_type"] in node_kinds]
        if edge_kinds:
            raw_edges = [e for e in raw_edges if e["relation"] in edge_kinds]

        if len(raw_nodes) > limit:
            raw_nodes = raw_nodes[:limit]
            truncated = True
        if len(raw_edges) > limit:
            raw_edges = raw_edges[:limit]
            truncated = True

        node_kind_counts = Counter(n["node_type"] for n in raw_nodes)
        edge_kind_counts = Counter(e["relation"] for e in raw_edges)

        return {
            "status": "completed",
            "source": self.source(snapshot),
            "stats": {"nodes": len(raw_nodes), "edges": len(raw_edges)},
            "facets": {
                "node_kinds": dict(node_kind_counts),
                "edge_kinds": dict(edge_kind_counts),
                "languages": {"document": len(raw_nodes)},
            },
            "nodes": [_node_to_frontend(n, source_type="knowledge_document") for n in raw_nodes],
            "edges": [_edge_to_frontend(e, source_type="knowledge_document") for e in raw_edges],
            "truncated": truncated,
            "duration_ms": int((time.time() - start) * 1000),
            "crg_version": "",
        }


class RequirementGraphClient:
    """面向前端知识图谱工作台的需求文档图客户端。"""

    def __init__(self, graph_source: Optional[PostgreSQLGraphSource] = None):
        self.graph = graph_source or PostgreSQLGraphSource()

    # ------------------------------------------------------------------ source

    def source(self, document) -> dict:
        node_count = GraphNode.objects.filter(
            project_id=document.project_id, external_id=f"req_doc:{document.pk}"
        ).count()
        return {
            "id": f"requirement:{document.pk}",
            "type": "requirement",
            "name": document.title or "未命名需求文档",
            "description": (document.description or "")[:200],
            "project": {"id": document.project_id, "name": document.project.name},
            "snapshot": {
                "id": str(document.pk),
                "commit": (document.version or "")[:20],
                "base_commit": "",
                "created_at": document.uploaded_at.isoformat() if document.uploaded_at else "",
                "crg_version": "",
            },
            "status": "completed" if node_count > 0 else "not_built",
            "stats": {
                "nodes": node_count,
                "edges": 0,
                "changed_symbols": 0,
                "affected_files": 0,
                "related_tests": 0,
            },
            "capabilities": ["overview", "search", "filter", "neighborhood", "provenance"],
            "provenance": {
                "source_type": "requirement",
                "source_id": str(document.pk),
                "snapshot_id": str(document.pk),
                "analysis_task_id": "",
            },
        }

    def list_sources(self, project_id: int) -> List[dict]:
        from requirements.models import RequirementDocument

        docs = RequirementDocument.objects.filter(project_id=project_id).order_by("-uploaded_at")[:200]
        return [self.source(doc) for doc in docs]

    # ------------------------------------------------------------------ browse

    def _ensure_built(self, document) -> None:
        if not GraphNode.objects.filter(
            project_id=document.project_id, external_id=f"req_doc:{document.pk}"
        ).exists():
            RequirementGraphAdapter(self.graph).build_from_document(document)

    def _resolve_center(self, project_id: int, center: str) -> Optional[GraphNode]:
        if not center:
            return None
        try:
            return GraphNode.objects.get(project_id=project_id, pk=center)
        except (GraphNode.DoesNotExist, ValueError, TypeError):
            pass
        return GraphNode.objects.filter(project_id=project_id, external_id=center).first()

    def browse(
        self,
        *,
        document,
        search: str = "",
        node_kinds: Optional[Iterable[str]] = None,
        edge_kinds: Optional[Iterable[str]] = None,
        center: str = "",
        depth: int = 1,
        limit: int = 120,
    ) -> dict:
        start = time.time()
        project_id = document.project_id
        self._ensure_built(document)

        node_kinds = set(node_kinds or [])
        edge_kinds = set(edge_kinds or [])
        limit = max(10, min(int(limit), 300))
        depth = max(1, min(int(depth), 3))

        raw_nodes: List[dict] = []
        raw_edges: List[dict] = []
        truncated = False

        center_node = self._resolve_center(project_id, center)
        if center_node:
            sub = self.graph.neighbors(
                project_id=project_id,
                external_id=center_node.external_id,
                depth=depth,
                limit=limit,
            )
            raw_nodes, raw_edges = sub["nodes"], sub["edges"]
        elif search:
            q = GraphNode.objects.filter(project_id=project_id).filter(
                Q(node_type__in=["requirement_document", "requirement_module"])
                & (Q(name__icontains=search) | Q(properties__description__icontains=search))
            )
            if node_kinds:
                q = q.filter(node_type__in=node_kinds)
            matched = list(q.order_by("-created_at")[:limit])
            if matched:
                sub = self.graph.subgraph(
                    project_id=project_id,
                    center_external_ids=[n.external_id for n in matched],
                    depth=depth,
                    limit=limit,
                )
                raw_nodes, raw_edges = sub["nodes"], sub["edges"]
        else:
            top = list(
                GraphNode.objects.filter(
                    project_id=project_id, node_type__in=["requirement_document", "requirement_module"]
                ).order_by("-created_at")[:limit]
            )
            if top:
                sub = self.graph.subgraph(
                    project_id=project_id,
                    center_external_ids=[n.external_id for n in top],
                    depth=1,
                    limit=limit,
                )
                raw_nodes, raw_edges = sub["nodes"], sub["edges"]

        if node_kinds:
            raw_nodes = [n for n in raw_nodes if n["node_type"] in node_kinds]
        if edge_kinds:
            raw_edges = [e for e in raw_edges if e["relation"] in edge_kinds]

        if len(raw_nodes) > limit:
            raw_nodes = raw_nodes[:limit]
            truncated = True
        if len(raw_edges) > limit:
            raw_edges = raw_edges[:limit]
            truncated = True

        node_kind_counts = Counter(n["node_type"] for n in raw_nodes)
        edge_kind_counts = Counter(e["relation"] for e in raw_edges)

        return {
            "status": "completed",
            "source": self.source(document),
            "stats": {"nodes": len(raw_nodes), "edges": len(raw_edges)},
            "facets": {
                "node_kinds": dict(node_kind_counts),
                "edge_kinds": dict(edge_kind_counts),
                "languages": {"requirement": len(raw_nodes)},
            },
            "nodes": [_node_to_frontend(n, source_type="requirement") for n in raw_nodes],
            "edges": [_edge_to_frontend(e, source_type="requirement") for e in raw_edges],
            "truncated": truncated,
            "duration_ms": int((time.time() - start) * 1000),
            "crg_version": "",
        }


class TestCaseGraphClient:
    """面向前端知识图谱工作台的测试用例图客户端。"""

    def __init__(self, graph_source: Optional[PostgreSQLGraphSource] = None):
        self.graph = graph_source or PostgreSQLGraphSource()

    # ------------------------------------------------------------------ source

    def source(self, project) -> dict:
        node_count = GraphNode.objects.filter(
            project_id=project.pk, node_type__in=["test_module", "test_case", "test_step"]
        ).count()
        return {
            "id": f"test_case:{project.pk}",
            "type": "test_case",
            "name": f"{project.name} 测试用例集",
            "description": "项目下全部测试用例、步骤与模块关系",
            "project": {"id": project.pk, "name": project.name},
            "snapshot": {
                "id": str(project.pk),
                "commit": "",
                "base_commit": "",
                "created_at": project.created_at.isoformat() if project.created_at else "",
                "crg_version": "",
            },
            "status": "completed" if node_count > 0 else "not_built",
            "stats": {
                "nodes": node_count,
                "edges": 0,
                "changed_symbols": 0,
                "affected_files": 0,
                "related_tests": 0,
            },
            "capabilities": ["overview", "search", "filter", "neighborhood", "provenance"],
            "provenance": {
                "source_type": "test_case",
                "source_id": str(project.pk),
                "snapshot_id": str(project.pk),
                "analysis_task_id": "",
            },
        }

    def list_sources(self, project_id: int) -> List[dict]:
        from projects.models import Project

        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return []
        return [self.source(project)]

    # ------------------------------------------------------------------ browse

    def _ensure_built(self, project) -> None:
        if not GraphNode.objects.filter(
            project_id=project.pk, node_type__in=["test_module", "test_case", "test_step"]
        ).exists():
            TestCaseGraphAdapter(self.graph).build_from_project(project.pk)

    def _resolve_center(self, project_id: int, center: str) -> Optional[GraphNode]:
        if not center:
            return None
        try:
            return GraphNode.objects.get(project_id=project_id, pk=center)
        except (GraphNode.DoesNotExist, ValueError, TypeError):
            pass
        return GraphNode.objects.filter(project_id=project_id, external_id=center).first()

    def browse(
        self,
        *,
        project,
        search: str = "",
        node_kinds: Optional[Iterable[str]] = None,
        edge_kinds: Optional[Iterable[str]] = None,
        center: str = "",
        depth: int = 1,
        limit: int = 120,
    ) -> dict:
        start = time.time()
        project_id = project.pk
        self._ensure_built(project)

        node_kinds = set(node_kinds or [])
        edge_kinds = set(edge_kinds or [])
        limit = max(10, min(int(limit), 300))
        depth = max(1, min(int(depth), 3))

        raw_nodes: List[dict] = []
        raw_edges: List[dict] = []
        truncated = False

        center_node = self._resolve_center(project_id, center)
        if center_node:
            sub = self.graph.neighbors(
                project_id=project_id,
                external_id=center_node.external_id,
                depth=depth,
                limit=limit,
            )
            raw_nodes, raw_edges = sub["nodes"], sub["edges"]
        elif search:
            q = GraphNode.objects.filter(project_id=project_id).filter(
                Q(node_type__in=["test_module", "test_case", "test_step"])
                & (Q(name__icontains=search) | Q(properties__description__icontains=search))
            )
            if node_kinds:
                q = q.filter(node_type__in=node_kinds)
            matched = list(q.order_by("-created_at")[:limit])
            if matched:
                sub = self.graph.subgraph(
                    project_id=project_id,
                    center_external_ids=[n.external_id for n in matched],
                    depth=depth,
                    limit=limit,
                )
                raw_nodes, raw_edges = sub["nodes"], sub["edges"]
        else:
            top = list(
                GraphNode.objects.filter(
                    project_id=project_id, node_type__in=["test_module", "test_case"]
                ).order_by("-created_at")[:limit]
            )
            if top:
                sub = self.graph.subgraph(
                    project_id=project_id,
                    center_external_ids=[n.external_id for n in top],
                    depth=1,
                    limit=limit,
                )
                raw_nodes, raw_edges = sub["nodes"], sub["edges"]

        if node_kinds:
            raw_nodes = [n for n in raw_nodes if n["node_type"] in node_kinds]
        if edge_kinds:
            raw_edges = [e for e in raw_edges if e["relation"] in edge_kinds]

        if len(raw_nodes) > limit:
            raw_nodes = raw_nodes[:limit]
            truncated = True
        if len(raw_edges) > limit:
            raw_edges = raw_edges[:limit]
            truncated = True

        node_kind_counts = Counter(n["node_type"] for n in raw_nodes)
        edge_kind_counts = Counter(e["relation"] for e in raw_edges)

        return {
            "status": "completed",
            "source": self.source(project),
            "stats": {"nodes": len(raw_nodes), "edges": len(raw_edges)},
            "facets": {
                "node_kinds": dict(node_kind_counts),
                "edge_kinds": dict(edge_kind_counts),
                "languages": {"test_case": len(raw_nodes)},
            },
            "nodes": [_node_to_frontend(n, source_type="test_case") for n in raw_nodes],
            "edges": [_edge_to_frontend(e, source_type="test_case") for e in raw_edges],
            "truncated": truncated,
            "duration_ms": int((time.time() - start) * 1000),
            "crg_version": "",
        }
