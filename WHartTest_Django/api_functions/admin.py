from django.contrib import admin
from .models import ApiCustomFunction


@admin.register(ApiCustomFunction)
class ApiCustomFunctionAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'is_active', 'created_by', 'created_at']
    list_filter = ['project', 'is_active']
    search_fields = ['name', 'description']
