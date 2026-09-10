import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from testcases.models import TestCaseReview
from testcases.review_service import _read_rows, _write_report
from skills.models import Skill


class TestCaseReviewApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reviewer", password="pass")
        self.project = Project.objects.create(name="Review Project", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="member")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    @patch("testcases.views.execute_testcase_review.delay")
    def test_upload_creates_async_review(self, delay):
        delay.return_value.id = "task-1"
        file = SimpleUploadedFile("cases.xlsx", b"fake", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response = self.client.post(
            f"/api/projects/{self.project.id}/testcase-reviews/",
            {"source_file": file, "business_context": "仅管理员可审批"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        review = TestCaseReview.objects.get()
        self.assertEqual(review.business_context, "仅管理员可审批")
        self.assertEqual(review.celery_task_id, "task-1")
        self.assertTrue(response.data["source_url"].startswith("/media/"))
        delay.assert_called_once_with(review.id)
        review.delete()

    @patch("testcases.views.execute_testcase_review.delay")
    def test_upload_rejects_unsupported_file(self, delay):
        response = self.client.post(
            f"/api/projects/{self.project.id}/testcase-reviews/",
            {"source_file": SimpleUploadedFile("cases.txt", b"case")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        delay.assert_not_called()

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    @patch("testcases.views.execute_testcase_review.delay")
    def test_specified_skill_and_custom_rules_are_snapshotted(self, delay):
        delay.return_value.id = "task-2"
        skill = Skill.objects.create(
            project=self.project, creator=self.user, name="payment-review",
            description="支付专项审查", skill_content="# 支付审查\n检查金额和幂等性", is_active=True,
        )
        response = self.client.post(
            f"/api/projects/{self.project.id}/testcase-reviews/",
            {
                "source_file": SimpleUploadedFile("payment.xlsx", b"fake"),
                "review_mode": "specified", "selected_skill": skill.id,
                "custom_rules": "退款金额必须与原订单一致",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        review = TestCaseReview.objects.get()
        self.assertEqual(review.skill_name, "payment-review")
        self.assertIn("检查金额和幂等性", review.skill_snapshot)
        self.assertEqual(review.custom_rules, "退款金额必须与原订单一致")
        review.delete()

    @patch("testcases.views.execute_testcase_review.delay")
    def test_specified_review_requires_skill(self, delay):
        response = self.client.post(
            f"/api/projects/{self.project.id}/testcase-reviews/",
            {"source_file": SimpleUploadedFile("cases.xlsx", b"fake"), "review_mode": "specified"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        delay.assert_not_called()


class TestCaseReviewReportTests(TestCase):
    def test_reads_xlsx_and_generates_traceable_report(self):
        with tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            source = os.path.join(root, "cases.xlsx")
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "登录"
            sheet.append(["编号", "模块", "步骤", "预期"])
            sheet.append(["TC-1", "登录", "输入账号密码", "登录成功"])
            workbook.save(source)
            rows = _read_rows(source)
            self.assertEqual(rows[1]["sheet"], "登录")
            self.assertEqual(rows[1]["row"], 2)

            user = get_user_model().objects.create_user(username="report-user")
            project = Project.objects.create(name="Report Project", creator=user)
            review = TestCaseReview.objects.create(project=project, creator=user, source_name="cases.xlsx", source_file="source/cases.xlsx")
            summary = _write_report(review, rows, [{
                "sheet": "登录", "row": 2, "case_id": "TC-1", "module": "登录",
                "original": "登录成功", "severity": "中", "issue_type": "判定标准模糊",
                "description": "缺少可观察结果", "suggestion": "补充跳转页面和登录态",
                "judgement": "确定缺陷", "rewrite": "跳转首页且展示当前用户名",
            }], [], ["统一登录用例模板"])
            review.save()
            self.assertEqual(summary["medium"], 1)
            generated = load_workbook(review.report_file.path)
            self.assertIn("问题明细", generated.sheetnames)
            self.assertEqual(generated["问题明细"]["B2"].value, 2)
            self.assertEqual(generated["审查摘要"]["A1"].value, "测试用例质量审查报告")
            self.assertEqual(generated["审查摘要"]["A1"].fill.fgColor.rgb, "001D3F66")
            self.assertEqual(generated["问题明细"]["F2"].fill.fgColor.rgb, "00FFF2D6")
            self.assertFalse(generated["问题明细"].sheet_view.showGridLines)
            self.assertEqual(generated["问题明细"].freeze_panes, "A2")
            generated.close()
            review.delete()
