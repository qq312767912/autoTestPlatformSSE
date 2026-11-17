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

    def _direct_review_engine(self, vision=True):
        from types import SimpleNamespace
        from .services import RequirementReviewEngine
        engine = RequirementReviewEngine.__new__(RequirementReviewEngine)
        engine.llm_config = SimpleNamespace(supports_vision=vision, name="test-model")
        return engine

    def test_direct_multimodal_review_sends_unconfirmed_original_in_place(self):
        engine = self._direct_review_engine()
        self.document.content = "前文\n![截图](docimg://raw)\n后文"
        DocumentImage.objects.create(
            document=self.document, module=self.module, image_id="raw",
            image_file=SimpleUploadedFile("raw.png", b"original-image", content_type="image/png"),
            content_type="image/png", review_status="pending",
        )
        content = engine._review_source_content(self.document)
        parts, multimodal, warning = engine._prepare_analysis_content(content, self.document)
        self.assertTrue(multimodal)
        self.assertIsNone(warning)
        self.assertEqual([part['type'] for part in parts], ['text', 'image_url', 'text'])
        self.assertEqual(parts[1]['image_url']['url'], 'data:image/png;base64,b3JpZ2luYWwtaW1hZ2U=')

    def test_module_direct_review_only_includes_own_enabled_images(self):
        engine = self._direct_review_engine()
        for key, module, enabled in [('own', self.module, True), ('disabled', self.module, False), ('other', None, True)]:
            DocumentImage.objects.create(
                document=self.document, module=module, image_id=key, is_enabled=enabled,
                image_file=SimpleUploadedFile(key + '.png', b'raw'), content_type='image/png',
            )
        content = engine._review_source_content(self.document, self.module)
        parts, multimodal, _ = engine._prepare_analysis_content(content, self.document)
        self.assertIn('docimg://own', content)
        self.assertNotIn('docimg://disabled', content)
        self.assertNotIn('docimg://other', content)
        self.assertTrue(multimodal)
        self.assertEqual(sum(part['type'] == 'image_url' for part in parts), 1)

    def test_text_model_keeps_confirmed_results_without_images(self):
        engine = self._direct_review_engine(vision=False)
        DocumentImage.objects.create(
            document=self.document, module=self.module, image_id='confirmed',
            image_file=SimpleUploadedFile('confirmed.png', b'raw'),
            analysis_result={'content_summary': '已确认的页面内容'}, review_status='confirmed',
        )
        content = engine._review_source_content(self.document, self.module)
        parts, multimodal, warning = engine._prepare_analysis_content(content, self.document)
        self.assertFalse(multimodal)
        self.assertIn('已确认的页面内容', parts)
        self.assertIsInstance(parts, str)

    def test_module_review_request_contains_original_image_without_vision_mcp(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        engine = self._direct_review_engine()
        engine.llm = Mock()
        engine._get_user_prompt = Mock(return_value='分析模块：{module_content}')
        DocumentImage.objects.create(
            document=self.document, module=self.module, image_id='module-raw',
            image_file=SimpleUploadedFile('module.png', b'raw'), content_type='image/png',
        )
        with patch('requirements.services.safe_llm_invoke', return_value=SimpleNamespace(content='{"issues": []}')) as invoke:
            result = engine._analyze_single_module(self.module, {})
        self.assertEqual(result['module_id'], str(self.module.id))
        message = invoke.call_args.args[1][1]
        self.assertTrue(any(part['type'] == 'image_url' for part in message.content))
        self.assertTrue(any('导入企业数据' in part.get('text', '') for part in message.content))

    def test_unsplit_review_also_sends_original_images(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        engine = self._direct_review_engine()
        engine.llm = Mock()
        engine._get_user_prompt = Mock(return_value='评审：{content}')
        self.document.content = '需求正文'
        DocumentImage.objects.create(
            document=self.document, image_id='direct',
            image_file=SimpleUploadedFile('direct.png', b'raw'), content_type='image/png',
        )
        with patch('requirements.services.safe_llm_invoke', return_value=SimpleNamespace(content='{}')) as invoke:
            engine.analyze_document_directly(self.document.content, document=self.document)
        self.assertTrue(any(p['type'] == 'image_url' for p in invoke.call_args.args[1][1].content))
