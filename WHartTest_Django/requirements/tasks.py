"""Celery tasks for requirement review and image analysis."""

import logging

from celery import shared_task

from .image_analysis import RequirementImageAnalysisService
from .models import RequirementDocument

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="requirements.execute_requirement_review")
def execute_requirement_review(
    self, document_id, analysis_options=None, review_type="comprehensive", user_id=None
):
    """异步执行需求评审任务。"""
    from django.contrib.auth import get_user_model

    from .services import RequirementReviewService

    user_model = get_user_model()
    try:
        document = RequirementDocument.objects.get(id=document_id)
        user = None
        if user_id:
            try:
                user = user_model.objects.get(id=user_id)
                logger.info(
                    "开始异步评审文档: %s, 类型: %s, 用户: %s",
                    document.title,
                    review_type,
                    user.username,
                )
            except user_model.DoesNotExist:
                logger.warning("用户 %s 不存在，使用默认配置", user_id)

        review_service = RequirementReviewService(user=user)
        if review_type == "direct":
            review_report = review_service.start_direct_review(document, analysis_options or {})
        else:
            review_report = review_service.start_comprehensive_review(document, analysis_options or {})

        logger.info("文档 %s 评审完成, 报告ID: %s", document.title, review_report.id)
        return {
            "status": "success",
            "document_id": str(document_id),
            "report_id": str(review_report.id),
            "completion_score": review_report.completion_score,
            "total_issues": review_report.total_issues,
        }
    except RequirementDocument.DoesNotExist:
        logger.error("文档不存在: %s", document_id)
        return {"status": "error", "message": f"文档不存在: {document_id}"}
    except Exception as exc:
        logger.error("评审任务失败: %s", exc, exc_info=True)
        try:
            document = RequirementDocument.objects.get(id=document_id)
            document.status = "failed"
            document.save()
        except RequirementDocument.DoesNotExist:
            pass
        return {"status": "error", "message": str(exc)}


@shared_task(bind=True, name="requirements.analyze_document_images")
def analyze_document_images(self, document_id: str, image_ids: list[str] | None = None):
    document = RequirementDocument.objects.get(id=document_id)
    try:
        return RequirementImageAnalysisService(document).analyze(image_ids=image_ids, max_workers=2)
    except Exception:
        logger.exception("后台需求图片分析失败: document=%s", document_id)
        document.image_analysis_status = "failed"
        document.save(update_fields=["image_analysis_status"])
        document.images.filter(review_status="processing").update(review_status="error", analysis_error="后台任务异常终止")
        raise
