import io
import re
import zipfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project
from testcases.models import TestCase as TestCaseModel, TestCaseModule

from .import_service import TestCaseImportService
from .models import ImportExportTemplate
from .serializers import ImportExportTemplateSerializer
from .views import ImportExportTemplateViewSet


class ModuleHierarchyImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='module-importer')
        self.project = Project.objects.create(name='层级导入项目', creator=self.user)

    @staticmethod
    def _excel_file(headers, rows):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '用例'
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        return ContentFile(stream.getvalue(), name='cases.xlsx')

    def test_imports_six_module_columns_without_modifying_excel(self):
        columns = [f'{index}级模块' for index in range(1, 7)]
        template = ImportExportTemplate.objects.create(
            name='六级模块模板',
            sheet_name='用例',
            field_mappings={'name': '用例名称'},
            module_parsing_mode='columns',
            module_hierarchy_columns=columns,
            module_path_delimiter='/',
            creator=self.user,
        )
        file = self._excel_file(
            [*columns, '用例名称'],
            [['系统', '版本', '需求', '功能', '场景', '数据', '六级模块导入用例']],
        )

        result = TestCaseImportService(template, self.project, self.user).import_from_file(file)

        self.assertTrue(result.success)
        self.assertEqual(result.imported_count, 1)
        testcase = TestCaseModel.objects.get(name='六级模块导入用例')
        self.assertEqual(testcase.module.level, 6)
        self.assertEqual(str(testcase.module), '系统 > 版本 > 需求 > 功能 > 场景 > 数据')
        self.assertEqual(TestCaseModule.objects.filter(project=self.project).count(), 6)

    def test_reports_hierarchy_gap_as_row_error(self):
        template = ImportExportTemplate.objects.create(
            name='断层模块模板',
            sheet_name='用例',
            field_mappings={'name': '用例名称'},
            module_parsing_mode='columns',
            module_hierarchy_columns=['一级模块', '二级模块', '三级模块'],
            creator=self.user,
        )
        file = self._excel_file(
            ['一级模块', '二级模块', '三级模块', '用例名称'],
            [['系统', '', '场景', '断层用例']],
        )

        result = TestCaseImportService(template, self.project, self.user).import_from_file(file)

        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.error_count, 1)
        self.assertIn('模块层级存在断层', result.errors[0]['error'])

    def test_legacy_module_mapping_auto_detects_all_hierarchy_headers(self):
        columns = ['一级模块', '二级模块', '三级模块', '四级模块', '五级模块', '六级模块']
        template = ImportExportTemplate.objects.create(
            name='旧版单列模块模板',
            sheet_name='用例',
            field_mappings={'name': '用例名称', 'module': '三级模块'},
            module_parsing_mode='path',
            creator=self.user,
        )
        file = self._excel_file(
            [*columns, '用例名称'],
            [[
                '投票统计系统', '2026迭代版本用例', '202608_联通免密登录',
                '免密登录功能', '正常场景', '手机号登录', '旧模板兼容用例',
            ]],
        )

        result = TestCaseImportService(template, self.project, self.user).import_from_file(file)

        self.assertTrue(result.success)
        testcase = TestCaseModel.objects.get(name='旧模板兼容用例')
        self.assertEqual(testcase.module.level, 6)
        self.assertEqual(
            str(testcase.module),
            '投票统计系统 > 2026迭代版本用例 > 202608_联通免密登录 > '
            '免密登录功能 > 正常场景 > 手机号登录',
        )

    def test_legacy_module_mapping_supports_three_level_document(self):
        template = ImportExportTemplate.objects.create(
            name='三级模块旧模板',
            sheet_name='用例',
            field_mappings={'name': '用例名称', 'module': '三级模块'},
            module_parsing_mode='path',
            creator=self.user,
        )
        file = self._excel_file(
            ['一级模块', '二级模块', '三级模块', '用例名称'],
            [['系统', '版本', '功能', '三级模块兼容用例']],
        )

        result = TestCaseImportService(template, self.project, self.user).import_from_file(file)

        self.assertTrue(result.success)
        testcase = TestCaseModel.objects.get(name='三级模块兼容用例')
        self.assertEqual(testcase.module.level, 3)
        self.assertEqual(str(testcase.module), '系统 > 版本 > 功能')

    def test_import_normalizes_chinese_level_and_test_type(self):
        template = ImportExportTemplate.objects.create(
            name='中文等级和类型模板',
            sheet_name='用例',
            field_mappings={
                'name': '用例名称', 'module': '模块路径',
                'level': '优先级', 'test_type': '用例类型',
            },
            creator=self.user,
        )
        file = self._excel_file(
            ['模块路径', '用例名称', '优先级', '用例类型'],
            [['系统/功能', '中文字段导入用例', '高', '功能测试']],
        )

        result = TestCaseImportService(template, self.project, self.user).import_from_file(file)

        self.assertTrue(result.success)
        testcase = TestCaseModel.objects.get(name='中文字段导入用例')
        self.assertEqual(testcase.level, 'P0')
        self.assertEqual(testcase.test_type, 'functional')

    def test_parse_headers_ignores_incorrect_excel_dimension_metadata(self):
        source = self._excel_file(
            ['ID', '一级模块', '二级模块', '三级模块', '用例名称'],
            [['T1', '系统', '功能', '场景', '维度兼容用例']],
        )
        source.seek(0)
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source.read()), 'r') as source_zip:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target_zip:
                for item in source_zip.infolist():
                    content = source_zip.read(item.filename)
                    if item.filename == 'xl/worksheets/sheet1.xml':
                        content = re.sub(
                            rb'<dimension ref="[^"]+"\s*/>',
                            b'<dimension ref="A1:A1"/>',
                            content,
                            count=1,
                        )
                    target_zip.writestr(item, content)

        request = APIRequestFactory().post(
            '/api/testcase-templates/parse_headers/',
            {
                'file': SimpleUploadedFile(
                    'wrong-dimension.xlsx',
                    output.getvalue(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                ),
                'header_row': 1,
            },
            format='multipart',
        )
        force_authenticate(request, user=self.user)
        view = ImportExportTemplateViewSet.as_view({'post': 'parse_headers'})

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['headers'],
            ['ID', '一级模块', '二级模块', '三级模块', '用例名称'],
        )

    def test_template_validation_is_conditional_by_module_mode(self):
        columns_serializer = ImportExportTemplateSerializer(data={
            'name': '多列表头',
            'field_mappings': {'name': '用例名称'},
            'module_parsing_mode': 'columns',
            'module_hierarchy_columns': ['一级模块', '二级模块'],
        })
        self.assertTrue(columns_serializer.is_valid(), columns_serializer.errors)

        path_serializer = ImportExportTemplateSerializer(data={
            'name': '单列路径',
            'field_mappings': {'name': '用例名称'},
            'module_parsing_mode': 'path',
        })
        self.assertFalse(path_serializer.is_valid())
        self.assertIn('field_mappings', path_serializer.errors)
