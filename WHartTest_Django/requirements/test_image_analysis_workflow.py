import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from projects.models import Project

from .image_analysis import (
    _plain_result,
    confirmed_document_content,
    confirmed_image_context,
    confirmed_module_content,
)
from .models import DocumentImage, RequirementDocument, RequirementModule
from .serializers import DocumentImageSerializer, RequirementModuleSerializer


class RequirementImageWorkflowTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.user = User.objects.create_user(username="vision-user")
        self.project = Project.objects.create(name="vision-project", creator=self.user)
        self.document = RequirementDocument.objects.create(
            project=self.project,
            uploader=self.user,
            title="vision requirement",
            document_type="docx",
        )
        self.module = RequirementModule.objects.create(
            document=self.document,
            title="科技评价导入",
            content="导入企业数据",
            order=1,
        )

    def test_only_confirmed_enabled_images_enter_module_context(self):
        image = DocumentImage.objects.create(
            document=self.document,
            module=self.module,
            image_id="img_001",
            image_file=SimpleUploadedFile("page.png", b"png", content_type="image/png"),
            page_title="导入页面",
            change_type="add",
            change_description="新增强制更新选项",
            ocr_text="强制更新",
            suggested_test_points=["默认不勾选", "勾选后覆盖旧数据"],
            review_status="confirmed",
            is_enabled=True,
        )
        context = confirmed_image_context(self.module)
        self.assertIn("新增强制更新选项", context)
        self.assertIn("默认不勾选", context)

        image.review_status = "analyzed"
        image.save(update_fields=["review_status"])
        self.assertEqual(confirmed_image_context(self.module), "")

    def test_module_serializer_exposes_confirmed_image_context(self):
        DocumentImage.objects.create(
            document=self.document,
            module=self.module,
            image_id="img_002",
            image_file=SimpleUploadedFile("page2.png", b"png", content_type="image/png"),
            change_description="新增查询按钮",
            review_status="confirmed",
        )
        data = RequirementModuleSerializer(self.module).data
        self.assertIn("新增查询按钮", data["confirmed_image_context"])

    def test_image_cannot_be_assigned_to_another_document_module(self):
        other_document = RequirementDocument.objects.create(
            project=self.project,
            uploader=self.user,
            title="other",
            document_type="pdf",
        )
        other_module = RequirementModule.objects.create(
            document=other_document,
            title="other",
            content="other",
            order=1,
        )
        image = DocumentImage.objects.create(
            document=self.document,
            image_id="img_003",
            image_file=SimpleUploadedFile("page3.png", b"png", content_type="image/png"),
        )
        serializer = DocumentImageSerializer(image, data={"module": str(other_module.id)}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn("module", serializer.errors)

    def test_mcp_text_content_is_unwrapped(self):
        payload = _plain_result({"type": "text", "text": '{"full_text":"查询","text_blocks":[]}', "id": "1"})
        self.assertEqual(payload["full_text"], "查询")

    def test_structured_result_has_priority_over_ocr(self):
        image = DocumentImage.objects.create(
            document=self.document,
            module=self.module,
            image_id="img_004",
            image_file=SimpleUploadedFile("page4.png", b"png", content_type="image/png"),
            ocr_text="冗长OCR原文",
            analysis_result={"content_summary": "新增企业查询区域", "business_rules": ["名称支持模糊查询"]},
            review_status="confirmed",
        )
        context = confirmed_image_context(self.module)
        self.assertIn("新增企业查询区域", context)
        self.assertNotIn("冗长OCR原文", context)
        image.analysis_result = {}
        image.save(update_fields=["analysis_result"])
        self.assertIn("冗长OCR原文", confirmed_image_context(self.module))

    def test_confirmed_image_is_expanded_at_marker_without_changing_stored_content(self):
        self.module.content = "查询条件\n![需求截图](docimg://img_005)\n提交查询"
        self.module.save(update_fields=["content"])
        self.document.content = self.module.content
        self.document.save(update_fields=["content"])
        DocumentImage.objects.create(
            document=self.document,
            module=self.module,
            image_id="img_005",
            image_file=SimpleUploadedFile("page5.png", b"png", content_type="image/png"),
            ocr_text="查询按钮",
            review_status="confirmed",
        )
        module_content = confirmed_module_content(self.module)
        document_content = confirmed_document_content(self.document)
        self.assertIn("查询条件\n[用户已确认的需求图片 img_005]", module_content)
        self.assertIn("OCR兜底", document_content)
        self.assertIn("docimg://img_005", self.module.content)
