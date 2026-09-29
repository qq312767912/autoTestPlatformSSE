"""任务 9：可版本化检索编排器配置模型。"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


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
            "graph_task_types": ["code_review", "defect_root_cause", "requirement_coverage", "impact_analysis"],
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
        return config
