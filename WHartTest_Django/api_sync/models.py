from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class ApiSyncConfig(models.Model):
    """Per-step sync configuration linking an interface to a test-case step."""

    SYNC_MODE_CHOICES = [
        ('manual', _('Manual')),
        ('auto', _('Auto')),
    ]

    name = models.CharField(_("Name"), max_length=100)
    description = models.TextField(_("Description"), blank=True)
    interface = models.ForeignKey(
        'api_interfaces.ApiInterface',
        on_delete=models.CASCADE,
        related_name='api_sync_configs',
        verbose_name=_("Interface"),
    )
    testcase = models.ForeignKey(
        'api_testcases.ApiTestCase',
        on_delete=models.CASCADE,
        related_name='api_sync_configs',
        verbose_name=_("Test Case"),
    )
    step = models.ForeignKey(
        'api_testcases.ApiTestCaseStep',
        on_delete=models.CASCADE,
        related_name='api_sync_configs',
        verbose_name=_("Step"),
    )
    sync_fields = models.JSONField(
        _("Sync Fields"),
        default=list,
        help_text=_("List of fields to synchronize."),
    )
    sync_enabled = models.BooleanField(_("Enabled"), default=True)
    sync_mode = models.CharField(
        _("Sync Mode"), max_length=20, choices=SYNC_MODE_CHOICES, default='manual',
    )
    sync_trigger = models.JSONField(
        _("Trigger Conditions"),
        default=dict,
        help_text=_("Trigger conditions for auto sync."),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_sync_configs',
        verbose_name=_("Created By"),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("API Sync Config")
        verbose_name_plural = _("API Sync Configs")
        unique_together = ['interface', 'testcase', 'step']
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} ({self.interface} -> {self.testcase})"

    @property
    def project(self):
        """Return the project via the interface FK for permission checks."""
        return self.interface.project if self.interface else None


class ApiSyncHistory(models.Model):
    """Audit log of each sync execution."""

    SYNC_TYPE_CHOICES = [
        ('manual', _('Manual')),
        ('auto', _('Auto')),
        ('batch', _('Batch')),
    ]
    SYNC_STATUS_CHOICES = [
        ('success', _('Success')),
        ('failed', _('Failed')),
        ('partial', _('Partial')),
    ]

    sync_config = models.ForeignKey(
        ApiSyncConfig,
        on_delete=models.CASCADE,
        related_name='api_sync_histories',
        verbose_name=_("Sync Config"),
    )
    sync_type = models.CharField(_("Sync Type"), max_length=20, choices=SYNC_TYPE_CHOICES)
    sync_status = models.CharField(_("Sync Status"), max_length=20, choices=SYNC_STATUS_CHOICES)
    sync_fields = models.JSONField(_("Sync Fields"))
    old_data = models.JSONField(_("Old Data"))
    new_data = models.JSONField(_("New Data"))
    error_message = models.TextField(_("Error Message"), blank=True)
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name=_("Operator"),
    )
    sync_time = models.DateTimeField(_("Sync Time"), auto_now_add=True)

    class Meta:
        verbose_name = _("API Sync History")
        verbose_name_plural = _("API Sync Histories")
        ordering = ['-sync_time']

    def __str__(self):
        return f"{self.sync_config.name} - {self.sync_time}"

    @property
    def project(self):
        """Return the project via the sync_config FK for permission checks."""
        return self.sync_config.project if self.sync_config else None


class ApiGlobalSyncConfig(models.Model):
    """Project-level global sync configuration. Only one may be active per project."""

    SYNC_MODE_CHOICES = [
        ('manual', _('Manual')),
        ('auto', _('Auto')),
    ]

    name = models.CharField(_("Name"), max_length=100)
    description = models.TextField(_("Description"), blank=True)
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_global_sync_configs',
        verbose_name=_("Project"),
    )
    sync_fields = models.JSONField(
        _("Sync Fields"),
        default=list,
        help_text=_("List of fields to synchronize."),
    )
    sync_enabled = models.BooleanField(_("Enabled"), default=True)
    sync_mode = models.CharField(
        _("Sync Mode"), max_length=20, choices=SYNC_MODE_CHOICES, default='manual',
    )
    is_active = models.BooleanField(
        _("Active"),
        default=False,
        help_text=_("Only one config may be active per project."),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_global_sync_configs',
        verbose_name=_("Created By"),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("API Global Sync Config")
        verbose_name_plural = _("API Global Sync Configs")
        ordering = ['-updated_at']
        unique_together = ['project', 'name']

    def __str__(self):
        status = _("active") if self.is_active else _("inactive")
        return f"{self.name} ({status})"

    def save(self, *args, **kwargs):
        if self.is_active:
            ApiGlobalSyncConfig.objects.filter(
                project=self.project,
            ).exclude(id=self.id).update(is_active=False)
        super().save(*args, **kwargs)
