from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApiSyncConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api_sync"
    verbose_name = _("API Sync")

    def ready(self):
        try:
            import api_sync.signals  # noqa: F401
        except ImportError:
            pass
