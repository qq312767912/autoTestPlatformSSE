import logging

import requests
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction

from .models import AnalysisTask
from .services import AnalysisCancelled, retry_ocr_analysis, run_analysis


logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"fetching", "machine_analyzing", "ai_analyzing", "generating_tests"}


def _claim_global_slot(task_id, current_step):
    """通过锁定任务表串行化抢占，保证全平台同时只执行一个代码审查。"""
    with transaction.atomic():
        # 强制执行 SELECT ... FOR UPDATE，避免多个 worker 同时判断为空。
        list(AnalysisTask.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
        try:
            task = AnalysisTask.objects.get(pk=task_id)
        except AnalysisTask.DoesNotExist:
            return "missing"
        if task.status == "cancelled":
            return "cancelled"
        if AnalysisTask.objects.filter(status__in=ACTIVE_STATUSES).exclude(pk=task_id).exists():
            AnalysisTask.objects.filter(pk=task_id).update(status="queued", progress=0, current_step="排队中")
            return "queued"
        AnalysisTask.objects.filter(pk=task_id).update(status="fetching", current_step=current_step)
        return "claimed"


@shared_task(
    bind=True,
    max_retries=None,
    autoretry_for=(requests.RequestException,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=140 * 60,
    time_limit=150 * 60,
)
def run_code_analysis(self, task_id, force_refresh=False):
    """在后台执行代码分析；业务进度和最终结果以数据库记录为准。"""
    claim = _claim_global_slot(task_id, "准备执行代码审查")
    if claim in {"missing", "cancelled"}:
        return {"status": claim}
    if claim == "queued":
        raise self.retry(countdown=15)
    try:
        task = AnalysisTask.objects.select_related(
            "project", "repository__connection", "creator", "executor"
        ).get(pk=task_id)
    except AnalysisTask.DoesNotExist:
        return {"status": "missing"}

    if task.status == "cancelled":
        return {"status": "cancelled"}

    try:
        run_analysis(task, force_refresh=force_refresh)
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


@shared_task(bind=True, max_retries=None, soft_time_limit=80 * 60, time_limit=90 * 60)
def retry_code_analysis_ocr(self, task_id):
    claim = _claim_global_slot(task_id, "准备重试 OCR 审查")
    if claim in {"missing", "cancelled"}:
        return {"status": claim}
    if claim == "queued":
        raise self.retry(countdown=15)
    try:
        task = AnalysisTask.objects.get(pk=task_id)
        retry_ocr_analysis(task)
        task.refresh_from_db(fields=["status"])
        return {"status": task.status}
    except AnalysisCancelled:
        return {"status": "cancelled"}
    except Exception:
        logger.exception("OCR 单独重试失败: %s", task_id)
        AnalysisTask.objects.filter(pk=task_id).exclude(status="cancelled").update(
            status="degraded", progress=100, current_step="OCR 重试失败，保留降级报告",
        )
        raise
