from django.test import SimpleTestCase
from .import_service import TestCaseImportService


class StepImportParsingTests(SimpleTestCase):
    def parse(self, actions, expected=''):
        return TestCaseImportService.__new__(TestCaseImportService)._parse_steps_single_cell(actions, expected)

    def test_repeated_numbers_are_sequential_and_expectations_preserved(self):
        steps=self.parse('[3]点击按钮\n[3]确认', '[3]出现弹窗\n[3]保存成功')
        self.assertEqual([s['step_number'] for s in steps],[1,2])
        self.assertEqual([s['expected_result'] for s in steps],['出现弹窗','保存成功'])

    def test_numbers_and_brackets_inside_content_are_preserved(self):
        steps=self.parse('[1]输入值[3]与版本1.2，访问 /items/3\n继续输入123\n[2]点击确认')
        self.assertEqual(len(steps),2)
        self.assertIn('值[3]与版本1.2',steps[0]['description'])
        self.assertIn('继续输入123',steps[0]['description'])

    def test_dot_numbers_and_multiline_content(self):
        steps=self.parse('3.输入123\n保留第二行\n3. 确认','3.预期A\n3. 预期B')
        self.assertEqual([s['step_number'] for s in steps],[1,2])
        self.assertEqual(steps[0]['description'],'输入123\n保留第二行')
        self.assertEqual(steps[1]['expected_result'],'预期B')

    def test_unmatched_expectations_not_discarded(self):
        steps=self.parse('[1]确认','[1]成功\n[5]其他检查')
        self.assertIn('其他检查',steps[0]['expected_result'])

    def test_expected_only_not_discarded(self):
        self.assertEqual(self.parse('','检查A\n检查B')[0]['expected_result'],'检查A\n检查B')
