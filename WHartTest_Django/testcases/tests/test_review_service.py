import os
import tempfile

from django.test import SimpleTestCase
from openpyxl import Workbook

from testcases.review_service import _read_rows


class TestCaseReviewReaderTests(SimpleTestCase):
    def test_recognizes_case_headers_and_filters_metadata_and_group_rows(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "cases.xlsx")
            workbook = Workbook()
            cover = workbook.active
            cover.title = "封面"
            cover.append(["系统测试用例"])

            cases = workbook.create_sheet("登录")
            cases.append(["编号", "模块", "步骤", "预期"])
            cases.append(["TC-1", "登录", "输入账号密码", "登录成功"])

            compatibility = workbook.create_sheet("兼容性")
            compatibility.append(["项目名称", "兼容性测试用例"])
            compatibility.append(["操作系统", "版本", "操作步骤", "期望输出"])
            compatibility.append(["Chrome 系列"])
            compatibility.append(["Windows 10", "64位", "打开页面", "页面布局完整"])
            workbook.save(source)

            rows = _read_rows(source)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sheet"], "登录")
        self.assertEqual(rows[0]["row"], 2)
        self.assertEqual(rows[0]["columns"]["预期"], "登录成功")
        self.assertEqual(rows[1]["sheet"], "兼容性")
        self.assertEqual(rows[1]["row"], 4)
