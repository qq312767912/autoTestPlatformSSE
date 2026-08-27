from rest_framework import serializers
from .models import ImportExportTemplate


class ImportExportTemplateSerializer(serializers.ModelSerializer):
    """导入导出模版序列化器"""
    creator_name = serializers.CharField(source='creator.username', read_only=True)
    template_type_display = serializers.CharField(source='get_template_type_display', read_only=True)
    step_parsing_mode_display = serializers.CharField(source='get_step_parsing_mode_display', read_only=True)

    class Meta:
        model = ImportExportTemplate
        fields = [
            'id',
            'name',
            'template_type',
            'template_type_display',
            'description',
            'sheet_name',
            'template_file',
            'template_headers',
            'header_row',
            'data_start_row',
            'field_mappings',
            'value_transformations',
            'step_parsing_mode',
            'step_parsing_mode_display',
            'step_config',
            'module_path_delimiter',
            'module_parsing_mode',
            'module_hierarchy_columns',
            'is_active',
            'creator',
            'creator_name',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'creator', 'creator_name', 'created_at', 'updated_at']

    def validate(self, attrs):
        instance = self.instance
        mode = attrs.get('module_parsing_mode', getattr(instance, 'module_parsing_mode', 'path'))
        mappings = attrs.get('field_mappings', getattr(instance, 'field_mappings', {}) or {})
        delimiter = attrs.get('module_path_delimiter', getattr(instance, 'module_path_delimiter', '/'))
        columns = attrs.get(
            'module_hierarchy_columns',
            getattr(instance, 'module_hierarchy_columns', []) or []
        )

        if mode == 'columns':
            normalized = [str(column).strip() for column in columns if str(column).strip()]
            if not normalized:
                raise serializers.ValidationError({
                    'module_hierarchy_columns': '多列表头层级模式下，请至少选择一个模块表头。'
                })
            if len(normalized) > 10:
                raise serializers.ValidationError({
                    'module_hierarchy_columns': '模块层级最多支持10级。'
                })
            if len(normalized) != len(set(normalized)):
                raise serializers.ValidationError({
                    'module_hierarchy_columns': '模块层级表头不能重复。'
                })
            attrs['module_hierarchy_columns'] = normalized
        else:
            if not mappings.get('module'):
                raise serializers.ValidationError({'field_mappings': '请选择所属模块对应的Excel列。'})
            if not delimiter:
                raise serializers.ValidationError({'module_path_delimiter': '模块路径分隔符不能为空。'})

        return attrs

    def create(self, validated_data):
        """创建时自动设置创建人"""
        request = self.context.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            validated_data['creator'] = request.user
        return super().create(validated_data)


class ImportExportTemplateListSerializer(serializers.ModelSerializer):
    """导入导出模版列表序列化器（简化版，用于列表展示）"""
    creator_name = serializers.CharField(source='creator.username', read_only=True)
    template_type_display = serializers.CharField(source='get_template_type_display', read_only=True)

    class Meta:
        model = ImportExportTemplate
        fields = [
            'id',
            'name',
            'template_type',
            'template_type_display',
            'description',
            'is_active',
            'creator_name',
            'created_at',
            'updated_at',
        ]


class ParseHeadersRequestSerializer(serializers.Serializer):
    """解析 Excel 表头请求序列化器"""
    file = serializers.FileField(help_text='要解析的 Excel 文件')
    sheet_name = serializers.CharField(required=False, allow_blank=True, help_text='工作表名称，为空则使用第一个工作表')
    header_row = serializers.IntegerField(required=False, default=1, min_value=1, help_text='表头所在行号（从1开始）')


class ParseHeadersResponseSerializer(serializers.Serializer):
    """解析 Excel 表头响应序列化器"""
    headers = serializers.ListField(child=serializers.CharField(), help_text='解析出的表头列名列表')
    sheet_names = serializers.ListField(child=serializers.CharField(), help_text='工作簿中所有工作表名称')
    row_count = serializers.IntegerField(help_text='数据行数（不含表头）')
    sample_data = serializers.ListField(help_text='前几行样例数据')
