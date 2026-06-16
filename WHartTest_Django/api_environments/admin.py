from django.contrib import admin

from .models import ApiEnvironment, ApiEnvironmentVariable, ApiGlobalRequestHeader


@admin.register(ApiEnvironment)
class ApiEnvironmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'base_url', 'is_active', 'created_at')
    list_filter = ('is_active', 'project')
    search_fields = ('name', 'base_url')


@admin.register(ApiEnvironmentVariable)
class ApiEnvironmentVariableAdmin(admin.ModelAdmin):
    list_display = ('name', 'environment', 'type', 'is_sensitive', 'created_at')
    list_filter = ('type', 'is_sensitive')
    search_fields = ('name',)


@admin.register(ApiGlobalRequestHeader)
class ApiGlobalRequestHeaderAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'is_enabled', 'created_at')
    list_filter = ('is_enabled', 'project')
    search_fields = ('name',)
