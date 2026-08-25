from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mcp_tools", "0007_add_mcp_tool_model")]

    operations = [
        migrations.CreateModel(
            name="VisionModelConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Vision MCP", max_length=255, verbose_name="配置名称")),
                ("base_url", models.URLField(max_length=2048, verbose_name="API 基础地址")),
                ("chat_completions_path", models.CharField(default="/chat/completions", max_length=255, verbose_name="Chat Completions 路径")),
                ("model", models.CharField(max_length=255, verbose_name="视觉模型")),
                ("encrypted_api_key", models.TextField(blank=True, default="", verbose_name="加密 API Key")),
                ("timeout_seconds", models.PositiveIntegerField(default=120, verbose_name="超时秒数")),
                ("max_retries", models.PositiveSmallIntegerField(default=2, verbose_name="最大重试次数")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Vision MCP 模型配置", "verbose_name_plural": "Vision MCP 模型配置"},
        )
    ]
