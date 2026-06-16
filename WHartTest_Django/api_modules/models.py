from django.db import models
from django.contrib.auth.models import User


class ApiModule(models.Model):
    name = models.CharField(max_length=100, verbose_name='Module Name')
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_modules',
        verbose_name='Project',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Parent Module',
    )
    description = models.TextField(null=True, blank=True, verbose_name='Description')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_modules_created',
        verbose_name='Created By',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'API Module'
        verbose_name_plural = 'API Modules'
        ordering = ['created_at']

    def __str__(self):
        return self.name

    def get_ancestors(self):
        """Get all ancestor modules."""
        ancestors = []
        current = self.parent
        while current:
            ancestors.append(current)
            current = current.parent
        return ancestors

    def get_descendants(self):
        """Get all descendant modules."""
        descendants = []
        for child in self.children.all():
            descendants.append(child)
            descendants.extend(child.get_descendants())
        return descendants
