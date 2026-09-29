"""任务 9：可版本化检索编排器测试。"""

from django.contrib.auth.models import User
from django.test import TestCase

from projects.models import Project

from knowledge.models import Document, DocumentChunk, KnowledgeBase

from .graph import GraphNodeSpec, GraphEdgeSpec, PostgreSQLGraphSource
from .graph_models import GraphNode, GraphEdge
from .knowledge_models import (
    KnowledgeAsset,
    KnowledgeConflict,
    KnowledgeVersion,
    SourceSnapshot,
)
from .retrieval import (
    BaseRetriever,
    DenseRetriever,
    GraphRetriever,
    HistoricalRetriever,
    RetrievalCandidate,
    RetrievalOrchestrator,
    RetrievalRequest,
    StructuredRetriever,
)
from .retrieval_models import RetrievalPolicy


class MockDenseRetriever(DenseRetriever):
    """返回固定候选的稠密召回，用于测试融合逻辑。"""

    def __init__(self, candidates):
        super().__init__()
        self._candidates = candidates

    def search(self, request, query, config):
        return self._candidates


class RetrievalOrchestratorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ret-user", password="p")
        self.project = Project.objects.create(name="Retrieval Project", creator=self.user)
        self.policy = RetrievalPolicy.objects.create(
            project=self.project,
            name="default",
            version=1,
            is_default=True,
            config={
                "sources": {
                    "dense": {"enabled": True, "k": 5, "weight": 0.5},
                    "sparse": {"enabled": True, "k": 5, "weight": 0.3},
                    "graph": {"enabled": True, "k": 10, "weight": 0.2},
                    "structured": {"enabled": False},
                    "historical": {"enabled": False},
                },
                "graph_policy": "conditional",
                "graph_task_types": ["code_review"],
                "rrf_k": 60,
                "mmr": {"enabled": True, "lambda_param": 0.7, "final_k": 4},
                "budget": {"time_ms": 5000, "token": 4000},
            },
        )
        self.graph_source = PostgreSQLGraphSource()
        self.orchestrator = RetrievalOrchestrator(graph_source=self.graph_source)

    def test_weighted_rrf_fusion(self):
        dense = [
            RetrievalCandidate(id="a", content="登录", source="dense", source_type="dense", score=0.9),
            RetrievalCandidate(id="b", content="验证码", source="dense", source_type="dense", score=0.8),
        ]
        sparse = [
            RetrievalCandidate(id="b", content="验证码", source="sparse", source_type="sparse", score=0.85),
            RetrievalCandidate(id="c", content="密码", source="sparse", source_type="sparse", score=0.7),
        ]
        retrievers = {
            "dense": MockDenseRetriever(dense),
            "sparse": MockDenseRetriever(sparse),
            "graph": BaseRetriever(),
            "structured": BaseRetriever(),
            "historical": BaseRetriever(),
        }
        orchestrator = RetrievalOrchestrator(
            graph_source=self.graph_source, retrievers=retrievers
        )
        request = RetrievalRequest(
            project_id=self.project.pk,
            query="登录",
            task_type="knowledge_query",
            policy=self.policy,
        )
        result = orchestrator.retrieve(request)
        evidence = result["evidence_package"]
        ids = [e["id"] for e in evidence]
        # b 在两个源都出现，应排第一
        self.assertEqual(ids[0], "b")
        self.assertIn("a", ids)
        self.assertIn("c", ids)

    def test_graph_policy_always_and_never(self):
        # 预置图节点
        self.graph_source.upsert_nodes(
            project_id=self.project.pk,
            projection_id=None,
            nodes=[
                GraphNodeSpec(node_type="rule", external_id="rule:1", name="登录必须校验验证码"),
            ],
        )
        retrievers = {
            "dense": BaseRetriever(),
            "sparse": BaseRetriever(),
            "graph": GraphRetriever(self.graph_source),
            "structured": BaseRetriever(),
            "historical": BaseRetriever(),
        }
        orchestrator = RetrievalOrchestrator(
            graph_source=self.graph_source, retrievers=retrievers
        )

        # always
        request = RetrievalRequest(
            project_id=self.project.pk,
            query="验证码",
            task_type="code_review",
            graph_policy="always",
            policy=self.policy,
        )
        result = orchestrator.retrieve(request)
        self.assertGreater(result["candidates_count"].get("graph", 0), 0)

        # never
        request.graph_policy = "never"
        result = orchestrator.retrieve(request)
        self.assertEqual(result["candidates_count"].get("graph", 0), 0)

    def test_conflict_detection(self):
        asset1 = KnowledgeAsset.objects.create(
            project=self.project, asset_type="rule", key="r1", title="规则1"
        )
        asset2 = KnowledgeAsset.objects.create(
            project=self.project, asset_type="rule", key="r2", title="规则2"
        )
        KnowledgeConflict.objects.create(
            project=self.project,
            left_asset=asset1,
            right_asset=asset2,
            conflict_type="contradiction",
            state="open",
            trust_penalty=0.5,
        )
        candidate = RetrievalCandidate(
            id="a1",
            content="规则1内容",
            source="asset",
            source_type="asset",
            asset_id=str(asset1.pk),
        )
        orchestrator = RetrievalOrchestrator()
        enriched = orchestrator._detect_conflicts([candidate], self.project.pk)
        self.assertEqual(len(enriched[0].conflicts), 1)
        self.assertEqual(enriched[0].conflicts[0]["type"], "contradiction")

    def test_mmr_dedup(self):
        candidates = [
            RetrievalCandidate(id="1", content="登录必须输入验证码才能继续", source="a", source_type="a", score=0.9),
            RetrievalCandidate(id="2", content="登录需要输入验证码才能继续", source="a", source_type="a", score=0.85),
            RetrievalCandidate(id="3", content="数据库连接池配置说明", source="a", source_type="a", score=0.5),
            RetrievalCandidate(id="4", content="Redis 缓存过期策略", source="a", source_type="a", score=0.4),
        ]
        # 低 lambda 让多样性权重更高，避免选与 1 高度重复的 2
        result = RetrievalOrchestrator._apply_mmr(
            candidates, {"mmr": {"enabled": True, "lambda_param": 0.3, "final_k": 2}}
        )
        # 第一个取 0.9；第二个应取多样性最高的 3 或 4，而非 2
        self.assertNotEqual(result[1].id, "2")

    def test_structured_retriever(self):
        kb = KnowledgeBase.objects.create(project=self.project, name="KB", creator=self.user)
        doc = Document.objects.create(
            knowledge_base=kb, title="测试文档", document_type="txt", content="登录验证码"
        )
        DocumentChunk.objects.create(
            document=doc, chunk_index=0, content="登录必须校验验证码",
            section_title="登录", block_type="paragraph", location={"page": 1},
        )
        retriever = StructuredRetriever()
        request = RetrievalRequest(project_id=self.project.pk, query="验证码")
        candidates = retriever.search(request, "验证码", {})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_type, "structured")
