"""需求与测试用例图谱适配器。

把业务实体（RequirementDocument / RequirementModule / TestCase / TestCaseStep / TestCaseModule）
投影到 PostgreSQLGraphSource，供知识图谱工作台统一浏览。
"""

from __future__ import annotations

from typing import List, Optional

from django.db import transaction

from .graph import GraphEdgeSpec, GraphNodeSpec, PostgreSQLGraphSource
from .graph_models import GraphNode


class RequirementGraphAdapter:
    """把需求文档及其模块投影成图谱。

    节点类型：requirement_document / requirement_module
    关系：CONTAINS（文档→模块、父模块→子模块）、NEXT（模块顺序）
    """

    def __init__(self, graph_source: Optional[PostgreSQLGraphSource] = None):
        self.graph = graph_source or PostgreSQLGraphSource()

    @staticmethod
    def _doc_external_id(document_id) -> str:
        return f"req_doc:{document_id}"

    @staticmethod
    def _module_external_id(module_id) -> str:
        return f"req_module:{module_id}"

    def build_from_document(self, document) -> dict:
        """基于 RequirementDocument 建图，幂等写入。"""
        from requirements.models import RequirementDocument, RequirementModule

        if not isinstance(document, RequirementDocument):
            document = RequirementDocument.objects.get(pk=document)

        project_id = document.project_id
        doc_external = self._doc_external_id(document.pk)

        nodes: List[GraphNodeSpec] = [
            GraphNodeSpec(
                node_type="requirement_document",
                external_id=doc_external,
                name=document.title or "未命名需求文档",
                properties={
                    "source_type": "requirement",
                    "source_id": str(document.pk),
                    "category": document.document_category,
                    "status": document.status,
                    "version": document.version,
                    "description": (document.description or "")[:500],
                },
            )
        ]
        edges: List[GraphEdgeSpec] = []

        modules = list(
            RequirementModule.objects.filter(document=document)
            .select_related("parent_module")
            .order_by("order", "created_at")
        )
        module_external_map = {}
        prev_external = None
        for module in modules:
            mod_external = self._module_external_id(module.pk)
            module_external_map[module.pk] = mod_external
            nodes.append(
                GraphNodeSpec(
                    node_type="requirement_module",
                    external_id=mod_external,
                    name=module.title or "未命名模块",
                    properties={
                        "source_type": "requirement",
                        "source_id": str(document.pk),
                        "module_id": str(module.pk),
                        "order": module.order,
                        "is_auto_generated": module.is_auto_generated,
                        "confidence": module.confidence_score,
                        "content_preview": (module.content or "")[:500],
                    },
                )
            )
            edges.append(GraphEdgeSpec(doc_external, mod_external, "CONTAINS"))
            if module.parent_module_id and module.parent_module_id in module_external_map:
                edges.append(
                    GraphEdgeSpec(module_external_map[module.parent_module_id], mod_external, "CONTAINS")
                )
            if prev_external:
                edges.append(GraphEdgeSpec(prev_external, mod_external, "NEXT"))
            prev_external = mod_external

        node_map = self.graph.upsert_nodes(project_id=project_id, projection_id=None, nodes=nodes)
        edge_count = self.graph.add_edges(project_id=project_id, projection_id=None, edges=edges)
        return {
            "document_external_id": doc_external,
            "node_count": len(node_map),
            "edge_count": edge_count,
            "module_count": len(modules),
        }

    def clear_document(self, document) -> int:
        """删除某个需求文档投影出的全部节点。"""
        from requirements.models import RequirementDocument, RequirementModule

        if not isinstance(document, RequirementDocument):
            document = RequirementDocument.objects.get(pk=document)
        project_id = document.project_id
        module_ids = RequirementModule.objects.filter(document=document).values_list("pk", flat=True)
        external_ids = {self._doc_external_id(document.pk)}
        external_ids.update(self._module_external_id(mid) for mid in module_ids)
        deleted = GraphNode.objects.filter(
            project_id=project_id, external_id__in=external_ids
        ).count()
        GraphNode.objects.filter(project_id=project_id, external_id__in=external_ids).delete()
        return deleted


