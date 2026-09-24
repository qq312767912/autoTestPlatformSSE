from rest_framework import serializers

from .models import (
    TestHostConfigVersion,
    TestHostDiagnosis,
    TestHostMapping,
    TestHostNodeStatus,
)
from .validators import normalize_hostname, validate_safe_ipv4


class TestHostMappingSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.username", read_only=True)
    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True)

    class Meta:
        model = TestHostMapping
        fields = [
            "id", "system_name", "hostname", "ipv4", "enabled", "remark",
            "created_by_name", "updated_by_name", "created_at", "updated_at",
        ]
        read_only_fields = ["created_by_name", "updated_by_name", "created_at", "updated_at"]

    def validate_hostname(self, value):
        return normalize_hostname(value)

    def validate_ipv4(self, value):
        return validate_safe_ipv4(value)


class TestHostConfigVersionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.username", read_only=True)
    source_version_number = serializers.IntegerField(source="source_version.version", read_only=True)

    class Meta:
        model = TestHostConfigVersion
        fields = [
            "id", "version", "source_draft_revision", "snapshot", "checksum", "status",
            "source_version_number", "created_by_name", "created_at", "published_at", "error_message",
        ]


class TestHostNodeStatusSerializer(serializers.ModelSerializer):
    effective_status = serializers.SerializerMethodField()

    class Meta:
        model = TestHostNodeStatus
        fields = [
            "node_id", "node_type", "display_name", "applied_version", "applied_checksum",
            "status", "effective_status", "message", "details", "last_seen_at", "updated_at",
        ]

    def get_effective_status(self, obj):
        from django.utils import timezone
        if (timezone.now() - obj.last_seen_at).total_seconds() > 90:
            return "offline"
        from .models import TestHostConfigState
        state = TestHostConfigState.get_state()
        if obj.status == "synced" and state.published_version and obj.applied_version != state.published_version.version:
            return "pending"
        return obj.status


class TestHostDiagnosisSerializer(serializers.ModelSerializer):
    hostname = serializers.CharField(source="mapping.hostname", read_only=True)
    ipv4 = serializers.CharField(source="mapping.ipv4", read_only=True)

    class Meta:
        model = TestHostDiagnosis
        fields = [
            "id", "mapping", "hostname", "ipv4", "status", "result", "error_code",
            "error_message", "duration_ms", "created_at", "started_at", "finished_at",
        ]
        read_only_fields = fields


class AgentNodeReportSerializer(serializers.Serializer):
    node_id = serializers.RegexField(r"^(host|wharttest-(?:backend|playwright-mcp|actuator-[A-Za-z0-9_.-]+))$", max_length=128)
    node_type = serializers.ChoiceField(choices=["host", "backend", "playwright", "actuator"])
    display_name = serializers.CharField(max_length=128)
    applied_version = serializers.IntegerField(min_value=1, allow_null=True)
    applied_checksum = serializers.RegexField(r"^[a-f0-9]{64}$", allow_blank=True)
    status = serializers.ChoiceField(choices=["synced", "pending", "failed"])
    message = serializers.CharField(max_length=500, allow_blank=True, required=False)
    details = serializers.JSONField(required=False)


class AgentReportSerializer(serializers.Serializer):
    nodes = AgentNodeReportSerializer(many=True, min_length=1, max_length=128)
