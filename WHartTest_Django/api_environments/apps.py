from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApiEnvironmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api_environments"
    verbose_name = _("API Environments")
