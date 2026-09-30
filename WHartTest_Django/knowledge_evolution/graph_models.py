"""任务 8：文档图谱节点/边模型（design.md §4.3 图谱投影）。

原则：不改写 CRG 代码图谱数据库，用 PostgreSQL 邻接表实现文档侧图投影。
GraphNode / GraphEdge 是事实层，IndexProjection（index_type=document_graph）做版本化投影。
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class GraphNode(models.Model):
    """图谱节点：Document / Section / Chunk / Concept / Rule / Evidence / Asset / Version / Candidate / Source。

    `external_id` 是项目级稳定的节点标识； projection 为空时表示事实层全局图。
    """

    NODE_TYPE_CHOICES = [
        ("document", "文档"),
        ("section", "章节"),
        ("chunk", "分块"),
        ("concept", "概念"),
        ("rule", "规则/约束"),
        ("fact", "事实"),
        ("procedure", "流程/步骤"),
        ("evidence", "证据"),
        ("asset", "知识资产"),
        ("version", "知识版本"),
        ("candidate", "知识候选"),
        ("source", "来源快照"),
        ("workflow_output", "流程产出"),
        ("requirement_document", "需求文档"),
        ("requirement_module", "需求模块"),
        ("test_case", "测试用例"),
        ("test_step", "测试步骤"),
        ("test_module", "测试模块"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_graph_nodes"
    )
    projection = models.ForeignKey(
        "knowledge_evolution.IndexProjection", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_nodes",
        help_text="所属投影；为空表示事实层全局图",
    )
    snapshot = models.ForeignKey(
        "knowledge_evolution.SourceSnapshot", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_nodes",
    )
    asset = models.ForeignKey(
        "knowledge_evolution.KnowledgeAsset", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_nodes",
    )
    version = models.ForeignKey(
        "knowledge_evolution.KnowledgeVersion", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_nodes",
    )
    candidate = models.ForeignKey(
        "knowledge_evolution.KnowledgeCandidate", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_nodes",
    )
    chunk = models.ForeignKey(
        "knowledge.DocumentChunk", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_nodes",
    )
    node_type = models.CharField(max_length=32, choices=NODE_TYPE_CHOICES, db_index=True)
    external_id = models.CharField(max_length=255, db_index=True, help_text="项目级稳定节点 ID")
    name = models.CharField(max_length=500, blank=True)
    properties = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "node_type", "external_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "external_id"], name="uniq_graphnode_project_external_id"
            ),
        ]
        indexes = [
            models.Index(fields=["project", "node_type", "created_at"]),
            models.Index(fields=["project", "projection", "node_type"]),
            models.Index(fields=["snapshot", "node_type"]),
            models.Index(fields=["asset", "node_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.node_type}:{self.external_id}"

    @property
    def project_id(self):
        return self.project_id


class GraphEdge(models.Model):
    """图谱边。关系类型按 design.md §4.3 分类：结构、语义、证据。"""

    RELATION_CHOICES = [
        # 结构
        ("CONTAINS", "包含"),
        ("PART_OF", "属于"),
        ("NEXT", "下一项"),
        # 语义
        ("MENTIONS", "提及"),
        ("DEFINES", "定义"),
        ("REFINES", "细化"),
        ("CONTRADICTS", "矛盾"),
        ("SUPERSEDES", "取代"),
        # 证据
        ("SUPPORTED_BY", "由…支持"),
        ("DERIVED_FROM", "派生自"),
        ("ACCEPTED_BY", "被…采纳"),
        ("REFUTED_BY", "被…反驳"),
        ("FEEDS_INTO", "流转到"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_graph_edges"
    )
    projection = models.ForeignKey(
        "knowledge_evolution.IndexProjection", on_delete=models.CASCADE,
        null=True, blank=True, related_name="graph_edges",
    )
    from_node = models.ForeignKey(
        GraphNode, on_delete=models.CASCADE, related_name="outgoing_edges"
    )
    to_node = models.ForeignKey(
        GraphNode, on_delete=models.CASCADE, related_name="incoming_edges"
    )
    relation = models.CharField(max_length=32, choices=RELATION_CHOICES, db_index=True)
    properties = models.JSONField(default=dict, blank=True)
    weight = models.FloatField(default=1.0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-weight", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "projection", "from_node", "to_node", "relation"],
                name="uniq_graphedge_project_proj_from_to_rel",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "relation", "created_at"]),
            models.Index(fields=["from_node", "relation"]),
            models.Index(fields=["to_node", "relation"]),
        ]

    def __str__(self) -> str:
        return f"{self.from_node.external_id} {self.relation} {self.to_node.external_id}"
