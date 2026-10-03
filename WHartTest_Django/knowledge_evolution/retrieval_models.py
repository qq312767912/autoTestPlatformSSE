"""任务 9：可版本化检索编排器配置模型。"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


def _default_graph_task_types() -> list[str]:
    """默认必用图谱召回的 task_type。

    四阶段（方案 / 用例 / 执行 / 报告）必须在这里 —— 它们恰恰是
    "要参考需求、既有用例与知识"的典型场景：生成方案要覆盖需求点，
    生成用例要参考需求模块与既有用例，报告要能追到"这条结论依据哪条需求"。

    漏掉它们的表现是**通道被静默摘掉**（``_should_use_graph`` 判否后
    ``per_source.pop("graph")``）：产出里看不出任何异常，只是"没有溯源"。
    页面上表现为"图谱明明有数据、产出却引不到"，排查时容易被误判成图谱侧没建好。

    从注册表取而不是抄字面量：阶段名只有一处真值，复制一份到检索配置里，
    改阶段名时必然漏改。
    """
    from .capability_registry import ALL_WORKFLOW_STAGES

    base = [
        "code_review",           # 代码影响面
        "defect_root_cause",     # 缺陷根因
        "requirement_coverage",  # 需求覆盖
        "impact_analysis",       # 影响分析
        "case_review",           # 用例审查：要对照需求与既有用例
    ]
    return list(dict.fromkeys([*base, *ALL_WORKFLOW_STAGES]))


class RetrievalPolicy(models.Model):
    """检索策略（版本化配置）。

    设计 §6.1：RetrievalRequest 包含 policy_version、task_type、graph_policy、预算。
    策略不随代码发布，支持项目级覆盖与回滚。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="retrieval_policies"
    )
    name = models.CharField(max_length=100, db_index=True)
    version = models.PositiveIntegerField(default=1)
    is_default = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    config = models.JSONField(
        default=dict, blank=True,
        help_text="检索策略：召回源权重、graph_policy、budget、rerank/mmr 参数",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_retrieval_policies",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "version"], name="uniq_retrieval_policy_project_name_ver"
            ),
        ]
        indexes = [
            models.Index(fields=["project", "is_default", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version} ({self.project.name})"

    @classmethod
    def get_default(cls, project_id) -> "RetrievalPolicy | None":
        return cls.objects.filter(
            project_id=project_id, is_default=True, is_active=True
        ).order_by("-version").first()

    def resolve_config(self, overrides: dict | None = None) -> dict:
        """合并默认配置与用户覆盖。"""
        default = {
            "sources": {
                "dense": {"enabled": True, "k": 40, "weight": 0.35},
                "sparse": {"enabled": True, "k": 40, "weight": 0.30},
                "graph": {"enabled": True, "k": 80, "weight": 0.20},
                "structured": {"enabled": True, "k": 20, "weight": 0.10},
                "historical": {"enabled": False, "k": 10, "weight": 0.05},
            },
            "graph_policy": "conditional",  # always / conditional / never
            "graph_task_types": _default_graph_task_types(),
            "rrf_k": 60,
            "rerank": {"enabled": False, "top_k": 30},
            "mmr": {"enabled": True, "lambda_param": 0.7, "final_k": 10},
            "budget": {"time_ms": 3000, "token": 4000},
            "citation": {
                "include_location": True,
                "include_freshness": True,
                "include_access_scope": True,
            },
            "conflict": {"include_open_conflicts": True, "trust_penalty_threshold": 0.5},
        }
        config = {**default, **(self.config or {})}
        if overrides:
            config.update(overrides)
        # 四阶段**不允许**被配置排除在图谱白名单外。
        #
        # 这里刻意取并集而不是"配置说了算"：本项配置的作用是"哪些任务需要图谱"，
        # 而排除阶段的后果是**静默断链** —— 通道被摘掉（``per_source.pop("graph")``），
        # 产出里没有任何异常，只是"引不到需求与既有用例"。页面上表现为
        # "图谱里明明有数据、产出却溯源为空"，排查方向会被带到图谱侧去。
        # 而错误地多跑一次图谱的后果只是多一点 token —— 两者的代价不对称。
        #
        # 真正想关掉图谱的项目有两个明确出口：``sources.graph.enabled=False``
        # 或 ``graph_policy="never"``。它们都在"通道"一层，语义清楚且可观测。
        merged = list(config.get("graph_task_types") or [])
        for task_type in _default_graph_task_types():
            if task_type not in merged:
                merged.append(task_type)
        config["graph_task_types"] = merged
        return config
