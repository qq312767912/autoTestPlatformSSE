"""任务 17–19：防腐检查、联合链路图和运营指标。"""
from collections import Counter

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from .capability_models import CapabilityRelease
from .evaluation_models import EvaluationResult, EvaluationRun
from .graph import GraphEdgeSpec, GraphNodeSpec, PostgreSQLGraphSource
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import IndexProjection, KnowledgeConflict, KnowledgeVersion
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


class KnowledgeHealthService:
    def inspect(self, project_id):
        expired = KnowledgeVersion.objects.filter(
            asset__project_id=project_id, status="published", expires_at__lt=timezone.now()
        )
        failed_projections = IndexProjection.objects.filter(project_id=project_id, state="failed")
        stale_projections = IndexProjection.objects.filter(project_id=project_id, state="stale")
        open_conflicts = KnowledgeConflict.objects.filter(project_id=project_id, state__in=["open", "resolving"])
        isolated_nodes = GraphNode.objects.filter(project_id=project_id).annotate(
            degree=Count("outgoing_edges") + Count("incoming_edges")
        ).filter(degree=0)
        return {
            "project_id": project_id,
            "checked_at": timezone.now().isoformat(),
            "expired_versions": expired.count(),
            "failed_projections": failed_projections.count(),
            "stale_projections": stale_projections.count(),
            "open_conflicts": open_conflicts.count(),
            "isolated_graph_nodes": isolated_nodes.count(),
            "healthy": not any((expired.exists(), failed_projections.exists(), open_conflicts.exists())),
        }


class WorkflowGraphBuilder:
    """从统一协议 metadata 构建需求/风险—方案—用例—执行—问题联合图。"""
    def __init__(self, graph=None):
        self.graph = graph or PostgreSQLGraphSource()

    def build(self, project_id, workflow_id):
        outputs = list(GenerationOutput.objects.filter(
            project_id=project_id, metadata__protocol__workflow_id=workflow_id
        ).order_by("created_at"))
        nodes, edges = [], []
        for output in outputs:
            protocol = (output.metadata or {}).get("protocol") or {}
            external_id = f"output:{output.id}"
            nodes.append(GraphNodeSpec(
                node_type="workflow_output", external_id=external_id,
                name=f"{protocol.get('stage', output.task_type)}:{output.task_id}",
                properties={"workflow_id": workflow_id, "stage": protocol.get("stage"), "output_id": str(output.id)},
            ))
            for parent_id in protocol.get("parent_output_ids") or []:
                edges.append(GraphEdgeSpec(
                    from_external_id=f"output:{parent_id}", to_external_id=external_id,
                    relation="FEEDS_INTO", properties={"workflow_id": workflow_id},
                ))
        self.graph.upsert_nodes(project_id=project_id, projection_id=None, nodes=nodes)
        edge_count = self.graph.add_edges(project_id=project_id, projection_id=None, edges=edges)
        return {"workflow_id": workflow_id, "node_count": len(nodes), "edge_count": edge_count}


class FlywheelMetricsService:
    def summarize(self, project_id):
        traces = RetrievalTrace.objects.filter(project_id=project_id)
        feedback = FeedbackEvent.objects.filter(project_id=project_id)
        results = EvaluationResult.objects.filter(run__suite__project_id=project_id, status="completed")
        signals = dict(feedback.values_list("signal").annotate(count=Count("id")))
        accepted = signals.get("accepted", 0) + signals.get("defect_confirmed", 0) + signals.get("test_passed", 0)
        rejected = signals.get("rejected", 0) + signals.get("false_positive", 0)
        decisions = accepted + rejected
        return {
            "project_id": project_id,
            "traces": traces.count(),
            "outputs": GenerationOutput.objects.filter(project_id=project_id).count(),
            "feedback": feedback.count(),
            "signals": signals,
            "adoption_rate": accepted / decisions if decisions else None,
            "false_positive_rate": signals.get("false_positive", 0) / decisions if decisions else None,
            "miss_count": signals.get("missed", 0),
            "token_usage": traces.aggregate(total=Sum("token_usage"))["total"] or 0,
            "average_l0": results.aggregate(value=Avg("l0_score"))["value"],
            "average_l1": results.aggregate(value=Avg("l1_score"))["value"],
            "average_l2": results.aggregate(value=Avg("l2_score"))["value"],
            "average_l3": results.aggregate(value=Avg("l3_score"))["value"],
            "evaluation_runs": EvaluationRun.objects.filter(suite__project_id=project_id).count(),
            "active_releases": CapabilityRelease.objects.filter(project_id=project_id, state="active").count(),
            "rollbacks": CapabilityRelease.objects.filter(project_id=project_id, state="rolled_back").count(),
            "open_conflicts": KnowledgeConflict.objects.filter(project_id=project_id, state__in=["open", "resolving"]).count(),
        }
