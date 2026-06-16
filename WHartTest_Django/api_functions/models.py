from django.db import models
from django.contrib.auth.models import User


class ApiCustomFunction(models.Model):
    name = models.CharField(max_length=100, verbose_name='Function Name')
    code = models.TextField(verbose_name='Function Code')
    description = models.TextField(blank=True, verbose_name='Description')
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_custom_functions',
        verbose_name='Project',
    )
    is_active = models.BooleanField(default=True, verbose_name='Is Active')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_custom_functions_created',
        verbose_name='Created By',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'API Custom Function'
        verbose_name_plural = 'API Custom Functions'
        ordering = ['-created_at']
        unique_together = ['name', 'project']

    def __str__(self):
        return f"{self.project.name}-{self.name}"
