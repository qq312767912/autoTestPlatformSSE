"""R5① ②：四阶段真正走图谱召回，且通道执行情况如实可查。

本文件钉住三件事：

1. **四阶段在白名单里**。不在的表现是通道被静默摘掉 ——
   产出里看不出异常，只是"引不到需求与既有用例"，排查会被带到图谱侧。
2. **白名单取并集、不可被配置收窄**。多跑一次图谱只多花一点 token，
   而漏掉一次是**静默断链**；两者的代价不对称。
3. **通道实况可查**：谁跑了、谁被跳过、因为什么。
   没有这份状态，"这条产出为什么没有溯源"只能靠猜。

判据来源：``specs/evolution-assisted-loop/design.md`` §3.1 / §3.2。
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase

from knowledge_evolution.capability_registry import ALL_WORKFLOW_STAGES
from knowledge_evolution.retrieval import (
    BaseRetriever,
    DenseRetriever,
    GraphRetriever,
    RetrievalCandidate,
    RetrievalOrchestrator,
    RetrievalRequest,
)
from knowledge_evolution.retrieval_models import (
    RetrievalPolicy,
    _default_graph_task_types,
)
from projects.models import Project

BASE_TYPES = ("code_review", "defect_root_cause", "requirement_coverage",
              "impact_analysis", "case_review")


class FixedDenseRetriever(DenseRetriever):
    """返回固定候选的稠密召回 —— 用来制造"直连检索已经很确定"的局面。"""

    def __init__(self, candidates):
        super().__init__()
        self._candidates = candidates

    def search(self, request, query, config):
        return self._candidates


class GraphWhitelistTests(TestCase):
    """白名单：四阶段必须在里面，且收不窄。"""

    def setUp(self):
        self.user = User.objects.create_user(username="t02", password="p")
        self.project = Project.objects.create(name="T02 项目", creator=self.user)

    def test_default_whitelist_covers_every_workflow_stage(self):
        """四阶段一个都不能少 —— 少一个就是那一个阶段的溯源永久断链。"""
        whitelist = _default_graph_task_types()

        for stage in ALL_WORKFLOW_STAGES:
            with self.subTest(stage=stage):
                self.assertIn(stage, whitelist)
        for task_type in BASE_TYPES:
            with self.subTest(task_type=task_type):
                self.assertIn(task_type, whitelist)
        # 不该有重复：重复项会让"配了几个任务类型"这个数字对不上。
        self.assertEqual(len(whitelist), len(set(whitelist)))

    def test_policy_config_cannot_narrow_the_whitelist(self):
        """存量/手工策略把白名单写成 ``["code_review"]``，也不许排除四阶段。

        这条正是那个线上断链的成因：老配置里只有 ``code_review``，
        于是四阶段全被摘掉图谱通道，而页面上只表现为"没溯源"。
        """
        policy = RetrievalPolicy.objects.create(
            project=self.project, name="旧策略", version=1, is_default=True,
            config={"graph_task_types": ["code_review"]},
        )

        resolved = policy.resolve_config()

        for stage in ALL_WORKFLOW_STAGES:
            with self.subTest(stage=stage):
                self.assertIn(stage, resolved["graph_task_types"])
        # 原有的定制项要保留，取的是并集不是替换。
        self.assertIn("code_review", resolved["graph_task_types"])

    def test_graph_policy_stays_conditional(self):
        """默认仍是 ``conditional``：直连够确定时不跑图谱，省 token 而不省可信度。"""
        policy = RetrievalPolicy.objects.create(
            project=self.project, name="默认", version=1, is_default=True, config={},
        )
        self.assertEqual(policy.resolve_config()["graph_policy"], "conditional")


class ChannelStatusTests(TestCase):
    """通道实况：谁跑了、谁被跳过、为什么。"""

    def setUp(self):
        self.user = User.objects.create_user(username="t02b", password="p")
        self.project = Project.objects.create(name="T02B 项目", creator=self.user)

    def _policy(self, **config):
        return RetrievalPolicy.objects.create(
            project=self.project, name="p", version=1, is_default=True, config=config,
        )

    def _orchestrator(self, dense_candidates=None):
        retrievers = {
            "dense": FixedDenseRetriever(dense_candidates or []),
            "sparse": BaseRetriever(),
            "graph": BaseRetriever(),
            "structured": BaseRetriever(),
            "historical": BaseRetriever(),
        }
        return RetrievalOrchestrator(retrievers=retrievers)

    def test_direct_recall_confidence_is_recorded_next_to_the_primary_reason(self):
        """直连已够确定 → 图谱被摘掉，主因是"任务类型不适用"，附带依据要一并记。

        主因必须是**可操作的那一条**（改白名单），而不是"直连召回已经很确定了"——
        后者会把人带到召回质量上去排查。两者都记，但顺序不能反。
        """
        policy = self._policy(
            sources={
                "dense": {"enabled": True, "k": 5, "weight": 0.5},
                "sparse": {"enabled": True, "k": 5, "weight": 0.3},
                "graph": {"enabled": True, "k": 10, "weight": 0.2},
            },
            graph_policy="conditional",
        )
        # 高分直连结果；任务类型不在白名单内 → 图谱退化为"信心不足才补"。
        orchestrator = self._orchestrator([
            RetrievalCandidate(id="a", content="登录", source="dense",
                               source_type="dense", score=0.99),
            RetrievalCandidate(id="b", content="验证码", source="dense",
                               source_type="dense", score=0.97),
        ])
        request = RetrievalRequest(
            project_id=self.project.pk, query="登录", task_type="knowledge_query",
            policy=policy,
        )

        result = orchestrator.retrieve(request)

        graph = result["channels"]["graph"]
        self.assertFalse(graph["enabled"])
        self.assertEqual(graph["reason"], "not_applicable_task_type")
        self.assertTrue(graph["direct_recall_confident"])
        # 策略也要记：只写 enabled=False 会被误读成"图谱被全局关停了"。
        self.assertEqual(graph["policy"], "conditional")

    def test_not_applicable_task_type_is_distinguished_from_policy_off(self):
        """"任务类型不适用"与"策略关停"必须分开记。

        两者原先都写 ``direct_recall_confident``：一个检索策略配置问题，
        在溯源里表现为"直连召回已经很确定了"，顺着这句话排查必然跑到召回质量上去。
        """
        policy = self._policy(
            sources={
                "dense": {"enabled": True, "k": 5, "weight": 0.5},
                "sparse": {"enabled": True, "k": 5, "weight": 0.3},
                "graph": {"enabled": True, "k": 10, "weight": 0.2},
            },
            graph_policy="conditional",
        )
        config = policy.resolve_config()
        config["graph_task_types"] = ["code_review"]  # 手工收窄，模拟老策略
        policy.resolve_config = lambda overrides=None: config  # type: ignore[method-assign]
        orchestrator = self._orchestrator([
            RetrievalCandidate(id="a", content="登录", source="dense",
                               source_type="dense", score=0.95),
        ])

        result = orchestrator.retrieve(RetrievalRequest(
            project_id=self.project.pk, query="识别风险",
            task_type=ALL_WORKFLOW_STAGES[0], policy=policy,
        ))

        graph = result["channels"]["graph"]
        self.assertFalse(graph["enabled"])
        self.assertEqual(graph["reason"], "not_applicable_task_type")

    def test_stage_task_type_is_not_dropped_as_not_selected(self):
        """四阶段不该因为白名单被摘掉 —— 这正是修复的断点。

        判据取"通道真的跑了"而不是"状态里没有某个词"：
        走过修复后，四阶段在 ``conditional`` 下是**必用图谱**，
        哪怕直连召回分数很高也不会被跳过。
        """
        policy = self._policy(
            sources={
                "dense": {"enabled": True, "k": 5, "weight": 0.5},
                "sparse": {"enabled": False},
                "graph": {"enabled": True, "k": 10, "weight": 0.2},
                "structured": {"enabled": False},
                "historical": {"enabled": False},
            },
            graph_policy="conditional",
        )
        # 高分的直连结果：修复前这会触发"直连已够确定"而摘掉图谱。
        orchestrator = self._orchestrator([
            RetrievalCandidate(id="a", content="登录", source="dense",
                               source_type="dense", score=0.99),
        ])
        stage = ALL_WORKFLOW_STAGES[0]
        request = RetrievalRequest(
            project_id=self.project.pk, query="识别风险", task_type=stage, policy=policy,
        )

        result = orchestrator.retrieve(request)

        graph = result["channels"]["graph"]
        self.assertTrue(graph["enabled"])
        self.assertNotIn("reason", graph)

    def test_policy_never_reports_its_own_reason(self):
        """显式关停记 ``policy_never``：这是预期行为，与配置漏项区分开。"""
        policy = self._policy(
            sources={
                "dense": {"enabled": True, "k": 5, "weight": 0.5},
                "sparse": {"enabled": False}, "structured": {"enabled": False},
                "graph": {"enabled": True, "k": 10, "weight": 0.2},
            },
            graph_policy="conditional",
        )
        request = RetrievalRequest(
            project_id=self.project.pk, query="识别风险",
            task_type=ALL_WORKFLOW_STAGES[0], policy=policy, graph_policy="never",
        )

        result = self._orchestrator([]).retrieve(request)

        self.assertEqual(result["channels"]["graph"]["reason"], "policy_never")

    def test_disabled_channel_reports_disabled_by_config(self):
        policy = self._policy(
            sources={
                "dense": {"enabled": True, "k": 5, "weight": 0.5},
                "sparse": {"enabled": False}, "structured": {"enabled": False},
                "graph": {"enabled": False},
            },
        )
        request = RetrievalRequest(
            project_id=self.project.pk, query="x", task_type="code_review", policy=policy,
        )

        result = self._orchestrator([]).retrieve(request)

        self.assertEqual(result["channels"]["graph"]["reason"], "disabled_by_config")
        self.assertEqual(result["channels"]["sparse"]["reason"], "disabled_by_config")

    def test_channels_are_returned_for_every_source(self):
        """每个源都要有一条记录，缺一条就会让"它到底跑没跑"变成猜测。"""
        policy = self._policy(
            sources={
                "dense": {"enabled": True, "k": 5, "weight": 0.5},
                "sparse": {"enabled": True, "k": 5, "weight": 0.3},
                "graph": {"enabled": True, "k": 10, "weight": 0.2},
                "structured": {"enabled": True, "k": 5, "weight": 0.1},
                "historical": {"enabled": False},
            },
        )
        request = RetrievalRequest(
            project_id=self.project.pk, query="登录", task_type="code_review", policy=policy,
        )

        result = self._orchestrator([]).retrieve(request)

        self.assertEqual(
            set(result["channels"]),
            {"dense", "sparse", "graph", "structured", "historical"},
        )
        self.assertIsInstance(result["channels"]["dense"]["hits"], int)
