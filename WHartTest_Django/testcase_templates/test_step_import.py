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

class ImportTargetModuleTests(SimpleTestCase):
    def service(self, target=None):
        from types import SimpleNamespace
        return TestCaseImportService(SimpleNamespace(module_path_delimiter='/'),SimpleNamespace(pk=1),None,target_module=target)

    def test_excel_hierarchy_belongs_to_chosen_target(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        target=SimpleNamespace(name='星企航',level=1)
        child=SimpleNamespace(name='迭代用例库',level=2)
        with patch('testcase_templates.import_service.TestCaseModule.objects.get_or_create',return_value=(child,True)) as create:
            self.assertIs(self.service(target)._get_or_create_module('迭代用例库'),child)
            self.assertIs(create.call_args.kwargs['parent'],target)
            self.assertEqual(create.call_args.kwargs['defaults']['level'],2)

    def test_empty_path_uses_chosen_target(self):
        from types import SimpleNamespace
        target=SimpleNamespace(name='星企航',level=1)
        self.assertIs(self.service(target)._get_or_create_module(''),target)
        self.assertIs(self.service(target)._get_or_create_module('星企航'),target)

    def test_no_target_retains_root_import(self):
        from unittest.mock import patch
        with patch('testcase_templates.import_service.TestCaseModule.objects.get_or_create',return_value=(object(),True)) as create:
            self.service()._get_or_create_module('迭代用例库')
            self.assertIsNone(create.call_args.kwargs['parent'])
