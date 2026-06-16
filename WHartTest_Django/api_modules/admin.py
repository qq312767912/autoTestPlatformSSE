from django.contrib import admin
from .models import ApiModule


@admin.register(ApiModule)
class ApiModuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'parent', 'created_by', 'created_at']
    list_filter = ['project']
    search_fields = ['name', 'description']
