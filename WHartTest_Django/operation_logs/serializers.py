from rest_framework import serializers
from .models import OperationLog, OperationLogSetting, AnonymizationRule, AnonymizedDocument, AnonymizationTemplate

class OperationLogSerializer(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", read_only=True)

    class Meta:
        model = OperationLog
        fields = [
            'id', 'user', 'username', 'ip_address', 'user_agent',
            'path', 'method', 'module', 'action', 'request_data',
            'response_code', 'response_data', 'duration', 'created_at'
        ]


class OperationLogSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationLogSetting
        fields = ['id', 'retention_days', 'updated_at']
        read_only_fields = ['id', 'updated_at']


class AnonymizationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnonymizationRule
        fields = [
            'id', 'name', 'entity_type', 'entity_label', 'regex',
            'score', 'is_active', 'description', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_regex(self, value):
        """验证正则表达式是否合法"""
        import re
        try:
            re.compile(value)
        except re.error as e:
            raise serializers.ValidationError(f"正则表达式无效: {e}")
        return value


class AnonymizedDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = AnonymizedDocument
        fields = [
            'id', 'original_filename', 'file_type', 'file_size',
            'status', 'status_label', 'anonymized_at', 'anonymization_report',
            'error_message', 'enabled_preset_types', 'custom_keywords',
            'uploaded_by', 'uploaded_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'original_filename', 'file_type', 'file_size',
            'status', 'status_label', 'anonymized_at', 'anonymization_report',
            'error_message', 'uploaded_by', 'uploaded_by_name', 'created_at', 'updated_at',
        ]

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.username if obj.uploaded_by else ''

    def get_status_label(self, obj):
        label_map = {
            'pending': '待脱敏',
            'anonymizing': '脱敏中',
            'anonymized': '已脱敏',
            'failed': '脱敏失败',
        }
        return label_map.get(obj.status, obj.status)


class AnonymizationTemplateSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AnonymizationTemplate
        fields = [
            'id', 'name', 'description', 'enabled_preset_types', 'custom_keywords',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_name', 'created_at', 'updated_at']

    def get_created_by_name(self, obj):
        return obj.created_by.username if obj.created_by else ''
