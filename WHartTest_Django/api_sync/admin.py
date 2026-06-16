from django.contrib import admin

from .models import ApiSyncConfig, ApiSyncHistory, ApiGlobalSyncConfig


@admin.register(ApiSyncConfig)
class ApiSyncConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'interface', 'testcase', 'step', 'sync_enabled', 'sync_mode', 'created_at')
    list_filter = ('sync_enabled', 'sync_mode')
    search_fields = ('name', 'description')
    raw_id_fields = ('interface', 'testcase', 'step', 'created_by')


@admin.register(ApiSyncHistory)
class ApiSyncHistoryAdmin(admin.ModelAdmin):
    list_display = ('sync_config', 'sync_type', 'sync_status', 'operator', 'sync_time')
    list_filter = ('sync_type', 'sync_status')
    search_fields = ('sync_config__name', 'error_message')
    raw_id_fields = ('sync_config', 'operator')


@admin.register(ApiGlobalSyncConfig)
class ApiGlobalSyncConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'sync_enabled', 'sync_mode', 'is_active', 'created_at')
    list_filter = ('sync_enabled', 'sync_mode', 'is_active')
    search_fields = ('name', 'description')
    raw_id_fields = ('created_by',)