class TestCaseGraphAdapter:
    """把测试用例及其步骤、模块投影成图谱。

    节点类型：test_module / test_case / test_step
    关系：CONTAINS（模块→用例、用例→步骤）、NEXT（步骤顺序）
    """

    def __init__(self, graph_source: Optional[PostgreSQLGraphSource] = None):
        self.graph = graph_source or PostgreSQLGraphSource()

    @staticmethod
    def _module_external_id(module_id) -> str:
        return f"test_module:{module_id}"

    @staticmethod
    def _case_external_id(case_id) -> str:
        return f"test_case:{case_id}"

    @staticmethod
    def _step_external_id(step_id) -> str:
        return f"test_step:{step_id}"

    def build_from_project(self, project_id: int) -> dict:
        """基于项目下全部测试用例建图，幂等写入。"""
        from testcases.models import TestCase, TestCaseModule, TestCaseStep

        nodes: List[GraphNodeSpec] = []
        edges: List[GraphEdgeSpec] = []

        # 模块节点
        modules = list(TestCaseModule.objects.filter(project_id=project_id).order_by("id"))
        module_external_map = {}
        for module in modules:
            mod_external = self._module_external_id(module.pk)
            module_external_map[module.pk] = mod_external
            nodes.append(
                GraphNodeSpec(
                    node_type="test_module",
                    external_id=mod_external,
                    name=module.name or "未命名模块",
                    properties={
                        "source_type": "test_case",
                        "source_id": str(project_id),
                        "module_id": str(module.pk),
                        "level": module.level,
                    },
                )
            )
            if module.parent_id and module.parent_id in module_external_map:
                edges.append(GraphEdgeSpec(module_external_map[module.parent_id], mod_external, "CONTAINS"))

        # 用例节点与步骤
        cases = list(
            TestCase.objects.filter(project_id=project_id)
            .select_related("module")
            .order_by("-created_at")
        )
        for case in cases:
            case_external = self._case_external_id(case.pk)
            nodes.append(
                GraphNodeSpec(
                    node_type="test_case",
                    external_id=case_external,
                    name=case.name or "未命名用例",
                    properties={
                        "source_type": "test_case",
                        "source_id": str(project_id),
                        "case_id": str(case.pk),
                        "level": case.level,
                        "test_type": case.test_type,
                        "review_status": case.review_status,
                        "execution_mode": case.execution_mode,
                        "precondition": (case.precondition or "")[:300],
                    },
                )
            )
            if case.module_id and case.module_id in module_external_map:
                edges.append(GraphEdgeSpec(module_external_map[case.module_id], case_external, "CONTAINS"))

            prev_step_external = None
            steps = list(TestCaseStep.objects.filter(test_case=case).order_by("step_number"))
            for step in steps:
                step_external = self._step_external_id(step.pk)
                nodes.append(
                    GraphNodeSpec(
                        node_type="test_step",
                        external_id=step_external,
                        name=f"步骤 {step.step_number}",
                        properties={
                            "source_type": "test_case",
                            "source_id": str(project_id),
                            "case_id": str(case.pk),
                            "step_number": step.step_number,
                            "description": (step.description or "")[:300],
                            "expected_result": (step.expected_result or "")[:300],
                        },
                    )
                )
                edges.append(GraphEdgeSpec(case_external, step_external, "CONTAINS"))
                if prev_step_external:
                    edges.append(GraphEdgeSpec(prev_step_external, step_external, "NEXT"))
                prev_step_external = step_external

        node_map = self.graph.upsert_nodes(project_id=project_id, projection_id=None, nodes=nodes)
        edge_count = self.graph.add_edges(project_id=project_id, projection_id=None, edges=edges)
        return {
            "project_id": project_id,
            "node_count": len(node_map),
            "edge_count": edge_count,
            "module_count": len(modules),
            "case_count": len(cases),
        }

    def clear_project(self, project_id: int) -> int:
        """删除某个项目测试用例投影出的全部节点。"""
        deleted = GraphNode.objects.filter(
            project_id=project_id, node_type__in=["test_module", "test_case", "test_step"]
        ).count()
        GraphNode.objects.filter(
            project_id=project_id, node_type__in=["test_module", "test_case", "test_step"]
        ).delete()
        return deleted
