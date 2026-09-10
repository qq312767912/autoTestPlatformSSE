import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from .models import TestCaseReview
from .review_service import run_testcase_review

logger = logging.getLogger(__name__)


@shared_task(bind=True, soft_time_limit=55 * 60, time_limit=60 * 60)
def execute_testcase_review(self, review_id):
    try:
        return run_testcase_review(review_id)
    except SoftTimeLimitExceeded:
        message = "审查超过 55 分钟，请缩小文件范围后重试"
        TestCaseReview.objects.filter(pk=review_id).update(status="failed", current_step="审查超时", error_message=message, completed_at=timezone.now())
        raise
    except Exception as exc:
        logger.exception("测试用例审查失败: %s", review_id)
        TestCaseReview.objects.filter(pk=review_id).update(status="failed", current_step="审查失败", error_message=str(exc)[:2000], completed_at=timezone.now())
        raise
