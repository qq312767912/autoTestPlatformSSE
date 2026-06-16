import json

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class ApiEnvironment(models.Model):
    """Environment configuration for API testing."""

    name = models.CharField(_("Name"), max_length=100)
    base_url = models.URLField(_("Base URL"), max_length=200)
    verify_ssl = models.BooleanField(_("Verify SSL"), default=True)
    description = models.TextField(_("Description"), blank=True)
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_environments',
        verbose_name=_("Project"),
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_children',
        verbose_name=_("Parent Environment"),
    )
    database_config = models.ForeignKey(
        'api_database_configs.ApiDatabaseConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_environments',
        verbose_name=_("Database Config"),
    )
    is_active = models.BooleanField(_("Active"), default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_environments',
        verbose_name=_("Created By"),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("API Environment")
        verbose_name_plural = _("API Environments")
        ordering = ['-created_at']
        unique_together = ['name', 'project']

    def __str__(self):
        return f"{self.project.name}-{self.name}"

    def clean(self):
        """Validate environment data to prevent circular inheritance and cross-project references."""
        if self.parent and self.parent.project_id != self.project_id:
            raise ValidationError({'parent': _('Parent environment must belong to the same project.')})

        if self.parent and self.parent.id == self.id:
            raise ValidationError({'parent': _('Cannot set self as parent environment.')})

        if self.parent:
            ancestor = self.parent
            while ancestor:
                if ancestor.parent_id and ancestor.parent_id == self.id:
                    raise ValidationError({'parent': _('Circular inheritance is not allowed.')})
                ancestor = ancestor.parent

        if self.database_config and self.database_config.project_id != self.project_id:
            raise ValidationError({
                'database_config': _('Database config must belong to the same project.'),
            })

    def get_all_variables(self):
        """Return all variables including inherited ones from parent environments."""
        variables = {}

        if self.parent:
            parent_vars = self.parent.get_all_variables()
            if isinstance(parent_vars, dict):
                variables.update(parent_vars)

        for var in self.api_variables.all():
            try:
                variables[var.name] = var.get_typed_value()
            except Exception:
                variables[var.name] = var.value

        return variables

    def get_database_config(self):
        """Return the database config, walking up the parent chain if necessary."""
        if self.database_config:
            return self.database_config
        if self.parent:
            return self.parent.get_database_config()
        return None


class ApiEnvironmentVariable(models.Model):
    """Environment variable with typed value support."""

    TYPE_CHOICES = [
        ('string', _('String')),
        ('integer', _('Integer')),
        ('float', _('Float')),
        ('boolean', _('Boolean')),
        ('json', _('JSON')),
        ('list', _('List')),
        ('dict', _('Dict')),
    ]

    environment = models.ForeignKey(
        ApiEnvironment,
        on_delete=models.CASCADE,
        related_name='api_variables',
        verbose_name=_("Environment"),
    )
    name = models.CharField(_("Name"), max_length=100)
    value = models.TextField(_("Value"))
    type = models.CharField(
        _("Type"), max_length=20, choices=TYPE_CHOICES, default='string'
    )
    description = models.CharField(_("Description"), max_length=200, blank=True)
    is_sensitive = models.BooleanField(_("Sensitive"), default=False)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("API Environment Variable")
        verbose_name_plural = _("API Environment Variables")
        ordering = ['name']
        unique_together = ['environment', 'name']

    def __str__(self):
        return f"{self.environment.name}-{self.name}"

    def clean(self):
        """Validate that the value matches the declared type."""
        try:
            if self.type == 'integer':
                int(self.value)
            elif self.type == 'float':
                float(self.value)
            elif self.type == 'boolean':
                if self.value.lower() not in ('true', 'false'):
                    raise ValueError
            elif self.type == 'json':
                json.loads(self.value)
        except (ValueError, json.JSONDecodeError):
            raise ValidationError({
                'value': _('Value does not match the declared type %(type)s.') % {
                    'type': self.get_type_display(),
                },
            })

    def get_typed_value(self):
        """Return the value cast to its declared Python type."""
        try:
            if self.type == 'integer':
                return int(self.value)
            elif self.type == 'float':
                return float(self.value)
            elif self.type == 'boolean':
                return self.value.lower() == 'true'
            elif self.type in ('json', 'list', 'dict'):
                return json.loads(self.value)
            return self.value
        except (ValueError, json.JSONDecodeError):
            return self.value


class ApiGlobalRequestHeader(models.Model):
    """Project-level global request header applied to all API requests."""

    name = models.CharField(_("Header Name"), max_length=100)
    value = models.TextField(_("Header Value"))
    description = models.CharField(_("Description"), max_length=200, blank=True)
    is_enabled = models.BooleanField(_("Enabled"), default=True)
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_global_headers',
        verbose_name=_("Project"),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_headers',
        verbose_name=_("Created By"),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("API Global Request Header")
        verbose_name_plural = _("API Global Request Headers")
        ordering = ['name']
        unique_together = ['name', 'project']

    def __str__(self):
        return f"{self.project.name}-{self.name}"
