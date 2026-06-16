from django.contrib import admin
from .models import ApiInterface, ApiInterfaceResult


@admin.register(ApiInterface)
class ApiInterfaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'type', 'project', 'module', 'created_by', 'created_at']
    list_filter = ['project', 'type']
    search_fields = ['name']


@admin.register(ApiInterfaceResult)
class ApiInterfaceResultAdmin(admin.ModelAdmin):
    list_display = ['interface', 'success', 'elapsed', 'executed_by', 'executed_at']
    list_filter = ['success']
