from django.contrib import admin

from .models import ApiDatabaseConfig


@admin.register(ApiDatabaseConfig)
class ApiDatabaseConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'db_type', 'host', 'port', 'is_active', 'created_at')
    list_filter = ('db_type', 'is_active', 'project')
    search_fields = ('name', 'host', 'database')
