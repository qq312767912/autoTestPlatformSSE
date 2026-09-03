import logging

import requests
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from .models import AnalysisTask
from .services import AnalysisCancelled, run_analysis


logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def run_code_analysis(self, task_id):
    """在后台执行代码分析；业务进度和最终结果以数据库记录为准。"""
    try:
        task = AnalysisTask.objects.select_related(
            "project", "repository__connection", "creator"
        ).get(pk=task_id)
    except AnalysisTask.DoesNotExist:
        return {"status": "missing"}

    if task.status == "cancelled":
        return {"status": "cancelled"}

    try:
        run_analysis(task)
        task.refresh_from_db(fields=["status"])
        return {"status": task.status}
    except AnalysisCancelled:
        return {"status": "cancelled"}
    except SoftTimeLimitExceeded:
        AnalysisTask.objects.filter(pk=task_id).exclude(status="cancelled").update(
            status="failed", current_step="分析超时", error_message="分析超过系统允许的最长执行时间"
        )
        raise
    except Exception:
        logger.exception("代码审查任务执行失败: %s", task_id)
        raise
