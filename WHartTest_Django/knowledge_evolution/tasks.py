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
