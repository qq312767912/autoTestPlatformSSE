import logging
from celery import shared_task
from .services import ApiTestTaskExecutionService

logger = logging.getLogger(__name__)


@shared_task
def execute_api_task_async(execution_id):
    """Async celery task for executing a test task suite."""
    logger.info(f"Starting async task execution [ID={execution_id}]")
    try:
        ApiTestTaskExecutionService.execute_task_async(execution_id)
        logger.info(f"Async task execution [ID={execution_id}] completed")
        return True
    except Exception as e:
        logger.error(f"Async task execution [ID={execution_id}] failed: {str(e)}")
        return False
