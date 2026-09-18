"""用例审查专用 LLM 配置表。

首次升级时把当前启用的平台通用配置复制一份，升级后两套配置彼此独立：
此后修改通用配置不再影响用例审查，反之亦然。
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations, models


def copy_active_platform_llm(apps, schema_editor):
    """复制当前启用的通用 LLM 配置作为用例审查的初始专用配置。

    刻意只声明对 testcases 自身迁移的依赖，避免绑死 langgraph_integration 的
    迁移进度（内网历史库的该应用迁移状态可能与代码分支不同步）。因此这里对
    「模型尚未进入 migration state」做兜底：取不到就跳过，留给管理员手工配置。
    """
    try:
        LLMConfig = apps.get_model("langgraph_integration", "LLMConfig")
    except LookupError:
        return

    TestCaseReviewLLMConfig = apps.get_model("testcases", "TestCaseReviewLLMConfig")
    if TestCaseReviewLLMConfig.objects.exists():
        return

    source = LLMConfig.objects.filter(is_active=True).order_by("pk").first()
    if not source:
        return

    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    cipher = Fernet(base64.urlsafe_b64encode(digest))
    api_key = getattr(source, "api_key", "") or ""
    encrypted_key = cipher.encrypt(api_key.encode("utf-8")).decode("ascii") if api_key else ""
    TestCaseReviewLLMConfig.objects.create(
        config_name="用例审查专用 - %s" % source.config_name,
        name=source.name,
        api_url=source.api_url,
        encrypted_api_key=encrypted_key,
        request_timeout=max(30, source.request_timeout or 600),
        max_retries=source.max_retries or 0,
        is_active=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("testcases", "0024_testcasereview_review_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="TestCaseReviewLLMConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("config_name", models.CharField(default="用例审查 LLM", max_length=255, verbose_name="配置名称")),
                ("name", models.CharField(max_length=255, verbose_name="模型名称")),
                ("api_url", models.URLField(max_length=2048, verbose_name="API 地址")),
                ("encrypted_api_key", models.TextField(blank=True, default="", verbose_name="加密 API Key")),
                ("request_timeout", models.PositiveIntegerField(default=600, verbose_name="请求超时秒数")),
                ("max_retries", models.PositiveSmallIntegerField(default=2, verbose_name="最大重试次数")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "用例审查 LLM 配置",
                "verbose_name_plural": "用例审查 LLM 配置",
            },
        ),
        migrations.RunPython(copy_active_platform_llm, migrations.RunPython.noop),
    ]
