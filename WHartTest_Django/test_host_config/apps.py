from django.apps import AppConfig


class TestHostConfigAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "test_host_config"
    verbose_name = "测试域名配置"
