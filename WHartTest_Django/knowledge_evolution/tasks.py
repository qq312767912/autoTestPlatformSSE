"""知识数据飞轮 Celery 任务。"""

import logging

from celery import shared_task

from .projection import ProjectionService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_projection_to_index(self, projection_id: str):
    """异步消费 Outbox，构建/重建版本化 Qdrant 投影。"""
    try:
        ProjectionService().build(projection_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("同步投影失败: %s", projection_id)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def rollback_projection_alias(self, project_id: str, index_type: str = "qdrant_dense"):
    """异步回滚项目级投影 alias 到上一版本。"""
    try:
        ProjectionService().rollback(project_id, index_type)
    except Exception as exc:  # noqa: BLE001
        logger.exception("回滚投影失败: project=%s index_type=%s", project_id, index_type)
        raise self.retry(exc=exc)


@shared_task
def inspect_all_projects_knowledge_health():
    """每日防腐巡检：过期、冲突、失败投影和孤立图节点。"""
    from projects.models import Project
    from .operations import KnowledgeHealthService
    service = KnowledgeHealthService()
    return [service.inspect(project_id) for project_id in Project.objects.values_list("id", flat=True)]


@shared_task
def rebuild_stale_projections():
    """每周投影对账后的重建入口；每个投影仍由原有幂等任务执行。"""
    from .knowledge_models import IndexProjection
    queued = []
    for projection_id in IndexProjection.objects.filter(state__in=["failed", "stale"]).values_list("id", flat=True):
        sync_projection_to_index.delay(str(projection_id))
        queued.append(str(projection_id))
    return {"queued": queued, "count": len(queued)}


@shared_task
def snapshot_monthly_flywheel_metrics():
    """每月评测/运营快照；结果由 Celery backend 留档，可被运营看板读取。"""
    from projects.models import Project
    from .operations import FlywheelMetricsService
    service = FlywheelMetricsService()
    return [service.summarize(project_id) for project_id in Project.objects.values_list("id", flat=True)]
