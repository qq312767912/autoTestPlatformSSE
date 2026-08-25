from rest_framework import serializers
from projects.models import Project  # 假设 Project 模型位于 'projects' 应用
from .models import RemoteMCPConfig, MCPTool, VisionModelConfig
import re
from urllib.parse import urlparse


class MCPProjectListSerializer(serializers.ModelSerializer):
    """
    用于 MCP 工具的项目列表序列化器。
    仅提供最小必要字段。
    """

    class Meta:
        model = Project
        fields = ["id", "name", "description"]  # 增加 description 以提供更多上下文
        # 在更复杂场景下，可添加 read_only_fields，
        # 防止字段被其他 MCP 工具端点更新。
        # 如需启用，请在此显式补充只读字段配置。


class RemoteMCPConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemoteMCPConfig
        fields = [
            "id",
            "name",
            "url",
            "transport",
            "headers",
            "is_active",
            "require_hitl",
            "hitl_tools",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_url(self, value):
        """
        自定义 URL 验证，支持：
        - 标准域名 (example.com)
        - IP 地址 (192.168.1.1)
        - Docker 容器名 (wharttest-mcp, mcp)
        - localhost
        """
        if not value:
            raise serializers.ValidationError("URL 不能为空")

        # 解析 URL
        try:
            parsed = urlparse(value)
        except Exception:
            raise serializers.ValidationError("无效的 URL 格式")

        # 检查协议
        if parsed.scheme not in ["http", "https"]:
            raise serializers.ValidationError("URL 必须使用 http 或 https 协议")

        # 检查主机名
        hostname = parsed.hostname or parsed.netloc.split(":")[0]
        if not hostname:
            raise serializers.ValidationError("URL 必须包含主机名或 IP 地址")

        # 验证主机名格式（支持多种格式）
        # 1. localhost（本机）
        if hostname == "localhost":
            return value

        # 2. IP 地址 (IPv4)
        ip_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
        if re.match(ip_pattern, hostname):
            # 验证 IP 地址范围
            parts = hostname.split(".")
            if all(0 <= int(part) <= 255 for part in parts):
                return value
            raise serializers.ValidationError("无效的 IP 地址")

        # 3. 域名或 Docker 容器名
        # 允许字母、数字、连字符、下划线和点
        hostname_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9\-_\.]*[a-zA-Z0-9])?$"
        if re.match(hostname_pattern, hostname):
            return value

        raise serializers.ValidationError("无效的主机名格式")


class MCPToolSerializer(serializers.ModelSerializer):
    """MCP 工具序列化器"""



    mcp_name = serializers.CharField(source="mcp_config.name", read_only=True)
    effective_require_hitl = serializers.BooleanField(read_only=True)

    class Meta:
        model = MCPTool
        fields = [
            "id",
            "name",
            "description",
            "input_schema",
            "mcp_name",
            "require_hitl",
            "effective_require_hitl",
            "synced_at",
        ]
        read_only_fields = ["synced_at"]


class VisionModelConfigSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.BooleanField(read_only=True)

    class Meta:
        model = VisionModelConfig
        fields = [
            "id", "name", "base_url", "chat_completions_path", "model",
            "api_key", "has_api_key", "timeout_seconds", "max_retries",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "has_api_key", "created_at", "updated_at"]

    def validate_chat_completions_path(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Chat Completions 路径不能为空")
        return "/" + value.strip("/")

    def validate(self, attrs):
        timeout = attrs.get("timeout_seconds", getattr(self.instance, "timeout_seconds", 120))
        retries = attrs.get("max_retries", getattr(self.instance, "max_retries", 2))
        if not 1 <= timeout <= 1800:
            raise serializers.ValidationError({"timeout_seconds": "超时时间必须在 1~1800 秒之间"})
        if retries > 10:
            raise serializers.ValidationError({"max_retries": "最大重试次数不能超过 10"})
        return attrs

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", "")
        instance = VisionModelConfig(**validated_data)
        instance.set_api_key(api_key)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        # 编辑时留空代表保留原密钥，前端无需也不能回填明文。
        if api_key:
            instance.set_api_key(api_key)
        instance.save()
        return instance
