"""任务 8：文档图谱 GraphSource / Adapter 测试。"""

from django.contrib.auth.models import User
from django.test import TestCase

from projects.models import Project

from .graph import GraphEdgeSpec, GraphNodeSpec, KnowledgeDocumentGraphAdapter, PostgreSQLGraphSource
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import (
    KnowledgeAsset,
    KnowledgeCandidate,
    KnowledgeVersion,
    SourceSnapshot,
)


TEXT = """# 登录规范

登录必须校验验证码。

## 错误提示

错误码如下表：
| 码 | 含义 |
| 401 | 未授权 |

```python
def login():
    pass
```
"""


class PostgreSQLGraphSourceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="graph-user", password="p")
        self.project = Project.objects.create(name="Graph Project", creator=self.user)
        self.source = PostgreSQLGraphSource()

    def test_upsert_nodes_and_edges(self):
        nodes = [
            GraphNodeSpec(node_type="document", external_id="doc:1", name="文档 A"),
            GraphNodeSpec(node_type="section", external_id="sec:1", name="章节 1"),
        ]
        node_map = self.source.upsert_nodes(
            project_id=self.project.pk, projection_id=None, nodes=nodes
        )
        self.assertEqual(len(node_map), 2)
        self.assertEqual(GraphNode.objects.filter(project=self.project).count(), 2)

        edges = [GraphEdgeSpec("doc:1", "sec:1", "CONTAINS")]
        count = self.source.add_edges(
            project_id=self.project.pk, projection_id=None, edges=edges
        )
        self.assertEqual(count, 1)
        self.assertEqual(GraphEdge.objects.count(), 1)

    def test_neighbors_and_subgraph(self):
        self.source.upsert_nodes(
            project_id=self.project.pk,
            projection_id=None,
            nodes=[
                GraphNodeSpec(node_type="document", external_id="d1", name="D1"),
                GraphNodeSpec(node_type="section", external_id="s1", name="S1"),
                GraphNodeSpec(node_type="chunk", external_id="c1", name="C1"),
            ],
        )
        self.source.add_edges(
            project_id=self.project.pk,
            projection_id=None,
            edges=[
                GraphEdgeSpec("d1", "s1", "CONTAINS"),
                GraphEdgeSpec("s1", "c1", "CONTAINS"),
            ],
        )

        neighborhood = self.source.neighbors(
            project_id=self.project.pk, external_id="d1", depth=2
        )
        self.assertEqual(len(neighborhood["nodes"]), 3)
        external_ids = {n["external_id"] for n in neighborhood["nodes"]}
        self.assertEqual(external_ids, {"d1", "s1", "c1"})

        sub = self.source.subgraph(
            project_id=self.project.pk, center_external_ids=["d1"], depth=2
        )
        self.assertEqual(len(sub["nodes"]), 3)

    def test_clear_projection(self):
        projection_id = "12345678-1234-1234-1234-123456789abc"
        self.source.upsert_nodes(
            project_id=self.project.pk,
            projection_id=projection_id,
            nodes=[GraphNodeSpec(node_type="document", external_id="d2", name="D2")],
        )
        deleted = self.source.clear_projection(projection_id)
        self.assertEqual(deleted, 0)
        self.assertEqual(GraphNode.objects.filter(projection_id=projection_id).count(), 0)

    def test_search_nodes(self):
        self.source.upsert_nodes(
            project_id=self.project.pk,
            projection_id=None,
            nodes=[
                GraphNodeSpec(node_type="concept", external_id="c1", name="验证码"),
                GraphNodeSpec(node_type="rule", external_id="r1", name="登录规则"),
            ],
        )
        results = self.source.search_nodes(
            project_id=self.project.pk, node_type="rule", keyword="登录"
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["external_id"], "r1")


class KnowledgeDocumentGraphAdapterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="doc-graph-user", password="p")
        self.project = Project.objects.create(name="Doc Graph Project", creator=self.user)
        self.snapshot = SourceSnapshot.objects.create(
            project=self.project,
            source_type="document",
            source_id="doc-login",
            content_hash="x" * 64,
            parsed_text=TEXT,
            location={"title": "登录规范"},
        )
        self.adapter = KnowledgeDocumentGraphAdapter(chunk_size=80)

    def test_build_from_snapshot_creates_document_and_chunks(self):
        result = self.adapter.build_from_snapshot(self.snapshot)
        self.assertIn("document_external_id", result)
        self.assertGreater(result["node_count"], 2)
        self.assertGreater(result["edge_count"], 1)
        self.assertGreaterEqual(result["chunk_count"], 2)

        doc_node = GraphNode.objects.get(
            project=self.project, external_id=result["document_external_id"]
        )
        self.assertEqual(doc_node.node_type, "document")
        self.assertEqual(doc_node.name, "登录规范")

    def test_build_from_version_links_asset_and_evidence(self):
        asset = KnowledgeAsset.objects.create(
            project=self.project, asset_type="rule", key="rule.captcha", title="验证码规则"
        )
        version = KnowledgeVersion.objects.create(
            asset=asset, version=1, content="登录必须校验验证码",
            content_hash="y" * 64, source_snapshot=self.snapshot,
        )
        from .knowledge_models import KnowledgeEvidence

        KnowledgeEvidence.objects.create(
            version=version, snapshot=self.snapshot, relation="supported_by",
            weight=1.0, location={"section": "登录规范"},
        )

        result = self.adapter.build_from_version(version)
        self.assertEqual(result["node_count"], 3)  # asset + version + source
        self.assertGreaterEqual(result["edge_count"], 2)

        asset_node = GraphNode.objects.get(
            project=self.project, external_id=result["asset_external_id"]
        )
        self.assertEqual(asset_node.node_type, "asset")

    def test_build_from_candidate_links_to_snapshot(self):
        candidate = KnowledgeCandidate.objects.create(
            project=self.project,
            kind="rule",
            origin="extraction",
            payload={"title": "必须校验验证码"},
            level="L1",
            confidence=0.9,
            source_snapshot=self.snapshot,
            dedup_key=KnowledgeCandidate.build_dedup_key(
                self.project.pk, "rule", "必须校验验证码"
            ),
        )
        result = self.adapter.build_from_candidate(candidate)
        self.assertEqual(result["node_count"], 2)  # candidate + source
        self.assertEqual(result["edge_count"], 1)

        edge = GraphEdge.objects.get(
            from_node__external_id=result["candidate_external_id"]
        )
        self.assertEqual(edge.relation, "DERIVED_FROM")

    def test_full_build_from_snapshot(self):
        asset = KnowledgeAsset.objects.create(
            project=self.project, asset_type="rule", key="rule.captcha", title="验证码规则"
        )
        KnowledgeVersion.objects.create(
            asset=asset, version=1, content="登录必须校验验证码",
            content_hash="z" * 64, source_snapshot=self.snapshot,
        )
        KnowledgeCandidate.objects.create(
            project=self.project,
            kind="rule",
            origin="extraction",
            payload={"title": "必须校验验证码"},
            level="L1",
            confidence=0.9,
            source_snapshot=self.snapshot,
            dedup_key=KnowledgeCandidate.build_dedup_key(
                self.project.pk, "rule", "必须校验验证码 v2"
            ),
        )

        result = self.adapter.build_full_for_snapshot(self.snapshot)
        self.assertIn("snapshot", result)
        self.assertEqual(len(result["versions"]), 1)
        self.assertEqual(len(result["candidates"]), 1)
